# Improvement Supervisor

Hermes already reviews completed conversations to improve memory and skills.
This optional plugin covers the separate product/code layer: it watches
observer events for concrete failures and explicit corrections, classifies only
qualifying turns, and stores deduplicated proposals under the active profile.
For narrowly proven, reversible runtime defects it can also repair the input
before execution. Duplicate clarification choices are normalized before they
reach the UI, and a privacy-safe incident records only counts—not user text.

Enable it with:

```bash
hermes plugins enable improvement-supervisor
```

Use `/improvements` to list proposals, `/improvements show <id>` to inspect
evidence, and `/improvements accept <id>` or `dismiss <id>` to resolve one.
Acceptance remains proposal-only for code work. The plugin has no model tool
and no code, Git, dependency, restart, deployment, credential, or permission
path. `/improvements status` reports how many incidents were repaired live.
