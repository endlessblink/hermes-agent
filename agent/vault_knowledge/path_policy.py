"""Vault path boundary and safety policy."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import VaultConfig


class VaultAccessError(ValueError):
    """Raised when a vault operation is rejected by policy."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": self.message, "reason": self.reason}


@dataclass(frozen=True)
class SourceReceipt:
    """Minimal receipt for data returned from the vault."""

    path: str
    heading: str | None
    modified_time: float | None
    content_hash: str | None
    trust: str
    safety_flags: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "heading": self.heading,
            "modified_time": self.modified_time,
            "content_hash": self.content_hash,
            "trust": self.trust,
            "safety_flags": self.safety_flags,
        }


SECRET_BASENAMES = frozenset(
    {
        ".env", ".env.local", ".env.development", ".env.production",
        ".env.test", ".env.staging", ".envrc", "auth.json",
        "credentials.json", "credential.json", "token.json", "tokens.json",
        "secrets.json", "secret.json", "id_rsa", "id_ed25519",
        "private_key.pem",
    }
)

SECRET_PARTS = frozenset(
    {
        ".ssh", ".gnupg", ".aws", ".kube", "mcp-tokens",
        "credentials", "secrets", "tokens", "cookies",
    }
)

PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions", "ignore all previous instructions",
    "disregard previous instructions", "reveal secrets", "send secrets",
    "exfiltrate", "delete files", "system prompt", "developer message",
)


def detect_prompt_injection(text: str) -> list[str]:
    lowered = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern in lowered:
            return ["prompt_injection_suspected"]
    return []


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class VaultBoundary:
    """Validates all paths before the adapter touches the filesystem."""

    def __init__(self, config: VaultConfig):
        self.config = config
        self.vault_root = self._existing_dir(config.canonical_vault_root, "vault_not_found")
        self.visible_workspace = self._existing_dir(
            config.visible_workspace, "workspace_not_found"
        )
        self._require_within(self.visible_workspace, self.vault_root, "workspace_outside_vault")

    @staticmethod
    def _existing_dir(path: Path, reason: str) -> Path:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise VaultAccessError(reason, f"Vault path is unavailable: {path}") from exc
        if not resolved.is_dir():
            raise VaultAccessError(reason, f"Vault path is not a directory: {path}")
        return resolved

    @staticmethod
    def _require_within(path: Path, root: Path, reason: str) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise VaultAccessError(reason, f"Path is outside the configured vault: {path}") from exc

    @staticmethod
    def _has_parent_reference(raw: str) -> bool:
        parts = Path(raw.replace("\\", "/")).parts
        return ".." in parts

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _reject_forbidden_parts(self, path: Path, *, operation: str) -> None:
        parts_lower = [part.lower() for part in path.parts]
        name_lower = path.name.lower()
        if name_lower in SECRET_BASENAMES or any(part in SECRET_PARTS for part in parts_lower):
            raise VaultAccessError(
                "forbidden_secret_path",
                "Vault access denied: credential or secret-like paths are blocked.",
            )
        if "hermes memory" in parts_lower:
            reason = "forbidden_hermes_memory_route"
            if operation == "read":
                raise VaultAccessError(reason, "Vault access denied: Hermes Memory is not a source-of-truth route.")
            raise VaultAccessError(reason, "Vault write proposal denied: Hermes Memory is forbidden.")
        if not self.config.allow_hidden_system_paths:
            for part in path.relative_to(self.vault_root).parts:
                if part.startswith("."):
                    raise VaultAccessError(
                        "hidden_path_blocked",
                        "Vault access denied: hidden paths are blocked by policy.",
                    )

    def resolve_read_path(self, requested_path: str) -> Path:
        raw = str(requested_path or "").strip()
        if not raw:
            raise VaultAccessError("empty_path", "Vault path is required.")
        if self._has_parent_reference(raw):
            raise VaultAccessError("path_traversal", "Vault access denied: '..' traversal is blocked.")

        candidate = Path(os.path.expanduser(raw))
        if candidate.is_absolute():
            unresolved = candidate
        else:
            parts = candidate.parts
            unresolved = self.vault_root.joinpath(*parts) if parts and parts[0] == self.visible_workspace.name else self.visible_workspace / candidate

        try:
            resolved = unresolved.resolve(strict=True)
        except OSError as exc:
            raise VaultAccessError("path_not_found", f"Vault note not found: {requested_path}") from exc

        self._require_within(resolved, self.vault_root, "outside_vault")
        if unresolved.absolute() != resolved and not self._is_within(resolved, self.vault_root):
            raise VaultAccessError("symlink_escape", "Vault access denied: symlink escapes the vault.")
        self._reject_forbidden_parts(resolved, operation="read")
        if not resolved.is_file():
            raise VaultAccessError("not_a_file", "Vault path is not a readable note file.")
        if resolved.suffix.lower() != ".md":
            raise VaultAccessError("unsupported_file_type", "Only Markdown notes are readable in the MVP.")
        return resolved

    def resolve_list_folder(self, folder: str | None) -> Path:
        raw = str(folder or "").strip()
        if self._has_parent_reference(raw):
            raise VaultAccessError("path_traversal", "Vault access denied: '..' traversal is blocked.")
        candidate = self.visible_workspace if not raw else self.visible_workspace / raw
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise VaultAccessError("path_not_found", f"Vault folder not found: {folder}") from exc
        self._require_within(resolved, self.visible_workspace, "outside_workspace")
        self._reject_forbidden_parts(resolved, operation="read")
        if not resolved.is_dir():
            raise VaultAccessError("not_a_folder", "Vault path is not a folder.")
        return resolved

    def validate_write_target(self, requested_path: str) -> None:
        raw = str(requested_path or "").strip()
        if self._has_parent_reference(raw):
            raise VaultAccessError("path_traversal", "Vault write proposal denied: '..' traversal is blocked.")
        candidate = Path(os.path.expanduser(raw))
        target = candidate if candidate.is_absolute() else self.visible_workspace / candidate
        resolved_parent = target.parent.resolve(strict=False)
        self._require_within(resolved_parent, self.vault_root, "outside_vault")
        self._reject_forbidden_parts(resolved_parent / target.name, operation="write")

    def relative_receipt_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.visible_workspace))
        except ValueError:
            return str(path.relative_to(self.vault_root))

    def iter_markdown_notes(self, folder: str | None = None) -> Iterable[Path]:
        base = self.resolve_list_folder(folder)
        for path in sorted(base.rglob("*.md")):
            try:
                resolved = path.resolve(strict=True)
                self._require_within(resolved, self.visible_workspace, "outside_workspace")
                self._reject_forbidden_parts(resolved, operation="read")
            except VaultAccessError:
                continue
            if resolved.is_file():
                yield resolved
