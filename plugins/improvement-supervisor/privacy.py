"""Strict redaction for text crossing the supervisor's model/storage boundary."""

from __future__ import annotations

import re
from typing import Any

from agent.redact import redact_cdp_url, redact_sensitive_text


_BARE_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|"
    r"passwd|secret|credential|authorization)\s*[:=]\s*[^\s,;]+"
)


def redact_for_review(value: Any, limit: int) -> str:
    """Apply Hermes redaction plus stricter inline/URL masking and bounds."""
    text = " ".join(str(value or "").split())
    text = redact_sensitive_text(text, force=True)
    text = redact_cdp_url(text)
    text = _BARE_BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:limit]
