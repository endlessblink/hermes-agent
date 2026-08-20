from pathlib import Path

from scripts.verify_installed_lifeboat_skills import resolve_skill_root


def test_resolve_skill_root_accepts_namespaced_profile_layout(tmp_path: Path) -> None:
    profile_root = tmp_path / "life-advisor" / "skills"
    expected = profile_root / "productivity" / "personal-emotional-coaching" / "references"
    expected.mkdir(parents=True)
    (expected / "2026-08-17-topic-queue-and-bully-work.md").write_text("# test\n")

    assert resolve_skill_root(profile_root) == profile_root / "productivity"


def test_resolve_skill_root_preserves_flat_profile_layout(tmp_path: Path) -> None:
    profile_root = tmp_path / "life-advisor" / "skills"
    expected = profile_root / "personal-emotional-coaching" / "references"
    expected.mkdir(parents=True)
    (expected / "2026-08-17-topic-queue-and-bully-work.md").write_text("# test\n")

    assert resolve_skill_root(profile_root) == profile_root
