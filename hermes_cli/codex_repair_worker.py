"""Fixed argv contracts for the bounded shadow Codex repair worker."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
from typing import Callable
from urllib.parse import urlsplit


REPAIR_INCIDENT_MAX_BYTES = 64 * 1024
REPAIR_MANIFEST_MAX_BYTES = 64 * 1024
REPAIR_PATCH_MAX_BYTES = 1024 * 1024
REPAIR_CHANGED_FILE_LIMIT = 20
REPAIR_ALLOWED_PREFIXES = (
    "agent/",
    "apps/desktop/electron/",
    "apps/desktop/src/",
    "cron/",
    "gateway/",
    "hermes_cli/",
    "integrations/",
    "plugins/",
    "scripts/",
    "systemd/",
    "tests/",
    "tools/",
    "tui_gateway/",
)
REPAIR_ALLOWED_ROOT_FILES = frozenset(
    {"hermes_state.py", "run_agent.py", "toolsets.py"}
)


REPAIR_MANIFEST_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "summary": {"type": "string", "maxLength": 2000},
        "changed_files": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 300},
        },
        "tests": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "maxLength": 500},
                    "status": {"type": "string", "enum": ["passed", "failed", "skipped"]},
                    "output": {"type": "string", "maxLength": 1000},
                },
                "required": ["command", "status", "output"],
                "additionalProperties": False,
            },
        },
        "remaining_failures": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 500},
        },
        "base_commit": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
        "head_commit": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
    },
    "required": [
        "schema_version",
        "summary",
        "changed_files",
        "tests",
        "remaining_failures",
        "base_commit",
        "head_commit",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RepairRunFiles:
    output_dir: Path
    incident: Path
    output_schema: Path
    manifest: Path


@dataclass(frozen=True)
class RepairCandidate:
    patch: Path
    changed_files: tuple[str, ...]
    base_commit: str
    head_commit: str


def _private_write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    os.chmod(path, 0o600)


def prepare_repair_run_files(*, output_dir: Path, incident: bytes) -> RepairRunFiles:
    if not incident or len(incident) > REPAIR_INCIDENT_MAX_BYTES:
        raise ValueError("repair incident exceeds fixed limit")
    try:
        parsed = json.loads(incident)
    except (TypeError, ValueError) as exc:
        raise ValueError("repair incident must be valid JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise ValueError("repair incident schema is unsupported")

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)
    incident_path = output_dir / "incident.json"
    schema_path = output_dir / "manifest-schema.json"
    manifest_path = output_dir / "manifest.json"
    _private_write(incident_path, incident)
    _private_write(
        schema_path,
        (json.dumps(REPAIR_MANIFEST_SCHEMA, sort_keys=True) + "\n").encode("utf-8"),
    )
    return RepairRunFiles(output_dir, incident_path, schema_path, manifest_path)


def _git(snapshot: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(snapshot), *args],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("repair snapshot git inspection failed") from exc
    return completed.stdout


def _allowed_changed_path(raw: str) -> str:
    path = raw.replace(os.sep, "/")
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or path != pure.as_posix():
        raise ValueError("repair changed path is invalid")
    folded = path.casefold()
    if any(part in {".git", ".env"} or "credential" in part or "secret" in part for part in folded.split("/")):
        raise ValueError("repair changed path is sensitive")
    if path not in REPAIR_ALLOWED_ROOT_FILES and not path.startswith(REPAIR_ALLOWED_PREFIXES):
        raise ValueError("repair changed path is outside the allowlist")
    return path


def _load_manifest(path: Path) -> dict:
    try:
        if path.stat().st_size > REPAIR_MANIFEST_MAX_BYTES:
            raise ValueError("repair manifest exceeds fixed limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("repair manifest is invalid") from exc
    if not isinstance(value, dict) or set(value) != set(REPAIR_MANIFEST_SCHEMA["required"]):
        raise ValueError("repair manifest shape is invalid")
    if value.get("schema_version") != 1:
        raise ValueError("repair manifest schema is unsupported")
    if not isinstance(value.get("summary"), str) or len(value["summary"]) > 2000:
        raise ValueError("repair manifest summary is invalid")
    changed = value.get("changed_files")
    if not isinstance(changed, list) or not (1 <= len(changed) <= REPAIR_CHANGED_FILE_LIMIT):
        raise ValueError("repair manifest changed files are invalid")
    if any(not isinstance(item, str) or len(item) > 300 for item in changed):
        raise ValueError("repair manifest changed files are invalid")
    tests = value.get("tests")
    if not isinstance(tests, list) or not tests or len(tests) > 20:
        raise ValueError("repair manifest tests are invalid")
    if any(
        not isinstance(item, dict)
        or set(item) != {"command", "status", "output"}
        or item.get("status") not in {"passed", "failed", "skipped"}
        or not isinstance(item.get("command"), str)
        or not isinstance(item.get("output"), str)
        for item in tests
    ):
        raise ValueError("repair manifest tests are invalid")
    if not any(item["status"] == "passed" for item in tests) or any(
        item["status"] == "failed" for item in tests
    ):
        raise ValueError("repair manifest does not report passing tests")
    if value.get("remaining_failures") != []:
        raise ValueError("repair manifest reports remaining failures")
    for key in ("base_commit", "head_commit"):
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(value.get(key) or "")):
            raise ValueError("repair manifest commit is invalid")
    return value


def seal_repair_candidate(*, snapshot: Path, files: RepairRunFiles) -> RepairCandidate:
    snapshot = snapshot.resolve(strict=True)
    manifest = _load_manifest(files.manifest)
    roots = [item for item in _git(snapshot, "rev-list", "--max-parents=0", "HEAD").decode().splitlines() if item]
    if len(roots) != 1:
        raise ValueError("repair snapshot baseline is ambiguous")
    base_commit = roots[0]
    head_commit = _git(snapshot, "rev-parse", "HEAD").decode().strip()
    if manifest["base_commit"] != base_commit or manifest["head_commit"] != head_commit:
        raise ValueError("repair manifest commit does not match snapshot")
    untracked = _git(snapshot, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked:
        raise ValueError("repair candidate contains untracked files")
    changed = tuple(
        _allowed_changed_path(item.decode("utf-8"))
        for item in _git(snapshot, "diff", "--name-only", "-z", base_commit).split(b"\0")
        if item
    )
    if not changed or len(changed) > REPAIR_CHANGED_FILE_LIMIT:
        raise ValueError("repair candidate changed files are invalid")
    if tuple(manifest["changed_files"]) != changed:
        raise ValueError("repair manifest changed files do not match snapshot")
    try:
        subprocess.run(
            ["git", "-C", str(snapshot), "diff", "--check", base_commit],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("repair candidate failed diff validation") from exc
    patch = _git(snapshot, "diff", "--binary", "--full-index", base_commit)
    if not patch or len(patch) > REPAIR_PATCH_MAX_BYTES:
        raise ValueError("repair patch exceeds fixed limit")
    patch_path = files.output_dir / "repair.patch"
    _private_write(patch_path, patch)
    os.chmod(files.manifest, 0o600)
    return RepairCandidate(patch_path, changed, base_commit, head_commit)


def build_codex_exec_argv(
    *,
    codex: str,
    snapshot: Path,
    output_schema: Path,
    manifest: Path,
    proxy_url: str,
    prompt: str,
) -> list[str]:
    if not Path(codex).is_absolute():
        raise ValueError("codex executable must be absolute")
    if len(prompt.encode("utf-8")) > 16 * 1024:
        raise ValueError("repair prompt exceeds fixed limit")
    proxy_url = _validated_loopback_proxy(proxy_url)
    return [
        codex,
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "workspace-write",
        "-C",
        str(snapshot),
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        'web_search="disabled"',
        "-c",
        'model_provider="hermes_repair"',
        "-c",
        'model_providers.hermes_repair.name="Hermes Repair Broker"',
        "-c",
        f"model_providers.hermes_repair.base_url={json.dumps(proxy_url)}",
        "-c",
        'model_providers.hermes_repair.env_key="HERMES_REPAIR_BROKER_TOKEN"',
        "-c",
        'model_providers.hermes_repair.wire_api="responses"',
        "-c",
        "model_providers.hermes_repair.request_max_retries=1",
        "-c",
        "model_providers.hermes_repair.stream_max_retries=1",
        "--json",
        "--color",
        "never",
        "--output-schema",
        str(output_schema),
        "--output-last-message",
        str(manifest),
        prompt,
    ]


def build_systemd_run_argv(
    *,
    unit_name: str,
    snapshot: Path,
    output_dir: Path,
    codex_argv: list[str],
    proxy_url: str,
) -> list[str]:
    if not re.fullmatch(r"hermes-repair-[a-zA-Z0-9_.-]+\.service", unit_name):
        raise ValueError("invalid repair unit name")
    proxy_url = _validated_loopback_proxy(proxy_url)
    properties = [
        "Type=exec",
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
        "PrivateTmp=disconnected",
        "PrivateDevices=yes",
        "CapabilityBoundingSet=",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "UnsetEnvironment=OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY",
        "UMask=0077",
        f"BindPaths={snapshot}",
        f"BindPaths={output_dir}",
        "Environment=HERMES_REPAIR_BROKER_TOKEN=local-broker",
        f"StandardOutput=append:{output_dir / 'codex.jsonl'}",
        f"StandardError=append:{output_dir / 'codex.stderr.log'}",
    ]
    argv = [
        "systemd-run",
        "--user",
        "--no-block",
        f"--unit={unit_name}",
        f"--working-directory={snapshot}",
    ]
    for prop in properties:
        argv.append(f"--property={prop}")
    argv.append("--")
    argv.extend(codex_argv)
    return argv


def build_verifier_systemd_run_argv(
    *,
    unit_name: str,
    snapshot: Path,
    output_dir: Path,
    python: str,
    changed_files: tuple[str, ...],
) -> list[str]:
    """Build a fixed, network-denied verifier for Python repair candidates."""
    if not re.fullmatch(r"hermes-repair-[a-zA-Z0-9_.-]+\.service", unit_name):
        raise ValueError("invalid repair verifier unit name")
    if not Path(python).is_absolute():
        raise ValueError("verifier Python executable must be absolute")
    changed = tuple(_allowed_changed_path(item) for item in changed_files)
    if not changed or any(not item.endswith(".py") for item in changed):
        raise ValueError("repair verifier only supports Python candidates")
    tests = tuple(
        item
        for item in changed
        if item.startswith("tests/")
        and PurePosixPath(item).name.startswith("test_")
    )
    if not tests:
        raise ValueError("repair candidate must include an independent regression test")
    properties = [
        "Type=exec",
        "RuntimeMaxSec=600",
        "TimeoutStopSec=15",
        "KillMode=control-group",
        "SendSIGKILL=yes",
        "Restart=no",
        "MemoryHigh=1G",
        "MemoryMax=2G",
        "CPUQuota=100%",
        "TasksMax=128",
        "NoNewPrivileges=yes",
        "PrivateTmp=disconnected",
        "PrivateDevices=yes",
        "CapabilityBoundingSet=",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "IPAddressDeny=any",
        "RestrictAddressFamilies=AF_UNIX",
        "UnsetEnvironment=OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY",
        "UMask=0077",
        f"BindReadOnlyPaths={snapshot}",
        f"BindPaths={output_dir}",
        f"StandardOutput=append:{output_dir / 'verify.log'}",
        f"StandardError=append:{output_dir / 'verify.stderr.log'}",
    ]
    argv = [
        "systemd-run",
        "--user",
        "--no-block",
        f"--unit={unit_name}",
        f"--working-directory={snapshot}",
    ]
    argv.extend(f"--property={prop}" for prop in properties)
    argv.extend(
        [
            "--",
            python,
            "-m",
            "pytest",
            "-q",
            "--maxfail=1",
            "--disable-warnings",
            *tests,
        ]
    )
    return argv


def build_systemctl_show_argv(unit_name: str) -> list[str]:
    if not re.fullmatch(r"hermes-repair-[a-zA-Z0-9_.-]+\.service", unit_name):
        raise ValueError("invalid repair unit name")
    return [
        "systemctl",
        "--user",
        "show",
        unit_name,
        "--property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus",
        "--no-pager",
    ]


def build_systemctl_stop_argv(unit_name: str) -> list[str]:
    if not re.fullmatch(r"hermes-repair-[a-zA-Z0-9_.-]+\.service", unit_name):
        raise ValueError("invalid repair unit name")
    return ["systemctl", "--user", "stop", unit_name]


def parse_systemd_unit_status(output: str) -> dict[str, str]:
    allowed = {
        "LoadState",
        "ActiveState",
        "SubState",
        "Result",
        "ExecMainCode",
        "ExecMainStatus",
    }
    parsed: dict[str, str] = {}
    for line in str(output or "").splitlines()[:20]:
        key, separator, value = line.partition("=")
        if separator and key in allowed:
            parsed[key] = value[:120]
    return parsed


def preflight_repair_host(
    *,
    codex: str,
    proxy_url: str,
    run: Callable[[list[str]], tuple[int, str]],
) -> str | None:
    """Return a stable rejection reason, or ``None`` when minimum controls exist."""
    if not proxy_url:
        return "model_proxy_unavailable"
    try:
        _validated_loopback_proxy(proxy_url)
    except ValueError:
        return "model_proxy_not_loopback"
    if not Path(codex).is_absolute():
        return "codex_unavailable"

    root_code, root_help = run([codex, "--help"])
    if root_code != 0 or "--ask-for-approval" not in root_help:
        return "codex_preflight_failed"
    exec_code, exec_help = run([codex, "exec", "--help"])
    required = {
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        "--output-last-message",
        "--json",
    }
    if exec_code != 0 or any(flag not in exec_help for flag in required):
        return "codex_preflight_failed"
    systemd_code, systemd_output = run(["systemd-run", "--user", "--version"])
    if systemd_code != 0 or "systemd" not in systemd_output.lower():
        return "systemd_preflight_failed"
    return None


def _validated_loopback_proxy(proxy_url: str) -> str:
    try:
        parsed = urlsplit(str(proxy_url or ""))
        port = parsed.port
    except ValueError as exc:
        raise ValueError("repair proxy must be loopback-only") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("repair proxy must be loopback-only")
    return parsed.geturl()
