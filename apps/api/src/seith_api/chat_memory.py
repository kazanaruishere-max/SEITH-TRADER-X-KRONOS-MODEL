"""Memori percakapan /ask per-user (Fase B2).

In-memory, keep N turn terakhir per user_id. Tidak persistent (bot personal,
satu proses). Aman untuk restart - histori hilang, tidak masalah untuk MVP.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

MAX_TURNS = 8


@dataclass
class _Turn:
    role: str  # "user" | "assistant"
    content: str


class ChatMemory:
    """Ring buffer per-user; get_context() mengembalikan teks untuk prompt LLM."""

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        self._max = max_turns
        self._store: dict[int, deque[_Turn]] = {}

    def add(self, user_id: int, role: str, content: str) -> None:
        buf = self._store.setdefault(user_id, deque(maxlen=self._max))
        buf.append(_Turn(role=role, content=content))

    def get_context(self, user_id: int) -> str:
        buf = self._store.get(user_id)
        if not buf:
            return ""
        lines = [
            f"{'User' if t.role == 'user' else 'Asisten'}: {t.content}" for t in buf
        ]
        return "\n".join(lines)

    def clear(self, user_id: int) -> None:
        self._store.pop(user_id, None)


_memory = ChatMemory()


def remember(user_id: int, role: str, content: str) -> None:
    _memory.add(user_id, role, content)


def recall_context(user_id: int) -> str:
    return _memory.get_context(user_id)
