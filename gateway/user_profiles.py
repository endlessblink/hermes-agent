"""Telegram identity-to-profile and per-profile credential helpers.

The gateway already knows how to run a turn inside a Hermes profile.  This
module owns only the durable mapping and the small amount of user-auth state
needed by Telegram; it never stores a credential in the shared gateway state.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_cli.profiles import create_profile, get_profile_dir, profile_exists


def profile_name_for_identity(platform: str, user_id: str) -> str:
    """Return a stable, path-safe name without putting the platform id in it."""
    if not platform or not user_id:
        raise ValueError("platform and user_id are required")
    digest = hashlib.sha256(f"{platform}:{user_id}".encode("utf-8")).hexdigest()[:24]
    return f"tg-u-{digest}"


def _mapping_path() -> Path:
    path = get_hermes_home() / "state" / "telegram-user-profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_mapping() -> dict[str, str]:
    path = _mapping_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_mapping(mapping: dict[str, str]) -> None:
    path = _mapping_path()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def resolve_user_profile(platform: str, user_id: str, *, approved: bool) -> str | None:
    """Resolve an approved identity, never creating state for an unapproved one."""
    if not approved:
        return None
    identity = f"{platform}:{user_id}"
    mapping = _read_mapping()
    name = mapping.get(identity)
    if name is None:
        name = profile_name_for_identity(platform, user_id)
        mapping[identity] = name
        _write_mapping(mapping)
    if not profile_exists(name):
        create_profile(name, no_alias=True, no_skills=True)
    return name


def user_profile_dir(platform: str, user_id: str, *, approved: bool) -> Path | None:
    name = resolve_user_profile(platform, user_id, approved=approved)
    return get_profile_dir(name) if name else None


def _auth_state_path(profile_dir: Path) -> Path:
    path = profile_dir / "state" / "telegram-auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_user_auth_state(profile_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_auth_state_path(profile_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_user_auth_state(profile_dir: Path, state: dict[str, Any]) -> None:
    """Persist metadata only; callers must not put API keys or bearer tokens here."""
    forbidden = ("api_key", "access_token", "refresh_token", "id_token", "secret")
    if any(key in state for key in forbidden):
        raise ValueError("credential material must not be stored in Telegram auth state")
    path = _auth_state_path(profile_dir)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def new_oauth_state(*, provider: str = "openai-codex") -> dict[str, str]:
    """Create non-secret, user-bound OAuth transaction metadata."""
    return {"provider": provider, "state": secrets.token_urlsafe(24), "status": "pending"}


def set_user_api_key(profile_dir: Path, key: str, value: str) -> None:
    """Set one profile-owned API key in its owner-only environment file."""
    if not key or not key.isupper() or not key.replace("_", "").isalnum() or not value.strip():
        raise ValueError("invalid API-key setting")
    env_path = profile_dir / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    lines = [line for line in lines if not line.startswith(prefix)]
    lines.append(f"{key}={value.strip()}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def revoke_user_api_key(profile_dir: Path, key: str = "OPENAI_API_KEY") -> bool:
    env_path = profile_dir / ".env"
    if not env_path.exists():
        return False
    prefix = f"{key}="
    lines = env_path.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in lines if not line.startswith(prefix)]
    changed = filtered != lines
    if changed:
        env_path.write_text("\n".join(filtered) + ("\n" if filtered else ""), encoding="utf-8")
        os.chmod(env_path, 0o600)
    return changed


def user_api_key_configured(profile_dir: Path, key: str = "OPENAI_API_KEY") -> bool:
    env_path = profile_dir / ".env"
    if not env_path.exists():
        return False
    return any(line.startswith(f"{key}=") and bool(line.split("=", 1)[1].strip()) for line in env_path.read_text(encoding="utf-8").splitlines())
