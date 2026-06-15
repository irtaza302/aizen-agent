"""
Cross-session usage analytics for Aizen.

Tracks per-session stats (model, tokens, cost, duration) in a SQLite database
at ~/.aizen_analytics.db. Provides aggregate views via /stats command.
"""

import os
import sqlite3
import time

from rich.panel import Panel
from rich.text import Text

from .config import Theme
from .logging_config import logger

ANALYTICS_DB_PATH = os.path.expanduser("~/.aizen_analytics.db")


class Analytics:
    """SQLite-backed cross-session usage analytics."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or ANALYTICS_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                estimated_cost REAL DEFAULT 0.0,
                messages_count INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0.0,
                project TEXT,
                started_at REAL,
                ended_at REAL
            )
        """)
        self.conn.commit()

    def log_session(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        messages_count: int,
        duration_seconds: float,
        project: str | None = None,
    ) -> int:
        """Log a completed session's stats."""
        now = time.time()
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO sessions
               (model, input_tokens, output_tokens, total_tokens, estimated_cost,
                messages_count, duration_seconds, project, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model,
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                estimated_cost,
                messages_count,
                duration_seconds,
                project,
                now - duration_seconds,
                now,
            ),
        )
        self.conn.commit()
        session_id = cursor.lastrowid
        logger.debug(
            "Logged session #%d: %s, %d tokens, $%.4f",
            session_id,
            model,
            input_tokens + output_tokens,
            estimated_cost,
        )
        return session_id

    def get_summary(self, days: int = 30) -> dict:
        """Get aggregate stats for the last N days."""
        cutoff = time.time() - (days * 86400)
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) as count FROM sessions WHERE ended_at > ?",
            (cutoff,),
        )
        total_sessions = cursor.fetchone()["count"]

        cursor.execute(
            """SELECT
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0) as total_cost,
                COALESCE(SUM(messages_count), 0) as total_messages,
                COALESCE(AVG(duration_seconds), 0) as avg_duration
               FROM sessions WHERE ended_at > ?""",
            (cutoff,),
        )
        agg = cursor.fetchone()

        # Most used model
        cursor.execute(
            """SELECT model, COUNT(*) as cnt
               FROM sessions WHERE ended_at > ?
               GROUP BY model ORDER BY cnt DESC LIMIT 1""",
            (cutoff,),
        )
        model_row = cursor.fetchone()
        favorite_model = model_row["model"] if model_row else "N/A"
        model_pct = (model_row["cnt"] / total_sessions * 100) if model_row and total_sessions else 0

        # Daily cost for sparkline (last 14 days)
        daily_costs = []
        for i in range(13, -1, -1):
            day_start = time.time() - ((i + 1) * 86400)
            day_end = time.time() - (i * 86400)
            cursor.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) as cost FROM sessions WHERE ended_at > ? AND ended_at <= ?",
                (day_start, day_end),
            )
            daily_costs.append(cursor.fetchone()["cost"])

        return {
            "days": days,
            "total_sessions": total_sessions,
            "total_tokens": agg["total_tokens"],
            "total_cost": agg["total_cost"],
            "total_messages": agg["total_messages"],
            "avg_duration": agg["avg_duration"],
            "favorite_model": favorite_model,
            "model_pct": model_pct,
            "daily_costs": daily_costs,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


def _sparkline(values: list[float]) -> str:
    """Generate a Unicode sparkline chart from a list of values."""
    if not values or max(values) == 0:
        return "▁" * len(values)

    blocks = "▁▂▃▄▅▆▇█"
    max_val = max(values)
    result = ""
    for v in values:
        idx = int((v / max_val) * (len(blocks) - 1)) if max_val > 0 else 0
        result += blocks[idx]
    return result


def format_stats_display(stats: dict) -> Panel:
    """Format analytics stats into a rich Panel display."""
    text = Text()

    text.append("  Total Sessions:    ", style=Theme.MUTED)
    text.append(f"{stats['total_sessions']}\n", style=f"bold {Theme.TEXT}")

    text.append("  Total Cost:        ", style=Theme.MUTED)
    text.append(f"${stats['total_cost']:.2f}\n", style=f"bold {Theme.SUCCESS}")

    text.append("  Tokens Used:       ", style=Theme.MUTED)
    text.append(f"{stats['total_tokens']:,}\n", style=f"bold {Theme.TEXT}")

    text.append("  Messages Sent:     ", style=Theme.MUTED)
    text.append(f"{stats['total_messages']:,}\n", style=Theme.TEXT)

    avg_min = stats["avg_duration"] / 60
    text.append("  Avg Session:       ", style=Theme.MUTED)
    text.append(f"{avg_min:.1f} min\n", style=Theme.TEXT)

    text.append("  Favorite Model:    ", style=Theme.MUTED)
    text.append(f"{stats['favorite_model']}", style=f"bold {Theme.ACCENT}")
    if stats["model_pct"] > 0:
        text.append(f" ({stats['model_pct']:.0f}%)", style=Theme.MUTED)
    text.append("\n\n", style=Theme.TEXT)

    # Sparkline chart
    sparkline = _sparkline(stats["daily_costs"])
    text.append("  Cost/Day (14d):  ", style=Theme.MUTED)
    text.append(sparkline, style=f"bold {Theme.PRIMARY}")

    return Panel(
        text,
        title=f"[bold {Theme.ACCENT}]📈 Aizen Usage (Last {stats['days']} Days)[/bold {Theme.ACCENT}]",
        border_style=Theme.BORDER,
    )


# ─── Global singleton ───────────────────────────────────────────────────────

_global_analytics: Analytics | None = None


def get_analytics() -> Analytics:
    """Get or create the global analytics singleton."""
    global _global_analytics
    if _global_analytics is None:
        _global_analytics = Analytics()
    return _global_analytics
