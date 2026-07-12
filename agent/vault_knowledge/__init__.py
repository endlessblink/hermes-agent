"""Safe read-only access layer for Obsidian-backed Hermes knowledge."""

from .config import VaultConfig, load_vault_config
from .path_policy import VaultAccessError, VaultBoundary
from .retrieval import RetrievalService

__all__ = [
    "RetrievalService",
    "VaultAccessError",
    "VaultBoundary",
    "VaultConfig",
    "load_vault_config",
]
