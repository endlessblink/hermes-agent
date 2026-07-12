"""Configuration for the Obsidian vault knowledge layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CANONICAL_VAULT_ROOT = (
    "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED"
)
DEFAULT_VISIBLE_WORKSPACE = f"{DEFAULT_CANONICAL_VAULT_ROOT}/MAIN VULT"


@dataclass(frozen=True)
class VaultConfig:
    """Resolved vault access configuration."""

    enabled: bool
    canonical_vault_root: Path
    visible_workspace: Path
    allow_hidden_system_paths: bool = False
    max_read_chars: int = 100_000
    max_search_results: int = 20


def _expand_path(value: Any) -> Path:
    raw = str(value or "").strip()
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_vault_config(config: Mapping[str, Any] | None = None) -> VaultConfig:
    """Load Obsidian vault settings from Hermes config.

    The caller may pass a config mapping for tests. Production callers use the
    profile-aware ``hermes_cli.config.load_config_readonly`` path.
    """
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception:
            config = {}

    section = config.get("obsidian_vault", {}) if isinstance(config, Mapping) else {}
    if not isinstance(section, Mapping):
        section = {}

    root = _expand_path(
        section.get("canonical_vault_root") or DEFAULT_CANONICAL_VAULT_ROOT
    )
    workspace = _expand_path(section.get("visible_workspace") or root / "MAIN VULT")
    enabled = bool(section.get("enabled", True))

    return VaultConfig(
        enabled=enabled,
        canonical_vault_root=root,
        visible_workspace=workspace,
        allow_hidden_system_paths=bool(section.get("allow_hidden_system_paths", False)),
        max_read_chars=_positive_int(section.get("max_read_chars"), 100_000),
        max_search_results=_positive_int(section.get("max_search_results"), 20),
    )


def vault_is_available(config: Mapping[str, Any] | None = None) -> bool:
    """Return True when the read-only vault toolset should be exposed."""
    cfg = load_vault_config(config)
    if not cfg.enabled:
        return False
    try:
        return cfg.canonical_vault_root.is_dir() and cfg.visible_workspace.is_dir()
    except OSError:
        return False
