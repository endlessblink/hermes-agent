# Which tree the gateway actually runs

**The running `hermes-gateway.service` does NOT import this repository.**

It runs `~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run`, and that
venv's editable install resolves `gateway.*`, `agent.*` and `hermes_cli.*` to
`~/.hermes/hermes-agent/`. Editing this repo changes nothing on the running bot until the
files are copied across.

This is not theoretical. On 2026-08-12 a Life-Boat fix (`f6f9046b7`) was committed here and
believed live. It was not. The old code kept running and on 2026-08-13 at 01:22–01:23 it
truncated the assistant's replies to their first sentence during a support conversation —
a 227-character reply was delivered as `צודק.`

## Verify before believing a fix is live

```bash
~/.hermes/hermes-agent/venv/bin/python - <<'PY'
import gateway.lifeboat_followups as m, gateway.run as r
print(m.__file__)
print(r.__file__)
PY
```

If those paths are under `~/.hermes/hermes-agent`, that is the tree you must edit or deploy
to. Confirm the _process_ picked it up too — a file edit does nothing until restart:

```bash
systemctl --user restart hermes-gateway.service
systemctl --user status hermes-gateway.service --no-pager | head -6
```

Restarting the desktop app does **not** restart the gateway.

## Deploying a Life-Boat change

```bash
LIVE=~/.hermes/hermes-agent
REPO=<this repo>
mkdir -p "$LIVE/.lifeboat-backup-$(date +%Y%m%d)"
cp "$LIVE"/gateway/lifeboat_*.py "$LIVE"/gateway/run.py "$LIVE"/agent/turn_finalizer.py \
   "$LIVE/.lifeboat-backup-$(date +%Y%m%d)/"
cp "$REPO"/gateway/lifeboat_followups.py "$REPO"/gateway/lifeboat_psychology.py "$LIVE/gateway/"
cp "$REPO"/agent/turn_finalizer.py "$LIVE/agent/"
cp "$REPO"/scripts/verify_lifeboat_psychology.py "$LIVE/scripts/"
systemctl --user restart hermes-gateway.service
```

`gateway/run.py` is edited by several workstreams at once — port Life-Boat changes to it by
hand rather than copying the whole file.

## Checking a Life-Boat change before the user sees it

Two scripts, both deployed alongside the code:

```bash
# free, no model call - prints the instruction bundle and lints it
~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_prompt_probe.py

# proves the sandbox is isolated; add --live to actually generate replies
~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_sandbox_replay.py
```

The probe is the one that matters for instruction edits. The 2026-08-13 regression was a
contradiction between two layers — the Telegram topic prompt prescribed a one-line verdict
while the conversation contract forbade closing — and the probe's lint reports exactly that
class of collision, plus the prohibition/commission balance and any rule that exists in the
DM prompt but not the topic prompt (which is how that bug got in). Run it after any prompt
or contract edit, before restarting the gateway.

The replay repoints `HERMES_HOME` at a throwaway copy of the profile. Plugins load from
`$HERMES_HOME/plugins`, so the Obsidian archive hook is absent; `skip_memory=True` and no
toolsets mean nothing reads or writes the real store; no adapter is built, so no Telegram
request is possible. It asserts all of that at the end of the run. `--live` spends real
provider quota, so it is opt-in.

## Two traps that bite specifically here

1. **`is_lifeboat_source` must keep the chat/thread branch.** The gateway logs
   `Profile 'life-advisor' shares the single telegram adapter owned by profile 'default'`,
   so `source.profile` is frequently _not_ `life-advisor` on a real Life-Boat turn. A
   profile-name-only check silently disables every Life-Boat behaviour — guidance, toolset
   filtering, follow-up suppression, interrupt demotion — while looking correct in tests.
   Both halves of the chat/thread pair are required: a Telegram thread id is only unique
   inside one chat.

2. **Never re-add a post-generation response filter.** Reply quality is shaped _before_
   generation, in the coaching guidance. A filter that scores a finished reply and rewrites
   it deletes real content and speaks canned sentences in the assistant's voice. Judge
   quality by reading the real Telegram conversation, never by tests.

## Related runtime facts

- The Obsidian turn log is written by `post_llm_call` in `agent/turn_finalizer.py`, i.e.
  _before_ the gateway delivery path. That is why it captured the full pre-filter replies.
- The archive plugin lives outside both repos, at
  `~/.hermes/plugins/obsidian-source-of-truth/`. It picks its folder from the process home,
  so Life-Boat turns are routed to their own folder by the `chat_id`/`thread_id` passed
  through the hook.
- Life-Boat memory writes to `~/.hermes/profiles/life-advisor/memory.db`, not the shared
  root store.
