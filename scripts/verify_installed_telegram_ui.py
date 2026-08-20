#!/usr/bin/env python3
"""Exercise deployed Telegram command and Life-Boat form rendering only."""

from __future__ import annotations

import json


def main() -> int:
    from hermes_cli.commands import telegram_bot_commands
    from plugins.platforms.telegram.adapter import _render_hermes_ui_payload_for_telegram

    commands = dict(telegram_bot_commands())
    artifact = {
        "type": "form",
        "direction": "rtl",
        "id": "energy",
        "title": "מה מתאים היום?",
        "fields": [{
            "id": "energy",
            "label": "בחרו את רמת האנרגיה",
            "type": "single-choice",
            "required": True,
            "options": [
                {"label": "גבוהה", "value": "high"},
                {"label": "בינונית", "value": "medium"},
                {"label": "נמוכה", "value": "low"},
                {"label": "מרוקנת", "value": "empty"},
            ],
        }],
        "submitLabel": "להמשיך",
    }
    content = "```hermes-ui\n" + json.dumps(artifact, ensure_ascii=False) + "\n```"
    rendered, controls = _render_hermes_ui_payload_for_telegram(content)
    checks = {
        "manual_new_is_available": "new" in commands,
        "form_code_removed": "hermes-ui" not in rendered.lower() and '"type"' not in rendered,
        "all_four_hebrew_options_rendered": controls.labels == ("גבוהה", "בינונית", "נמוכה", "מרוקנת"),
        "continue_label_rendered": controls.submit_label == "להמשיך",
        "controls_created": controls.kind == "single-choice" and len(controls.labels) == 4,
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "controlCount": len(controls.labels),
        "rendered": rendered,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
