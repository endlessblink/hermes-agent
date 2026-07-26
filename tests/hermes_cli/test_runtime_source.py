from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli.runtime_source import (
    RuntimeSourceError,
    build_runtime_source_manifest,
    capture_runtime_source_snapshot,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _git_output(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    (repo / "deleted.py").write_text("old\n", encoding="utf-8")
    _git(repo, "add", "app.py", "deleted.py")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_runtime_source_manifest_covers_dirty_and_untracked_bytes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("print('two')\n", encoding="utf-8")
    (repo / "deleted.py").unlink()
    (repo / "new_test.py").write_text("def test_new(): pass\n", encoding="utf-8")

    first = build_runtime_source_manifest(repo)
    second = build_runtime_source_manifest(repo)

    assert first.digest == second.digest
    assert first.dirty is True
    assert {entry.path: entry.kind for entry in first.entries} == {
        "app.py": "file",
        "deleted.py": "deleted",
        "new_test.py": "file",
    }


def test_runtime_source_manifest_rejects_tracked_secret(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env.production").write_text("API_KEY=private\n", encoding="utf-8")
    _git(repo, "add", "-f", ".env.production")

    with pytest.raises(RuntimeSourceError, match="sensitive"):
        build_runtime_source_manifest(repo)


def test_runtime_source_manifest_allows_tracked_env_template(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
    _git(repo, "add", ".env.example")

    manifest = build_runtime_source_manifest(repo)

    assert ".env.example" in {entry.path for entry in manifest.entries}


def test_runtime_source_manifest_rejects_sparse_file(tmp_path):
    repo = _repo(tmp_path)
    sparse = repo / "sparse.bin"
    with sparse.open("wb") as handle:
        handle.seek(1024 * 1024)
        handle.write(b"x")
    _git(repo, "add", "sparse.bin")

    with pytest.raises(RuntimeSourceError, match="sparse"):
        build_runtime_source_manifest(repo)


def test_snapshot_captures_dirty_source_without_mutating_checkout(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("print('dirty')\n", encoding="utf-8")
    (repo / "deleted.py").unlink()
    (repo / "new_test.py").write_text("def test_dirty(): pass\n", encoding="utf-8")
    before_status = _git_output(repo, "status", "--porcelain=v1", "-z")
    manifest = build_runtime_source_manifest(repo)

    snapshot = capture_runtime_source_snapshot(
        repo,
        expected_digest=manifest.digest,
        destination_root=tmp_path / "snapshots",
        snapshot_id="repair-1",
    )

    assert (snapshot / "app.py").read_text() == "print('dirty')\n"
    assert not (snapshot / "deleted.py").exists()
    assert (snapshot / "new_test.py").is_file()
    assert (snapshot / ".git").is_dir()
    assert _git_output(repo, "status", "--porcelain=v1", "-z") == before_status


def test_snapshot_rejects_incident_digest_mismatch(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(RuntimeSourceError, match="digest mismatch"):
        capture_runtime_source_snapshot(
            repo,
            expected_digest="0" * 64,
            destination_root=tmp_path / "snapshots",
            snapshot_id="repair-2",
        )
