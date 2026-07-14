from __future__ import annotations

import fcntl
import os
from pathlib import Path
import zipfile

import pytest

from scripts import hermes_full_backup_rotation as rotation


def _zip_runner(payload: bytes = b"state"):
    calls: list[list[str]] = []

    def run(command: list[str], *, env: dict[str, str]) -> None:
        calls.append(command)
        destination = Path(command[command.index("--output") + 1])
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("state.db", payload)

    return calls, run


def test_success_is_validated_and_atomically_published(tmp_path, monkeypatch):
    home = tmp_path / "home"
    output = tmp_path / "backups"
    home.mkdir()
    (home / "state.db").write_bytes(b"state")
    calls, runner = _zip_runner()
    monkeypatch.setattr(rotation, "available_bytes", lambda _path: 10_000)

    archive = rotation.create_rotated_backup(
        home=home,
        output_root=output,
        kind="daily",
        keep=7,
        minimum_free_bytes=0,
        runner=runner,
    )

    assert archive.parent == output / "daily"
    assert archive.name.startswith("hermes-full-daily-")
    assert zipfile.ZipFile(archive).testzip() is None
    assert calls[0][calls[0].index("--output") + 1].endswith(".tmp.zip")
    assert not list(output.rglob("*.tmp.zip"))


def test_disk_preflight_runs_before_backup_or_pruning(tmp_path, monkeypatch):
    home = tmp_path / "home"
    output = tmp_path / "backups"
    home.mkdir()
    (home / "large.bin").write_bytes(b"x" * 20)
    old = output / "daily" / "hermes-full-daily-20000101-000000.zip"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    calls, runner = _zip_runner()
    monkeypatch.setattr(rotation, "available_bytes", lambda _path: 19)

    with pytest.raises(rotation.InsufficientSpace):
        rotation.create_rotated_backup(
            home=home,
            output_root=output,
            kind="daily",
            keep=1,
            minimum_free_bytes=0,
            runner=runner,
        )

    assert calls == []
    assert old.read_bytes() == b"old"


def test_failed_validation_keeps_existing_archives_and_removes_temp(tmp_path, monkeypatch):
    home = tmp_path / "home"
    output = tmp_path / "backups"
    home.mkdir()
    (home / "config.yaml").write_text("model: test", encoding="utf-8")
    old = output / "daily" / "hermes-full-daily-20000101-000000.zip"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    monkeypatch.setattr(rotation, "available_bytes", lambda _path: 10_000)

    def corrupt(command: list[str], *, env: dict[str, str]) -> None:
        Path(command[command.index("--output") + 1]).write_bytes(b"not a zip")

    with pytest.raises(rotation.InvalidArchive):
        rotation.create_rotated_backup(
            home=home,
            output_root=output,
            kind="daily",
            keep=1,
            minimum_free_bytes=0,
            runner=corrupt,
        )

    assert old.read_bytes() == b"old"
    assert not list(output.rglob("*.tmp.zip"))


def test_prunes_only_same_kind_after_success(tmp_path, monkeypatch):
    home = tmp_path / "home"
    output = tmp_path / "backups"
    home.mkdir()
    (home / "config.yaml").write_text("model: test", encoding="utf-8")
    daily = output / "daily"
    weekly = output / "weekly"
    daily.mkdir(parents=True)
    weekly.mkdir(parents=True)
    for index in range(3):
        archive = daily / f"hermes-full-daily-2000010{index + 1}-000000.zip"
        archive.write_bytes(b"old")
        os.utime(archive, (index + 1, index + 1))
    weekly_archive = weekly / "hermes-full-weekly-20000101-000000.zip"
    weekly_archive.write_bytes(b"weekly")
    _, runner = _zip_runner()
    monkeypatch.setattr(rotation, "available_bytes", lambda _path: 10_000)

    rotation.create_rotated_backup(
        home=home,
        output_root=output,
        kind="daily",
        keep=2,
        minimum_free_bytes=0,
        runner=runner,
    )

    assert len(list(daily.glob("hermes-full-daily-*.zip"))) == 2
    assert weekly_archive.read_bytes() == b"weekly"


def test_exclusive_lock_rejects_overlapping_runs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    output = tmp_path / "backups"
    home.mkdir()
    (home / "config.yaml").write_text("model: test", encoding="utf-8")
    output.mkdir()
    lock_path = output / ".hermes-full-backup.lock"
    monkeypatch.setattr(rotation, "available_bytes", lambda _path: 10_000)
    _, runner = _zip_runner()

    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(rotation.BackupAlreadyRunning):
            rotation.create_rotated_backup(
                home=home,
                output_root=output,
                kind="weekly",
                keep=4,
                minimum_free_bytes=0,
                runner=runner,
            )


def test_source_estimate_skips_agent_checkout_and_symlinks(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "state.db").write_bytes(b"1234")
    agent = home / "hermes-agent"
    agent.mkdir()
    (agent / "huge.bin").write_bytes(b"x" * 100)
    (home / "link").symlink_to(agent / "huge.bin")

    assert rotation.estimate_source_bytes(home) == 4


def test_rejects_output_root_inside_hermes_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: test", encoding="utf-8")

    with pytest.raises(rotation.BackupRotationError, match="outside Hermes home"):
        rotation.create_rotated_backup(
            home=home,
            output_root=home / "backups",
            kind="daily",
            keep=7,
        )
