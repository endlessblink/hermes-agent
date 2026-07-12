"""hermes-memory-store — holographic memory plugin using MemoryProvider interface.

Registers as a MemoryProvider plugin, giving the agent structured fact storage
with entity resolution, trust scoring, and HRR-based compositional retrieval.

Original plugin by dusterbloom (PR #2351), adapted to the MemoryProvider ABC.

Config in $HERMES_HOME/config.yaml (profile-scoped):
  plugins:
    hermes-memory-store:
      db_path: $HERMES_HOME/memory_store.db   # omit to use the default
      auto_extract: false
      default_trust: 0.5
      min_trust_threshold: 0.3
      temporal_decay_half_life: 0
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from .store import MemoryStore
from .retrieval import FactRetriever
from hermes_cli.config import cfg_get

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from original PR)
# ---------------------------------------------------------------------------

FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall and compositional queries.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember.\n"
        "• search — Keyword lookup ('editor config', 'deploy process').\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• set_project — Declare which project this conversation is about (a short "
        "stable id/slug, e.g. 'too-much-video-art'). Do this once you know the project "
        "(infer it from the conversation or the note you're working in; if genuinely "
        "unsure between projects, ASK the user via clarify first — don't guess). It scopes "
        "recall and tags what you save to that project, so separate projects stop merging.\n"
        "• contradict — Memory hygiene: find facts making conflicting claims.\n"
        "• supersede — Retire a stale fact in favour of a newer one. When the user states a "
        "decision that contradicts a fact you already stored, add the new fact, then "
        "supersede the old one (fact_id = old, superseded_by = new id) so the stale fact "
        "stops being recalled as current. This is how a newer decision wins over an older one.\n"
        "• update/remove/list — CRUD operations.\n\n"
        "IMPORTANT: Before answering questions about the user, ALWAYS probe or reason first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "set_project", "contradict", "supersede", "update", "remove", "list"],
            },
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "projects": {"type": "array", "items": {"type": "string"}, "description": "Project id(s) this fact belongs to (for 'add'). Omit to use the current project; pass [] to make it global/shared across all projects; pass multiple ids for a technique shared by specific projects."},
            "project": {"type": "string", "description": "Project id/slug to make active (for 'set_project')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'; the OLD (retired) fact for 'supersede'."},
            "superseded_by": {"type": "integer", "description": "The NEW fact ID that replaces 'fact_id' (required for 'supersede')."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_delta": {"type": "number", "description": "Trust adjustment for 'update'."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# Inferred-fact categories that represent an explicit user choice and should be
# captured at higher trust so a recent decision outranks older/generic facts.
# Names match agent.memory_extraction.CATEGORIES.
_AUTHORITATIVE_CATEGORIES = frozenset(
    {"decision", "correction", "preference", "change", "rejected"}
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    from hermes_constants import get_hermes_home
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "hermes-memory-store", default={}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._retriever = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))
        # Two sources for "which project is this conversation":
        #  * _resolved_project — derived per turn from the code lane (repo →
        #    projects.db). Empty for note-based creative work.
        #  * _declared_project — set by the model via the fact_store 'set_project'
        #    action when it infers the project from the conversation. Sticky for
        #    the life of this (cached) provider instance, so it survives across
        #    turns and is NOT wiped by the per-turn lane resolution.
        # The declared project wins; effective project = declared or resolved.
        # None = unscoped (recall unfiltered, captures untagged) = old behaviour.
        self._resolved_project: str | None = None
        self._declared_project: str | None = None

    def set_active_project(self, project_id: str | None) -> None:
        """Set the lane-resolved project for this turn (does not clear a model
        declaration — a None from an unresolved lane must never wipe it)."""
        self._resolved_project = (project_id or None)

    @property
    def _active_project(self) -> "str | None":
        """Effective project: an explicit model declaration wins over the lane."""
        return self._declared_project or self._resolved_project

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hermes_home):
        """Write config to config.yaml under plugins.hermes-memory-store."""
        from pathlib import Path
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hermes-memory-store"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self):
        from hermes_constants import display_hermes_home
        _default_db = f"{display_hermes_home()}/memory_store.db"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": _default_db},
            {"key": "auto_extract", "description": "Auto-extract facts at session end", "default": "false", "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": "0.5"},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": "1024"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        _hermes_home = str(get_hermes_home())
        _default_db = _hermes_home + "/memory_store.db"
        db_path = self._config.get("db_path", _default_db)
        # Expand $HERMES_HOME in user-supplied paths so config values like
        # "$HERMES_HOME/memory_store.db" or "~/.hermes/memory_store.db" both
        # resolve to the active profile's directory.
        if isinstance(db_path, str):
            db_path = db_path.replace("$HERMES_HOME", _hermes_home)
            db_path = db_path.replace("${HERMES_HOME}", _hermes_home)
        default_trust = float(self._config.get("default_trust", 0.5))
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        hrr_weight = float(self._config.get("hrr_weight", 0.3))
        temporal_decay = int(self._config.get("temporal_decay_half_life", 0))

        self._store = MemoryStore(db_path=db_path, default_trust=default_trust, hrr_dim=hrr_dim)
        self._retriever = FactRetriever(
            store=self._store,
            temporal_decay_half_life=temporal_decay,
            hrr_weight=hrr_weight,
            hrr_dim=hrr_dim,
        )
        self._session_id = session_id
        try:
            _n = self._store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        except Exception:
            _n = "?"
        logger.info("[MEM] holographic active: db=%s facts=%s session=%s infer_facts=%s",
                    db_path, _n, session_id, self._config.get("infer_facts", self._config.get("auto_extract", False)))

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store._conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
        except Exception:
            total = 0
        if total == 0:
            return (
                "# Holographic Memory\n"
                "Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                "Use fact_feedback to rate facts after using them (trains trust scores)."
            )
        if self._active_project:
            project_line = (
                f"Active project: {self._active_project} — recall and new facts are scoped to it.\n"
            )
        else:
            project_line = (
                "No active project set. If this conversation is about a specific project, "
                "declare it with fact_store(action='set_project', project='<slug>') so memory "
                "doesn't mix separate projects (ask the user which project if unsure).\n"
            )
        return (
            f"# Holographic Memory\n"
            f"Active. {total} facts stored with entity resolution and trust scoring.\n"
            f"{project_line}"
            f"Use fact_store to search, probe entities, reason across entities, or add facts.\n"
            f"Use fact_feedback to rate facts after using them (trains trust scores)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not self._store:
            return ""
        try:
            # 1. GUARANTEED section: the latest authoritative decisions, injected
            #    every turn regardless of the query. A critical recent decision
            #    (e.g. "we switched to GPT Image 2 + Magnific, not Higgsfield")
            #    thus can't be crowded out by keyword hits, and — because the
            #    store persists across sessions — it survives a compression
            #    recovery instead of being dropped. Recency + supersede ordering
            #    means this always reflects the NEWEST context. Nothing hardcoded.
            try:
                decisions = self._store.recent_facts(
                    limit=6, min_trust=0.0, project_id=self._active_project,
                    categories=list(_AUTHORITATIVE_CATEGORIES),
                )
            except Exception as e:
                logger.debug("latest-decisions fetch failed: %s", e)
                decisions = []
            seen = {r.get("fact_id") for r in decisions}

            # 2. Keyword/semantic hits for THIS turn's query, project-scoped.
            results = []
            if query:
                results = self._retriever.search(
                    query, min_trust=self._min_trust, limit=5,
                    project_id=self._active_project,
                )
            relevant = [r for r in results if r.get("fact_id") not in seen]
            for r in relevant:
                seen.add(r.get("fact_id"))
            # ...filled with general recents so a vague continuation still gets
            # the recent subject/task facts. Capped separately from decisions.
            try:
                for r in self._store.recent_facts(
                    limit=5, min_trust=self._min_trust, project_id=self._active_project
                ):
                    if r.get("fact_id") not in seen and len(relevant) < 6:
                        seen.add(r.get("fact_id"))
                        relevant.append(r)
            except Exception as e:
                logger.debug("recency merge failed: %s", e)

            if not decisions and not relevant:
                logger.info("[MEM] recall query=%r -> 0 facts", (query or "")[:80])
                return ""

            out: List[str] = []
            if decisions:
                out.append("## Current context — latest decisions (newest first)")
                out.extend(f"- [{r.get('category', '')}] {r.get('content', '')}" for r in decisions)
            if relevant:
                out.append("## Relevant memory")
                for r in relevant:
                    trust = r.get("trust_score", r.get("trust", 0))
                    out.append(f"- [{trust:.1f}] {r.get('content', '')}")
            logger.info("[MEM] recall query=%r -> %d decisions + %d relevant",
                        (query or "")[:80], len(decisions), len(relevant))
            return "\n".join(out)
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Capture high-signal user statements (corrections, preferences,
        # decisions) AS THEY HAPPEN, so a long creative session accumulates
        # memory mid-conversation instead of only at session end (on_session_end
        # never fires for a session that stays open). Deterministic — regex, no
        # model call, so it's cheap and fits the no-cloud / low-VRAM rules — and
        # the manager already runs sync_turn off the main loop. Scoped to the
        # active project so projects don't cross-contaminate.
        if not self._store or not isinstance(user_content, str) or not user_content.strip():
            return
        try:
            from agent.memory_capture import mechanical_facts
        except Exception as e:
            logger.debug("memory_capture import failed in sync_turn: %s", e)
            return
        # Only the user's own words this turn; workspace/command facts belong to
        # the session-end pass (they need the full transcript).
        facts = [
            f for f in mechanical_facts([{"role": "user", "content": user_content}])
            if f.category in ("correction", "user_pref", "project")
        ]
        stored = 0
        for fact in facts:
            try:
                self._store.add_fact(
                    fact.content,
                    category=fact.category,
                    origin="mechanical",
                    source_session=self._session_id or "",
                    trust=0.8,  # explicit user statement — authoritative
                    projects=[self._active_project] if self._active_project else None,
                )
                stored += 1
            except Exception as e:
                logger.debug("sync_turn fact store failed: %s", e)
        if stored:
            logger.info("[MEM] sync_turn captured %d live fact(s): %s",
                        stored, [f.content[:60] for f in facts[:3]])

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "fact_store":
            return self._handle_fact_store(args)
        elif tool_name == "fact_feedback":
            return self._handle_fact_feedback(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        logger.info("[MEM] on_session_end fired: session=%s messages=%d store=%s",
                    self._session_id, len(messages) if messages else 0, bool(self._store))
        if not self._store or not messages:
            return
        # Mechanical capture always runs -- it's deterministic ground truth
        # (repo, commands, explicit user statements), not model inference, so
        # there's nothing to gate.
        self._capture_mechanical(messages)
        # Model-inferred capture (the substance: what we're working on, lessons
        # to remember, requested changes, decisions, rejected approaches,
        # preferences, open threads). Costs a model call, so it's opt-in.
        if self._config.get("infer_facts", self._config.get("auto_extract", False)):
            self._capture_inferred(messages)

    def _capture_inferred(self, messages: list) -> None:
        """Extract substantive facts with the model; store at the inferred tier."""
        try:
            from agent.memory_extraction import extract_inferred_facts
        except Exception as e:
            logger.debug("memory_extraction import failed: %s", e)
            return
        try:
            from agent.memory_capture import looks_like_secret as _is_secret
        except Exception:
            _is_secret = lambda _c: False
        stored = 0
        durable = []
        for fact in extract_inferred_facts(messages):
            if _is_secret(fact.content):
                continue  # never persist a secret, even model-inferred
            try:
                # Explicit user decisions carry more authority than generic
                # inferred facts, so a recent decision outranks an older note or
                # a stale peer fact in recall (ranking multiplies trust_score).
                trust = 0.8 if fact.category in _AUTHORITATIVE_CATEGORIES else None
                self._store.add_fact(
                    fact.content,
                    category=fact.category,
                    origin="inferred",
                    source_session=self._session_id or "",
                    trust=trust,
                    projects=[self._active_project] if self._active_project else None,
                )
                stored += 1
                durable.append(fact)
            except Exception as e:
                logger.debug("inferred fact store failed: %s", e)
        logger.info("[MEM] inferred capture: stored %d facts", stored)
        # Mirror durable facts into the human-editable Obsidian vault. Off by
        # default (writes to the real vault); enable with mirror_to_obsidian.
        if durable and self._config.get("mirror_to_obsidian", False):
            self._mirror_to_obsidian(durable)

    # Durable categories worth a human-editable note (not high-volume mechanical
    # facts like every command run — those stay in SQLite).
    _MIRROR_CATEGORIES = frozenset({"subject", "lesson", "change", "decision",
                                    "rejected", "preference", "open_thread", "project"})

    def _mirror_to_obsidian(self, facts: list) -> None:
        """Append durable facts to a per-profile, human-editable vault note.

        Reuses the raw-append pattern of the obsidian-source-of-truth plugin.
        A human/Obsidian edit to the note always wins over the SQLite copy.
        """
        try:
            import os
            from datetime import datetime
            from pathlib import Path

            vault = Path(os.environ.get("OBSIDIAN_VAULT_PATH")
                         or "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED")
            profile = self._config.get("profile") or "default"
            note_dir = vault / "MAIN VULT" / "_System" / "Hermes Knowledge Graph" / "Memory"
            note_dir.mkdir(parents=True, exist_ok=True)
            note = note_dir / f"{profile} - Memory Facts.md"
            if not note.exists():
                note.write_text(
                    f"---\ntype: memory\nowner_profile: {profile}\n"
                    "tags: [hermes, memory]\n---\n\n"
                    "# Hermes Memory Facts\n\n"
                    "Editable durable facts Hermes captured. Your edits here win "
                    "over its internal store.\n\n",
                    encoding="utf-8",
                )
            day = datetime.now().strftime("%Y-%m-%d")
            body = [f"\n## Update - {day}\n"]
            for f in facts:
                if getattr(f, "category", "") in self._MIRROR_CATEGORIES:
                    body.append(f"- **{f.category}**: {f.content}")
            if len(body) > 1:
                with note.open("a", encoding="utf-8") as fh:
                    fh.write("\n".join(body) + "\n")
        except Exception as e:
            logger.debug("Obsidian mirror failed: %s", e)

    def _capture_mechanical(self, messages: list) -> None:
        """Store deterministic facts from the conversation at the top trust tier."""
        try:
            from agent.memory_capture import mechanical_facts
        except Exception as e:
            logger.debug("memory_capture import failed: %s", e)
            return
        stored = 0
        for fact in mechanical_facts(messages):
            try:
                self._store.add_fact(
                    fact.content,
                    category=fact.category,
                    origin="mechanical",
                    source_session=self._session_id or "",
                    source_message_id=fact.source_message_id,
                    projects=[self._active_project] if self._active_project else None,
                )
                stored += 1
            except Exception as e:
                logger.debug("mechanical fact store failed: %s", e)
        logger.info("[MEM] mechanical capture: stored %d facts", stored)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes as facts."""
        if action == "add" and self._store and content:
            try:
                category = "user_pref" if target == "user" else "general"
                self._store.add_fact(content, category=category)
            except Exception as e:
                logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        # Release the shared SQLite connection deterministically on the
        # caller's thread. Dropping the reference alone leaves fd finalization
        # to GC, which keeps the connection (and its write lock) alive on a
        # long-running gateway and prolongs the "database is locked" contention
        # this store's shared-connection refcounting is meant to eliminate.
        # close() is idempotent and refcount-guarded, so siblings stay safe.
        if self._store is not None:
            try:
                self._store.close()
            except Exception as e:
                logger.debug("Holographic shutdown close() failed: %s", e)
        self._store = None
        self._retriever = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_fact_store(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            retriever = self._retriever

            if action == "add":
                try:
                    from agent.memory_capture import looks_like_secret as _is_secret
                    if _is_secret(args.get("content", "")):
                        return json.dumps({"status": "skipped", "reason": "content looks like a secret; not stored"})
                except Exception:
                    pass
                # Tag with the projects the model named, else default to the
                # active project so decisions stated in a project stay scoped to
                # it. An empty list (model passed []) means intentionally global.
                _projects = args.get("projects")
                if _projects is None and self._active_project:
                    _projects = [self._active_project]
                fact_id = store.add_fact(
                    args["content"],
                    category=args.get("category", "general"),
                    tags=args.get("tags", ""),
                    projects=_projects,
                )
                logger.info("[MEM] fact_store ADD id=%s cat=%s: %s",
                            fact_id, args.get("category", "general"),
                            str(args.get("content", ""))[:100])
                return json.dumps({"fact_id": fact_id, "status": "added"})

            elif action == "search":
                results = retriever.search(
                    args["query"],
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", self._min_trust)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "probe":
                results = retriever.probe(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "related":
                results = retriever.related(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "reason":
                entities = args.get("entities", [])
                if not entities:
                    return tool_error("reason requires 'entities' list")
                results = retriever.reason(
                    entities,
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "contradict":
                results = retriever.contradict(
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "set_project":
                # The model declares which project this conversation is about
                # (infer from context; ask the user via clarify when unsure).
                # Scopes recall + tags captures for the rest of the session.
                proj = (args.get("project") or "").strip()
                self._declared_project = proj or None
                logger.info("[MEM] set_project -> %r", self._declared_project)
                return json.dumps({"active_project": self._declared_project, "status": "set"})

            elif action == "supersede":
                old_id = int(args["fact_id"])
                new_id = int(args["superseded_by"])
                store.supersede_fact(old_id, new_id)
                logger.info("[MEM] supersede: fact %s retired by %s", old_id, new_id)
                return json.dumps({"superseded": old_id, "by": new_id, "status": "retired"})

            elif action == "update":
                updated = store.update_fact(
                    int(args["fact_id"]),
                    content=args.get("content"),
                    trust_delta=float(args["trust_delta"]) if "trust_delta" in args else None,
                    tags=args.get("tags"),
                    category=args.get("category"),
                )
                return json.dumps({"updated": updated})

            elif action == "remove":
                removed = store.remove_fact(int(args["fact_id"]))
                return json.dumps({"removed": removed})

            elif action == "list":
                facts = store.list_facts(
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", 0.0)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"facts": facts, "count": len(facts)})

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            fact_id = int(args["fact_id"])
            helpful = args["action"] == "helpful"
            result = self._store.record_feedback(fact_id, helpful=helpful)
            return json.dumps(result)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    # -- Auto-extraction (on_session_end) ------------------------------------

    def _auto_extract_facts(self, messages: list) -> None:
        _PREF_PATTERNS = [
            re.compile(r'\bI\s+(?:prefer|like|love|use|want|need)\s+(.+)', re.IGNORECASE),
            re.compile(r'\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)', re.IGNORECASE),
            re.compile(r'\bI\s+(?:always|never|usually)\s+(.+)', re.IGNORECASE),
        ]
        _DECISION_PATTERNS = [
            re.compile(r'\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)', re.IGNORECASE),
            re.compile(r'\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)', re.IGNORECASE),
        ]

        extracted = 0
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 10:
                continue

            for pattern in _PREF_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="user_pref")
                        extracted += 1
                    except Exception:
                        pass
                    break

            for pattern in _DECISION_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="project")
                        extracted += 1
                    except Exception:
                        pass
                    break

        if extracted:
            logger.info("Auto-extracted %d facts from conversation", extracted)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = HolographicMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
