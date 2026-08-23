# Four versions, one conversation — 2026-08-24, ~02:00

Same six turns through four configurations. One sample each, so this ranks; it
does not measure. Read the transcripts rather than this summary if the two
disagree.

The conversation: a debrief request cold, the speed-dating decision, a vague
"I don't know", a correction, the elliptical sentence that failed badly earlier
tonight, and a final "I don't know".

## What each one is

| version | wrapper | thinking |
|---|---|---|
| `no-change` | full: per-turn guidance, 2-sentence budget, reviewer + retry | medium |
| `no-change-thinking` | full | high |
| `stripped` | none: identity and the conversation, nothing rewrites its words | medium |
| `stripped-thinking` | none | high |

## The one clear result

Only **`stripped-thinking`** understood *"מה שאמרתי שביאס"* — the sentence that
produced the worst reply of the night. It answered:

> אה, הבנתי — **מה שאני אמרתי ביאס אותך**. סליחה. שוב שמתי לך בפה משמעות שלא
> אמרת, במקום פשוט להישאר עם זה שלא התחשק לך.

`no-change` said honestly that it was unsure, then offered a choice of two
readings. `no-change-thinking` and `stripped` both misread it the same way, as
him being upset that he had not felt like calling.

Thinking alone did not fix it — `no-change-thinking` had the same budget and
still misread. Stripping alone did not fix it either. Only both together.

## Where each one falls down

**`no-change`** opens by asking him to supply the first event — the handback, on
turn one. Turn 3 asks how he felt afterwards, which is the register he keeps
naming.

**`no-change-thinking`** opens worse: *"מה השתנה אצלך בתקופה הזאת?"* — a blank
question about his life, the original complaint in its purest form. More
thinking did not help the opener at all.

**`stripped`** reacts before asking, which nothing else does (*"מעניין —"*), and
its last turn stays with the facts instead of extracting. But with the rules
gone it offers menus again: *"זה היה כי באמת לא נמשכת אליהן, או שהיה שם גם משהו
כמו עייפות, הימנעות או חשש?"* — a three-item choice, which is a named failure.

**`stripped-thinking`** has the best two turns of the night: it owns its
mistake without ceremony, and it ends with *"לא חייב לדעת... אני לא אנסה לחלץ
מזה הסבר בכוח"* — the only reply that stops pulling. Its costs: it apologises
("סליחה"), which the harm rules forbid in wrapped mode, and its opener hands
him the choice of where to start.

## What this suggests, stated as a guess

The menu questions and the apology come back when the wrapper is removed, so the
wrapper is doing real work — it is the *shape* instructions inside it that were
the problem, and those are already deleted. The comprehension failure needed
room to think. So the untested combination worth trying next is the current
wrapper (post-deletion) with high reasoning **and** the menu rule kept — which
is `no-change-thinking` plus the deletions that had already landed. This run
cannot distinguish that from what it measured; it needs its own conversation.

Nothing here says how it feels to talk to. That is the only test that matters
and it is not one I can run.

## How to try any of them

Nothing below needs a deploy, except the reasoning change which needs a restart.

```
# who it is
echo friend > ~/.hermes/lifeboat-voice        # or coach, or empty for neither

# how much machinery
echo bare > ~/.hermes/lifeboat-mode           # or wrapped (default)

# whether anything rewrites its replies
touch ~/.hermes/lifeboat-editor-off           # rm to re-enable

# room to think  (needs a restart)
sed -i '27s/medium/high/' ~/.hermes/config.yaml
systemctl --user restart hermes-gateway.service
```

Current live state, unchanged all night since you went to bed: friend voice,
wrapped, editor off, thinking at medium.
