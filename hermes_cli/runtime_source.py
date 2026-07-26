"""Deterministic, fail-closed identity for a running Hermes source checkout."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Iterable


MAX_FILES = 25_000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "token.json",
        "tokens.json",
    }
)


class RuntimeSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSourceEntry:
    path: str
    kind: str
    executable: bool
    size: int
    sha256: str


@dataclass(frozen=True)
class RuntimeSourceManifest:
    schema_version: int
    root: str
    revision: str
    dirty: bool
    digest: str
    total_bytes: int
    entries: tuple[RuntimeSourceEntry, ...]

    def public_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "root": self.root,
            "revision": self.revision,
            "dirty": self.dirty,
            "sourceManifestDigest": self.digest,
            "fileCount": len(self.entries),
            "totalBytes": self.total_bytes,
        }


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeSourceError(f"git source inspection failed: {exc}") from exc
    return result.stdout


def _paths(raw: bytes) -> Iterable[str]:
    for item in raw.split(b"\0"):
        if item:
            yield item.decode("utf-8", errors="strict")


def _validate_relative_path(value: str) -> str:
    normalized = value.replace(os.sep, "/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or ".." in pure.parts or normalized != pure.as_posix():
        raise RuntimeSourceError("invalid source path")
    folded_parts = [part.casefold() for part in pure.parts]
    if any(part == ".git" for part in folded_parts):
        raise RuntimeSourceError("git metadata cannot enter runtime source manifest")
    filename = folded_parts[-1]
    safe_env_template = filename in {".env.example", ".env.sample", ".env.template"}
    if filename in _SENSITIVE_NAMES or (filename.startswith(".env.") and not safe_env_template):
        raise RuntimeSourceError(f"sensitive source path rejected: {normalized}")
    return normalized


def _file_entry(root: Path, relative: str, *, tracked: bool) -> RuntimeSourceEntry:
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if tracked:
            return RuntimeSourceEntry(relative, "deleted", False, 0, "")
        raise RuntimeSourceError("untracked source vanished during inspection")
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeSourceError(f"symlink source path rejected: {relative}")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeSourceError(f"special source path rejected: {relative}")
    if metadata.st_nlink != 1:
        raise RuntimeSourceError(f"hardlinked source path rejected: {relative}")
    size = int(metadata.st_size)
    if size > MAX_FILE_BYTES:
        raise RuntimeSourceError(f"source file limit exceeded: {relative}")
    sparse = False
    if size and hasattr(os, "SEEK_DATA") and hasattr(os, "SEEK_HOLE"):
        try:
            with path.open("rb", buffering=0) as handle:
                first_data = os.lseek(handle.fileno(), 0, os.SEEK_DATA)
                first_hole = os.lseek(handle.fileno(), 0, os.SEEK_HOLE)
            sparse = first_data > 0 or first_hole < size
        except OSError:
            sparse = False
    safe_sparse_asset = tracked and path.suffix.casefold() in {
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".webp",
    }
    if sparse and not safe_sparse_asset:
        raise RuntimeSourceError(f"sparse source path rejected: {relative}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeSourceError(f"source read failed: {relative}") from exc
    if len(content) != size:
        raise RuntimeSourceError(f"source changed during inspection: {relative}")
    return RuntimeSourceEntry(
        relative,
        "file",
        bool(metadata.st_mode & stat.S_IXUSR),
        size,
        hashlib.sha256(content).hexdigest(),
    )


def build_runtime_source_manifest(root: Path | str) -> RuntimeSourceManifest:
    source_root = Path(root).expanduser()
    if not source_root.is_absolute():
        raise RuntimeSourceError("runtime source root must be absolute")
    try:
        source_root = source_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeSourceError("runtime source root is unavailable") from exc
    git_root = Path(_git(source_root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if git_root != source_root:
        raise RuntimeSourceError("runtime source root must be the git top level")

    tracked = {_validate_relative_path(item) for item in _paths(_git(source_root, "ls-files", "-z", "--cached"))}
    untracked = {
        _validate_relative_path(item)
        for item in _paths(_git(source_root, "ls-files", "-z", "--others", "--exclude-standard"))
    }
    all_paths = sorted(tracked | untracked)
    if len(all_paths) > MAX_FILES:
        raise RuntimeSourceError("runtime source file limit exceeded")

    entries = tuple(
        _file_entry(source_root, relative, tracked=relative in tracked)
        for relative in all_paths
    )
    total_bytes = sum(entry.size for entry in entries)
    if total_bytes > MAX_TOTAL_BYTES:
        raise RuntimeSourceError("runtime source byte limit exceeded")

    digest_input = "".join(
        f"{entry.path}\0{entry.kind}\0{int(entry.executable)}\0{entry.size}\0{entry.sha256}\n"
        for entry in entries
    ).encode("utf-8")
    revision = _git(source_root, "rev-parse", "HEAD").decode().strip()[:64]
    dirty = bool(_git(source_root, "status", "--porcelain=v1", "-z"))
    return RuntimeSourceManifest(
        schema_version=1,
        root=str(source_root),
        revision=revision,
        dirty=dirty,
        digest=hashlib.sha256(digest_input).hexdigest(),
        total_bytes=total_bytes,
        entries=entries,
    )


def capture_runtime_source_snapshot(
    root: Path | str,
    *,
    expected_digest: str,
    destination_root: Path | str,
    snapshot_id: str,
) -> Path:
    """Copy the proven dirty source into a private, independent Git repository."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", snapshot_id):
        raise RuntimeSourceError("invalid snapshot id")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_digest or "")):
        raise RuntimeSourceError("invalid expected source digest")
    source_root = Path(root).expanduser().resolve(strict=True)
    snapshots_root = Path(destination_root).expanduser()
    snapshots_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(snapshots_root, 0o700)
    except OSError:
        pass
    target = snapshots_root / snapshot_id
    if target.exists():
        raise RuntimeSourceError("snapshot target already exists")

    before = build_runtime_source_manifest(source_root)
    if before.digest != expected_digest:
        raise RuntimeSourceError("runtime source digest mismatch")

    temporary = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=snapshots_root))
    try:
        os.chmod(temporary, 0o700)
        for entry in before.entries:
            if entry.kind == "deleted":
                continue
            source = source_root.joinpath(*PurePosixPath(entry.path).parts)
            destination = temporary.joinpath(*PurePosixPath(entry.path).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            os.chmod(destination, 0o700 if entry.executable else 0o600)
            copied = destination.read_bytes()
            if len(copied) != entry.size or hashlib.sha256(copied).hexdigest() != entry.sha256:
                raise RuntimeSourceError(f"snapshot verification failed: {entry.path}")

        after = build_runtime_source_manifest(source_root)
        if after.digest != before.digest:
            raise RuntimeSourceError("runtime source changed during snapshot")

        _git(temporary, "init", "-q")
        _git(temporary, "add", "-f", "-A")
        _git(
            temporary,
            "-c",
            "user.name=Hermes Repair Snapshot",
            "-c",
            "user.email=repair@localhost",
            "commit",
            "-qm",
            "Hermes repair baseline",
            "--allow-empty",
        )
        temporary.rename(target)
        return target
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--include-entries", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = build_runtime_source_manifest(args.root)
    except RuntimeSourceError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)[:200]}, sort_keys=True))
        return 2
    payload = {"ok": True, **manifest.public_dict()}
    if args.include_entries:
        payload["entries"] = [asdict(entry) for entry in manifest.entries]
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
