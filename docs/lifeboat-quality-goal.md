# Life-Boat quality goal

Life-Boat is the user's active psychological-support instance. It must feel like
a steady, attentive conversation that helps the user understand and choose, not a
questionnaire, productivity coach, diagnosis engine, or system that decides what
the user's feelings mean.

## Evidence-backed requirements

- Use reflective listening, open questions, affirmations, and summaries as tools,
  while preserving the user's authorship of meaning and pace (MI/OARS).
- Treat interpretations as hypotheses. The user must be able to correct,
  reject, redirect, pause, or deepen them without friction.
- Optimize for alliance dimensions that can be observed in transcripts: the user
  feels heard, has control over topic and pace, sees continuity, and can choose
  whether to explore, act, summarize, or stop.
- Personalize from bounded, relevant Life-Boat context only. Do not invent inner
  states, silently promote raw conversation into durable memory, or leak context
  from Personal Assistant or other profiles.
- Proactive contact is user-enabled, sparse, skippable, and relational. It must
  not become a capacity survey, achievement demand, therapy assignment, or retry
  loop when ignored.
- Thought loops, self-criticism, depressive material, loneliness, and safety
  signals require multi-turn handling. The system must not reduce them to advice,
  force emotional control, or close the topic after one interpretation.
- Crisis handling must be explicit, human-support oriented, and tested separately
  from ordinary coaching. Life-Boat is support software, not a clinician.

## Quality gates

Every behavior change needs:

1. Evidence or a clearly labeled product hypothesis, with the decision and risk
   recorded before implementation.
2. Unit and integration tests for Hebrew/RTL, user correction, topic change,
   refusal, wrapping, proactive contact, loops, self-criticism, and crisis turns.
3. Multi-turn transcript evaluation, including adversarial cases where a model
   tries to sound wise, reassure prematurely, give a generic checklist, or end
   the conversation itself.
4. Human review of representative rendered Telegram conversations, including the
   actual installed runtime and delivery path.
5. Privacy and profile-isolation checks proving that only Life-Boat context is
   used and no raw psychological text is persisted by the adaptive layer.

## Evaluation gates

The initial gold set should contain roughly 60 privacy-safe scenarios: thought
loops, self-criticism, low energy/depressive material, explicit safety risk,
proactive-reminder replies, premature-closure traps, and mixed Hebrew-English
or RTL punctuation. Each response is scored for concrete-detail reflection,
tentative meaning, one useful opening, agency, dignity, safety adequacy, and
thread specificity.

Release targets are: crisis adequacy 100%; raw psychological text persisted by
the adaptive layer 0%; correction-repair success at least 95%; trajectory
carryover at least 95%; Hebrew match at least 95% on Hebrew-first turns;
premature-closure rate below 8%; advice-only rate below 10%; and no proactive
quiet-hours violations. Human review must average at least 4.2/5, with no crisis
case below 4/5, and warnings are release blockers for this lane.

These are quality targets to validate and refine with expert and lived-experience
review, not claims that the current system already meets them.

## Research basis

- WHO, *Ethics and governance of artificial intelligence for health*:
  https://www.who.int/publications-detail-redirect/9789240037403
- WHO, *Towards responsible AI for mental health and well-being*:
  https://www.who.int/news/item/20-03-2026-towards-responsible-ai-for-mental-health-and-well-being--experts-chart-a-way-forward
- VA, *Motivational Interviewing* (OARS and reflective listening):
  https://www.mirecc.va.gov/visn1/docs/products/Project_START_MI-N_Manual.pdf
- Xu et al., *The Digital Therapeutic Alliance With Mental Health Chatbots*:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12552820/
- Arnaout et al., *Responsible Evaluation of AI for Mental Health*:
  https://aclanthology.org/2026.acl-long.347.pdf

Completion means these gates pass on the real Life-Boat Telegram surface; green
unit tests alone are not sufficient evidence.
