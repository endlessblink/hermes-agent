#!/usr/bin/env python3
"""Create validated full Hermes backups with bounded daily/weekly retention."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
import fcntl
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


DEFAULT_KEEP = {"daily": 7, "weekly": 4}
DEFAULT_MINIMUM_FREE_BYTES = 1024 * 1024 * 1024
_SOURCE_EXCLUDED_ROOT_DIRS = {"hermes-agent"}


class BackupRotationError(RuntimeError):
    """Base class for a safe, user-actionable backup failure."""


class BackupAlreadyRunning(BackupRotationError):
    """Another daily or weekly backup owns the output-root lock."""


class InsufficientSpace(BackupRotationError):
    """The destination cannot safely hold a new full backup."""


class InvalidArchive(BackupRotationError):
    """The newly created archive failed ZIP integrity validation."""


Runner = Callable[..., object]


def available_bytes(path: Path) -> int:
    """Return free bytes on the filesystem containing *path*."""
    return shutil.disk_usage(path).free


def estimate_source_bytes(home: Path) -> int:
    """Conservatively estimate regular-file bytes in the Hermes home tree."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(home, followlinks=False):
        current = Path(dirpath)
        if current == home:
            dirnames[:] = [
                name for name in dirnames if name not in _SOURCE_EXCLUDED_ROOT_DIRS
            ]
        for filename in filenames:
            path = current / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                total += path.stat().st_size
            except OSError:
                # The canonical backup command reports files that vanish while
                # scanning. A missing file must not defeat the space preflight.
                continue
    return total


def _run_backup(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, check=True, env=env)


def _validate_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if not archive.namelist():
                raise InvalidArchive("backup archive is empty")
            corrupt_member = archive.testzip()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise InvalidArchive(f"backup is not a valid ZIP archive: {exc}") from exc
    if corrupt_member is not None:
        raise InvalidArchive(f"backup contains a corrupt member: {corrupt_member}")


def _prune_archives(kind_dir: Path, kind: str, keep: int) -> None:
    archives = sorted(
        kind_dir.glob(f"hermes-full-{kind}-*.zip"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for archive in archives[keep:]:
        archive.unlink()


def create_rotated_backup(
    *,
    home: Path,
    output_root: Path,
    kind: str,
    keep: int,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    runner: Runner = _run_backup,
) -> Path:
    """Create, validate, publish, then prune a full daily or weekly backup."""
    home = home.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if kind not in DEFAULT_KEEP:
        raise ValueError(f"unsupported backup kind: {kind}")
    if keep < 1:
        raise ValueError("retention must keep at least one archive")
    if minimum_free_bytes < 0:
        raise ValueError("minimum free bytes cannot be negative")
    if not home.is_dir():
        raise BackupRotationError(f"Hermes home does not exist: {home}")
    if output_root == home or output_root.is_relative_to(home):
        raise BackupRotationError("backup output root must be outside Hermes home")

    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".hermes-full-backup.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupAlreadyRunning("another full backup is already running") from exc

        source_bytes = estimate_source_bytes(home)
        required_bytes = math.ceil(source_bytes * 1.05) + minimum_free_bytes
        free_bytes = available_bytes(output_root)
        if free_bytes < required_bytes:
            raise InsufficientSpace(
                f"backup needs at least {required_bytes} free bytes; "
                f"destination has {free_bytes}"
            )

        kind_dir = output_root / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        final_path = kind_dir / f"hermes-full-{kind}-{stamp}.zip"
        temp_path = kind_dir / f".{final_path.name}.{os.getpid()}.tmp.zip"
        command = [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "backup",
            "--output",
            str(temp_path),
        ]
        env = {**os.environ, "HERMES_HOME": str(home)}

        try:
            runner(command, env=env)
            if not temp_path.is_file():
                raise InvalidArchive("backup command did not create an archive")
            _validate_archive(temp_path)
            with temp_path.open("rb") as archive_file:
                os.fsync(archive_file.fileno())
            os.replace(temp_path, final_path)
            directory_fd = os.open(kind_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)

        # Existing recovery points are touched only after a new archive is
        # validated and durably published.
        _prune_archives(kind_dir, kind, keep)
        return final_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(DEFAULT_KEEP), required=True)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ.get("HERMES_HOME", "~/.hermes")),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("HERMES_BACKUP_ROOT", "~/.hermes-backups")),
    )
    parser.add_argument("--keep", type=int)
    parser.add_argument(
        "--minimum-free-bytes", type=int, default=DEFAULT_MINIMUM_FREE_BYTES
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    keep = args.keep if args.keep is not None else DEFAULT_KEEP[args.kind]
    try:
        archive = create_rotated_backup(
            home=args.home,
            output_root=args.output_root,
            kind=args.kind,
            keep=keep,
            minimum_free_bytes=args.minimum_free_bytes,
        )
    except BackupAlreadyRunning as exc:
        print(f"Backup skipped: {exc}", file=sys.stderr)
        return 75
    except BackupRotationError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Backup command failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    print(f"Backup published: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
