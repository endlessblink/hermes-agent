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

- [x] A menu of readings of his own experience is rejected. Two alternatives
      can be a real question; three or more is a form.
- [x] The instructions themselves rewritten. This was the actual cause and it
      went unexamined for two days: the file told the bot to announce that it
      was about to interview him, and to acknowledge its mistakes — which is
      precisely where "I'll interview you one question at a time" and every
      "you're right, I spoke to you like a case manager" came from. It was 42
      prohibitions with almost no picture of the thing itself, so each
      correction only moved the failure somewhere new. It now opens with how to
      lead a conversation, and the two harmful lines are gone.

- [x] The bot's transcript is written where its readers look. Every check that
      claimed to verify behaviour from the real conversation was reading a
      folder the Telegram bot never wrote to — the hook that writes transcripts
      was never given the chat or topic, so the routing that already existed
      could not match.
- [x] Life-Boat's scattered code brought under one roof. The
      emotional-candidate-capture plugin's only source was untracked inside a
      stale working copy while the plugin runs live; deleting that copy would
      have destroyed it.

## Known hole

- [ ] When a rejected reply is rewritten and the rewrite also fails, the model's
      words are delivered anyway. The gate reports it and ships it. That is a
      deliberate choice not to invent prose, but it means blocking is not
      solving, and a bad shape can still arrive.

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
