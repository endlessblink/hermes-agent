"""Hermes plugin entry point for the Life-Boat response reviewer."""

try:
    from .reviewer import register
except ImportError:  # Direct module loading in isolated test/verification contexts.
    from reviewer import register

__all__ = ["register"]
