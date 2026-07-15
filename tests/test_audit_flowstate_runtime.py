import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_flowstate_runtime.py"
SPEC = importlib.util.spec_from_file_location("audit_flowstate_runtime", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _verified_snapshot():
    commit = "a" * 40
    return {
        "evidenceSource": "fixture",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "schemaVersion": 1,
        "hermes": {
            "source": {"commit": commit, "dirty": False},
            "package": {"commit": commit, "dirty": False, "sha256": "b" * 64},
            "gateway": {"commit": commit, "running": True},
        },
        "flowstate": {
            "source": {"commit": "f" * 40, "dirty": False},
            "installed": {"present": True, "sha256": "c" * 64, "version": "1.4.262"},
            "process": {"running": True, "usesInstalledBytes": True},
            "renderer": {"started": True, "canSyncRemotely": True},
            "sidecar": {
                "appVersion": "1.4.262",
                "health": True,
                "protectedRead": True,
            },
        },
    }


def test_health_only_sidecar_is_blocked_until_renderer_and_protected_read_work():
    snapshot = _verified_snapshot()
    snapshot["flowstate"]["renderer"] = {
        "started": False,
        "canSyncRemotely": False,
    }
    snapshot["flowstate"]["sidecar"]["protectedRead"] = False

    result = audit.evaluate_runtime_truth(snapshot)

    assert result["verdict"] == "blocked"
    assert result["reasonCodes"] == [
        "flowstate_protected_read_failed",
        "flowstate_renderer_not_started",
        "flowstate_remote_sync_unavailable",
    ]
    assert result["flowstate"]["sidecar"]["health"] is True


def test_stale_live_evidence_cannot_be_reported_as_verified():
    snapshot = _verified_snapshot()
    snapshot["evidenceSource"] = "live"
    snapshot["observedAt"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()

    result = audit.evaluate_runtime_truth(snapshot, max_age_seconds=120)

    assert result["verdict"] == "blocked"
    assert result["reasonCodes"] == ["evidence_stale"]
    assert result["evidenceSource"] == "live"


def test_source_package_gateway_and_installed_versions_must_agree():
    snapshot = _verified_snapshot()
    snapshot["hermes"]["package"]["commit"] = "d" * 40
    snapshot["hermes"]["gateway"]["commit"] = "e" * 40
    snapshot["flowstate"]["process"]["usesInstalledBytes"] = False
    snapshot["flowstate"]["sidecar"]["appVersion"] = "1.4.255"

    result = audit.evaluate_runtime_truth(snapshot)

    assert result["hermes"]["sourcePackageMatch"] is False
    assert result["hermes"]["packageGatewayMatch"] is False
    assert result["flowstate"]["sidecar"]["appVersionMatchesInstalled"] is False
    assert result["verdict"] == "blocked"
    assert result["reasonCodes"] == [
        "hermes_source_package_mismatch",
        "hermes_package_gateway_mismatch",
        "flowstate_process_package_mismatch",
        "flowstate_sidecar_version_mismatch",
    ]


def test_missing_build_provenance_has_specific_reasons_not_mismatch_guessing():
    snapshot = _verified_snapshot()
    snapshot["hermes"]["package"] = {
        "commit": None,
        "dirty": None,
        "sha256": None,
    }
    snapshot["hermes"]["gateway"]["commit"] = None

    result = audit.evaluate_runtime_truth(snapshot)

    assert result["verdict"] == "blocked"
    assert result["reasonCodes"] == [
        "hermes_package_provenance_missing",
        "hermes_gateway_provenance_missing",
    ]


def test_live_collector_proves_runtime_without_emitting_credentials_or_task_data(
    tmp_path,
):
    repo_root = Path(__file__).resolve().parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    package = tmp_path / "app.asar"
    package.write_bytes(b"packaged hermes")
    stamp = tmp_path / "install-stamp.json"
    stamp.write_text(
        json.dumps({
            "schemaVersion": 1,
            "commit": commit,
            "dirty": False,
        }),
        encoding="utf-8",
    )
    secret = "never-emit-this-token"
    task_title = "never emit this private task title"

    def transport(path, token):
        if path == "/api/health":
            return 200, {"ok": True}
        if path == "/api/timer/diagnostics":
            return 200, {
                "appVersion": "1.4.262",
                "rendererAuthState": {
                    "canSyncRemotely": True,
                    "isInitialized": True,
                },
            }
        assert path == "/api/tasks/inventory?limit=1"
        assert token == secret
        return 200, {"tasks": [{"title": task_title}]}

    config = tmp_path / "local-api.json"
    config.write_text(
        json.dumps({"enabled": True, "port": 5577, "token": secret}),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        ["/usr/bin/sleep", "30"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        result = audit.collect_runtime_truth(
            audit.AuditConfig(
                flowstate_config=config,
                flowstate_installed_package=Path("/usr/bin/sleep"),
                flowstate_installed_version="1.4.262",
                flowstate_pid=process.pid,
                flowstate_source_root=repo_root,
                gateway_pid=process.pid,
                hermes_package=package,
                hermes_package_stamp=stamp,
                hermes_source_root=repo_root,
            ),
            transport=transport,
        )
    finally:
        process.terminate()
        process.wait(timeout=5)

    encoded = json.dumps(result, sort_keys=True)
    assert result["evidenceSource"] == "live"
    assert result["hermes"]["source"]["commit"] == commit
    assert result["hermes"]["gateway"] == {"commit": commit, "running": True}
    assert result["flowstate"]["process"] == {
        "running": True,
        "usesInstalledBytes": True,
    }
    assert result["flowstate"]["renderer"] == {
        "canSyncRemotely": True,
        "started": True,
    }
    assert result["flowstate"]["sidecar"]["protectedRead"] is True
    assert result["flowstate"]["sidecar"]["protectedReadStatus"] == "ok"
    assert secret not in encoded
    assert task_title not in encoded
    assert "argv" not in encoded.lower()


def test_classifier_rebuilds_an_allowlisted_ledger_instead_of_echoing_input():
    snapshot = _verified_snapshot()
    snapshot["token"] = "top-level-secret"
    snapshot["hermes"]["source"]["commit"] = "credential-shaped-commit"
    snapshot["hermes"]["package"]["rawArgv"] = ["--token=argv-secret"]
    snapshot["flowstate"]["sidecar"]["appVersion"] = "token-shaped-version"
    snapshot["flowstate"]["sidecar"]["tasks"] = [
        {"title": "private task", "id": "private-id"}
    ]

    result = audit.evaluate_runtime_truth(snapshot)
    encoded = json.dumps(result, sort_keys=True)

    assert "top-level-secret" not in encoded
    assert "credential-shaped-commit" not in encoded
    assert "argv-secret" not in encoded
    assert "token-shaped-version" not in encoded
    assert "private task" not in encoded
    assert "private-id" not in encoded
    assert set(result) == {
        "evidenceSource",
        "flowstate",
        "hermes",
        "observedAt",
        "reasonCodes",
        "schemaVersion",
        "verdict",
    }


def test_every_required_live_boundary_has_a_stable_blocking_reason():
    snapshot = _verified_snapshot()
    snapshot["hermes"]["source"]["dirty"] = True
    snapshot["hermes"]["package"]["dirty"] = True
    snapshot["hermes"]["gateway"] = {"commit": None, "running": False}
    snapshot["flowstate"]["installed"] = {
        "present": False,
        "sha256": None,
        "version": None,
    }
    snapshot["flowstate"]["process"] = {
        "running": False,
        "usesInstalledBytes": False,
    }
    snapshot["flowstate"]["renderer"] = {
        "started": False,
        "canSyncRemotely": False,
    }
    snapshot["flowstate"]["sidecar"] = {
        "appVersion": None,
        "health": False,
        "protectedRead": False,
        "protectedReadStatus": "connection_error",
    }

    result = audit.evaluate_runtime_truth(snapshot)

    assert result["verdict"] == "blocked"
    assert result["reasonCodes"] == [
        "hermes_source_dirty",
        "hermes_package_dirty",
        "hermes_gateway_not_running",
        "flowstate_installed_package_missing",
        "flowstate_installed_version_missing",
        "flowstate_process_not_running",
        "flowstate_sidecar_version_missing",
        "flowstate_sidecar_health_failed",
        "flowstate_protected_read_failed",
        "flowstate_renderer_not_started",
        "flowstate_remote_sync_unavailable",
    ]


def test_fixture_cli_cannot_masquerade_as_live_evidence(tmp_path, capsys):
    snapshot = _verified_snapshot()
    snapshot["evidenceSource"] = "live"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(snapshot), encoding="utf-8")

    exit_code = audit.main(["--fixture", str(fixture)])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["evidenceSource"] == "fixture"
    assert result["verdict"] == "verified"
