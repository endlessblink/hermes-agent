from __future__ import annotations

import json
from pathlib import Path
import subprocess

from hermes_cli.codex_repair_worker import (
    build_codex_exec_argv,
    build_verifier_systemd_run_argv,
    build_systemd_run_argv,
    preflight_repair_host,
    prepare_repair_run_files,
    seal_repair_candidate,
)


def test_codex_exec_contract_is_noninteractive_and_workspace_only(tmp_path):
    argv = build_codex_exec_argv(
        codex="/usr/bin/codex",
        snapshot=tmp_path / "snapshot",
        output_schema=tmp_path / "schema.json",
        manifest=tmp_path / "manifest.json",
        proxy_url="http://127.0.0.1:43123/v1",
        prompt="Prepare a candidate only.",
    )

    assert argv[:4] == ["/usr/bin/codex", "--ask-for-approval", "never", "exec"]
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert 'sandbox_workspace_write.network_access=false' in argv
    assert 'web_search="disabled"' in argv
    assert 'model_provider="hermes_repair"' in argv
    assert 'model_providers.hermes_repair.base_url="http://127.0.0.1:43123/v1"' in argv
    assert 'model_providers.hermes_repair.env_key="HERMES_REPAIR_BROKER_TOKEN"' in argv
    assert "--json" in argv
    assert "--output-schema" in argv
    assert "--output-last-message" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_systemd_contract_kills_cgroup_and_bounds_resources(tmp_path):
    argv = build_systemd_run_argv(
        unit_name="hermes-repair-task-run.service",
        snapshot=tmp_path / "snapshot",
        output_dir=tmp_path / "output",
        codex_argv=["codex", "exec", "prompt"],
        proxy_url="http://127.0.0.1:43123/v1",
    )

    joined = "\n".join(argv)
    for required in (
        "RuntimeMaxSec=1200",
        "TimeoutStopSec=15",
        "KillMode=control-group",
        "SendSIGKILL=yes",
        "Restart=no",
        "MemoryHigh=1G",
        "MemoryMax=2G",
        "CPUQuota=100%",
        "TasksMax=128",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "UnsetEnvironment=OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY",
    ):
        assert required in joined
    assert not any(
        item.startswith("--property=Environment=OPENAI_API_KEY") for item in argv
    )
    assert "CODEX_HOME" not in joined
    assert "Environment=HERMES_REPAIR_BROKER_TOKEN=local-broker" in joined
    assert argv[-3:] == ["codex", "exec", "prompt"]


def test_verifier_is_network_denied_read_only_and_runs_candidate_tests(tmp_path):
    argv = build_verifier_systemd_run_argv(
        unit_name="hermes-repair-task-run-verify.service",
        snapshot=tmp_path / "snapshot",
        output_dir=tmp_path / "output",
        python="/usr/bin/python3",
        changed_files=("agent/repair.py", "tests/agent/test_repair.py"),
    )

    joined = "\n".join(argv)
    assert "RuntimeMaxSec=600" in joined
    assert "KillMode=control-group" in joined
    assert "IPAddressDeny=any" in joined
    assert f"BindReadOnlyPaths={tmp_path / 'snapshot'}" in joined
    assert f"BindPaths={tmp_path / 'output'}" in joined
    assert argv[-7:] == [
        "/usr/bin/python3",
        "-m",
        "pytest",
        "-q",
        "--maxfail=1",
        "--disable-warnings",
        "tests/agent/test_repair.py",
    ]


def test_preflight_fails_closed_without_tokenless_proxy():
    calls = []

    assert (
        preflight_repair_host(
            codex="/usr/bin/codex",
            proxy_url="",
            run=lambda argv: calls.append(argv) or (0, ""),
        )
        == "model_proxy_unavailable"
    )
    assert calls == []


def test_preflight_rejects_loopback_prefix_with_remote_authority():
    assert (
        preflight_repair_host(
            codex="/usr/bin/codex",
            proxy_url="http://127.0.0.1:43123@evil.example/v1",
            run=lambda _argv: (0, ""),
        )
        == "model_proxy_not_loopback"
    )


def test_preflight_checks_codex_flags_and_systemd_controls():
    calls = []

    def run(argv):
        calls.append(argv)
        if argv == ["/usr/bin/codex", "--help"]:
            return 0, "--ask-for-approval"
        if argv[:2] == ["/usr/bin/codex", "exec"]:
            return 0, "--ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --json"
        return 0, "systemd 258"

    assert (
        preflight_repair_host(
            codex="/usr/bin/codex",
            proxy_url="http://127.0.0.1:43123/v1",
            run=run,
        )
        is None
    )
    assert calls[0] == ["/usr/bin/codex", "--help"]
    assert calls[1] == ["/usr/bin/codex", "exec", "--help"]
    assert calls[2] == ["systemd-run", "--user", "--version"]


def test_repair_run_files_are_private_and_schema_bounded(tmp_path):
    files = prepare_repair_run_files(
        output_dir=tmp_path / "run",
        incident=b'{"schema_version":1}',
    )

    assert files.incident.read_bytes() == b'{"schema_version":1}'
    schema = __import__("json").loads(files.output_schema.read_text())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "summary",
        "changed_files",
        "tests",
        "remaining_failures",
        "base_commit",
        "head_commit",
    }
    assert files.output_dir.stat().st_mode & 0o777 == 0o700
    assert files.incident.stat().st_mode & 0o777 == 0o600


def test_seal_repair_candidate_matches_manifest_to_bounded_git_patch(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    subprocess.run(["git", "-C", str(snapshot), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "config", "user.name", "Test"], check=True)
    (snapshot / "agent").mkdir()
    target = snapshot / "agent" / "repair.py"
    target.write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(snapshot), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "commit", "-qm", "baseline"], check=True)
    base = subprocess.check_output(["git", "-C", str(snapshot), "rev-parse", "HEAD"], text=True).strip()
    target.write_text("VALUE = 2\n")
    subprocess.run(["git", "-C", str(snapshot), "add", "-A"], check=True)
    files = prepare_repair_run_files(
        output_dir=tmp_path / "run",
        incident=b'{"schema_version":1}',
    )
    files.manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "summary": "Repair the value.",
                "changed_files": ["agent/repair.py"],
                "tests": [{"command": "pytest", "status": "passed", "output": "1 passed"}],
                "remaining_failures": [],
                "base_commit": base,
                "head_commit": base,
            }
        )
    )

    candidate = seal_repair_candidate(snapshot=snapshot, files=files)

    assert candidate.changed_files == ("agent/repair.py",)
    assert candidate.patch.stat().st_size < 1024 * 1024
    assert b"VALUE = 2" in candidate.patch.read_bytes()
