"""Narrow, consent-aware candidate capture for the Life-Boat profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable

PLUGIN_ID = "lifeboat-emotional-candidate-capture"
_ACTIVE_RE = re.compile(r"^\s*status:\s*active\s*$", re.MULTILINE)
_ITEM_RE = re.compile(r"(?ms)^- id: `([^`]+)`\n(.*?)(?=^\n- id: |^\n## |\Z)")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_EXPLICIT_RE = re.compile(
    r"(?:\b(?:add|save|remember|record|note)\b.*(?:\b(?:this|that|moment|what happened|emotional|bully)\b)"
    r"|(?:תוסיף|שמור|תזכור|תעד).*(?:רגע|מה שקרה|רגשי|בולי|זה))",
    re.IGNORECASE | re.DOTALL,
)
_PREFIX_RE = re.compile(
    r"^\s*(?:please\s+)?(?:add|save|remember|record|note)\s+(?:this\s+)?"
    r"(?:moment\s*)?(?:for\s+later\s+(?:emotional|bully)\s+work\s*)?[:,-]?\s*"
    r"|^\s*(?:תוסיף|שמור|תזכור|תעד)\s*(?:את\s*)?(?:הרגע\s*)?(?:לעבודה\s*רגשית\s*)?[:,-]?\s*",
    re.IGNORECASE,
)
_URL_EMAIL_RE = re.compile(r"(?:https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b)")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_TELEGRAM_RE = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{2,31}")
_EMOTION_RE = re.compile(
    r"(?:\b(?:felt|feel|made me feel|worried|ashamed|embarrassed|rejected|"
    r"too much|not enough|left out|outside|harsh(?:ly)? judged|self[- ]criticism)\b|"
    r"(?:הרגשתי|מרגיש(?:ה)?|גרם לי להרגיש|דחו אותי|התביישתי|נפגעתי|"
    r"יותר מדי|לא מספיק|נשארתי בחוץ|ביקורת עצמית|שפטתי את עצמי))",
    re.IGNORECASE,
)
_EVENT_RE = re.compile(
    r"(?:\b(?:reply|response|message|invitation|conversation|comment|silence|"
    r"ignored|unanswered|volunteer|friend|boss|look|tone)\b|"
    r"(?:תגובה|הודעה|הזמנה|שיחה|הערה|שתיקה|התעלמו|מתנדב(?:ת)?|חבר(?:ה)?|מנהל(?:ת)?))",
    re.IGNORECASE,
)
# Hebrew inference stays narrower than the general emotional vocabulary: it
# needs a concrete dating-app action/scene and a jealousy, comparison, or
# self-worth distress cue in the same message.
_HEBREW_TRIGGER_RE = re.compile(
    r"(?:אפליקציית\s+היכרויות|אפליקציות\s+היכרויות|פתחתי|נכנסתי|גללתי|"
    r"ראיתי\s+זוגות|בדקתי\s+פרופילים)",
    re.IGNORECASE,
)
_HEBREW_DISTRESS_RE = re.compile(
    r"(?:קינאתי|מקנא(?:ת)?|קנאה|השוויתי|השוואה|"
    r"פחות\s+שווה|לא\s+שווה|לא\s+מספיק|נחות(?:ה)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CaptureResult:
    status: str
    candidate_id: str | None = None
    message: str = ""


def is_lifeboat_profile(profile_name: str | None) -> bool:
    return str(profile_name or "").strip().casefold() == "life-advisor"


def explicit_capture_requested(user_message: str) -> bool:
    return bool(_EXPLICIT_RE.search(" ".join(str(user_message or "").split())))


def inferred_candidate_signal(user_message: str) -> bool:
    """Require both a concrete interpersonal/event cue and an emotional cue."""
    text = " ".join(str(user_message or "").split())
    broad_signal = _EVENT_RE.search(text) and _EMOTION_RE.search(text)
    narrow_hebrew_signal = _HEBREW_TRIGGER_RE.search(text) and _HEBREW_DISTRESS_RE.search(text)
    return bool(broad_signal or narrow_hebrew_signal)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _curate_summary(user_message: str) -> str:
    text = _PREFIX_RE.sub("", " ".join(str(user_message or "").split()), count=1).strip()
    text = _URL_EMAIL_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    text = _TELEGRAM_RE.sub("[redacted]", text)
    text = re.split(r"(?<=[.!?؟])\s+", text, maxsplit=1)[0]
    return text[:220].rstrip(" .,;:")


def _candidate_id(summary: str) -> str:
    digest = hashlib.sha256(_normalized(summary).encode("utf-8")).hexdigest()[:12]
    return f"candidate-{digest}"


def _items(queue_text: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in _ITEM_RE.finditer(queue_text)]


def _validate_queue(queue_text: str) -> list[tuple[str, str]]:
    if not queue_text.startswith("---\n") or "# Emotional Processing Queue" not in queue_text:
        raise ValueError("queue header is malformed")
    items = _items(queue_text)
    if not items:
        raise ValueError("queue has no items")
    ids = [item_id for item_id, _ in items]
    if len(ids) != len(set(ids)) or any(not _ID_RE.fullmatch(item_id) for item_id in ids):
        raise ValueError("queue item IDs are malformed or duplicated")
    if sum(bool(_ACTIVE_RE.search(body)) for _, body in items) > 1:
        raise ValueError("queue has more than one active topic")
    return items


def _existing_content(items: Iterable[tuple[str, str]]) -> set[str]:
    values: set[str] = set()
    for item_id, body in items:
        values.add(_normalized(item_id))
        values.update(
            _normalized(match)
            for match in re.findall(r"^\s*(?:topic|next_point):\s*(.+)$", body, re.MULTILINE)
        )
    return values


def _plugin_config() -> dict:
    try:
        from hermes_cli.config import load_config

        config = load_config() or {}
        return (((config.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {})
    except Exception:
        return {}


def configured_queue_path() -> Path | None:
    value = _plugin_config().get("queue_path")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def _atomic_replace(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if mode is not None:
            os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capture_candidate(
    user_message: str,
    *,
    profile_name: str | None,
    queue_path: Path | None,
    today: date | None = None,
    validate_queue: Callable[[str], list[tuple[str, str]]] = _validate_queue,
) -> CaptureResult:
    if not is_lifeboat_profile(profile_name) or queue_path is None:
        return CaptureResult("disabled", message="candidate capture is not configured for Life-Boat")

    explicit = explicit_capture_requested(user_message)
    if not explicit and not inferred_candidate_signal(user_message):
        return CaptureResult("none")
    summary = _curate_summary(user_message)
    if not summary:
        return CaptureResult("rejected", message="the moment was too vague to curate safely")
    if not explicit:
        return CaptureResult("proposal", message=f"Possible later emotional candidate: {summary}")

    original = queue_path.read_text(encoding="utf-8")
    items = validate_queue(original)
    candidate_id = _candidate_id(summary)
    if _normalized(candidate_id) in _existing_content(items) or _normalized(summary) in _existing_content(items):
        return CaptureResult("duplicate", candidate_id=candidate_id, message="candidate already exists")

    added = (today or date.today()).isoformat()
    item = (
        f"- id: `{candidate_id}`\n"
        "  status: pending\n"
        f"  added: {added}\n"
        f"  topic: {summary}\n"
        "  next_point: Start with the concrete moment; separate observable facts from the bully's verdict and action-demand.\n\n"
    )
    marker = "## Separate threads"
    if marker not in original:
        raise ValueError("queue insertion marker is missing")
    updated = original.replace(marker, item + marker, 1)
    mode = queue_path.stat().st_mode & 0o777
    _atomic_replace(queue_path, updated, mode)
    try:
        read_back = queue_path.read_text(encoding="utf-8")
        read_items = validate_queue(read_back)
        if [item_id for item_id, _ in read_items] != [item_id for item_id, _ in items] + [candidate_id]:
            raise ValueError("queue read-back changed existing order or lost an item")
        if sum(bool(_ACTIVE_RE.search(body)) for _, body in read_items) > 1:
            raise ValueError("queue read-back violated the one-active invariant")
    except Exception:
        _atomic_replace(queue_path, original, mode)
        raise
    return CaptureResult("captured", candidate_id=candidate_id, message="candidate captured and verified")


def pre_llm_capture(**kwargs: object) -> dict[str, str] | None:
    """Adapter for the current pre_llm_call kwargs; failures stay fail-closed."""
    try:
        from hermes_cli.profiles import get_active_profile_name

        result = capture_candidate(
            str(kwargs.get("user_message") or ""),
            profile_name=get_active_profile_name(),
            queue_path=configured_queue_path(),
        )
        if result.status == "proposal":
            return {"context": result.message}
        if result.status == "captured":
            return {"context": f"Life-Boat candidate receipt: {result.candidate_id} (read-back verified)."}
    except Exception:
        return None
    return None
