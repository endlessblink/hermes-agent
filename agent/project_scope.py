"""Resolve the active project (a variable) for the current session.

Bridges the existing lane resolver's output — a repo path already stored on the
session's working state by ``turn_finalizer`` — to a project record in
``projects.db`` via ``project_for_path`` (longest-prefix folder match). This
lets memory be scoped per project with no hardcoded project map anywhere.

Fail-open by design: any missing input or error returns ``None``, which
disables project scoping (recall isn't filtered, captures aren't tagged), so a
wrong or unresolved lane never blanks out memory.
"""
from __future__ import annotations

from typing import Any, Optional


def project_id_for_session(session_db: Any, session_id: str) -> Optional[str]:
    """Return the project id owning this session's working-state lane, or None.

    ``None`` means "unscoped" — the caller should not filter/tag by project.
    """
    if session_db is None or not session_id:
        return None
    try:
        ws = session_db.get_working_state(session_id) or {}
        lane = ws.get("lane") if isinstance(ws, dict) else None
        repo_path = lane.get("repo_path") if isinstance(lane, dict) else None
        if not repo_path:
            return None
        from hermes_cli.projects_db import connect_closing, project_for_path

        with connect_closing() as conn:
            proj = project_for_path(conn, repo_path)
            return getattr(proj, "id", None) if proj else None
    except Exception:
        return None
