# Life-Boat: how it actually fails

Compiled 2026-08-24 from the Telegram turn logs and the delivery logs, not from
impressions. Every entry below is something that reached Noam, with his own
words for it where he gave them.

The reason this document exists: for two nights, fixes were aimed at whatever
failure was on screen, and each one surfaced a different failure somewhere else.
That is what happens without a catalogue — you cannot tell a regression from a
variation, and you cannot tell whether a change helped or just moved the
problem. Nothing here should be "fixed" one at a time again. This is the target
list to test any change against, all of it, before deploying.

Two failures are marked **caused by a fix**. Those are the expensive ones.

---

## 1. Blank question with an invented subject

**He saw** (2026-08-24 01:36, a fresh thread): he asked for a debrief on the
recent period and got *"מה קרה בתקופה האחרונה בעבודה?"* — nothing about work had
been mentioned, that night or before.

**Cause, verified**: the material block was empty, so the model had nothing to
be specific about and filled the gap. The subject was invented to make the
question sound attentive.

**Caused by a fix.** Material had been filtered to exclude the current
conversation — correct inside a live thread, wrong in a new one, where those
turns are the only material there is. The filter used date, not recency.

**Guarded now**: material is cut by recency (45 minutes), so a reopened thread
still arrives with his week. `scripts/lifeboat_preflight.py` asserts material is
non-empty in a fresh conversation.

---

## 2. Confident false claims about his life

**He saw** (sandbox replay, 2026-08-23): the bot opened a debrief with *"the
last two days were much better than the ones before"*. That was his own line
praising two bot replies during development, collected as "what he said
recently" and handed over as material about his week.

**Cause, verified**: the shared turn log is his general-purpose thread — dev
work, bug reports, engine bookkeeping recorded under his name — and it was being
mined for personal material.

**Guarded now**: the shared log is no longer read; talk addressed to the
assistant and engine-written blocks are filtered. Note the lesson: two keyword
filters were added and each only revealed a deeper layer. No vocabulary
separates a man's life from his work when both were typed into the same box.

---

## 3. Presupposing something that never happened

**He saw** (01:15): *"נראה לי שלא שמרת את המספרים כי כבר אז לא באמת רצית שזה
ימשיך"*. His reply: *"מה ימשיך? לא היה כלום"*. Nothing had started; there was
nothing to continue.

**Cause, suspected**: not an invented entity or date, so the groundedness checks
do not see it — the presupposition is carried by a verb. `lifeboat_debrief` has a
presupposition check, but it runs only on debrief turns.

**Not guarded.**

---

## 4. Therapist register

**He saw** (00:24): *"מה קרה אצלך בין קבלת המספרים להחלטה הזאת?"* — and again
*"והביאוס... היה עדיין איתך"*, *"מה היה קשה באפשרות לסמן את כולן בלא"*.

**His words**: *"the last sentence is like a therapist again"*, *"written like a
person talking from afar"*, *"I don't want it to talk like a shrink"*.

**Cause, verified**: the only document describing the bot's manner opens by
calling itself *Personal Coaching*, names its work *therapy-adjacent*, and tells
it to be *analytical and strategic*. Nobody had ever told it what else to be.

**Guarded partly**: an identity is now chosen, switchable, and injected into all
three writers. The coaching document underneath is unchanged and is still the
deepest source.

**Do not** guard this with a banned-phrase list. It was tried the same night,
banned a question type he had explicitly asked for, and had to be removed.

---

## 5. The formula

**He saw**, on nearly every turn for weeks: *"נשמע לי ש… אני טועה?"* — a hedged
read, then a question checking the read. Same two-part shape every time.

**Cause, verified**: the per-turn guidance ordered it. *"Choose one concrete
detail; reflect it tentatively"* and *"End with one open question or unfinished
invitation"*, plus *"keep any interpretation tentative and open to correction"*.

**Guarded now**: those orders are deleted. This also explains two earlier
mysteries — switching the editor off changed nothing (it was restating what the
guidance demanded), and adding an identity changed little (a description of who
is speaking loses to an operational order about sentence construction).

---

## 6. Muddled, roundabout replies

**He saw** (01:22): *"אה, אז הבאסה הייתה גדולה יותר מה'לא' שלה: עברת ערב שלם עם
מלא מפגשים, ואף אחת לא באמת הדליקה אותך. נכנסת לערב עם ציפייה די חזקה שלפחות
אחת תעשה לך וואו?"* — four moves in one message: a reaction, a restatement of
what he had just said, an interpretation, and a speculative question.

**His words**: *"muddled and could have been more concise with less walking
around"*.

**Cause, suspected**: the guidance allowed three sentences and 480 characters
and ordered it to hold more than one thread at once. Both are now cut (two
sentences, 260 characters, "say one thing"), but the change has not been judged
in a real conversation.

---

## 7. Handing the work back

**He saw** (00:00, 00:03): *"מה היה האירוע המוקדם ביותר בתקופה הזאת?"*,
*"נתחיל מהאירוע האחרון... מה קרה בו, לפי הסדר?"* — asked to choose the starting
point and narrate it himself.

**His words**, from the previous session: *"the bot is throwing responsibility on
me instead of trying to understand"*.

**Cause, partly verified**: when it has no material it must ask; when it has
material it was ordered to reflect rather than to arrive with a read. The
editing agent was built for exactly this and then found to stall (see 13).

---

## 8. Misreading an elliptical sentence

**He saw** (01:23): he wrote *"מה שאמרתי שביאס"* — meaning *what I already told
you is the thing that upset me*. The bot took it as him regretting having said
something, and asked *"התחרטת על זה ישר כשאמרת?"*.

**His words**: *"a super idiotic fail"*, *"I said that I already answered it and
it took that as an answer by itself"*.

**Cause, suspected**: short Hebrew ellipsis, answered without checking. Reasoning
effort was `low` globally until this night.

**Guarded weakly**: one line now asks it to understand before answering and to
say plainly when unsure. Untested.

---

## 9. Circling back over settled ground

**He saw**: the bot returning to points he had already answered.

**Cause, verified**: the material block handed it every line from the live
conversation under the heading *"HISTORICAL USER TURNS ... fragments from prior
threads; do not treat them as current"*. The conversation in progress was
described to it as old, unrelated material.

**Caused by a fix** — the provenance framing added to prevent invention.

**Guarded now**: the live exchange is excluded from material; preflight asserts
it.

---

## 10. Narrating itself, and apologising

**He saw** (01:23): *"נכון, כבר אמרת, ואני סתם ביקשתי ממך לנסח את זה שוב."* Also
*"סליחה, הבנתי אותך לא נכון. תמשיך/י במילים שלך — אני איתך"* — apology, handback,
and a gendered slash form, produced by the retry path on a rejected draft.

**Guarded now**: self-narration and apology are in the remaining harm rules, and
the retry path is given the identity. The gendered slash is not guarded.

---

## 11. Going slangy and empty

**He saw** (01:24): *"אה, קלטתי."*

**His words**: *"now it talks dumb"*.

**Cause, suspected**: the friend identity asks it to react before asking; with a
two-sentence budget the reaction can consume the whole reply. The line between
*close* and *dumb* is real and currently unmanaged.

---

## 12. The same sentence, again and again

**He saw** (BUG-6, historical): one hardcoded Hebrew sentence delivered eight
times in an afternoon, because a gate wrote replacement prose of its own.

**Guarded**: no module on the delivery path contains reply text. This is the
oldest lesson here and the reason worked examples are not supplied to the model —
handed a sentence, it delivers that sentence.

---

## 13. The editor stalls

**He saw** (six-turn replay): with the editing agent on, every delivered reply
was a read offered for confirmation. He said *"כן"*; it re-asked. He said
*"אוקיי"*; it reopened the framing a third time. He said *"לא יודע מאיפה
להתחיל"*; the model's own draft picked a starting point and the editor replaced
it with asking him to choose.

**Cause, verified**: the editor's brief prescribes one shape and applies it
unconditionally, including where the read is already agreed.

**Guarded by**: the editor is currently switched off.

---

## 14. Truncation and canned closure

**He saw** (2026-08-13 01:23, historical): a 227-character reply delivered to him
as *"צודק."* while he was at his lowest, by a post-generation gate that rewrote
failures down to their first sentence.

**Guarded**: the gate suppresses or passes; it never truncates.

---

## What has never been true of any version

No version of this bot has been evaluated over a whole conversation by someone
who was not exhausted and mid-crisis. Every state, including ones called
improvements the same night, was judged on two or three replies. There is no
known-good baseline to return to, and pretending otherwise has cost real nights.
