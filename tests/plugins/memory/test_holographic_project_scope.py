"""Project-scoped memory: facts tagged to project(s), recall filtered by project.

Standalone projects in one profile must not bleed into each other, while a fact
tagged 'shared' across projects surfaces for each, and unresolved project
(project_id=None) disables the filter so recall never blanks out.
"""

import json

import pytest

from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore
from plugins.memory.holographic import HolographicMemoryProvider


@pytest.fixture
def retriever(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield FactRetriever(store=store, hrr_dim=store.hrr_dim), store
    store.close()


def test_add_fact_tags_projects_and_reads_back(retriever):
    _, store = retriever
    fid = store.add_fact("dialogue comes from the video model", projects=["projA"])
    assert store.fact_projects(fid) == ["projA"]
    # Untagged fact = global.
    gid = store.add_fact("render at 4k")
    assert store.fact_projects(gid) == []


def test_duplicate_content_merges_project_tags(retriever):
    _, store = retriever
    first = store.add_fact("shared upscaling technique", projects=["projA"])
    # Same content re-added for another project returns the same id, tags merge.
    again = store.add_fact("shared upscaling technique", projects=["projB"])
    assert again == first
    assert store.fact_projects(first) == ["projA", "projB"]


def test_recall_scoped_to_project(retriever):
    r, store = retriever
    store.add_fact("projA uses seedance for shot 13", projects=["projA"])
    store.add_fact("projB uses kling for the intro", projects=["projB"])
    store.add_fact("always render at 4k", projects=None)  # global

    a_hits = {h["content"] for h in r.search("shot intro render", min_trust=0.0, limit=10, project_id="projA")}
    assert any("seedance" in c for c in a_hits)      # projA's fact
    assert not any("kling" in c for c in a_hits)     # projB's fact excluded
    assert any("4k" in c for c in a_hits)            # global fact still surfaces


def test_shared_fact_surfaces_for_each_tagged_project(retriever):
    r, store = retriever
    store.add_fact("dialogue from video model, never TTS", projects=["projA", "projB"])
    for p in ("projA", "projB"):
        hits = {h["content"] for h in r.search("dialogue TTS video", min_trust=0.0, limit=5, project_id=p)}
        assert any("never TTS" in c for c in hits), f"shared fact missing for {p}"


def test_none_project_disables_filter(retriever):
    r, store = retriever
    store.add_fact("projA only fact about seedance", projects=["projA"])
    # No active project → recall everything (no blanking out).
    hits = {h["content"] for h in r.search("seedance", min_trust=0.0, limit=5, project_id=None)}
    assert any("seedance" in c for c in hits)


def test_model_declared_project_sticks_and_wins(tmp_path):
    provider = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    provider.initialize(session_id="s")
    try:
        # Model declares the project (note-based work: no lane resolved).
        res = json.loads(provider._handle_fact_store(
            {"action": "set_project", "project": "too-much-video-art"}))
        assert res["active_project"] == "too-much-video-art"
        assert provider._active_project == "too-much-video-art"

        # A per-turn lane resolution of None must NOT wipe the declaration.
        provider.set_active_project(None)
        assert provider._active_project == "too-much-video-art"

        # Captured facts get tagged with the declared project.
        fid = json.loads(provider._handle_fact_store(
            {"action": "add", "content": "hand piece uses seedance"}))["fact_id"]
        assert provider._store.fact_projects(fid) == ["too-much-video-art"]

        # Declaration wins over a later lane resolution too.
        provider.set_active_project("some-repo-project")
        assert provider._active_project == "too-much-video-art"
    finally:
        provider.shutdown()


def test_provider_scopes_recall_and_tags_capture(tmp_path):
    provider = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    provider.initialize(session_id="s")
    try:
        provider.set_active_project("projA")
        fid = json.loads(provider._handle_fact_store(
            {"action": "add", "content": "projA decision: use Magnific"}))["fact_id"]
        # Captured fact was tagged with the active project.
        assert provider._store.fact_projects(fid) == ["projA"]

        # A projB fact must not surface while projA is active.
        provider.set_active_project("projB")
        provider._handle_fact_store({"action": "add", "content": "projB uses kling"})
        provider.set_active_project("projA")
        block = provider.prefetch("which tool")
        assert "Magnific" in block
        assert "kling" not in block
    finally:
        provider.shutdown()
