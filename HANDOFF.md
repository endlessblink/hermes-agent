# Dropoff — 2026-07-28 22:17 Tuesday

```
You are continuing work in hermes-agent (worktree exercise-library) on branch feat/exercise-library.

## Current task & next step
The fitness bot (Telegram topic 303, "excersize bot") now draws its own illustrated exercise
demos on demand and has a 20-exercise kettlebell library — next: regenerate
Kettlebell_Sumo_High_Pull and One-Arm_Kettlebell_Clean, whose figures slide sideways
(49.5px and 110px drift) because the model drew the athlete in a different spot per panel.

## Files touched / in flight
Working tree clean; everything committed and pushed. Recently added:
- tools/exercise_demo_generator.py — the exercise_generate_demo tool (Codex -> gpt-image-2)
- tools/exercise_strip.py — deterministic strip -> centred looping GIF (Pillow only)
- tools/exercise_library_tool.py — search, workout builder, demo delivery, provenance
- assets/exercise_demos/cast/*.png — the three locked reference figures
- tests/tools/test_exercise_demo_{generator,provenance,delivery_chain}.py
Outside the repo: ~/.codex/skills/exercise-demo-gifs/ (symlinked into ~/.claude/skills/),
~/.claude/workflows/exercise-demo-batch.js, and generated assets in ~/exercise-demos/.

## Key decisions & gotchas
- One strip, not N images. Separately generated poses drift in camera distance and scale;
  panels inside a single image match by construction, and cost one generation.
- Don't interpolate. ffmpeg minterpolate cannot bridge large pose changes — it silently
  duplicates frames. Draw more panels instead.
- VERIFY THE CONTAINER RUNS YOUR CODE before trusting any check. A quiet
  `docker build -q ... >/dev/null` can leave the old image in place; the container looks
  healthy and your check measures stale behaviour. Grep the running container for a line
  you just wrote. The tell that caught it: a semantically impossible result.
- exercise_demo ALWAYS succeeds — it falls back to crossfading the dataset's two stock
  photos. Never conclude demos are live from a green check. Run scripts/check_bot_demos.py
  inside the container: prints ILLUSTRATION vs photo, exits non-zero on any photo.
- A demo's path NEVER changes. Topic 303 is one unbroken chat, so every path the bot ever
  emitted stays in history and gets reused. Provenance lives in a .illustrated marker file
  beside the GIF, never in the file's location. Moving files broke delivery once.
- Codex invocation: prompt goes on STDIN, never as a positional — -i/--image takes a list
  and swallows a trailing positional as another image path ("No prompt provided", exit 1).
- config.yaml: never write a colon-followed-by-space inside a persona prompt; YAML reads it
  as a mapping and the whole file stops parsing. The gateway then serves NO personas while
  looking healthy. Validate the candidate in memory, then write.
- uid 10000 owns everything under /opt/hermes-data. A docker exec without -u 10000 creates
  root-owned files and locks the bot out.
- No numpy in the container — Pillow only.
- .dockerignore excludes assets/; there is an explicit exception for assets/exercise_demos/.
- Image model rule: only GPT Image 2 or Seedream 5, ever. Codex's built-in image_generation
  (stable, on by default in 0.133.0) uses gpt-image-2 on the ChatGPT login — no API key.
  Flux was raised and explicitly declined.
- The Codex login EXPIRES; it did mid-session. Re-auth is interactive:
  CODEX_HOME=/opt/codex-auth codex login --device-auth, then chown -R 10000:10000
  /opt/codex-auth. The device-code page rejects automated keystrokes — a human must type it.

## Env / run state
Branch: feat/exercise-library (pushed, in sync with origin)
Last commit: e72551b68 fix(exercise): feed codex the prompt on stdin, and name an expired login
Base: branched off the DEPLOYED commit b2143eae5, not origin/main — main lacks the health
tooling (tools/health_tool.py is untracked in the live tree). Check the diff before merging.
VPS 84.46.253.137: hermes-gateway up, image hermes-agent:council-20260719
(rollback tags: rollback-20260725, rollback-20260728).
Library: 55 demos cached, 21 illustrated (20 kettlebell + bench press).
Deploy = copy files to /opt/hermes-runtime/source-council-20260719/, rebuild, then
docker compose up -d --force-recreate gateway (compose service is "gateway").
Open items: the two sliding demos; switching the library to MP4 (measured 789 KB GIF ->
91 KB MP4, ~9x smaller — WebP is smaller still but gets delivered as a static photo);
batching several generations per request.

Start by: regenerate Kettlebell_Sumo_High_Pull with a prompt that pins the athlete's feet to
the same spot in all three panels, using the pattern in /tmp/gen_batch.py in the container:
docker exec -u 10000 -w /opt/hermes hermes-gateway python /tmp/gen_batch.py <exercise_id>
```
