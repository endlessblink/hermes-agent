from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_monitor_service_is_bounded_and_activation_remains_explicit():
    installer = (
        ROOT / "scripts" / "install-personal-assistant-monitor-service.sh"
    ).read_text(encoding="utf-8")

    assert "Type=oneshot" in installer
    assert "TimeoutStartSec=45s" in installer
    assert "SuccessExitStatus=75" in installer
    assert "OnUnitActiveSec=15min" in installer
    assert "Installed but not enabled" in installer
    assert "systemctl --user enable --now" not in installer
