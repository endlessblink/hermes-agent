# Hermes — OpenHuman borrow lanes (memory + privacy)

**Created:** 2026-07-17
**Source:** patterns observed in `tinyhumansai/openhuman` (GPL-3.0 host app).
**Hard licensing rule for this whole doc:** the OpenHuman *desktop app* and its
orchestrator crate `tinyagents` are **GPL-3.0** — do NOT copy their source into
Hermes. The two pieces we actually want are permissively licensed and reusable
directly:

| Component | Repo | License | Reusable in Hermes? |
| --- | --- | --- | --- |
| `tinycortex` (Memory Tree engine) | `tinyhumansai/tinycortex` | **MIT** | ✅ vendor/depend directly |
| `agentmemory` (shared backend) | `rohitg00/agentmemory` | **Apache-2.0** | ✅ vendor/depend directly |
| OpenHuman app + `tinyagents` | `tinyhumansai/openhuman` | GPL-3.0 | ❌ ideas/patterns only |

These lanes live on the fork (`origin = endlessblink/hermes-agent`) so they
survive `hermes update` against `upstream = NousResearch/hermes-agent`.

---

## HB-01 — Memory Tree: scored Markdown mirrored as an editable Obsidian vault
**Priority:** P1 · **Status:** TODO · **Deps:** —

**Goal.** Replace/augment Hermes memory with the OpenHuman "Memory Tree" model:
raw context (chats, docs, mail) compressed into *scored Markdown files* held in
SQLite, and **mirrored as a plain Obsidian vault** the user can open and edit —
"no vector-soup black box." Fits the existing Obsidian-first workflow.

**Approach.**
1. Evaluate `tinycortex` (MIT) as a vendored crate/dependency vs. porting only
   its data model. Prefer depending on the crate; it exposes an async CRUD
   `Memory` trait, `MemoryEntry`/`MemoryCategory`/`MemoryTaint`, recall/scoring,
   and a SQLite-backed store.
2. Define the vault mirror path (default under the user's main vault at
   `…/OBSIDIAN_SYNCED/MAIN VULT/` — confirm subfolder with user before writing).
   One markdown file per memory node; wiki-links between nodes.
3. Two-way sync contract: SQLite is source of truth; vault edits are re-ingested
   on change (watch or on-open), scored, and reconciled. Never silently
   overwrite a user's hand-edit — diff and merge.
4. Preserve `MemoryTaint` fail-closed semantics (internal vs external_sync)
   before any tool that produces external effects reads memory.

**Acceptance.** A Hermes conversation writes at least one scored markdown node;
the same node is visible/editable in the Obsidian vault; a manual vault edit
survives the next ingest without being clobbered.

---

## HB-02 — Shared memory backend via `agentmemory` (cross-tool)
**Priority:** P2 · **Status:** TODO · **Deps:** HB-01 (backend abstraction)

**Goal.** One durable memory store that Hermes **and** the coding agents
(Claude Code, Codex, Cursor, OpenCode) read/write, instead of each tool keeping
its own island. OpenHuman ships this as an optional `Memory` backend that
proxies to `agentmemory` (Apache-2.0) via `memory.backend = "agentmemory"`.

**Approach.**
1. Land a backend abstraction in HB-01 so the store is swappable
   (`tinycortex` native ⇄ `agentmemory` proxy).
2. Stand up `agentmemory` locally; wire Hermes to it behind a config flag.
3. Document the shared-store setup so the same memory powers the other agents
   (this is the cross-tool memory goal we keep gravitating toward).

**Acceptance.** Hermes and one coding agent read the same memory record through
`agentmemory`; flipping the config flag switches backend with no data loss.

---

## HB-03 — Privacy Mode: one switch, enforced in the core
**Priority:** P2 · **Status:** TODO · **Deps:** —

**Goal.** A single toggle that guarantees **no inference leaves the machine**,
enforced at the provider/routing layer (not just a UI hint). Matches the global
hard rule: only local models (Ollama on localhost) for testing; no live cloud
calls without explicit approval.

**Approach.**
1. Add a `privacy_mode` flag read at the provider-selection boundary.
2. When on, hard-fail (fail-closed) any non-local provider dispatch with a clear
   error; only local endpoints (Ollama/localhost) are allowed.
3. Surface the state in the UI/TUI; log every blocked outbound attempt.

**Acceptance.** With Privacy Mode on, an attempted cloud-provider call is
refused in the core with a logged reason; a local Ollama call still succeeds.

---

## Not borrowing (scope control)
Mascot/avatar, video-call joining, and the 17-channel messaging fan-out are out
of scope for Hermes — they add surface area without serving the memory goal.
