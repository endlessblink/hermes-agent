from pathlib import Path

import pytest

from gateway import user_profiles


def test_identity_names_are_stable_path_safe_and_distinct():
    first = user_profiles.profile_name_for_identity("telegram", "100")
    second = user_profiles.profile_name_for_identity("telegram", "200")
    assert first == user_profiles.profile_name_for_identity("telegram", "100")
    assert first != second
    assert first.startswith("tg-u-")
    assert "/" not in first and ".." not in first


def test_unapproved_identity_does_not_create_mapping_or_profile(monkeypatch):
    mapping = {}
    monkeypatch.setattr(user_profiles, "_read_mapping", lambda: mapping)
    monkeypatch.setattr(user_profiles, "_write_mapping", lambda value: mapping.update(value))
    created = []
    monkeypatch.setattr(user_profiles, "create_profile", lambda *args, **kwargs: created.append(args))
    assert user_profiles.resolve_user_profile("telegram", "blocked", approved=False) is None
    assert mapping == {}
    assert created == []


def test_two_approved_identities_get_separate_profiles(monkeypatch):
    mapping = {}
    existing = set()
    monkeypatch.setattr(user_profiles, "_read_mapping", lambda: dict(mapping))
    monkeypatch.setattr(user_profiles, "_write_mapping", lambda value: mapping.update(value))
    monkeypatch.setattr(user_profiles, "profile_exists", lambda name: name in existing)

    def create(name, **_kwargs):
        existing.add(name)

    monkeypatch.setattr(user_profiles, "create_profile", create)
    first = user_profiles.resolve_user_profile("telegram", "100", approved=True)
    second = user_profiles.resolve_user_profile("telegram", "200", approved=True)
    assert first != second
    assert mapping["telegram:100"] == first
    assert mapping["telegram:200"] == second


def test_profile_state_rejects_credential_material(tmp_path: Path):
    with pytest.raises(ValueError):
        user_profiles.write_user_auth_state(tmp_path, {"access_token": "secret"})
