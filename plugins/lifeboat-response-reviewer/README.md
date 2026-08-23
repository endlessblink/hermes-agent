# lifeboat-response-reviewer

Conditional final-response review for the `life-advisor` profile. It keeps the normal path local and fast: `pre_llm_call` stores only an expiring session boolean when the incoming turn looks like emotional/meta-coaching, then `transform_llm_output` classifies the final text locally. Only flagged text may call the host-owned `ctx.llm().complete` facade.

## Architecture

- Scope is profile-local (`life-advisor`) and turn-local; raw user text is not stored, logged, or written to disk.
- The deterministic classifier detects polished closure, amplified negative-affect mirroring, unsolicited reassurance, praise/growth assignment, forced binary questions, and operational receipts.
- Normal work, mapping, coding, and calendar turns bypass the reviewer.
- The reviewer call uses Hermes’ public `ctx.llm()` facade, which routes through configured profile auth without exposing credentials. Its thread-local depth guard prevents recursive transformation if a future host path emits hooks around plugin calls.
- Errors, malformed reviewer output, and timeouts fail open by returning no replacement. `transform_llm_output` itself remains bounded by the configured host timeout (clamped to 2 seconds).

## Configuration

```yaml
plugins:
  entries:
    lifeboat-response-reviewer:
      reviewer:
        enabled: true
        timeout: 0.8
        dry_run: false
        reviewer_mode: rewrite # rewrite | review | disabled
```

`dry_run: true` classifies but never calls the model. `reviewer_mode: review` is a review-capable mode reserved for future audit surfaces and currently behaves as rewrite; `disabled` skips all review work.

## Installation and rollback

Copy this directory to the profile’s plugin directory, enable it with Hermes’ plugin command, and add the configuration above. Do not copy secrets or edit auth files. Roll back by disabling the plugin and removing only this plugin directory and its config entry; do not restart a live gateway as part of installation without an explicit operational change window.

## Privacy, latency, and cost

The classifier is in-process and performs no I/O. The second pass receives only the final assistant draft plus fixed editing instructions, and runs only for scoped flagged turns. No raw vulnerable text is persisted by this plugin. Model cost is zero on ordinary turns and bounded to one completion on a flagged turn; failures leave the original response unchanged.

## Verification

```bash
python3 -m pytest -q
python3 benchmark.py
python3 -m py_compile reviewer.py __init__.py
```

For an isolated Hermes probe, use a temporary `HERMES_HOME`, enable only this plugin, invoke the real `PluginManager`, and pass `session_id`, `user_message`, `response_text`, `model`, and `platform` kwargs. Verify that a work/mapping message produces no model call and that a flagged Hebrew emotional draft produces at most one. Do not install into the live profile or restart the gateway for this project’s verification.

## Current hook boundary

The current Hermes source exposes enough for a safe conditional call: `pre_llm_call` includes `user_message`, and `ctx.llm().complete` accepts a bounded timeout and uses host-owned routing. `transform_llm_output` itself has no `user_message`, so the ephemeral scope marker is required. If a deployment lacks `ctx.llm`, the safe behavior is classifier-only fail-open; the smallest host extension would be to expose the same `PluginLlm` facade on `PluginContext` rather than allowing plugins to call provider clients directly.
