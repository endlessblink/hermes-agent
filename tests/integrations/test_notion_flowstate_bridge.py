from __future__ import annotations

import importlib
import hashlib
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from integrations.notion_flowstate_bridge.bridge import (
    Bridge,
    BridgeConfig,
    BridgeError,
    JsonTransport,
)


class FakeTransport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, headers, payload=None):
        self.calls.append((method, url, headers, payload))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(method, url, headers, payload)
        return response


def config(tmp_path: Path, **overrides) -> BridgeConfig:
    values = {
        "notion_token": "notion-secret-value",
        "notion_data_source_id": "source-1",
        "notion_idempotency_property": "Hermes operation ID",
        "notion_writable_properties": ("Name", "Status", "Done"),
        "flowstate_base_url": "https://flowstate.test",
        "flowstate_token": "flowstate-secret-value",
        "state_path": tmp_path / "receipts.sqlite3",
        "preview_ttl_seconds": 300,
    }
    values.update(overrides)
    return BridgeConfig(**values)


def rich_text(content: str):
    return {"rich_text": [{"type": "text", "text": {"content": content}}]}


def page(page_id="page-1", source_id="source-1", properties=None):
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "last_edited_time": "2026-07-13T18:00:00Z",
        "parent": {"type": "data_source_id", "data_source_id": source_id},
        "properties": properties or {},
    }


def test_manifest_and_register_expose_user_installed_standalone_tools():
    root = Path(__file__).parents[2] / "integrations" / "notion_flowstate_bridge"
    manifest = (root / "plugin.yaml").read_text(encoding="utf-8")
    assert "kind: standalone" in manifest
    assert "notion_flowstate_activate" in manifest

    class Context:
        def __init__(self, profile_name="office-work"):
            self.tools = []
            self.profile_name = profile_name

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

    module = importlib.import_module("integrations.notion_flowstate_bridge")
    context = Context()
    module.register(context)
    assert {tool["name"] for tool in context.tools} == {
        "notion_data_source_schema",
        "notion_data_source_list",
        "notion_page_get",
        "notion_mutation",
        "notion_flowstate_activate",
    }
    assert all(tool["toolset"] == "notion_flowstate_bridge" for tool in context.tools)


def test_registered_tools_fail_closed_outside_office_work(monkeypatch):
    class Context:
        def __init__(self, profile_name):
            self.profile_name = profile_name
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

    module = importlib.import_module("integrations.notion_flowstate_bridge")
    monkeypatch.setenv("NOTION_TOKEN", "configured")
    office = Context("office-work")
    module.register(office)
    assert all(tool["check_fn"]() for tool in office.tools)
    default = Context("default")
    module.register(default)
    assert not any(tool["check_fn"]() for tool in default.tools)


def test_config_repr_never_exposes_tokens(tmp_path):
    rendered = repr(config(tmp_path))
    assert "notion-secret-value" not in rendered
    assert "flowstate-secret-value" not in rendered


def test_read_tools_use_current_data_source_endpoints_and_bound_page_size(tmp_path):
    transport = FakeTransport(
        [
            {"id": "source-1", "properties": {}},
            {"results": [page()]},
            page(),
        ]
    )
    bridge = Bridge(config(tmp_path), transport=transport)
    assert bridge.read_schema({})["data_source"]["id"] == "source-1"
    assert bridge.list_pages({"page_size": 1})["query"]["results"][0]["id"] == "page-1"
    assert bridge.read_page({"page_id": "page-1"})["page"]["id"] == "page-1"
    assert transport.calls[0][1].endswith("/data_sources/source-1")
    assert transport.calls[1][1].endswith("/data_sources/source-1/query")
    assert transport.calls[1][3] == {"page_size": 1}
    assert transport.calls[2][1].endswith("/pages/page-1")
    assert transport.calls[0][2]["Authorization"] == "Bearer notion-secret-value"
    with pytest.raises(BridgeError, match="page_size"):
        bridge.list_pages({"page_size": 101})


def test_data_source_and_writable_properties_are_exactly_allowlisted(tmp_path):
    bridge = Bridge(config(tmp_path), transport=FakeTransport([page(source_id="other-source")]))
    with pytest.raises(BridgeError) as wrong_source:
        bridge.read_schema({"data_source_id": "other-source"})
    assert wrong_source.value.code == "scope_violation"
    with pytest.raises(BridgeError) as wrong_page:
        bridge.read_page({"page_id": "page-1"})
    assert wrong_page.value.code == "provenance_mismatch"
    with pytest.raises(BridgeError) as wrong_property:
        bridge.mutate_notion(
            {
                "operation_id": "op-out-of-scope",
                "action": "property_update",
                "page_id": "page-1",
                "properties": {"Secret admin field": {"checkbox": True}},
            }
        )
    assert wrong_property.value.code == "scope_violation"


def test_property_update_requires_exact_preview_then_verifies_and_deduplicates(tmp_path):
    properties = {"Status": {"status": {"name": "In progress"}}}
    transport = FakeTransport(
        [
            page(properties={"Status": {"status": {"name": "Backlog"}}}),  # preview target
            page(properties={"Status": {"status": {"name": "Backlog"}}}),  # recovery read
            {},  # PATCH
            page(properties=properties),  # verification read
        ]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "op-update-1",
        "action": "property_update",
        "page_id": "page-1",
        "properties": properties,
    }
    preview = bridge.mutate_notion(base)
    assert preview["mode"] == "preview"
    assert [call[0] for call in transport.calls] == ["GET"]
    applied = bridge.mutate_notion(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert applied == {
        "mode": "applied",
        "operation_id": "op-update-1",
        "action": "property_update",
        "page_id": "page-1",
        "data_source_id": "source-1",
        "verified": True,
        "duplicate": False,
    }
    duplicate = bridge.mutate_notion(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert duplicate["duplicate"] is True
    assert [call[0] for call in transport.calls] == ["GET", "GET", "PATCH", "GET"]


def test_verified_receipt_and_preview_survive_bridge_restart(tmp_path):
    properties = {"Done": {"checkbox": True}}
    first_transport = FakeTransport(
        [
            page(properties={"Done": {"checkbox": False}}),
            page(properties={"Done": {"checkbox": False}}),
            {},
            page(properties=properties),
        ]
    )
    bridge_config = config(tmp_path)
    first = Bridge(bridge_config, transport=first_transport, clock=lambda: 1000)
    args = {
        "operation_id": "op-persistent",
        "action": "property_update",
        "page_id": "page-1",
        "properties": properties,
    }
    preview = first.mutate_notion(args)
    first.mutate_notion({**args, "mode": "apply", "preview_digest": preview["preview_digest"]})

    second_transport = FakeTransport()
    restarted = Bridge(bridge_config, transport=second_transport, clock=lambda: 1001)
    duplicate = restarted.mutate_notion(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert duplicate["duplicate"] is True
    assert second_transport.calls == []


def test_verified_receipt_replays_after_preview_expiry(tmp_path):
    properties = {"Done": {"checkbox": True}}
    now = [1000.0]
    transport = FakeTransport(
        [page(properties=properties), page(properties=properties)]
    )
    bridge = Bridge(
        config(tmp_path, preview_ttl_seconds=30),
        transport=transport,
        clock=lambda: now[0],
    )
    args = {
        "operation_id": "op-expired-receipt",
        "action": "property_update",
        "page_id": "page-1",
        "properties": properties,
    }
    preview = bridge.mutate_notion(args)
    bridge.mutate_notion(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    now[0] = 2000
    replay = bridge.mutate_notion(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert replay["duplicate"] is True
    assert len(transport.calls) == 2


def test_atomic_claim_rejects_concurrent_apply_and_allows_stale_recovery(tmp_path):
    bridge = Bridge(config(tmp_path), transport=FakeTransport())
    request = {"operation_id": "op-claim", "value": 1}
    assert bridge.store.claim("op-claim", "test", request, 1000) is None
    with pytest.raises(BridgeError) as active:
        bridge.store.claim("op-claim", "test", request, 1059)
    assert active.value.code == "operation_in_progress"
    assert bridge.store.claim("op-claim", "test", request, 1061) is None


def test_apply_rejects_missing_mismatched_and_expired_previews(tmp_path):
    properties = {"Done": {"checkbox": True}}
    base = {
        "operation_id": "op-expiry",
        "action": "property_update",
        "page_id": "page-1",
        "properties": properties,
    }
    now = [1000.0]
    transport = FakeTransport([page(properties=properties)])
    bridge = Bridge(config(tmp_path, preview_ttl_seconds=30), transport=transport, clock=lambda: now[0])
    with pytest.raises(BridgeError) as missing:
        bridge.mutate_notion({**base, "mode": "apply", "preview_digest": "sha256:nope"})
    assert missing.value.code == "preview_required"
    preview = bridge.mutate_notion(base)
    with pytest.raises(BridgeError) as mismatch:
        bridge.mutate_notion({**base, "mode": "apply", "preview_digest": "sha256:wrong"})
    assert mismatch.value.code == "preview_mismatch"
    now[0] = 1031
    with pytest.raises(BridgeError) as expired:
        bridge.mutate_notion({**base, "mode": "apply", "preview_digest": preview["preview_digest"]})
    assert expired.value.code == "preview_expired"


def test_operation_id_cannot_be_reused_for_different_intent(tmp_path):
    transport = FakeTransport([page(), page()])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    first = {
        "operation_id": "stable-op",
        "action": "property_update",
        "page_id": "page-1",
        "properties": {"Done": {"checkbox": True}},
    }
    bridge.mutate_notion(first)
    with pytest.raises(BridgeError) as conflict:
        bridge.mutate_notion({**first, "properties": {"Done": {"checkbox": False}}})
    assert conflict.value.code == "operation_conflict"


def test_preview_digest_binds_original_expiry_without_silent_extension(tmp_path):
    now = [1000.0]
    transport = FakeTransport([page(), page()])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: now[0])
    args = {
        "operation_id": "expiry-bound-op",
        "action": "property_update",
        "page_id": "page-1",
        "properties": {"Done": {"checkbox": True}},
    }
    first = bridge.mutate_notion(args)
    now[0] = 1001
    with pytest.raises(BridgeError) as conflict:
        bridge.mutate_notion(args)
    assert conflict.value.code == "operation_conflict"
    now[0] = 1301
    with pytest.raises(BridgeError) as expired:
        bridge.mutate_notion(
            {**args, "mode": "apply", "preview_digest": first["preview_digest"]}
        )
    assert expired.value.code == "preview_expired"


def test_create_requires_configured_rich_text_idempotency_property(tmp_path):
    transport = FakeTransport([{"properties": {"Hermes operation ID": {"type": "number"}}}])
    bridge = Bridge(config(tmp_path), transport=transport)
    with pytest.raises(BridgeError) as error:
        bridge.mutate_notion(
            {
                "operation_id": "op-create-invalid",
                "action": "create",
                "properties": {"Name": rich_text("Task")},
            }
        )
    assert error.value.code == "invalid_schema"


def test_create_recovers_by_exact_query_without_duplicate_post(tmp_path):
    expected = {
        "Name": rich_text("Task"),
        "Hermes operation ID": rich_text("op-create-recover"),
    }
    existing = page("existing-page", properties=expected)
    transport = FakeTransport(
        [
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},  # preview schema
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},  # apply schema
            {"results": [existing]},
            existing,
        ]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "op-create-recover",
        "action": "create",
        "properties": {"Name": rich_text("Task")},
    }
    preview = bridge.mutate_notion(base)
    receipt = bridge.mutate_notion(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["page_id"] == "existing-page"
    assert not any(call[0] == "POST" and call[1].endswith("/pages") for call in transport.calls)
    query_payload = transport.calls[2][3]
    assert query_payload["filter"] == {
        "property": "Hermes operation ID",
        "rich_text": {"equals": "op-create-recover"},
    }


def test_notion_readback_accepts_augmented_rich_text_response_shape(tmp_path):
    requested = {"Name": rich_text("Task")}
    actual = {
        "Name": {
            "id": "title",
            "type": "rich_text",
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": "Task", "link": None},
                    "plain_text": "Task",
                    "href": None,
                }
            ],
        }
    }
    transport = FakeTransport([page(properties={}), page(properties=actual)])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "op-rich-text-readback",
        "action": "property_update",
        "page_id": "page-1",
        "properties": requested,
    }
    preview = bridge.mutate_notion(base)
    receipt = bridge.mutate_notion(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["verified"] is True
    assert [call[0] for call in transport.calls] == ["GET", "GET"]


def test_ambiguous_create_failure_recovers_with_same_exact_operation_query(tmp_path):
    expected = {
        "Name": rich_text("Task"),
        "Hermes operation ID": rich_text("op-ambiguous"),
    }
    created = page("created-page", properties=expected)
    transport = FakeTransport(
        [
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"results": []},
            BridgeError("remote_unavailable", "Remote service is unavailable"),
            {"results": [created]},
            created,
        ]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "op-ambiguous",
        "action": "create",
        "properties": {"Name": rich_text("Task")},
    }
    preview = bridge.mutate_notion(base)
    receipt = bridge.mutate_notion(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["page_id"] == "created-page"
    query_calls = [call for call in transport.calls if call[1].endswith("/query")]
    assert len(query_calls) == 2
    assert query_calls[0][3] == query_calls[1][3]


def test_expired_stale_create_claim_only_recovers_existing_remote_page(tmp_path):
    now = [1000.0]
    transport = FakeTransport(
        [
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"results": []},
        ]
    )
    bridge = Bridge(
        config(tmp_path, preview_ttl_seconds=30),
        transport=transport,
        clock=lambda: now[0],
    )
    args = {
        "operation_id": "op-stale-create",
        "action": "create",
        "properties": {"Name": rich_text("Task")},
    }
    preview = bridge.mutate_notion(args)
    _, request = bridge._notion_request(args)
    bridge.store.claim("op-stale-create", "notion_mutation", request, now[0])
    now[0] = 1061
    with pytest.raises(BridgeError) as error:
        bridge.mutate_notion(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "preview_expired"
    assert not any(call[0] == "POST" and call[1].endswith("/pages") for call in transport.calls)


def test_expired_stale_claim_recovers_matching_notion_readback_without_new_write(tmp_path):
    now = [1000.0]
    properties = {"Done": {"checkbox": True}}
    transport = FakeTransport(
        [page(properties={"Done": {"checkbox": False}}), page(properties=properties)]
    )
    bridge = Bridge(
        config(tmp_path, preview_ttl_seconds=30),
        transport=transport,
        clock=lambda: now[0],
    )
    args = {
        "operation_id": "op-stale-update",
        "action": "property_update",
        "page_id": "page-1",
        "properties": properties,
    }
    preview = bridge.mutate_notion(args)
    _, request = bridge._notion_request(args)
    bridge.store.claim("op-stale-update", "notion_mutation", request, now[0])
    now[0] = 1061
    receipt = bridge.mutate_notion(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["verified"] is True
    assert [call[0] for call in transport.calls] == ["GET", "GET"]


def test_multiple_exact_create_matches_fail_closed(tmp_path):
    transport = FakeTransport(
        [
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"properties": {"Hermes operation ID": {"type": "rich_text"}}},
            {"results": [page("one"), page("two")]},
        ]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {"operation_id": "op-dupes", "action": "create", "properties": {"Name": rich_text("x")}}
    preview = bridge.mutate_notion(base)
    with pytest.raises(BridgeError) as error:
        bridge.mutate_notion({**base, "mode": "apply", "preview_digest": preview["preview_digest"]})
    assert error.value.code == "duplicate_remote"


def activation_preview(
    operation="activation-1",
    page_id="page-1",
    source_id="source-1",
    already=False,
    expires_at=1200,
):
    return {
        "ok": True,
        "result": "preview",
        "contractVersion": "notion-activation-v1",
        "operationId": operation,
        "alreadyActivated": already,
        "previewDigest": "sha256:flowstate-preview",
        "previewExpiresAt": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
        "normalizedPayload": {
            "operationId": operation,
            "notionPageId": page_id,
            "notionDataSourceId": source_id,
            "notionUrl": f"https://notion.so/{page_id}",
            "notionLastEditedAt": "2026-07-13T18:00:00Z",
            "task": {
                "title": "Work",
                "description": "",
                "priority": None,
                "dueDate": None,
                "projectId": None,
            },
            "workBlock": {
                "scheduledDate": "2026-07-13",
                "scheduledTime": "18:30",
                "duration": 25,
            },
        },
        "readBack": None,
    }


def activation_receipt(operation="activation-1", page_id="page-1", source_id="source-1"):
    response = {
        "ok": True,
        "result": "committed",
        "receipt": {
            "contractVersion": "notion-activation-v1",
            "source": "notion",
            "externalId": page_id,
            "operationId": operation,
            "entityType": "task",
            "action": "activate",
            "entityId": "flow-task-1",
            "canonicalRevision": 3,
            "canonicalUpdatedAt": "2026-07-13T18:30:00Z",
            "changeSequence": 42,
            "replayed": False,
            "committedAt": "2026-07-13T18:30:00Z",
            "readBackHash": "",
            "provenance": {
                "source": "notion",
                "externalId": page_id,
                "dataSourceId": source_id,
                "url": f"https://notion.so/{page_id}",
                "lastEditedAt": "2026-07-13T18:00:00Z",
            },
            "readBack": {
                "id": "flow-task-1",
                "title": "Work",
                "description": "",
                "priority": None,
                "dueDate": None,
                "projectId": None,
                "canonicalRevision": 3,
                "canonicalUpdatedAt": "2026-07-13T18:30:00Z",
                "externalSource": "notion",
                "externalId": page_id,
                "externalDataSourceId": source_id,
                "externalUrl": f"https://notion.so/{page_id}",
                "externalLastEditedAt": "2026-07-13T18:00:00Z",
                "provenance": {
                    "source": "notion",
                    "externalId": page_id,
                    "dataSourceId": source_id,
                    "url": f"https://notion.so/{page_id}",
                    "lastEditedAt": "2026-07-13T18:00:00Z",
                },
                "instances": [
                    {
                        "scheduledDate": "2026-07-13",
                        "scheduledTime": "18:30",
                        "duration": 25,
                    }
                ],
            },
        }
    }
    refresh_activation_hash(response)
    return response


def refresh_activation_hash(response):
    read_back = response["receipt"]["readBack"]
    response["receipt"]["readBackHash"] = hashlib.sha256(
        json.dumps(
            read_back,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_activation_fetches_exact_page_and_uses_preview_apply_contract(tmp_path):
    notion_page = page(properties={"Name": rich_text("Work")})
    transport = FakeTransport(
        [notion_page, activation_preview(), notion_page, activation_receipt(), notion_page]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {"scheduledDate": "2026-07-13", "scheduledTime": "18:30", "duration": 25},
    }
    preview = bridge.activate(base)
    assert preview["preview_digest"] == "sha256:flowstate-preview"
    assert preview["already_activated"] is False
    assert transport.calls[1][3]["preview"] is True
    assert transport.calls[1][3]["notion"] == {
        "pageId": "page-1",
        "dataSourceId": "source-1",
        "url": "https://notion.so/page-1",
        "lastEditedAt": "2026-07-13T18:00:00Z",
    }
    assert transport.calls[1][3]["task"] == {"title": "Work"}
    receipt = bridge.activate(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["flowstate_task_id"] == "flow-task-1"
    apply_payload = transport.calls[3][3]
    assert apply_payload["preview"] is False
    assert apply_payload["previewDigest"] == "sha256:flowstate-preview"
    assert apply_payload["previewExpiresAt"] == datetime.fromtimestamp(
        1200, timezone.utc
    ).isoformat()
    assert apply_payload["operationId"] == "activation-1"
    duplicate = bridge.activate(
        {**base, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert duplicate["duplicate"] is True
    assert len([call for call in transport.calls if call[1].endswith("/activations")]) == 2
    assert len([call for call in transport.calls if call[1].endswith("/pages/page-1")]) == 3


def test_activation_surfaces_existing_task_and_requires_exact_approved_block_in_readback(tmp_path):
    existing = activation_preview(already=True)
    receipt = activation_receipt()
    receipt["receipt"]["readBack"]["instances"] = []
    refresh_activation_hash(receipt)
    transport = FakeTransport([page(), existing, page(), receipt])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    assert preview["already_activated"] is True
    with pytest.raises(BridgeError) as error:
        bridge.activate(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "verification_failed"


def test_activation_can_start_without_scheduling_a_work_block(tmp_path):
    preview_response = activation_preview()
    preview_response["normalizedPayload"]["workBlock"] = None
    receipt_response = activation_receipt()
    receipt_response["receipt"]["readBack"]["instances"] = []
    refresh_activation_hash(receipt_response)
    transport = FakeTransport(
        [page(), preview_response, page(), receipt_response]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
    }

    preview = bridge.activate(args)
    receipt = bridge.activate(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )

    assert preview["preview"]["workBlock"] is None
    assert receipt["flowstate_task_id"] == "flow-task-1"


def test_ambiguous_activation_commit_can_replay_after_local_preview_expiry(tmp_path):
    now = [1000.0]
    transport = FakeTransport(
        [page(), activation_preview(expires_at=1030), page(), activation_receipt()]
    )
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: now[0])
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    _, request = bridge._activation_request(args, page())
    bridge.store.claim("activation-1", "flowstate_activation", request, now[0])
    now[0] = 1061
    receipt = bridge.activate(
        {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
    )
    assert receipt["flowstate_task_id"] == "flow-task-1"


def test_activation_rejects_page_and_receipt_provenance_mismatches(tmp_path):
    bridge = Bridge(
        config(tmp_path),
        transport=FakeTransport([page(source_id="other-source")]),
        clock=lambda: 1000,
    )
    with pytest.raises(BridgeError) as wrong_parent:
        bridge.activate(
                {"operation_id": "a", "page_id": "page-1", "task": {"title": "Work"}, "work_block": {"scheduledDate": "2026-07-13", "scheduledTime": "18:30", "duration": 10}}
        )
    assert wrong_parent.value.code == "provenance_mismatch"

    transport = FakeTransport([page(), activation_preview(operation="other-operation")])
    bridge = Bridge(config(tmp_path, state_path=tmp_path / "other.sqlite3"), transport=transport, clock=lambda: 1000)
    with pytest.raises(BridgeError) as wrong_receipt:
        bridge.activate(
                {"operation_id": "activation-1", "page_id": "page-1", "task": {"title": "Work"}, "work_block": {"scheduledDate": "2026-07-13", "scheduledTime": "18:30", "duration": 10}}
        )
    assert wrong_receipt.value.code == "receipt_mismatch"

    changed_preview = activation_preview()
    changed_preview["normalizedPayload"]["notionUrl"] = "https://notion.so/other"
    transport = FakeTransport([page(), changed_preview])
    bridge = Bridge(
        config(tmp_path, state_path=tmp_path / "changed-preview.sqlite3"),
        transport=transport,
        clock=lambda: 1000,
    )
    with pytest.raises(BridgeError) as changed_intent:
        bridge.activate(
            {
                "operation_id": "activation-1",
                "page_id": "page-1",
                "task": {"title": "Work"},
                "work_block": {
                    "scheduledDate": "2026-07-13",
                    "scheduledTime": "18:30",
                    "duration": 25,
                },
            }
        )
    assert changed_intent.value.code == "receipt_mismatch"


def test_activation_accepts_semantically_equal_normalized_due_date(tmp_path):
    preview_response = activation_preview()
    preview_response["normalizedPayload"]["task"]["dueDate"] = "2026-07-14T08:00:00Z"
    transport = FakeTransport([page(), preview_response])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    preview = bridge.activate(
        {
            "operation_id": "activation-1",
            "page_id": "page-1",
            "task": {"title": "Work", "dueDate": "2026-07-14T10:00:00+02:00"},
            "work_block": {
                "scheduledDate": "2026-07-13",
                "scheduledTime": "18:30",
                "duration": 25,
            },
        }
    )
    assert preview["preview_digest"] == "sha256:flowstate-preview"


def test_activation_requires_flowstate_verified_receipt(tmp_path):
    unverified = activation_receipt()
    unverified["receipt"]["readBack"]["externalId"] = "other-page"
    transport = FakeTransport([page(), activation_preview(), page(), unverified])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    base = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(base)
    with pytest.raises(BridgeError) as error:
        bridge.activate({**base, "mode": "apply", "preview_digest": preview["preview_digest"]})
    assert error.value.code == "receipt_mismatch"


def test_activation_rejects_receipt_with_changed_notion_provenance(tmp_path):
    changed = activation_receipt()
    changed["receipt"]["provenance"]["dataSourceId"] = "other-source"
    transport = FakeTransport([page(), activation_preview(), page(), changed])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    with pytest.raises(BridgeError) as error:
        bridge.activate(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "receipt_mismatch"


def test_activation_rejects_changed_task_projection_even_with_matching_hash(tmp_path):
    changed = activation_receipt()
    changed["receipt"]["readBack"]["title"] = "Different task"
    refresh_activation_hash(changed)
    transport = FakeTransport([page(), activation_preview(), page(), changed])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    with pytest.raises(BridgeError) as error:
        bridge.activate(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "receipt_mismatch"


def test_activation_rejects_readback_hash_mismatch(tmp_path):
    changed = activation_receipt()
    changed["receipt"]["readBack"]["description"] = "Changed after hashing"
    transport = FakeTransport([page(), activation_preview(), page(), changed])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    with pytest.raises(BridgeError) as error:
        bridge.activate(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "receipt_mismatch"


def test_activation_rejects_receipt_without_canonical_revision_sequence_and_hash(tmp_path):
    invalid = activation_receipt()
    del invalid["receipt"]["changeSequence"]
    transport = FakeTransport([page(), activation_preview(), page(), invalid])
    bridge = Bridge(config(tmp_path), transport=transport, clock=lambda: 1000)
    args = {
        "operation_id": "activation-1",
        "page_id": "page-1",
        "task": {"title": "Work"},
        "work_block": {
            "scheduledDate": "2026-07-13",
            "scheduledTime": "18:30",
            "duration": 25,
        },
    }
    preview = bridge.activate(args)
    with pytest.raises(BridgeError) as error:
        bridge.activate(
            {**args, "mode": "apply", "preview_digest": preview["preview_digest"]}
        )
    assert error.value.code == "receipt_mismatch"


def test_http_transport_redacts_credentials_and_remote_body_from_errors():
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "token=leaked-by-server",
            hdrs=None,
            fp=None,
        )

    transport = JsonTransport(opener=reject)
    with pytest.raises(BridgeError) as error:
        transport.request(
            "GET",
            "https://notion.test/page",
            headers={"Authorization": "Bearer super-secret"},
        )
    assert error.value.code == "remote_auth"
    rendered = str(error.value)
    assert "super-secret" not in rendered
    assert "leaked-by-server" not in rendered


def test_inputs_are_bounded_before_transport(tmp_path):
    bridge = Bridge(config(tmp_path), transport=FakeTransport())
    with pytest.raises(BridgeError) as error:
        bridge.mutate_notion(
            {
                "operation_id": "op-large",
                "action": "create",
                "properties": {"Name": rich_text("x" * (70 * 1024))},
            }
        )
    assert error.value.code == "input_too_large"
