# Life-Boat recovery plan

## Goal

Life-Boat stays with a conversation instead of wrapping it up.

Concretely, on a message where Noam raises several things at once, the reply
engages with what he actually raised — more than one thread, in his own detail —
without picking a single angle, without turning it into a bulleted framework or a
quoted maxim, and without landing on a closing summary. When he pushes back, it
adapts rather than restating. Success is Noam reading real replies and wanting to
keep talking. No test may declare this goal met.

## What went wrong

Two separate things, often confused with each other.

**1. The original defect (present from 2026-08-09, never fixed).** The bot grabbed
one thread out of a message, wrapped it in structure, and closed early. Noam said so
directly on 08-09 at 20:29 — "אתה ממשיך להיות נעול על משהו כשזה לא העניין" — and
again at 20:29 — "אתה שוב תוקע את השיחה". The bot's own account at 20:30 was
accurate: it tries to catch the point too fast, summarizes too early, and "סוגר את
השיחה במקום לפתוח אותה". The long multi-bubble replies were a _symptom_ of this
worksheet habit, not the defect itself.

**2. The repair that broke it (2026-08-10 19:40 onward).** The fix read the
complaint as a length-and-format problem and answered it with a gate that scores a
finished reply on character count, sentence count, question count and list markers,
then rewrites failures — keeping only the first sentence and appending a canned
question from a fixed table. A one-sentence stub is maximum closure, so this pushed
harder in the exact direction Noam was complaining about.

**3. The injected guidance fights the skill that already says the right thing.**
The `personal-coaching` skill already carries two session-derived corrections —
`live-coaching-processing-not-packaging.md` and
`telegram-hebrew-coaching-conversation-not-packaging.md` — which record this exact
complaint in Noam's own earlier words ("אל תארוז את זה! די עם האריזות!", "ושוב
סיימת את השיחה. תשאל, תדבר.") and explicitly forbid mini-frameworks, homework,
"הליבה כאן היא…" formulations and multi-bubble polished summaries. The content is
correct and already written.

But `build_signal_guidance` in `gateway/lifeboat_psychology.py:307-349` injects a
wall of directive paragraphs that push the opposite way — "offer one very small,
concrete, optional next step" (homework, which the skill forbids) and "name the loop
tentatively, ask what it is trying to solve, predict, protect, or avoid".

That last one is confirmed verbatim in the transcript. On 08-10 23:28 the bot asked
"מה הלופ הזה מנסה לפתור או למנוע כרגע?" — a direct translation of the injected
line — and Noam rejected it immediately: "למה אתה חושב שהלופ הזה בא לפתוע או למנוע
משהו? למה אתה עוצר במקום הזה?"

So the generic formulaic questions are not the model's invention. They are the
injected guidance overriding a skill that already knew better. The fix for defect 1
is therefore mostly subtraction, not new authoring.

The research behind it (≈49 sources: WHO, APA, MI manuals, Woebot/Wysa trials,
crisis-safety benchmarks) is sound and stays. The failure was in translating
qualities that can only be judged by reading a conversation into rules a test could
check. Forty-five commits in three days, judged entirely against self-written tests,
with no real conversation ever read — the scores improved while the bot got worse.

## Boundaries

Last usable revision: `70a5c5685` (08-10 19:30). Usable, not good — it still has
defect 1.

Wound back: `240235e31`, `7889d1e5a`, `2e38674ee`, `c13f0129a`, `f90eb2ed8`,
`48db24110`, `96f11916a`, `a4ee05b85`, `c85b9cd59`, and the reply-shaping parts of
`678f88414`, `c16230caa`, `e4b2103d6`.

Not a blanket `git revert` — `c16230caa` also carries the busy-session ack, which
stays.

Kept: crisis handling and the Israel crisis resource, Hebrew distress detection,
opt-in check-ins, journal-write gating, the refusal to persist personal detail, the
research register, and **all status notices** (skill reads, vault reads, file edits,
the interrupt bubble) — Noam wants these; they are his only view into what it is
doing.

Untouched: the unrelated dirty edit in `agent/tool_executor.py`.

## Steps

1. Wind the reply-shaping layer back to `70a5c5685`; remove the finalizer call site.
2. Fix defect 1 in the guidance the model receives — hold multiple threads without
   picking a winner, drop the bullet/maxim/closing-line habit, collapse the stacked
   directive paragraphs into one compact contract.
3. Stop the check-in replaying stored draft text (08-09 22:30 re-sent its own reply
   chopped at "1."). Confirm before removing the `lifeboat-delivery-verification-3`
   cron.
4. Replace tests that encode the counter with tests for behaviour: all raised
   threads addressed, no forced-choice closure, correction turns adapt, crisis turns
   check safety plainly, Hebrew/RTL intact, multi-turn trajectory.
5. Focused tests, static checks, broadest relevant local suite — separating real
   failures from missing optional deps and pre-existing baseline failures. Then
   confirm the authenticated Telegram runtime is on this revision (the desktop
   restart does not restart the gateway).
6. Noam reads real replies to the two hardest cases — the party/self-criticism case
   and the colleague/friendship case, both ones where it grabbed one thread and
   dropped the rest.

## Release discipline

Local work and local commits throughout. No CI, no workflow dispatch, no paid
inference. Exactly one push, only after Noam says the conversations are better. If
blocked, the missing evidence is named and the goal stays open.
