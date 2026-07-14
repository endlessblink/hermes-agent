from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_templates_schedule_separate_daily_and_weekly_retention():
    service = (ROOT / "systemd" / "hermes-full-backup@.service.in").read_text()
    daily = (ROOT / "systemd" / "hermes-full-backup-daily.timer").read_text()
    weekly = (ROOT / "systemd" / "hermes-full-backup-weekly.timer").read_text()

    assert "--kind %i" in service
    assert 'Environment="HERMES_HOME=@HERMES_HOME@"' in service
    assert "WorkingDirectory=@REPO_ROOT@" in service
    assert "--output-root \"@BACKUP_ROOT@\"" in service
    assert "OnCalendar=daily" in daily
    assert "Unit=hermes-full-backup@daily.service" in daily
    assert "OnCalendar=weekly" in weekly
    assert "Unit=hermes-full-backup@weekly.service" in weekly
    assert "Persistent=true" in daily
    assert "Persistent=true" in weekly


def test_installer_renders_units_and_enables_both_timers(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >>\"$SYSTEMCTL_LOG\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    config_home = tmp_path / "config"
    backup_root = tmp_path / "backups"
    hermes_home = tmp_path / "hermes-home"
    env = {
        **os.environ,
        "HERMES_BACKUP_ROOT": str(backup_root),
        "HERMES_HOME": str(hermes_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "XDG_CONFIG_HOME": str(config_home),
    }

    subprocess.run(
        [str(ROOT / "scripts" / "install-hermes-full-backup-service.sh")],
        check=True,
        env=env,
        text=True,
    )

    unit_dir = config_home / "systemd" / "user"
    service = (unit_dir / "hermes-full-backup@.service").read_text()
    assert "@REPO_ROOT@" not in service
    assert str(backup_root) in service
    assert str(hermes_home) in service
    assert systemctl_log.read_text().splitlines() == [
        "--user daemon-reload",
        "--user enable --now hermes-full-backup-daily.timer",
        "--user enable --now hermes-full-backup-weekly.timer",
    ]
