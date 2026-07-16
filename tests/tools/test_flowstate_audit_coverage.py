"""Tests for the FlowState audit coverage tool (TASK-1959 Hermes adoption).

Hermes must route review/audit summaries through FlowState's
POST /api/audit/coverage so wording can never claim more than the
receipt-backed evidence supports.
"""

import io
import json
import urllib.error

import pytest

from tools import flowstate_tool as fst


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _capturing_urlopen(seen, payload):
    def _urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = dict(req.header_items())
        seen["body"] = None if req.data is None else json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response(payload)

    return _urlopen


def _http_error(code, payload):
    return urllib.error.HTTPError(
        url="http://127.0.0.1:5577/api/audit/coverage",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def _raising_urlopen(exc):
    def _urlopen(req, timeout):
        raise exc

    return _urlopen


@pytest.fixture(autouse=True)
def flowstate_config(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_URL", "http://127.0.0.1:5577")
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "token-123")


_ACCEPTED_PAYLOAD = {
    "ok": True,
    "contractVersion": "audit-coverage-v2",
    "receipt": {"contractVersion": "audit-coverage-v2", "completeness": "partial"},
    "claimLevel": "partial",
    "summary": "Reviewed 1 of 2 expected items. Exact task coverage was not completed.",
    "summaryLevel": "partial",
    "summaryGuard": {"ok": True, "violations": []},
    "persisted": True,
}

_ARGS = {
    "auditScope": "open tasks in personal scope",
    "sourceSurface": "local-api /api/tasks/inventory",
    "expectedItemIds": ["task-a", "task-b"],
    "reviewedItems": [{"itemId": "task-a", "evidenceClass": "exact-record-read"}],
    "summaryDraft": "Reviewed 1 of 2 expected tasks; exact task coverage was not completed.",
}


def test_audit_coverage_posts_draft_and_returns_receipt(monkeypatch):
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, _ACCEPTED_PAYLOAD))

    result = json.loads(fst._handle_audit_coverage(dict(_ARGS)))

    assert result["result"]["accepted"] is True
    assert result["result"]["claimLevel"] == "partial"
    assert result["result"]["receipt"]["contractVersion"] == "audit-coverage-v2"
    assert seen["url"] == "http://127.0.0.1:5577/api/audit/coverage"
    assert seen["method"] == "POST"
    assert seen["headers"]["Authorization"] == "Bearer token-123"
    assert seen["body"]["summaryDraft"] == _ARGS["summaryDraft"]
    assert seen["body"]["expectedItemIds"] == ["task-a", "task-b"]


def test_audit_coverage_never_asserts_live_verification(monkeypatch):
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, _ACCEPTED_PAYLOAD))

    fst._handle_audit_coverage({**_ARGS, "liveVerified": True})

    # Hermes has no server-owned live proof; the flag must be forced false.
    assert seen["body"]["liveVerified"] is False


def test_audit_coverage_requires_scope_surface_and_draft():
    for missing in ("auditScope", "sourceSurface", "summaryDraft"):
        args = dict(_ARGS)
        args.pop(missing, None)
        result = json.loads(fst._handle_audit_coverage(args))
        assert "error" in result
        assert missing in result["error"]


def test_blocked_draft_returns_safe_summary_as_structured_result(monkeypatch):
    blocked = {
        "error": "broad_claim_blocked",
        "violations": [{"code": "broad-claim", "detail": '"Reviewed all" requires proof'}],
        "claimLevel": "partial",
        "receipt": {"contractVersion": "audit-coverage-v2", "completeness": "partial"},
        "safeSummary": "Reviewed 1 of 2 expected items. Exact task coverage was not completed.",
        "blockedAttempt": {"persisted": True},
    }
    monkeypatch.setattr(fst.urllib.request, "urlopen", _raising_urlopen(_http_error(422, blocked)))

    result = json.loads(
        fst._handle_audit_coverage({**_ARGS, "summaryDraft": "Reviewed all tasks; everything verified."})
    )

    payload = result["result"]
    assert payload["accepted"] is False
    assert payload["blocked"] == "broad_claim_blocked"
    assert payload["safeSummary"] == blocked["safeSummary"]
    assert payload["violations"] == blocked["violations"]
    assert "verbatim" in payload["instruction"]


def test_missing_endpoint_reports_typed_unverifiable_blocker(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _raising_urlopen(_http_error(404, {"error": "not found"})),
    )

    result = json.loads(fst._handle_audit_coverage(dict(_ARGS)))

    assert result["code"] == "audit_endpoint_unavailable"
    assert "cannot be receipt-verified" in result["error"]


def test_connector_down_preserves_unavailable_blocker(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _raising_urlopen(urllib.error.URLError("connection refused")),
    )

    result = json.loads(fst._handle_audit_coverage(dict(_ARGS)))

    assert "error" in result
    assert "unavailable" in result["error"]


def test_audit_coverage_tool_is_registered():
    from tools.registry import registry

    tool = registry.get_entry("flowstate_audit_coverage")
    assert tool is not None
    assert tool.toolset == "flowstate"


def test_prompt_guidance_mandates_audit_coverage_before_summaries():
    from agent.prompt_builder import FLOWSTATE_TOOL_USE_GUIDANCE

    guidance = FLOWSTATE_TOOL_USE_GUIDANCE
    assert "flowstate_audit_coverage" in guidance
    assert "safeSummary" in guidance
