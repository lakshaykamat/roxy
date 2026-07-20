"""Conversation state for multi-turn expense flows.

Roxy is a single-user bot, so pending clarification (candidate matches) and a
pending delete confirmation are held in a small in-process store. This lets a
follow-up turn ("yes", "the second one") resolve to a concrete expense ``id``
without re-deriving it, and lets the delete handler refuse to act until the user
has confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    id: str
    summary: str


@dataclass
class _State:
    matches: list[Candidate] = field(default_factory=list)
    pending_delete_id: str | None = None
    pending_delete_summary: str | None = None


_state = _State()


def remember_matches(candidates: list[Candidate]) -> None:
    _state.matches = list(candidates)


def get_matches() -> list[Candidate]:
    return list(_state.matches)


def resolve_selection(index: int) -> Candidate | None:
    """Resolve a 1-based selection from the last shown candidate list."""
    if 1 <= index <= len(_state.matches):
        return _state.matches[index - 1]
    return None


def set_pending_delete(expense_id: str, summary: str) -> None:
    _state.pending_delete_id = expense_id
    _state.pending_delete_summary = summary


def get_pending_delete() -> tuple[str | None, str | None]:
    return _state.pending_delete_id, _state.pending_delete_summary


def clear_pending_delete() -> None:
    _state.pending_delete_id = None
    _state.pending_delete_summary = None


def clear() -> None:
    _state.matches = []
    _state.pending_delete_id = None
    _state.pending_delete_summary = None
