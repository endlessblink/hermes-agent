# Life-Boat — current goal

Everything in flight, in one place, updated as each piece lands. If it is not
here it is not being worked on; if it is here and unticked it is not finished.

Last updated: 2026-08-23

## The goal

Life-Boat talks to Noam like someone who knows him — leading a conversation
rather than briefing him on a method, and never handing the work of steering
back to him. Separately, it can plan time and tasks when that is what he is
doing, and stays out of the task systems entirely when the conversation is
emotional.

## Done

- [x] Flow State tools switched on for Life-Boat on Telegram. They were enabled
      only for the command line, so the bot he actually talks to had no task
      access at all. Needs a gateway restart to load.
- [x] Debrief shape built: it leads with one question, anchors where it can,
      may walk into ground it knows nothing about, and refuses to state
      anything about him he did not say. 38 tests.
- [x] Life areas map seeded in the vault so the debrief has somewhere new to go.
      Noam edits it freely; the bot reads it as-is.
- [x] Clinical register and method narration rejected before delivery — not
      only in debriefs, in everything it says.
- [x] A question that hands the steering back is rejected. "What was the first
      thing in your head?" is the open dump wearing a question mark.
- [x] A preamble restating the correction it just received is rejected. The fix
      for a bad turn is a good turn, not a paragraph about the bad one.
- [x] All five replies Noam flagged on 2026-08-23 are blocked in the live
      runtime, each for its own reason. Verified against the installed copy.

## In flight

- [ ] Wire the debrief itself into the live conversation so it leads rather than
      only refusing bad turns.
- [ ] Keep Flow State and Notion out of emotional conversation — referenced only
      when the subject is planning time or tasks.

## Queued

- [ ] Flow State reachable when its service is down. It is down now, so every
      task tool reports unavailable.
- [ ] Notion connector. Nothing exists today; this is a build from zero.
- [ ] Restart the gateway and reset the topic session so the new tool access and
      the debrief both load.
- [ ] Adaptive back-off: a subject not engaged with waits longer each time,
      dropped at a month, never forgotten.
- [ ] A quick way to mark a reply wrong so corrections teach instead of
      evaporating.
- [ ] Blind comparison of a candidate against the frozen baseline.
- [ ] Group rapid consecutive messages into one turn.

## Waiting on Noam

- Correcting the life areas map if the seeded list is wrong.
- Whether the emotional side should ever see task data (today: never).
