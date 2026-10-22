"""Agent state. A dict so LangGraph can checkpoint it, plus a result model."""

from __future__ import annotations

import sqlite3
from typing import Any

from caretrace.models.domain import AgentAnswer


class AgentState(dict[str, Any]):
    """Mutable state bag. Keys match the product contract."""

    defaults: dict[str, Any] = {
        "messages": [],
        "intent": "general",
        "full_journey": False,
        "patient_ctx": None,
        "payer_id": None,
        "plan": None,
        "zip": None,
        "code_candidates": [],
        "tool_results": {},
        "citations": [],
        "confidence": 0.0,
        "confidence_reasons": [],
        "needs_clarification": None,
        "answer": "",
        "regen": 0,
        "trace": [],
    }

    @classmethod
    def create(cls, message: str, **extra: Any) -> AgentState:
        state = cls()
        state.update(cls.defaults)
        state["messages"] = [message]
        state.update(extra)
        return state


class SessionStore:
    """SQLite checkpointer for multi-turn chats."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT)")

    def load(self, session_id: str) -> dict[str, Any] | None:
        import json

        row = self.conn.execute("SELECT state FROM sessions WHERE id = ?", [session_id]).fetchone()
        if row is None:
            return None
        loaded = json.loads(row[0])
        return loaded if isinstance(loaded, dict) else None

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        import json

        safe = {key: value for key, value in state.items() if key != "settings"}
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions (id, state) VALUES (?, ?)",
            [session_id, json.dumps(safe, default=str)],
        )
        self.conn.commit()


def to_answer(state: dict[str, Any], trace_id: str = "") -> AgentAnswer:
    """Convert graph state into the API model."""
    from caretrace.models.domain import Citation

    citations = [Citation.model_validate(item) for item in state.get("citations", [])]
    return AgentAnswer(
        answer=str(state.get("answer") or ""),
        citations=citations,
        confidence=float(state.get("confidence") or 0),
        confidence_reasons=list(state.get("confidence_reasons") or []),
        intent=str(state.get("intent") or "general"),
        needs_clarification=state.get("needs_clarification"),
        trace_id=trace_id,
        abstained=bool(state.get("abstained")),
    )
