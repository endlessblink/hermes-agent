import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_watchdog_installer_is_bounded_and_never_enables_itself():
    installer = (
        ROOT / "scripts" / "install-personal-assistant-watchdog-service.sh"
    ).read_text(encoding="utf-8")

    assert "Type=oneshot" in installer
    assert "TimeoutStartSec=15s" in installer
    assert "OnUnitActiveSec=2min" in installer
    assert '--profile-home "$escaped_profile" --notify' in installer
    assert "Installed but not enabled" in installer
    assert "systemctl --user enable" not in installer


def test_watchdog_installer_writes_profile_scoped_units(tmp_path):
    home = tmp_path / "home"
    config = tmp_path / "config"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)
    profile_home = tmp_path / "office-work % profile"
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }

    completed = subprocess.run(
        [
            str(ROOT / "scripts" / "install-personal-assistant-watchdog-service.sh"),
            "--profile-home",
            str(profile_home),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    unit = (
        config / "systemd" / "user" / "hermes-personal-assistant-watchdog.service"
    ).read_text(encoding="utf-8")
    escaped_profile = str(profile_home).replace("%", "%%")
    assert f'Environment="HERMES_HOME={escaped_profile}"' in unit
    assert f'--profile-home "{escaped_profile}"' in unit
    assert "Installed but not enabled" in completed.stdout
