from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SessionEntry:
    last_accessed_at: float
    data: object | None = None


class SessionStore:
    def __init__(self, *, max_sessions: int = 100, idle_ttl_s: float = 30 * 60) -> None:
        self.max_sessions = max_sessions
        self.idle_ttl_s = idle_ttl_s
        self._sessions: dict[str, SessionEntry] = {}

    def sweep(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        deadline = now - self.idle_ttl_s
        expired = [sid for sid, entry in self._sessions.items() if entry.last_accessed_at <= deadline]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def get(self, session_id: str) -> SessionEntry | None:
        self.sweep()
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        entry.last_accessed_at = time.time()
        return entry

    def commit(self, session_id: str, data: object | None = None) -> bool:
        self.sweep()
        if session_id not in self._sessions and len(self._sessions) >= self.max_sessions:
            return False
        self._sessions[session_id] = SessionEntry(last_accessed_at=time.time(), data=data)
        return True

    def close(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)

    def can_create(self) -> bool:
        self.sweep()
        return len(self._sessions) < self.max_sessions
