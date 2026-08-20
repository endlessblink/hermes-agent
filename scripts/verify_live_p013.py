#!/usr/bin/env python3
"""Exercise P-013 through the installed Hermes Flow State handlers."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

installed_root = os.environ.get("P013_INSTALLED_ROOT")
if installed_root:
    sys.path.insert(0, installed_root)

from tools.flowstate_tool import (
    _handle_create_task,
    _handle_delete_task,
    _handle_get_task,
    _handle_update_task,
)


def parse(value: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise AssertionError("handler returned a non-object")
    return parsed


def require_ok(value: str, label: str) -> dict:
    outer = parse(value)
    parsed = outer.get("result") if isinstance(outer.get("result"), dict) else outer
    if parsed.get("ok") is not True:
        raise AssertionError(f"{label} failed: {json.dumps(parsed, ensure_ascii=False)}")
    return parsed


def unwrap(value: str) -> dict:
    outer = parse(value)
    return outer.get("result") if isinstance(outer.get("result"), dict) else outer


def main() -> int:
    task_id = str(uuid.uuid4())
    prefix = f"P013 live probe {task_id[:8]}"
    created = False
    revision = 0
    results: dict[str, object] = {}

    try:
        create_args = {
            "taskId": task_id,
            "operationId": f"p013-create-{task_id}",
            "title": prefix,
            "description": "Disposable automated verification task",
            "status": "planned",
            "dueTime": "09:15",
            "estimatedDuration": 45,
            "projectId": None,
            "preview": True,
        }
        create_preview = require_ok(_handle_create_task(create_args), "create preview")
        results["create_preview"] = create_preview.get("result") == "preview"
        create_args.update(
            {
                "preview": False,
                "previewDigest": create_preview["previewDigest"],
                "previewExpiresAt": create_preview["previewExpiresAt"],
                "requestHash": create_preview["requestHash"],
            }
        )
        create_commit = require_ok(_handle_create_task(create_args), "create commit")
        created = True
        revision = int(create_commit["receipt"]["readBack"]["canonicalRevision"])
        results["create_receipt"] = create_commit["receipt"].get("status") == "committed"

        patch = {
            "title": f"{prefix} renamed",
            "dueTime": "10:30",
            "estimatedDuration": 60,
        }
        update_base = {
            "id": task_id,
            "operationId": f"p013-update-{task_id}",
            "baseRevision": revision,
            "patch": patch,
            "preview": True,
        }
        update_preview = require_ok(_handle_update_task(update_base), "update preview")
        results["update_preview"] = update_preview.get("result") == "preview"
        before_apply = require_ok(_handle_get_task({"taskId": task_id}), "pre-apply read-back")
        before_task = before_apply.get("task", before_apply.get("result", {}))
        results["preview_did_not_mutate"] = (
            isinstance(before_task, dict)
            and before_task.get("title") == prefix
            and before_task.get("dueTime") == "09:15"
            and before_task.get("estimatedDuration") == 45
        )
        update_commit_args = {
            **update_base,
            "preview": False,
            "previewDigest": update_preview["previewDigest"],
            "previewExpiresAt": update_preview["previewExpiresAt"],
            "requestHash": update_preview["requestHash"],
        }
        update_commit = require_ok(_handle_update_task(update_commit_args), "update commit")
        revision = int(update_commit["receipt"]["readBack"]["canonicalRevision"])
        results["update_receipt"] = update_commit["receipt"].get("status") == "committed"

        replay = require_ok(_handle_update_task(update_commit_args), "update replay")
        results["replay"] = replay.get("result") == "committed"

        stale = unwrap(
            _handle_update_task(
                {
                    **update_base,
                    "operationId": f"p013-stale-{task_id}",
                    "baseRevision": revision - 1,
                }
            )
        )
        results["conflict"] = stale.get("code") == "stale_revision" and stale.get("status") == 409

        unsupported = unwrap(
            _handle_update_task(
                {
                    **update_base,
                    "operationId": f"p013-unsupported-{task_id}",
                    "baseRevision": revision,
                    "patch": {"unsupportedP013Field": True},
                }
            )
        )
        results["unsupported_field"] = "unsupported patch fields" in str(unsupported.get("error", ""))

        read_back = require_ok(_handle_get_task({"taskId": task_id}), "read-back")
        task = read_back.get("task", read_back.get("result", {}))
        results["read_back"] = (
            isinstance(task, dict)
            and task.get("title") == f"{prefix} renamed"
            and task.get("dueTime") == "10:30"
            and task.get("estimatedDuration") == 60
            and task.get("canonicalRevision") == revision
        )
        if not all(results.values()):
            raise AssertionError(
                f"failed checks: {json.dumps({'checks': results, 'conflictResponse': stale, 'unsupportedResponse': unsupported}, ensure_ascii=False)}"
            )
        print(json.dumps({"ok": True, "taskRevision": revision, "checks": results}))
        return 0
    finally:
        if created:
            delete_preview_args = {
                "taskId": task_id,
                "operationId": f"p013-cleanup-{task_id}",
                "baseRevision": revision,
                "preview": True,
            }
            try:
                delete_preview = require_ok(_handle_delete_task(delete_preview_args), "cleanup preview")
                delete_preview_args.update(
                    {
                        "preview": False,
                        "previewDigest": delete_preview["previewDigest"],
                        "previewExpiresAt": delete_preview["previewExpiresAt"],
                        "requestHash": delete_preview["requestHash"],
                    }
                )
                require_ok(_handle_delete_task(delete_preview_args), "cleanup commit")
            except Exception as exc:
                print(f"cleanup failed: {type(exc).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
