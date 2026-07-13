import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_live_watchdog_systemd_template_is_restartable_and_installable():
    template = (ROOT / "systemd" / "hermes-live-watchdog.service.in").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts" / "install-hermes-live-watchdog-service.sh").read_text(
        encoding="utf-8"
    )

    assert "ExecStart=\"@PYTHON@\" \"@REPO_ROOT@/scripts/hermes_live_watchdog.py\"" in template
    assert "--home \"@HERMES_HOME@\"" in template
    assert "--notify" in template
    assert "Restart=on-failure" in template
    assert "WantedBy=default.target" in template
    assert "systemctl --user daemon-reload" in installer
    assert "systemctl --user enable --now hermes-live-watchdog.service" in installer


def test_installer_renders_and_starts_user_service(tmp_path):
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
    hermes_home = tmp_path / "hermes-home"
    env = {
        **os.environ,
        "HERMES_HOME": str(hermes_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "XDG_CONFIG_HOME": str(config_home),
    }

    subprocess.run(
        [str(ROOT / "scripts" / "install-hermes-live-watchdog-service.sh")],
        check=True,
        env=env,
        text=True,
    )

    unit = (config_home / "systemd" / "user" / "hermes-live-watchdog.service").read_text(
        encoding="utf-8"
    )
    assert "@REPO_ROOT@" not in unit
    assert str(ROOT) in unit
    assert f'--home "{hermes_home}"' in unit
    assert systemctl_log.read_text(encoding="utf-8").splitlines() == [
        "--user daemon-reload",
        "--user enable --now hermes-live-watchdog.service",
    ]
