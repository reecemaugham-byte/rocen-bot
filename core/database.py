import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from config import config


class Database:
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or config.DB_PATH
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_tables()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        conn = self._connect()
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                emotional_state TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT '',
                communication_style TEXT DEFAULT 'casual',
                interests TEXT DEFAULT '',
                personality_notes TEXT DEFAULT '',
                preferences TEXT DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS emotional_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                context TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS outreach_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reason TEXT,
                response_received BOOLEAN DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                proactive_enabled BOOLEAN DEFAULT 1,
                quiet_hours_start INTEGER DEFAULT 22,
                quiet_hours_end INTEGER DEFAULT 8,
                outreach_frequency TEXT DEFAULT 'normal',
                timezone TEXT DEFAULT 'Europe/London',
                last_outreach DATETIME,
                unanswered_count INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    # ── Conversations ──

    def save_message(self, user_id: int, role: str, content: str, emotional_state: str = None):
        conn = self._connect()
        conn.execute(
            "INSERT INTO conversations (user_id, role, content, emotional_state) VALUES (?, ?, ?, ?)",
            (user_id, role, content, emotional_state),
        )
        conn.commit()
        conn.close()

    def get_recent_messages(self, user_id: int, limit: int = 20) -> list[dict]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT role, content, emotional_state, timestamp FROM conversations "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cursor.fetchall()
        conn.close()
        messages = [{"role": r["role"], "content": r["content"],
                      "emotion": r["emotional_state"], "time": r["timestamp"]} for r in rows]
        messages.reverse()  # oldest first
        return messages

    def get_last_user_message(self, user_id: int) -> dict | None:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT content, emotional_state, timestamp FROM conversations "
            "WHERE user_id = ? AND role = 'user' ORDER BY timestamp DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"content": row["content"], "emotion": row["emotional_state"], "time": row["timestamp"]}
        return None

    def get_last_contact_time(self, user_id: int) -> datetime | None:
        conn = self._connect()
        cursor = conn.execute(
            "SELECT timestamp FROM conversations "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row[0])
        return None

    def get_conversation_summary(self, user_id: int, max_messages: int = 6) -> str:
        """Return a short summary of the recent conversation for context."""
        messages = self.get_recent_messages(user_id, limit=max_messages)
        if not messages:
            return "No previous conversation."
        lines = []
        for m in messages:
            role = "User" if m["role"] == "user" else config.BOT_NAME
            lines.append(f"{role}: {m['content'][:200]}")
        return "\n".join(lines)

    # ── Emotional States ──

    def save_emotional_state(self, user_id: int, state: str, confidence: float, context: str = ""):
        conn = self._connect()
        conn.execute(
            "INSERT INTO emotional_states (user_id, state, confidence, context) VALUES (?, ?, ?, ?)",
            (user_id, state, confidence, context),
        )
        conn.commit()
        conn.close()

    def get_latest_emotional_state(self, user_id: int) -> dict:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT state, confidence, timestamp FROM emotional_states "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"state": row["state"], "confidence": row["confidence"], "timestamp": row["timestamp"]}
        return {"state": "neutral", "confidence": 0.5, "timestamp": None}

    def get_emotional_trajectory(self, user_id: int, days: int = 7) -> list[dict]:
        """Get emotional states over the past N days to see trends."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT state, confidence, timestamp FROM emotional_states "
            "WHERE user_id = ? AND timestamp > ? ORDER BY timestamp ASC",
            (user_id, since),
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"state": r["state"], "confidence": r["confidence"], "timestamp": r["timestamp"]} for r in rows]

    # ── User Profiles ──

    def get_profile(self, user_id: int) -> dict:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            prefs = row["preferences"]
            if isinstance(prefs, str):
                try:
                    prefs = json.loads(prefs)
                except json.JSONDecodeError:
                    prefs = {}
            return {
                "name": row["name"],
                "communication_style": row["communication_style"],
                "interests": row["interests"],
                "personality_notes": row["personality_notes"],
                "preferences": prefs,
            }
        # Create default profile
        self._ensure_profile(user_id)
        return {
            "name": "",
            "communication_style": "casual",
            "interests": "",
            "personality_notes": "",
            "preferences": {},
        }

    def update_profile(self, user_id: int, **kwargs):
        self._ensure_profile(user_id)
        allowed = {"name", "communication_style", "interests", "personality_notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [datetime.now().isoformat(), user_id]
        conn = self._connect()
        conn.execute(
            f"UPDATE user_profiles SET {set_clause}, updated_at = ? WHERE user_id = ?",
            values,
        )
        conn.commit()
        conn.close()

    def _ensure_profile(self, user_id: int):
        conn = self._connect()
        cursor = conn.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            conn.execute(
                "INSERT INTO user_profiles (user_id) VALUES (?)", (user_id,)
            )
            conn.commit()
        conn.close()

    # ── Outreach ──

    def log_outreach(self, user_id: int, message: str, reason: str):
        conn = self._connect()
        conn.execute(
            "INSERT INTO outreach_log (user_id, message, reason) VALUES (?, ?, ?)",
            (user_id, message, reason),
        )
        conn.commit()
        conn.close()

    def get_recent_outreach_count(self, user_id: int, hours: int = 24) -> int:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        conn = self._connect()
        cursor = conn.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE user_id = ? AND timestamp > ?",
            (user_id, since),
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_unanswered_outreach_count(self, user_id: int) -> int:
        conn = self._connect()
        cursor = conn.execute(
            "SELECT COUNT(*) FROM outreach_log "
            "WHERE user_id = ? AND response_received = 0 "
            "ORDER BY timestamp DESC LIMIT 5",
            (user_id,),
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def mark_outreach_answered(self, user_id: int):
        """When the user responds, mark their unanswered outreach as answered."""
        conn = self._connect()
        conn.execute(
            "UPDATE outreach_log SET response_received = 1 "
            "WHERE user_id = ? AND response_received = 0",
            (user_id,),
        )
        conn.commit()
        conn.close()

    # ── User Settings ──

    def get_settings(self, user_id: int) -> dict:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "proactive_enabled": bool(row["proactive_enabled"]),
                "quiet_hours_start": row["quiet_hours_start"],
                "quiet_hours_end": row["quiet_hours_end"],
                "outreach_frequency": row["outreach_frequency"],
                "timezone": row["timezone"],
            }
        self._ensure_settings(user_id)
        return {
            "proactive_enabled": True,
            "quiet_hours_start": config.QUIET_HOURS_START,
            "quiet_hours_end": config.QUIET_HOURS_END,
            "outreach_frequency": "normal",
            "timezone": "Europe/London",
        }

    def update_settings(self, user_id: int, **kwargs):
        self._ensure_settings(user_id)
        allowed = {"proactive_enabled", "quiet_hours_start", "quiet_hours_end",
                    "outreach_frequency", "timezone"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        conn = self._connect()
        conn.execute(
            f"UPDATE user_settings SET {set_clause} WHERE user_id = ?",
            values,
        )
        conn.commit()
        conn.close()

    def _ensure_settings(self, user_id: int):
        conn = self._connect()
        cursor = conn.execute("SELECT 1 FROM user_settings WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            conn.execute(
                "INSERT INTO user_settings (user_id) VALUES (?)", (user_id,)
            )
            conn.commit()
        conn.close()

    def update_last_outreach(self, user_id: int):
        conn = self._connect()
        conn.execute(
            "UPDATE user_settings SET last_outreach = ?, unanswered_count = unanswered_count + 1 "
            "WHERE user_id = ?",
            (datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()

    def reset_unanswered_count(self, user_id: int):
        conn = self._connect()
        conn.execute(
            "UPDATE user_settings SET unanswered_count = 0 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        conn.close()

    def get_last_outreach_time(self, user_id: int) -> datetime | None:
        conn = self._connect()
        cursor = conn.execute(
            "SELECT last_outreach FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    def get_unanswered_count(self, user_id: int) -> int:
        conn = self._connect()
        cursor = conn.execute(
            "SELECT unanswered_count FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else 0

    # ── Registered Users ──

    def get_all_proactive_users(self) -> list[int]:
        """Get all user IDs with proactive outreach enabled."""
        conn = self._connect()
        cursor = conn.execute(
            "SELECT user_id FROM user_settings WHERE proactive_enabled = 1"
        )
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users

    def get_all_known_users(self) -> list[int]:
        """Get all user IDs who have ever started a conversation."""
        conn = self._connect()
        cursor = conn.execute(
            "SELECT DISTINCT user_id FROM conversations"
        )
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users


# Singleton
db = Database()
