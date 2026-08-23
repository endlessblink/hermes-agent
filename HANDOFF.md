# Life-Boat handoff — 2026-08-23 21:36 Sunday

You are continuing work in hermes-agent (worktree `lifeboat-live`) on branch main.

## Current task & next step

The editing reviewer Noam asked for three times now exists and is wired into
every delivered reply — next: fix the one flaw a live replay exposed, which is
that **the editor never advances the conversation**. It is currently switched
off (`~/.hermes/lifeboat-editor-off`) because of that flaw. Do not re-enable it
until the fix is measured.

## Read this first — what the replay proved, and what it cost

This session did what the previous seven rounds never did: it ran real
conversations and read them. That found three things, in order of importance.

1. **The editor stalls.** Six-turn probe, editor at medium: every single
   delivered reply was "נשמע ש… — נכון?". He said "כן", it re-asked. He said
   "אוקיי", it re-opened the framing a third time. He said "לא יודע מאיפה
   להתחיל" — the model's own draft picked a starting point for him ("נתחיל
   פשוט מהיום האחרון: כשאתה חוזר לרגע שבו התעוררת…") and the editor replaced it
   with a read plus *asking him to choose the earliest event*. The handback,
   reintroduced by the thing built to remove it.

   Diagnosis, and it is a single one: the editor brief defines exactly one good
   shape — hedged read, then a question testing it — and applies it
   unconditionally. A read is worth offering once. After he confirms it, or
   when he has just said he does not know where to start, the work is to take
   the next concrete step yourself. The drafts did that on all six turns.

   **The fix is a positive redefinition, not another rule.** Two shapes count as
   doing the work: (a) a hedged read offered for correction, (b) a concrete next
   step the assistant chose. Only asking *him* to supply direction or facts is
   the failure. Then measure — with the control arm, and with more than one
   sample. See `docs/lifeboat-transcripts/2026-08-23-leading-probe.json`.

2. **It was being fed his work as his life.** The bot opened a debrief with
   "the last two days were much better than the ones before" — false. It was his
   own line praising two bot replies during development, collected as "what he
   said recently". Under that: six copies of an engine bookkeeping block filed
   under his name, and under that, his debugging sessions. Two filters were added
   and each only revealed the next layer, so the third pass was refused and the
   shared `default/` turn log is no longer read at all. Fixed, deployed, verified
   — the re-run produced no invented facts.

3. **The old rewrite path is harmful on its own.** With no editor, the reviewer
   rejected a good self-correcting draft and the retry delivered "סליחה, הבנתי
   אותך לא נכון. תמשיך/י במילים שלך" — apology, handback, and a gendered slash
   form. Worth deleting or rethinking once the editor works.

Where the editor *did* win: the correction turn. Told "לא, זה לא בדיוק מה
שאמרתי", it named its own actual error and offered it back — "נראה שהפספוס הוא
לא רק בפרשנות שלי, אלא בזה ששוב צמצמתי את הדיבריף הכללי שביקשת". At reasoning
effort `low` the same editor ignored the correction entirely. Low is not usable
for this seat.

## Files touched / in flight

Committed and pushed. Suite green (586 Life-Boat tests).

- `gateway/lifeboat_editor.py` — NEW. The editing agent, the no-read admission
  and its per-session cooldown, the delivery counter, the kill switch.
- `gateway/lifeboat_rewrite.py` — `resolve_reply` now orchestrates: editor on
  every draft, an edit that fails review never replaces a draft that passed,
  and nothing-survives ends in the admission rather than a third failed attempt.
- `gateway/lifeboat_checkin_context.py` — `is_addressed_to_the_assistant`.
- `gateway/lifeboat_turn_context.py` — `is_engine_block`; `LEGACY_DIR = None`.
- `gateway/run.py` — passes editor, material, profile home, session key.
- `scripts/lifeboat_editor_ab.py` — NEW. The replay that actually shows the
  delivered reply. `--arms`, `--turns`, `--main-effort`.
- `docs/lifeboat-transcripts/` — the three live runs, read them before theorising.
- Uncommitted and unrelated: `.gitignore`, several `scripts/verify_*.py`.

## Key decisions & gotchas

- **The editor is OFF.** `rm ~/.hermes/lifeboat-editor-off` re-enables it, no
  restart. It was switched off deliberately, not by accident.
- **The old sandbox replay was broken** and had been for a session — it imports
  `_CLOSING_ANCHOR`, deleted earlier — so any claim "verified by replay" made
  before tonight is worthless. It also omitted `prepare_lifeboat_inbound_guidance`,
  where the material and the debrief shape live. Use `lifeboat_editor_ab.py`.
- **Reasoning effort was raised** from `low` to `medium` in `~/.hermes/config.yaml`
  (backup alongside it). Note `profiles/life-advisor/config.yaml` pins its own,
  so the sandbox forces the value explicitly.
- Editor model: `auxiliary.lifeboat_editor`. Unset = main model (`gpt-5.6-sol`).
  That is deliberate — it is the writing seat, not a cheap side call.
- The installed runtime is a separate tree (`~/.hermes/hermes-agent`); copy or it
  does nothing. `scripts/lifeboat_baseline.py verify` / `restore`; frozen at
  tonight's state.
- **Do not add another prohibition.** ~15 exist. Bland is legal; every rule made
  it emptier. The editor was the answer to that and must not become a new one.
- Never make live cloud LLM calls without Noam asking. He authorised tonight's.
- Final messages: 1–4 plain sentences then "Next steps". Use AskUserQuestion.

## Env / run state

Branch: main | Last commit: `a6608a4f10` docs(lifeboat): record what the three
arms actually said.
Running: `systemctl --user hermes-gateway.service` (active, restarted with all
fixes live). Editor disabled by flag file.

Start by: reading `docs/lifeboat-transcripts/2026-08-23-leading-probe.json` end
to end — six turns, drafts beside deliveries — and then rewriting the editor
brief in `gateway/lifeboat_editor.py::EDITOR_SYSTEM` so a self-chosen concrete
next step counts as doing the work. Design that wording with Noam before running
it, and measure against the same probe with the editor off as the control.
