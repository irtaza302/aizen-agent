"""
Persistent memory system for Aizen.

Stores facts, preferences, and project knowledge across sessions in a SQLite
database at ~/.aizen_memory.db. Supports manual `/remember` and AI-driven
`remember_fact` tool invocations.
"""

import os
import sqlite3
import time
from typing import Any

from .logging_config import logger

MEMORY_DB_PATH = os.path.expanduser("~/.aizen_memory.db")


class MemoryStore:
    """SQLite-backed persistent memory with semantic and keyword retrieval."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or MEMORY_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create the memory table if it doesn't exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                source TEXT DEFAULT 'user',
                project TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        # Create index for faster project-based lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_project
            ON memories(project)
        """)
        self.conn.commit()

    def remember(
        self, fact: str, category: str = "general", source: str = "user", project: str | None = None
    ) -> int:
        """
        Store a fact in memory.

        Args:
            fact: The fact to remember.
            category: Category (general, preference, project, architecture, convention).
            source: Who stored it ('user' or 'ai').
            project: Optional project name to scope the memory.

        Returns:
            The ID of the stored memory.
        """
        # Deduplicate — don't store the exact same fact twice
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id FROM memories WHERE fact = ? AND (project = ? OR (project IS NULL AND ? IS NULL))",
            (fact, project, project),
        )
        existing = cursor.fetchone()
        if existing:
            # Update timestamp
            cursor.execute(
                "UPDATE memories SET updated_at = ? WHERE id = ?",
                (time.time(), existing["id"]),
            )
            self.conn.commit()
            return existing["id"]

        now = time.time()
        cursor.execute(
            "INSERT INTO memories (fact, category, source, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (fact, category, source, project, now, now),
        )
        self.conn.commit()
        memory_id = cursor.lastrowid
        logger.debug("Stored memory #%d: %s", memory_id, fact[:80])
        return memory_id

    def recall(
        self,
        query: str | None = None,
        project: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Retrieve memories, optionally filtered by keyword, project, or category.

        Uses simple keyword matching. For full semantic search, use the RAG system.
        """
        cursor = self.conn.cursor()
        conditions = []
        params: list[Any] = []

        if query:
            # Simple keyword matching on the fact text
            words = query.lower().split()
            for word in words:
                conditions.append("LOWER(fact) LIKE ?")
                params.append(f"%{word}%")

        if project:
            conditions.append("(project = ? OR project IS NULL)")
            params.append(project)

        if category:
            conditions.append("category = ?")
            params.append(category)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        cursor.execute(
            f"SELECT * FROM memories WHERE {where_clause} ORDER BY updated_at DESC LIMIT ?",
            params + [limit],
        )

        return [dict(row) for row in cursor.fetchall()]

    def list_all(self, project: str | None = None, limit: int = 50) -> list[dict]:
        """List all stored memories, optionally filtered by project."""
        cursor = self.conn.cursor()
        if project:
            cursor.execute(
                "SELECT * FROM memories WHERE project = ? OR project IS NULL ORDER BY updated_at DESC LIMIT ?",
                (project, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in cursor.fetchall()]

    def forget(self, memory_id: int) -> bool:
        """Remove a specific memory by ID."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("Deleted memory #%d", memory_id)
        return deleted

    def forget_all(self, project: str | None = None) -> int:
        """Remove all memories, optionally scoped to a project. Returns count deleted."""
        cursor = self.conn.cursor()
        if project:
            cursor.execute("DELETE FROM memories WHERE project = ?", (project,))
        else:
            cursor.execute("DELETE FROM memories")
        self.conn.commit()
        return cursor.rowcount

    def get_context_for_prompt(self, project: str | None = None, limit: int = 10) -> str:
        """
        Get formatted memory context for injection into the system prompt.

        Returns an empty string if no memories exist.
        """
        memories = self.list_all(project=project, limit=limit)
        if not memories:
            return ""

        lines = []
        for mem in memories:
            cat_tag = f"[{mem['category']}]" if mem["category"] != "general" else ""
            lines.append(f"- {mem['fact']} {cat_tag}")

        return (
            "\n\n## Persistent Memory\n"
            "These are facts and preferences I remember from previous sessions:\n"
            + "\n".join(lines)
        )

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


# ─── Tool schema for AI-driven memory storage ───────────────────────────────

REMEMBER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember_fact",
        "description": (
            "Store an important fact, user preference, or project detail in persistent memory. "
            "Use this when you learn something that would be useful to remember across sessions, "
            "such as: project architecture decisions, user coding style preferences, "
            "important file paths, deployment procedures, or conventions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or preference to remember.",
                },
                "category": {
                    "type": "string",
                    "enum": ["general", "preference", "project", "architecture", "convention"],
                    "description": "Category of the memory. Default: 'general'.",
                },
            },
            "required": ["fact"],
        },
    },
}


# ─── Global singleton ───────────────────────────────────────────────────────

_global_memory: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """Get or create the global memory store singleton."""
    global _global_memory
    if _global_memory is None:
        _global_memory = MemoryStore()
    return _global_memory
