#!/usr/bin/env python3
"""Build a privacy-safe source-to-live Hermes and FlowState truth ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class AuditConfig:
    """Paths and process identities required for bounded live collection."""

    hermes_source_root: Path
    hermes_package_stamp: Path
    hermes_package: Path
    gateway_pid: int | None
    flowstate_installed_package: Path
    flowstate_installed_version: str | None
    flowstate_pid: int | None
    flowstate_source_root: Path | None
    flowstate_config: Path
    timeout_seconds: float = 1.5


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _git_identity(path: Path) -> tuple[str | None, bool | None]:
    try:
        commit_result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        if commit_result.returncode != 0:
            return None, None
        commit = commit_result.stdout.strip()
        dirty_result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        dirty = bool(dirty_result.stdout) if dirty_result.returncode == 0 else None
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def _safe_stamp(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        "commit": value.get("commit") if isinstance(value.get("commit"), str) else None,
        "dirty": value.get("dirty") if isinstance(value.get("dirty"), bool) else None,
    }


def _process_running(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return False
    fields = stat.split()
    return len(fields) > 2 and fields[2] != "Z"


def _process_uses_file(pid: int | None, expected: Path) -> bool:
    if not _process_running(pid):
        return False
    try:
        return os.path.samefile(Path("/proc") / str(pid) / "exe", expected)
    except OSError:
        return False


def _gateway_commit(pid: int | None) -> str | None:
    if not _process_running(pid):
        return None
    try:
        cwd = (Path("/proc") / str(pid) / "cwd").resolve(strict=True)
    except OSError:
        return None
    commit, _dirty = _git_identity(cwd)
    return commit


def _systemd_main_pid(service: str) -> int | None:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                service,
                "--property=MainPID",
                "--value",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        pid = int(result.stdout.strip()) if result.returncode == 0 else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return pid if pid > 0 else None


def _find_process_using(path: Path) -> int | None:
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if os.path.samefile(entry / "exe", path) and _process_running(
                int(entry.name)
            ):
                return int(entry.name)
        except OSError:
            continue
    return None


def _load_sidecar_config(path: Path) -> tuple[str, int]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return "", 5577
    if not isinstance(value, dict):
        return "", 5577
    token = value.get("token") if isinstance(value.get("token"), str) else ""
    port = value.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        port = 5577
    return token, port


def _make_transport(base_url: str, timeout_seconds: float):
    def transport(path: str, token: str) -> tuple[int, dict[str, Any]]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{base_url}{path}", headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status = int(response.status)
                if path.startswith("/api/tasks/"):
                    return status, {}
                raw = response.read(64 * 1024)
        except HTTPError as exc:
            return int(exc.code), {}
        except (OSError, URLError, ValueError):
            return 0, {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return status, {}
        return status, value if isinstance(value, dict) else {}

    return transport


def _protected_status(status: int) -> str:
    if status == 200:
        return "ok"
    if status == 401:
        return "unauthorized"
    if status == 503:
        return "auth_unavailable"
    if status == 0:
        return "connection_error"
    return "http_error"


def collect_runtime_truth(
    config: AuditConfig,
    *,
    transport=None,
) -> dict[str, Any]:
    """Collect only allowlisted runtime facts and return their evaluated ledger."""

    source_commit, source_dirty = _git_identity(config.hermes_source_root)
    flowstate_source_commit, flowstate_source_dirty = (
        _git_identity(config.flowstate_source_root)
        if config.flowstate_source_root is not None
        else (None, None)
    )
    stamp = _safe_stamp(config.hermes_package_stamp)
    gateway_running = _process_running(config.gateway_pid)
    token, port = _load_sidecar_config(config.flowstate_config)
    request = transport or _make_transport(
        f"http://127.0.0.1:{port}", config.timeout_seconds
    )
    health_status, _health_payload = request("/api/health", "")
    diagnostics_status, diagnostics = request("/api/timer/diagnostics", "")
    protected_status, _protected_payload = request(
        "/api/tasks/inventory?limit=1", token
    )
    renderer_state = (
        diagnostics.get("rendererAuthState")
        if diagnostics_status == 200
        and isinstance(diagnostics.get("rendererAuthState"), dict)
        else None
    )
    app_version = (
        diagnostics.get("appVersion")
        if diagnostics_status == 200 and isinstance(diagnostics.get("appVersion"), str)
        else None
    )
    snapshot = {
        "schemaVersion": 1,
        "evidenceSource": "live",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "hermes": {
            "source": {"commit": source_commit, "dirty": source_dirty},
            "package": {
                "commit": stamp.get("commit"),
                "dirty": stamp.get("dirty"),
                "sha256": _sha256(config.hermes_package),
            },
            "gateway": {
                "commit": _gateway_commit(config.gateway_pid),
                "running": gateway_running,
            },
        },
        "flowstate": {
            "source": {
                "commit": flowstate_source_commit,
                "dirty": flowstate_source_dirty,
            },
            "installed": {
                "present": config.flowstate_installed_package.is_file(),
                "sha256": _sha256(config.flowstate_installed_package),
                "version": config.flowstate_installed_version,
            },
            "process": {
                "running": _process_running(config.flowstate_pid),
                "usesInstalledBytes": _process_uses_file(
                    config.flowstate_pid, config.flowstate_installed_package
                ),
            },
            "renderer": {
                "started": renderer_state is not None,
                "canSyncRemotely": bool(
                    renderer_state and renderer_state.get("canSyncRemotely") is True
                ),
            },
            "sidecar": {
                "appVersion": app_version,
                "health": health_status == 200,
                "protectedRead": protected_status == 200,
                "protectedReadStatus": _protected_status(protected_status),
            },
        },
    }
    return evaluate_runtime_truth(snapshot)


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _full_commit_or_none(value: Any) -> str | None:
    return (
        value.lower()
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value)
        else None
    )


def _sha256_or_none(value: Any) -> str | None:
    return (
        value.lower()
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
        else None
    )


def _version_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return (
        value
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", value)
        else None
    )


def _timestamp_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat()


def _allowlisted_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    hermes = snapshot.get("hermes") if isinstance(snapshot.get("hermes"), dict) else {}
    flowstate = (
        snapshot.get("flowstate") if isinstance(snapshot.get("flowstate"), dict) else {}
    )
    source = hermes.get("source") if isinstance(hermes.get("source"), dict) else {}
    package = hermes.get("package") if isinstance(hermes.get("package"), dict) else {}
    gateway = hermes.get("gateway") if isinstance(hermes.get("gateway"), dict) else {}
    installed = (
        flowstate.get("installed")
        if isinstance(flowstate.get("installed"), dict)
        else {}
    )
    flowstate_source = (
        flowstate.get("source") if isinstance(flowstate.get("source"), dict) else {}
    )
    process = (
        flowstate.get("process") if isinstance(flowstate.get("process"), dict) else {}
    )
    renderer = (
        flowstate.get("renderer") if isinstance(flowstate.get("renderer"), dict) else {}
    )
    sidecar = (
        flowstate.get("sidecar") if isinstance(flowstate.get("sidecar"), dict) else {}
    )
    evidence_source = snapshot.get("evidenceSource")
    if evidence_source not in {"fixture", "live"}:
        evidence_source = "fixture"
    protected_status = sidecar.get("protectedReadStatus")
    if protected_status not in {
        "ok",
        "unauthorized",
        "auth_unavailable",
        "connection_error",
        "http_error",
    }:
        protected_status = "unknown"
    return {
        "schemaVersion": 1,
        "evidenceSource": evidence_source,
        "observedAt": _timestamp_or_none(snapshot.get("observedAt")),
        "hermes": {
            "source": {
                "commit": _full_commit_or_none(source.get("commit")),
                "dirty": _bool_or_none(source.get("dirty")),
            },
            "package": {
                "commit": _full_commit_or_none(package.get("commit")),
                "dirty": _bool_or_none(package.get("dirty")),
                "sha256": _sha256_or_none(package.get("sha256")),
            },
            "gateway": {
                "commit": _full_commit_or_none(gateway.get("commit")),
                "running": _bool_or_none(gateway.get("running")) is True,
            },
        },
        "flowstate": {
            "source": {
                "commit": _full_commit_or_none(flowstate_source.get("commit")),
                "dirty": _bool_or_none(flowstate_source.get("dirty")),
            },
            "installed": {
                "present": _bool_or_none(installed.get("present")) is True,
                "sha256": _sha256_or_none(installed.get("sha256")),
                "version": _version_or_none(installed.get("version")),
            },
            "process": {
                "running": _bool_or_none(process.get("running")) is True,
                "usesInstalledBytes": _bool_or_none(process.get("usesInstalledBytes")),
            },
            "renderer": {
                "started": _bool_or_none(renderer.get("started")) is True,
                "canSyncRemotely": _bool_or_none(renderer.get("canSyncRemotely"))
                is True,
            },
            "sidecar": {
                "appVersion": _version_or_none(sidecar.get("appVersion")),
                "health": _bool_or_none(sidecar.get("health")) is True,
                "protectedRead": _bool_or_none(sidecar.get("protectedRead")) is True,
                "protectedReadStatus": protected_status,
            },
        },
    }


def evaluate_runtime_truth(
    snapshot: dict[str, Any], *, max_age_seconds: float = 120.0
) -> dict[str, Any]:
    """Return a stable verdict without adding sensitive runtime evidence."""

    result = _allowlisted_snapshot(snapshot)
    hermes = result.get("hermes") or {}
    flowstate = result.get("flowstate") or {}
    source = hermes.get("source") or {}
    package = hermes.get("package") or {}
    gateway = hermes.get("gateway") or {}
    installed = flowstate.get("installed") or {}
    flowstate_source = flowstate.get("source") or {}
    process = flowstate.get("process") or {}
    renderer = flowstate.get("renderer") or {}
    sidecar = flowstate.get("sidecar") or {}
    reasons: list[str] = []

    observed_at = result.get("observedAt")
    try:
        observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    except (TypeError, ValueError):
        reasons.append("evidence_timestamp_missing")
    else:
        if age_seconds > max_age_seconds:
            reasons.append("evidence_stale")

    hermes["sourcePackageMatch"] = bool(source.get("commit")) and (
        source.get("commit") == package.get("commit")
    )
    hermes["packageGatewayMatch"] = bool(package.get("commit")) and (
        package.get("commit") == gateway.get("commit")
    )
    sidecar["appVersionMatchesInstalled"] = bool(installed.get("version")) and (
        installed.get("version") == sidecar.get("appVersion")
    )
    result["hermes"] = hermes
    flowstate["sidecar"] = sidecar
    result["flowstate"] = flowstate

    source_commit = source.get("commit")
    package_commit = package.get("commit")
    gateway_commit = gateway.get("commit")
    if not source_commit:
        reasons.append("hermes_source_provenance_missing")
    elif source.get("dirty") is True:
        reasons.append("hermes_source_dirty")
    if not package_commit or not package.get("sha256") or package.get("dirty") is None:
        reasons.append("hermes_package_provenance_missing")
    elif package.get("dirty") is True:
        reasons.append("hermes_package_dirty")
    if not gateway.get("running"):
        reasons.append("hermes_gateway_not_running")
    elif not gateway_commit:
        reasons.append("hermes_gateway_provenance_missing")
    if source_commit and package_commit and not hermes["sourcePackageMatch"]:
        reasons.append("hermes_source_package_mismatch")
    if package_commit and gateway_commit and not hermes["packageGatewayMatch"]:
        reasons.append("hermes_package_gateway_mismatch")

    if not flowstate_source.get("commit"):
        reasons.append("flowstate_source_provenance_missing")
    elif flowstate_source.get("dirty") is True:
        reasons.append("flowstate_source_dirty")
    if not installed.get("present"):
        reasons.append("flowstate_installed_package_missing")
    elif not installed.get("sha256"):
        reasons.append("flowstate_installed_hash_missing")
    if not installed.get("version"):
        reasons.append("flowstate_installed_version_missing")
    if not process.get("running"):
        reasons.append("flowstate_process_not_running")
    elif process.get("usesInstalledBytes") is not True:
        reasons.append("flowstate_process_package_mismatch")
    if not sidecar.get("appVersion"):
        reasons.append("flowstate_sidecar_version_missing")
    elif installed.get("version") and not sidecar["appVersionMatchesInstalled"]:
        reasons.append("flowstate_sidecar_version_mismatch")
    if not sidecar.get("health"):
        reasons.append("flowstate_sidecar_health_failed")
    if not sidecar.get("protectedRead"):
        reasons.append("flowstate_protected_read_failed")
    if not renderer.get("started"):
        reasons.append("flowstate_renderer_not_started")
    if not renderer.get("canSyncRemotely"):
        reasons.append("flowstate_remote_sync_unavailable")

    result["reasonCodes"] = reasons
    result["verdict"] = "verified" if not reasons else "blocked"
    return result


def _default_hermes_resources(source_root: Path) -> Path:
    candidates = [
        source_root / "apps" / "desktop" / "release" / "linux-unpacked" / "resources",
        Path.home()
        / ".hermes"
        / "hermes-agent"
        / "apps"
        / "desktop"
        / "release"
        / "linux-unpacked"
        / "resources",
    ]
    for candidate in candidates:
        if (candidate / "app.asar").is_file():
            return candidate
    return candidates[0]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    resources = _default_hermes_resources(repo_root)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    parser = argparse.ArgumentParser(
        description="Emit a redacted Hermes and FlowState source-to-live truth ledger."
    )
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--hermes-source-root", type=Path, default=repo_root)
    parser.add_argument(
        "--hermes-package-stamp",
        type=Path,
        default=resources / "install-stamp.json",
    )
    parser.add_argument("--hermes-package", type=Path, default=resources / "app.asar")
    parser.add_argument("--gateway-pid", type=int)
    parser.add_argument(
        "--flowstate-installed-package",
        type=Path,
        default=Path.home() / ".local" / "bin" / "FlowState.AppImage",
    )
    parser.add_argument("--flowstate-source-root", type=Path)
    parser.add_argument("--flowstate-installed-version")
    parser.add_argument("--flowstate-pid", type=int)
    parser.add_argument(
        "--flowstate-config",
        type=Path,
        default=config_home / "flow-state" / "local-api.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.fixture is not None:
        try:
            snapshot = json.loads(args.fixture.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["evidenceSource"] = "fixture"
        result = evaluate_runtime_truth(snapshot)
    else:
        gateway_pid = args.gateway_pid or _systemd_main_pid("hermes-gateway.service")
        flowstate_pid = args.flowstate_pid or _find_process_using(
            args.flowstate_installed_package
        )
        result = collect_runtime_truth(
            AuditConfig(
                hermes_source_root=args.hermes_source_root,
                hermes_package_stamp=args.hermes_package_stamp,
                hermes_package=args.hermes_package,
                gateway_pid=gateway_pid,
                flowstate_installed_package=args.flowstate_installed_package,
                flowstate_installed_version=args.flowstate_installed_version,
                flowstate_pid=flowstate_pid,
                flowstate_source_root=args.flowstate_source_root,
                flowstate_config=args.flowstate_config,
            )
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["verdict"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
