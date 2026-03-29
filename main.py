import os
import json
import re
import shutil
import sqlite3
import time
import uuid
import webbrowser
from urllib import error as urllib_error
from urllib.parse import quote_plus
from urllib import request as urllib_request
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def load_local_env(env_path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from a local .env file into os.environ."""
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
    except OSError:
        return


load_local_env()


def save_local_env_value(key: str, value: str, env_path: str = ".env") -> None:
    """Insert or update a KEY=VALUE pair in the local .env file."""
    lines: list[str] = []
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                lines = env_file.read().splitlines()
        except OSError:
            lines = []

    updated = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            updated = True
            break

    if not updated:
        lines.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines).rstrip() + "\n")


# -------------------------------------------------
# App constants
# -------------------------------------------------
DB_NAME = "CyberXchange.db"
LOGO_PATH = "Xchange.png"   # Change this if your image file name is different
UPLOADS_DIR = "uploads"
APP_TITLE = "Cyber Xchange"
WINDOW_SIZE = "1240x820"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
OPENAI_LOOKUP_MODEL = os.getenv("OPENAI_SEARCH_MODEL", OPENAI_VISION_MODEL)
OPENAI_API_KEY_PLACEHOLDER = "your_openai_api_key_here"
AI_BACKEND_URL = os.getenv("AI_BACKEND_URL", "http://127.0.0.1:8765/price-lookup")
LISTING_STATUSES = ("active", "pending_trade", "traded", "cancelled")
LISTING_STATUS_LABELS = {
    "active": "Active",
    "pending_trade": "Pending Trade",
    "traded": "Traded",
    "cancelled": "Cancelled",
}

# -------------------------------------------------
# Neon theme colors
# -------------------------------------------------
BG_MAIN = "#07040f"
BG_PANEL = "#12091f"
BG_CARD = "#171126"
BG_INPUT = "#0d0a18"
BG_ALT = "#1a1430"
BG_EDGE = "#20183a"
BG_GLOW = "#28194d"

NEON_BLUE = "#61f3ff"
NEON_GREEN = "#8affb6"
NEON_PINK = "#ff8ea5"
NEON_PURPLE = "#b05cff"
NEON_GOLD = "#fff4ff"
TEXT_MAIN = "#ffffff"
TEXT_SOFT = "#d8d2ff"
TEXT_MUTED = "#9b93c8"


# -------------------------------------------------
# Database setup
# -------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Open SQLite with a write timeout and WAL mode for better multi-window behavior."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create required tables if they do not already exist."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Item listings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            condition TEXT NOT NULL,
            description TEXT,
            estimated_value REAL NOT NULL,
            desired_trade_value REAL NOT NULL,
            photo_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    ensure_column(cur, "items", "status", "TEXT NOT NULL DEFAULT 'active'")

    # Local inbox/messages table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            item_id INTEGER,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
        """
    )

    # Conversation threads / request states
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_one_id INTEGER NOT NULL,
            user_two_id INTEGER NOT NULL,
            requested_by INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_one_id, user_two_id),
            FOREIGN KEY(user_one_id) REFERENCES users(id),
            FOREIGN KEY(user_two_id) REFERENCES users(id),
            FOREIGN KEY(requested_by) REFERENCES users(id)
        )
        """
    )

    ensure_column(cur, "messages", "conversation_id", "INTEGER")
    migrate_legacy_messages(cur)

    conn.commit()
    conn.close()


def ensure_column(cur: sqlite3.Cursor, table_name: str, column_name: str, definition: str) -> None:
    """Add a missing column to an existing SQLite table."""
    cur.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cur.fetchall()}
    if column_name not in existing_columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def normalize_pair(user_a: int, user_b: int) -> tuple[int, int]:
    """Store user pairs in a consistent order for conversation uniqueness."""
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


def migrate_legacy_messages(cur: sqlite3.Cursor) -> None:
    """Attach old one-off messages to active conversation threads."""
    cur.execute("SELECT id, sender_id, receiver_id, created_at FROM messages WHERE conversation_id IS NULL ORDER BY id")
    orphan_messages = cur.fetchall()

    for message_id, sender_id, receiver_id, created_at in orphan_messages:
        user_one_id, user_two_id = normalize_pair(sender_id, receiver_id)
        cur.execute(
            """
            SELECT id FROM conversations
            WHERE user_one_id = ? AND user_two_id = ?
            """,
            (user_one_id, user_two_id),
        )
        row = cur.fetchone()

        if row:
            conversation_id = row[0]
        else:
            cur.execute(
                """
                INSERT INTO conversations (
                    user_one_id, user_two_id, requested_by, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (user_one_id, user_two_id, sender_id, created_at, created_at),
            )
            conversation_id = cur.lastrowid

        cur.execute(
            "UPDATE messages SET conversation_id = ? WHERE id = ?",
            (conversation_id, message_id),
        )


# -------------------------------------------------
# User/account helpers
# -------------------------------------------------
def create_user(username: str, password: str) -> tuple[bool, str]:
    """Create a new user account."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
            (username.strip(), password.strip(), datetime.now().isoformat()),
        )
        conn.commit()
        return True, "Account created successfully."
    except sqlite3.IntegrityError:
        return False, "That username already exists."
    finally:
        if conn is not None:
            conn.close()


def authenticate_user(username: str, password: str) -> tuple[bool, int | None]:
    """Check if a username/password pair exists."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM users WHERE username = ? AND password = ?",
        (username.strip(), password.strip()),
    )
    row = cur.fetchone()
    conn.close()

    if row:
        return True, row[0]
    return False, None


def get_username_by_id(user_id: int) -> str:
    """Return a username from a user id."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Unknown"


def get_all_users_except(user_id: int) -> list[tuple[int, str]]:
    """Return all users except the currently logged-in user."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username FROM users WHERE id != ? ORDER BY username COLLATE NOCASE",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# -------------------------------------------------
# Item/listing helpers
# -------------------------------------------------
def save_item(
    user_id: int,
    title: str,
    category: str,
    condition: str,
    description: str,
    estimated_value: float,
    desired_trade_value: float,
    photo_path: str,
) -> None:
    """Save a new barter listing."""
    params = (
        user_id,
        title.strip(),
        category.strip(),
        condition.strip(),
        description.strip(),
        estimated_value,
        desired_trade_value,
        photo_path.strip(),
        datetime.now().isoformat(),
    )

    last_error = None
    for attempt in range(3):
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO items (
                    user_id, title, category, condition, description,
                    estimated_value, desired_trade_value, photo_path, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                params,
            )
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.35)
        finally:
            conn.close()

    if last_error is not None:
        raise last_error


def get_user_items(user_id: int) -> list[tuple]:
    """Return all listings for a specific user."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, category, condition, description,
               estimated_value, desired_trade_value, photo_path, created_at, status
        FROM items
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_other_items(user_id: int) -> list[tuple]:
    """Return all listings from other users."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
               i.description, i.estimated_value, i.desired_trade_value,
               i.photo_path, i.created_at, i.status
        FROM items i
        JOIN users u ON i.user_id = u.id
        WHERE i.user_id != ?
          AND i.status = 'active'
        ORDER BY i.id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_other_items_by_category(user_id: int, category: str) -> list[tuple]:
    """Return filtered listings from other users by category."""
    conn = get_db_connection()
    cur = conn.cursor()

    if category == "All":
        cur.execute(
            """
            SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
                   i.description, i.estimated_value, i.desired_trade_value,
                   i.photo_path, i.created_at, i.status
            FROM items i
            JOIN users u ON i.user_id = u.id
            WHERE i.user_id != ?
              AND i.status = 'active'
            ORDER BY i.id DESC
            """,
            (user_id,),
        )
    else:
        cur.execute(
            """
            SELECT i.id, i.user_id, u.username, i.title, i.category, i.condition,
                   i.description, i.estimated_value, i.desired_trade_value,
                   i.photo_path, i.created_at, i.status
            FROM items i
            JOIN users u ON i.user_id = u.id
            WHERE i.user_id != ? AND i.category = ? AND i.status = 'active'
            ORDER BY i.id DESC
            """,
            (user_id, category),
        )

    rows = cur.fetchall()
    conn.close()
    return rows


def normalize_listing_status(status: str) -> str:
    """Keep listing status values constrained to the supported state set."""
    cleaned = (status or "").strip().lower()
    return cleaned if cleaned in LISTING_STATUSES else "active"


def update_item_status(user_id: int, item_id: int, status: str) -> bool:
    """Update a listing status for the owning user."""
    next_status = normalize_listing_status(status)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE items
        SET status = ?
        WHERE id = ? AND user_id = ?
        """,
        (next_status, item_id, user_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# -------------------------------------------------
# Message helpers
# -------------------------------------------------
def get_or_create_conversation(
    cur: sqlite3.Cursor,
    sender_id: int,
    receiver_id: int,
) -> tuple[int, str, int]:
    """Return a conversation id/status pair, creating a new request thread when needed."""
    user_one_id, user_two_id = normalize_pair(sender_id, receiver_id)
    cur.execute(
        """
        SELECT id, status, requested_by
        FROM conversations
        WHERE user_one_id = ? AND user_two_id = ?
        """,
        (user_one_id, user_two_id),
    )
    row = cur.fetchone()
    now = datetime.now().isoformat()

    if row is None:
        cur.execute(
            """
            INSERT INTO conversations (
                user_one_id, user_two_id, requested_by, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (user_one_id, user_two_id, sender_id, now, now),
        )
        return cur.lastrowid, "pending", sender_id

    conversation_id, status, requested_by = row
    if status == "declined":
        cur.execute(
            """
            UPDATE conversations
            SET status = 'pending', requested_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (sender_id, now, conversation_id),
        )
        return conversation_id, "pending", sender_id

    return conversation_id, status, requested_by


def send_message(
    sender_id: int,
    receiver_id: int,
    subject: str,
    body: str,
    item_id: int | None = None,
    conversation_id: int | None = None,
) -> tuple[int, str]:
    """Save a message and return its conversation id plus conversation status."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now().isoformat()

    if conversation_id is None:
        conversation_id, status, _requested_by = get_or_create_conversation(cur, sender_id, receiver_id)
    else:
        cur.execute(
            """
            SELECT status
            FROM conversations
            WHERE id = ? AND (user_one_id = ? OR user_two_id = ?) AND (user_one_id = ? OR user_two_id = ?)
            """,
            (conversation_id, sender_id, sender_id, receiver_id, receiver_id),
        )
        row = cur.fetchone()
        if row is None:
            conn.close()
            raise ValueError("Conversation not found.")
        status = row[0]

    cur.execute(
        """
        INSERT INTO messages (
            sender_id, receiver_id, subject, body, item_id, created_at, is_read, conversation_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            sender_id,
            receiver_id,
            subject.strip() or "Trade Message",
            body.strip(),
            item_id,
            now,
            conversation_id,
        ),
    )
    cur.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()
    conn.close()
    return conversation_id, status


def get_message_requests(user_id: int) -> list[tuple]:
    """Return pending conversation requests awaiting this user's approval."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id,
               u.username,
               m.subject,
               m.body,
               m.created_at,
               c.requested_by
        FROM conversations c
        JOIN messages m ON m.id = (
            SELECT MAX(id) FROM messages WHERE conversation_id = c.id
        )
        JOIN users u ON u.id = c.requested_by
        WHERE c.status = 'pending'
          AND c.requested_by != ?
          AND (c.user_one_id = ? OR c.user_two_id = ?)
        ORDER BY c.updated_at DESC
        """,
        (user_id, user_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_visible_conversations(user_id: int) -> list[tuple]:
    """Return active chats and outgoing pending requests for the user."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id,
               CASE
                   WHEN c.user_one_id = ? THEN u2.username
                   ELSE u1.username
               END AS other_username,
               m.subject,
               m.body,
               m.created_at,
               c.status,
               (
                   SELECT COUNT(*)
                   FROM messages unread
                   WHERE unread.conversation_id = c.id
                     AND unread.receiver_id = ?
                     AND unread.is_read = 0
               ) AS unread_count
        FROM conversations c
        JOIN users u1 ON u1.id = c.user_one_id
        JOIN users u2 ON u2.id = c.user_two_id
        JOIN messages m ON m.id = (
            SELECT MAX(id) FROM messages WHERE conversation_id = c.id
        )
        WHERE (c.user_one_id = ? OR c.user_two_id = ?)
          AND (c.status = 'active' OR (c.status = 'pending' AND c.requested_by = ?))
        ORDER BY c.updated_at DESC
        """,
        (user_id, user_id, user_id, user_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_conversation_messages(user_id: int, conversation_id: int) -> tuple[tuple | None, list[tuple]]:
    """Return conversation metadata plus its messages if the user is a participant."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id,
               c.status,
               c.requested_by,
               c.user_one_id,
               c.user_two_id,
               CASE
                   WHEN c.user_one_id = ? THEN u2.username
                   ELSE u1.username
               END AS other_username
        FROM conversations c
        JOIN users u1 ON u1.id = c.user_one_id
        JOIN users u2 ON u2.id = c.user_two_id
        WHERE c.id = ?
          AND (c.user_one_id = ? OR c.user_two_id = ?)
        """,
        (user_id, conversation_id, user_id, user_id),
    )
    meta = cur.fetchone()
    if meta is None:
        conn.close()
        return None, []

    cur.execute(
        """
        SELECT m.id, m.sender_id, u.username, m.subject, m.body, m.created_at, m.is_read
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.conversation_id = ?
        ORDER BY m.id ASC
        """,
        (conversation_id,),
    )
    messages = cur.fetchall()
    conn.close()
    return meta, messages


def mark_conversation_read(user_id: int, conversation_id: int) -> None:
    """Mark all received messages in a conversation as read."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE messages
        SET is_read = 1
        WHERE conversation_id = ? AND receiver_id = ?
        """,
        (conversation_id, user_id),
    )
    conn.commit()
    conn.close()


def approve_message_request(user_id: int, conversation_id: int) -> bool:
    """Approve a pending message request."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE conversations
        SET status = 'active', updated_at = ?
        WHERE id = ?
          AND status = 'pending'
          AND requested_by != ?
          AND (user_one_id = ? OR user_two_id = ?)
        """,
        (datetime.now().isoformat(), conversation_id, user_id, user_id, user_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def decline_message_request(user_id: int, conversation_id: int) -> bool:
    """Decline a pending message request."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE conversations
        SET status = 'declined', updated_at = ?
        WHERE id = ?
          AND status = 'pending'
          AND requested_by != ?
          AND (user_one_id = ? OR user_two_id = ?)
        """,
        (datetime.now().isoformat(), conversation_id, user_id, user_id, user_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# -------------------------------------------------
# Trade scoring / matching
# -------------------------------------------------
def fairness_score(my_value: float, target_value: float) -> tuple[str, float]:
    """Compare two item values and return a trade balance label."""
    if my_value <= 0 or target_value <= 0:
        return "Unknown", 0.0

    ratio = min(my_value, target_value) / max(my_value, target_value)
    score = round(ratio * 100, 1)

    if score >= 90:
        label = "Excellent trade balance"
    elif score >= 75:
        label = "Good trade balance"
    elif score >= 60:
        label = "Possible trade gap"
    else:
        label = "Unbalanced trade"

    return label, score


def extract_match_keywords(*parts: str) -> set[str]:
    """Build a small keyword set from listing text for lightweight match scoring."""
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "your", "item",
        "trade", "want", "have", "just", "into", "about", "used", "like",
        "very", "good", "fair", "new", "other", "only", "will", "would",
        "can", "you", "are", "but", "not", "all", "any", "too", "one",
    }
    keywords: set[str] = set()
    for part in parts:
        for token in re.findall(r"[a-z0-9]+", (part or "").lower()):
            if len(token) >= 3 and token not in stop_words:
                keywords.add(token)
    return keywords


def describe_condition_gap(source_condition: str, target_condition: str) -> str | None:
    """Explain whether listing conditions feel closely aligned."""
    condition_rank = {
        "New": 5,
        "Like New": 4,
        "Good": 3,
        "Used": 2,
        "Fair": 1,
    }
    source_rank = condition_rank.get(source_condition, 0)
    target_rank = condition_rank.get(target_condition, 0)
    if source_rank == 0 or target_rank == 0:
        return None

    gap = abs(source_rank - target_rank)
    if gap == 0:
        return f"Condition lines up well at {target_condition.lower()}."
    if gap == 1:
        return f"Condition is still close to your {source_condition.lower()} listing."
    return None


def build_trade_match_candidates(user_id: int, my_item: tuple, limit: int = 3) -> list[dict]:
    """Rank other listings that look like strong barter candidates for one user item."""
    (
        my_item_id,
        my_title,
        my_category,
        my_condition,
        my_description,
        my_estimated_value,
        my_desired_trade_value,
        _my_photo_path,
        _my_created_at,
        _my_status,
    ) = my_item
    candidates = get_all_other_items(user_id)
    my_keywords = extract_match_keywords(my_title, my_description, my_category)
    scored_matches: list[dict] = []

    for candidate in candidates:
        (
            candidate_id,
            owner_id,
            owner_username,
            title,
            category,
            condition,
            description,
            estimated_value,
            desired_trade_value,
            photo_path,
            created_at,
            _status,
        ) = candidate

        reasons: list[str] = []
        total_score = 0.0

        receive_label, receive_score = fairness_score(my_desired_trade_value, estimated_value)
        give_label, give_score = fairness_score(my_estimated_value, desired_trade_value)
        average_trade_score = round((receive_score + give_score) / 2, 1)
        total_score += average_trade_score * 0.45

        if average_trade_score >= 88:
            reasons.append("Value expectations are closely aligned on both sides.")
        elif average_trade_score >= 72:
            reasons.append("Trade value looks reasonably close for a swap.")

        if category == my_category:
            total_score += 24
            reasons.append(f"Same category match in {category.lower()}.")
        elif my_category != "Other" and category != "Other":
            total_score += 8
            reasons.append("Different category, but still a possible cross-trade.")

        condition_reason = describe_condition_gap(my_condition, condition)
        if condition_reason is not None:
            total_score += 10
            reasons.append(condition_reason)

        candidate_keywords = extract_match_keywords(title, description, category)
        shared_keywords = sorted(my_keywords & candidate_keywords)
        if shared_keywords:
            keyword_boost = min(18, len(shared_keywords) * 6)
            total_score += keyword_boost
            reasons.append(
                "Shared interest terms: " + ", ".join(shared_keywords[:3]) + "."
            )

        total_score += max(0, 12 - min(12, abs(my_estimated_value - estimated_value) / 20))

        if not reasons:
            reasons.append("Overall listing details still suggest a possible barter fit.")

        summary = f"{receive_label} on what you would receive; {give_label.lower()} on what they are seeking."
        scored_matches.append(
            {
                "candidate_id": candidate_id,
                "owner_id": owner_id,
                "owner_username": owner_username,
                "title": title,
                "category": category,
                "condition": condition,
                "description": description or "No description provided.",
                "estimated_value": estimated_value,
                "desired_trade_value": desired_trade_value,
                "photo_path": photo_path,
                "created_at": created_at,
                "match_score": round(min(total_score, 99.0), 1),
                "value_score": average_trade_score,
                "summary": summary,
                "reasons": reasons[:3],
            }
        )

    scored_matches.sort(
        key=lambda match: (match["match_score"], match["value_score"], match["estimated_value"]),
        reverse=True,
    )
    return scored_matches[:limit]


def store_uploaded_photo(photo_path: str) -> str:
    """Copy an uploaded image into the app uploads folder and return the saved path."""
    if not photo_path:
        return ""

    if not os.path.exists(photo_path):
        return photo_path

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    original_name = os.path.basename(photo_path)
    _, extension = os.path.splitext(original_name)
    safe_extension = extension or ".png"
    saved_name = f"{uuid.uuid4().hex}{safe_extension}"
    saved_path = os.path.join(UPLOADS_DIR, saved_name)
    shutil.copy2(photo_path, saved_path)
    return saved_path


def format_timestamp(timestamp_text: str) -> str:
    """Render stored ISO timestamps in a friendlier inbox format."""
    return timestamp_text[:16].replace("T", " ")


# -------------------------------------------------
# Main UI app
# -------------------------------------------------
class SilkRouteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(1120, 760)
        self.configure(bg=BG_MAIN)

        # Current session state
        self.current_user_id: int | None = None
        self.current_username: str = ""
        self.selected_photo_path: str = ""
        self.logo_photo = None
        self.preview_photo = None
        self.browse_image_refs: list = []
        self.profile_image_refs: list = []
        self.background_canvas = None
        self.current_conversation_id: int | None = None
        self.request_lookup: list[int] = []
        self.conversation_lookup: list[int] = []
        self.profile_status_lookup: dict[str, tuple[int, str]] = {}
        self.user_agreement_acknowledged = False
        self.interaction_locked = False
        self.lockable_widgets: list[tk.Widget] = []
        self.agreement_shine_job: str | None = None
        self.agreement_shine_on = False
        self.agreement_button: tk.Button | None = None

        # ttk styling
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure_styles()

        # Main screen container
        self.main_container = tk.Frame(self, bg=BG_MAIN)
        self.main_container.pack(fill="both", expand=True)
        self.bind("<Configure>", self.on_window_resize)

        # Start on login screen
        self.show_login_screen()

    # -------------------------------------------------
    # Styling
    # -------------------------------------------------
    def configure_styles(self) -> None:
        """Set global ttk styles for the neon theme."""
        self.style.configure("TFrame", background=BG_MAIN)

        self.style.configure(
            "Header.TLabel",
            background=BG_MAIN,
            foreground=TEXT_MAIN,
            font=("Bahnschrift SemiBold", 34, "italic"),
        )

        self.style.configure(
            "SubHeader.TLabel",
            background=BG_MAIN,
            foreground=TEXT_SOFT,
            font=("Bahnschrift SemiBold", 12),
        )

        self.style.configure(
            "Accent.TButton",
            background=BG_EDGE,
            foreground=NEON_BLUE,
            font=("Bahnschrift SemiBold", 12, "italic"),
            padding=12,
            borderwidth=2,
            relief="flat",
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", NEON_PINK), ("pressed", NEON_PURPLE)],
            foreground=[("active", BG_MAIN), ("pressed", BG_MAIN)],
        )

        self.style.configure(
            "Secondary.TButton",
            background=BG_EDGE,
            foreground=NEON_GOLD,
            font=("Bahnschrift SemiBold", 11, "italic"),
            padding=10,
            borderwidth=2,
            relief="flat",
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", NEON_BLUE), ("pressed", NEON_PINK)],
            foreground=[("active", BG_MAIN), ("pressed", BG_MAIN)],
        )

        self.style.configure(
            "TEntry",
            fieldbackground=BG_INPUT,
            foreground=TEXT_MAIN,
            insertcolor=NEON_BLUE,
            padding=8,
            font=("Bahnschrift SemiBold", 10),
        )

        self.style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BG_INPUT,
            foreground=TEXT_MAIN,
            selectbackground=BG_INPUT,
            selectforeground=TEXT_MAIN,
            arrowcolor=NEON_BLUE,
            padding=6,
            font=("Bahnschrift SemiBold", 10),
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INPUT), ("disabled", BG_INPUT)],
            background=[("readonly", BG_INPUT), ("disabled", BG_INPUT)],
            foreground=[("readonly", TEXT_MAIN), ("disabled", TEXT_MUTED)],
            selectbackground=[("readonly", BG_EDGE)],
            selectforeground=[("readonly", TEXT_MAIN)],
            arrowcolor=[("readonly", NEON_BLUE), ("active", NEON_PINK)],
        )
        self.option_add("*TCombobox*Listbox.background", BG_INPUT)
        self.option_add("*TCombobox*Listbox.foreground", TEXT_MAIN)
        self.option_add("*TCombobox*Listbox.selectBackground", BG_EDGE)
        self.option_add("*TCombobox*Listbox.selectForeground", NEON_BLUE)

    # -------------------------------------------------
    # Utility helpers
    # -------------------------------------------------
    def clear_screen(self) -> None:
        """Remove all widgets from the current screen."""
        self.stop_agreement_shine()
        self.lockable_widgets = []
        self.agreement_button = None
        for widget in self.main_container.winfo_children():
            widget.destroy()
        self.background_canvas = None

    def on_window_resize(self, event) -> None:
        """Redraw the interface backdrop when the window size changes."""
        if event.widget == self and self.background_canvas is not None:
            self.draw_background_scene()

    def create_background_scene(self) -> None:
        """Create a reusable cyberpunk background behind each screen."""
        self.background_canvas = tk.Canvas(
            self.main_container,
            bg=BG_MAIN,
            highlightthickness=0,
            bd=0,
        )
        self.background_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.draw_background_scene()

    def draw_background_scene(self) -> None:
        """Paint a poster-like neon backdrop inspired by the visual references."""
        if self.background_canvas is None:
            return

        canvas = self.background_canvas
        width = max(self.main_container.winfo_width(), 1240)
        height = max(self.main_container.winfo_height(), 820)
        canvas.delete("all")

        canvas.create_rectangle(0, 0, width, height, fill=BG_MAIN, outline="")
        canvas.create_rectangle(0, 0, width, 88, fill="#0a0b24", outline="")
        for y_pos, shade in ((16, "#1722a3"), (32, "#2028b8"), (48, "#1f2b9d")):
            canvas.create_line(0, y_pos, width, y_pos, fill=shade, width=3)

        horizon = 68
        vanishing_x = width * 0.5
        grid_color = "#4a1f66"
        for y_step in range(11):
            ratio = (y_step / 10) ** 2
            y_pos = horizon + ratio * (height - horizon)
            canvas.create_line(0, y_pos, width, y_pos, fill=grid_color, width=1)
        for x_pos in range(-260, width + 260, 70):
            canvas.create_line(x_pos, horizon, vanishing_x + (x_pos - vanishing_x) * 1.9, height, fill=grid_color, width=1)

        for x1, y1, x2, y2, color in (
            (94, 56, width - 110, 56, NEON_PINK),
            (94, 56, 94, min(height - 92, 420), NEON_PINK),
            (94, min(height - 92, 420), width - 110, min(height - 92, 420), NEON_PINK),
            (width - 110, 56, width - 110, min(height - 92, 420), NEON_PINK),
        ):
            canvas.create_line(x1, y1, x2, y2, fill=color, width=4)
            canvas.create_line(x1, y1, x2, y2, fill="#ffc0c7", width=10, stipple="gray25")

        canvas.create_rectangle(86, 18, width * 0.58, 124, outline=NEON_PURPLE, width=2)
        wave_points_one = [
            (96, 96), (136, 96), (192, 82), (238, 100), (284, 54), (340, 88), (392, 44),
            (448, 62), (506, 48), (562, 90), (618, 60), (676, 102), (740, 92), (802, 86),
        ]
        wave_points_two = [
            (96, 76), (154, 78), (210, 68), (262, 90), (314, 42), (374, 60), (432, 52),
            (488, 84), (548, 64), (604, 42), (664, 74), (726, 60), (788, 108), (848, 112),
        ]
        canvas.create_line(*[coord for point in wave_points_one for coord in point], fill=NEON_BLUE, width=3, smooth=True)
        canvas.create_line(*[coord for point in wave_points_two for coord in point], fill="#8aa2ff", width=3, smooth=True)

        for cx, cy, radius, color, stipple in (
            (40, 120, 64, "#ff37a8", "gray25"),
            (width - 64, 120, 56, "#ff37a8", "gray25"),
            (width - 80, height - 130, 72, "#ff37a8", "gray25"),
            (vanishing_x, height - 86, 180, "#7c1dc9", "gray50"),
            (160, height - 52, 120, "#55f3ff", "gray50"),
            (width * 0.68, 210, 160, "#2730ff", "gray50"),
        ):
            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=color, outline="", stipple=stipple)

        for x_pos, y_pos, radius in (
            (70, 196, 18),
            (width - 110, 184, 22),
            (width - 150, 432, 18),
            (86, height - 74, 16),
        ):
            canvas.create_oval(x_pos - radius, y_pos - radius, x_pos + radius, y_pos + radius, fill="#d12aa9", outline="")

        x_center, x_top, x_bottom = 232, 140, 324
        for offset, line_width, color in ((0, 7, "#59f3ff"), (3, 13, "#59f3ff"), (-3, 13, "#59f3ff")):
            canvas.create_line(x_top, 76 + offset, x_bottom, 274 + offset, fill=color, width=line_width, stipple="gray50")
            canvas.create_line(x_bottom, 76 + offset, x_top, 274 + offset, fill=color, width=line_width, stipple="gray50")
        canvas.create_line(x_top, 76, x_bottom, 274, fill=NEON_BLUE, width=6)
        canvas.create_line(x_bottom, 76, x_top, 274, fill=NEON_BLUE, width=6)

        dot_origin_x = 196
        dot_origin_y = 148
        for row in range(15):
            for col in range(15):
                distance = abs(row - 7) + abs(col - 7)
                if distance > 10:
                    continue
                size = max(2, 7 - distance // 2)
                gap = 14
                x_pos = dot_origin_x + col * gap
                y_pos = dot_origin_y + row * gap
                canvas.create_oval(x_pos, y_pos, x_pos + size, y_pos + size, fill="#f390ff", outline="")

        for center_x, center_y, size, color in (
            (102, 30, 26, NEON_GREEN),
            (width * 0.67, 278, 34, NEON_BLUE),
        ):
            for scale in (1.0, 0.68):
                half = size * scale
                points = [
                    center_x, center_y - half,
                    center_x + half, center_y,
                    center_x, center_y + half,
                    center_x - half, center_y,
                ]
                canvas.create_polygon(points, outline=color, fill="", width=4)

        canvas.create_text(
            width * 0.5,
            min(height - 42, 454),
            text="ONLINE BARTER SYSTEM",
            fill=TEXT_MAIN,
            font=("Bahnschrift SemiBold", 22, "italic"),
        )


    def logout(self) -> None:
        """Clear session state and return to login."""
        self.current_user_id = None
        self.current_username = ""
        self.selected_photo_path = ""
        self.preview_photo = None
        self.current_conversation_id = None
        self.user_agreement_acknowledged = False
        self.interaction_locked = False
        self.show_login_screen()

    def register_lockable_widget(self, widget: tk.Widget) -> None:
        """Track widgets that should be disabled until the agreement is acknowledged."""
        self.lockable_widgets.append(widget)
        if self.interaction_locked and widget.winfo_exists():
            try:
                widget.configure(state="disabled")
            except tk.TclError:
                pass

    def set_interaction_lock(self, locked: bool) -> None:
        """Temporarily disable app actions until the user acknowledges the agreement."""
        self.interaction_locked = locked
        state = "disabled" if locked else "normal"
        for widget in list(self.lockable_widgets):
            if not widget.winfo_exists():
                continue
            try:
                widget.configure(state=state)
            except tk.TclError:
                continue

    def stop_agreement_shine(self) -> None:
        """Cancel the minimize-button shine loop if it is active."""
        if self.agreement_shine_job is not None:
            self.after_cancel(self.agreement_shine_job)
            self.agreement_shine_job = None

    def animate_agreement_button(self) -> None:
        """Pulse the agreement minimize button until it is acknowledged."""
        if self.agreement_button is None or not self.agreement_button.winfo_exists():
            self.agreement_button = None
            self.agreement_shine_job = None
            return

        self.agreement_shine_on = not self.agreement_shine_on
        if self.agreement_shine_on:
            self.agreement_button.configure(
                bg=NEON_GOLD,
                fg=BG_MAIN,
                activebackground=NEON_PINK,
                activeforeground=BG_MAIN,
                highlightbackground=NEON_GOLD,
            )
        else:
            self.agreement_button.configure(
                bg=BG_EDGE,
                fg=NEON_GOLD,
                activebackground=NEON_GOLD,
                activeforeground=BG_MAIN,
                highlightbackground=NEON_GOLD,
            )

        self.agreement_shine_job = self.after(550, self.animate_agreement_button)

    def build_topbar(self, title_text: str, back_command=None) -> tk.Frame:
        """Reusable neon top bar for app sections."""
        topbar = tk.Frame(
            self.main_container,
            bg=BG_EDGE,
            highlightbackground=NEON_PINK,
            highlightthickness=1,
        )
        topbar.pack(fill="x", padx=26, pady=(18, 10))

        inner_bar = tk.Frame(topbar, bg=BG_EDGE)
        inner_bar.pack(fill="x", padx=14, pady=12)

        left_side = tk.Frame(inner_bar, bg=BG_EDGE)
        left_side.pack(side="left")

        if back_command is not None:
            back_button = ttk.Button(
                left_side,
                text="< Back",
                command=back_command,
                style="Secondary.TButton",
            )
            back_button.pack(side="left", padx=(0, 8))
            self.register_lockable_widget(back_button)

        tk.Label(
            left_side,
            text=title_text,
            bg=BG_EDGE,
            fg=NEON_BLUE,
            font=("Bahnschrift SemiBold", 24, "italic"),
        ).pack(side="left")

        right_side = tk.Frame(inner_bar, bg=BG_EDGE)
        right_side.pack(side="right")

        if self.current_username:
            tk.Label(
                right_side,
                text=f"USER: {self.current_username}",
                bg=BG_EDGE,
                fg=NEON_GOLD,
                font=("Bahnschrift SemiBold", 12, "italic"),
            ).pack(side="left", padx=(0, 12))

        logout_button = ttk.Button(
            right_side,
            text="Logout",
            command=self.logout,
            style="Secondary.TButton",
        )
        logout_button.pack(side="left")
        self.register_lockable_widget(logout_button)

        tk.Frame(topbar, bg=NEON_PINK, height=3).pack(fill="x", side="bottom")

        return topbar

    def add_quick_scan_button(self) -> None:
        """Add a floating quick scan button for image-based value suggestions."""
        if self.current_user_id is None:
            return

        button = tk.Button(
            self.main_container,
            text="Price Search",
            command=self.open_trade_widget,
            bg=NEON_PINK,
            fg=BG_MAIN,
            activebackground=NEON_BLUE,
            activeforeground=BG_MAIN,
            font=("Bahnschrift SemiBold", 11, "italic"),
            relief="raised",
            bd=2,
            highlightbackground=NEON_PINK,
            highlightcolor=NEON_PINK,
            highlightthickness=2,
            cursor="hand2",
            padx=14,
            pady=8,
        )
        button.place(relx=0.955, rely=0.92, anchor="se")
        self.register_lockable_widget(button)

    def autofill_estimated_value(self, suggestion: str) -> None:
        """Fill the estimated value entry using the midpoint of a price range."""
        if not hasattr(self, "estimated_entry") or not self.estimated_entry.winfo_exists():
            return

        match = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*-\s*\$?\s*([\d,]+(?:\.\d+)?)", suggestion)
        if not match:
            return

        low = float(match.group(1).replace(",", ""))
        high = float(match.group(2).replace(",", ""))
        self.estimated_entry.delete(0, "end")
        self.estimated_entry.insert(0, f"{(low + high) / 2:.2f}")

    def extract_search_sources(self, response) -> list[tuple[str, str]]:
        """Pull source titles and URLs from an OpenAI web search response."""
        sources: list[tuple[str, str]] = []

        try:
            payload = response.model_dump()
        except Exception:
            return sources

        for item in payload.get("output", []):
            if item.get("type") == "web_search_call":
                action = item.get("action") or {}
                for source in action.get("sources", []) or []:
                    title = source.get("title") or source.get("url") or "Source"
                    url = source.get("url")
                    if url and (title, url) not in sources:
                        sources.append((title, url))

        return sources[:3]

    def render_source_links(self, parent: tk.Widget, sources: list[tuple[str, str]]) -> None:
        """Render clickable source links inside a container."""
        for widget in parent.winfo_children():
            widget.destroy()

        if not sources:
            return

        tk.Label(
            parent,
            text="Sources",
            bg=parent.cget("bg"),
            fg=NEON_GOLD,
            font=("Consolas", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        for title, url in sources:
            link = tk.Label(
                parent,
                text=title,
                bg=parent.cget("bg"),
                fg=NEON_BLUE,
                cursor="hand2",
                font=("Consolas", 9, "underline"),
                wraplength=340,
                justify="left",
            )
            link.pack(anchor="w")
            link.bind("<Button-1>", lambda event, target=url: webbrowser.open(target))

    def get_live_pricing_status(self) -> str:
        """Describe how the price search works for the current session."""
        return "Live market search opens current web results in your browser."

    def build_market_search_sources(self, search_term: str) -> list[tuple[str, str]]:
        """Build browser search links for current item pricing."""
        query = quote_plus(f"{search_term.strip()} new price")
        return [
            ("Google Shopping", f"https://www.google.com/search?tbm=shop&q={query}"),
            ("Google Search", f"https://www.google.com/search?q={query}"),
            ("eBay Search", f"https://www.ebay.com/sch/i.html?_nkw={query}"),
        ]

    def open_market_search(self, search_term: str) -> list[tuple[str, str]]:
        """Open a browser search for current item pricing and return the search links."""
        sources = self.build_market_search_sources(search_term)
        if sources:
            webbrowser.open(sources[0][1])
        return sources

    def lookup_market_value_from_backend(self, search_term: str) -> tuple[str, str, list[tuple[str, str]]] | None:
        """Call the pricing backend so the desktop app never needs the OpenAI secret."""
        if not self.has_backend_pricing() or not search_term.strip():
            return None

        payload = json.dumps({"query": search_term.strip()}).encode("utf-8")
        request = urllib_request.Request(
            AI_BACKEND_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
                payload = json.loads(raw)
                error_message = payload.get("error") or payload.get("detail")
            except Exception:
                error_message = None
            return None if error_message is None else ("Unavailable", f"Pricing backend error: {error_message}", [])
        except Exception:
            return None

        try:
            payload = json.loads(raw)
            price_range = payload.get("price_range")
            summary = payload.get("summary")
            sources = payload.get("sources") or []
        except Exception:
            return None

        if not price_range or not summary:
            return None

        normalized_sources: list[tuple[str, str]] = []
        for source in sources:
            if isinstance(source, dict):
                title = source.get("title") or source.get("url") or "Source"
                url = source.get("url")
                if url:
                    normalized_sources.append((title, url))

        return price_range, summary, normalized_sources[:3]

    def lookup_market_value(self, search_term: str) -> tuple[str, str, list[tuple[str, str]]]:
        """Open a live browser search for a text query and return the search links."""
        sources = self.open_market_search(search_term)
        return (
            "Browser search opened",
            "Opened Google Shopping for this item. Use the links below to compare current listings and prices.",
            sources,
        )

    def lookup_market_value_with_ai(self, search_term: str) -> tuple[str, str, list[tuple[str, str]]] | None:
        """Use OpenAI web search to estimate the current new retail price for a search term."""
        if (
            not OPENAI_API_KEY
            or OPENAI_API_KEY == OPENAI_API_KEY_PLACEHOLDER
            or OpenAI is None
            or not search_term.strip()
        ):
            return None

        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.responses.create(
                model=OPENAI_LOOKUP_MODEL,
                tools=[
                    {
                        "type": "web_search",
                        "search_context_size": "medium",
                        "user_location": {
                            "type": "approximate",
                            "country": "US",
                        },
                    }
                ],
                include=["web_search_call.action.sources"],
                instructions=(
                    "Use live web search results to estimate the current United States new retail price range. "
                    "Prefer current manufacturer pages and major retailers. Avoid resale, auction, and used-item pricing "
                    "unless new retail pricing cannot be found."
                ),
                input=(
                    f"Find the current typical new retail price in the United States for '{search_term}'. "
                    "Use current web results in real time and synthesize a reasonable new-price range from the latest available listings. "
                    "Reply in plain text with two lines only. "
                    "Line 1 must be: PRICE_RANGE: $X - $Y "
                    "Line 2 must be: SUMMARY: <short explanation mentioning the retailers or sources used>."
                ),
            )

            raw_text = getattr(response, "output_text", "").strip()
            if not raw_text:
                return None

            match = re.search(r"PRICE_RANGE:\s*\$?\s*([\d,]+(?:\.\d+)?)\s*-\s*\$?\s*([\d,]+(?:\.\d+)?)", raw_text, re.IGNORECASE)
            summary_match = re.search(r"SUMMARY:\s*(.+)", raw_text, re.IGNORECASE | re.DOTALL)
            if not match:
                return None

            low = float(match.group(1).replace(",", ""))
            high = float(match.group(2).replace(",", ""))
            summary = summary_match.group(1).strip() if summary_match else raw_text
            summary = f"Live AI pricing: {summary}"
            return f"${low:.0f} - ${high:.0f}", summary, self.extract_search_sources(response)
        except Exception:
            return None

    def open_trade_widget(self) -> None:
        """Open a popup widget for text-based AI price lookups."""
        popup = tk.Toplevel(self)
        popup.title("Market Price Search")
        popup.geometry("420x435")
        popup.resizable(False, False)
        popup.configure(bg=BG_PANEL)
        popup.transient(self)

        tk.Label(
            popup,
            text="MARKET PRICE SEARCH",
            bg=BG_PANEL,
            fg=NEON_BLUE,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="center", pady=(18, 8))

        tk.Label(
            popup,
            text="Type an item name to search current new retail prices.",
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            font=("Consolas", 10),
            wraplength=320,
            justify="center",
        ).pack(pady=(0, 14))

        search_var = tk.StringVar()
        ttk.Entry(popup, textvariable=search_var, width=34).pack(fill="x", padx=24, pady=(0, 12))

        status_var = tk.StringVar(value=self.get_live_pricing_status())
        result_var = tk.StringVar(value="Search results will open in your browser.")
        detail_var = tk.StringVar(value="")
        sources_frame = tk.Frame(popup, bg=BG_PANEL)
        sources_frame.pack(fill="x", padx=24, pady=(8, 0))

        def run_lookup() -> None:
            term = search_var.get().strip()
            if not term:
                messagebox.showwarning("Missing search", "Type an item name to search.")
                return

            result_var.set("Searching current prices...")
            detail_var.set("Opening current web search results for pricing...")
            popup.update_idletasks()

            suggestion, details, sources = self.lookup_market_value(term)
            result_var.set(suggestion)
            detail_var.set(details)
            self.render_source_links(sources_frame, sources)

        tk.Label(
            popup,
            textvariable=status_var,
            bg=BG_PANEL,
            fg=NEON_GOLD,
            font=("Consolas", 9, "bold"),
            wraplength=340,
            justify="center",
        ).pack(padx=20, pady=(0, 10))

        ttk.Button(
            popup,
            text="Open Price Search",
            command=run_lookup,
            style="Accent.TButton",
        ).pack(fill="x", padx=24, pady=(0, 12))

        tk.Label(
            popup,
            textvariable=result_var,
            bg=BG_PANEL,
            fg=NEON_GREEN,
            font=("Consolas", 11, "bold"),
            wraplength=340,
            justify="center",
        ).pack(padx=20, pady=(0, 8))

        tk.Label(
            popup,
            textvariable=detail_var,
            bg=BG_PANEL,
            fg=TEXT_MUTED,
            font=("Consolas", 9),
            wraplength=340,
            justify="center",
        ).pack(padx=20, pady=(0, 10))

        ttk.Button(
            popup,
            text="Close",
            command=popup.destroy,
            style="Secondary.TButton",
        ).pack(fill="x", padx=24, pady=(12, 0))

    def load_logo_widget(self, parent: tk.Widget, size: tuple[int, int] = (220, 220)) -> tk.Label:
        """Load the app logo image if present, otherwise show text."""
        label = tk.Label(parent, bg=BG_MAIN)

        if Image and os.path.exists(LOGO_PATH):
            image = Image.open(LOGO_PATH)
            image = image.resize(size)
            self.logo_photo = ImageTk.PhotoImage(image)
            label.configure(image=self.logo_photo, bd=0, highlightthickness=0)
        else:
            label.configure(
                text="CYBER\nXCHANGE",
                fg=TEXT_MAIN,
                bg=BG_MAIN,
                font=("Bahnschrift SemiBold", 26, "italic"),
                justify="center",
            )
        return label

    def load_text_thumbnail(self, image_path: str, size: tuple[int, int] = (220, 220)):
        """Load a thumbnail that can be embedded in a Text widget."""
        if not (Image and ImageTk and image_path and os.path.exists(image_path)):
            return None

        try:
            image = Image.open(image_path)
            image.thumbnail(size)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def make_card_button(
        self,
        parent: tk.Widget,
        title: str,
        subtitle: str,
        fg_color: str,
        command,
    ) -> tk.Frame:
        """Build a large clickable menu card."""
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            highlightbackground=fg_color,
            highlightthickness=2,
            cursor="hand2",
        )

        tk.Frame(card, bg=fg_color, height=6).pack(fill="x")
        inner = tk.Frame(card, bg=BG_CARD)
        inner.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            inner,
            text="ENTER PORTAL",
            bg=BG_CARD,
            fg=fg_color,
            font=("Bahnschrift SemiBold", 9, "italic"),
        ).pack(anchor="w", pady=(0, 10))

        title_label = tk.Label(
            inner,
            text=title,
            bg=BG_CARD,
            fg=TEXT_MAIN,
            font=("Bahnschrift SemiBold", 24, "italic"),
        )
        title_label.pack(anchor="w", pady=(0, 8))

        subtitle_label = tk.Label(
            inner,
            text=subtitle,
            bg=BG_CARD,
            fg=TEXT_SOFT,
            font=("Bahnschrift SemiBold", 11),
            justify="left",
            wraplength=260,
        )
        subtitle_label.pack(anchor="w", pady=(0, 18))

        tk.Frame(inner, bg=fg_color, width=92, height=3).pack(anchor="w")

        def click_card(event=None):
            if self.interaction_locked:
                self.bell()
                return
            command()

        card.bind("<Button-1>", click_card)
        inner.bind("<Button-1>", click_card)
        title_label.bind("<Button-1>", click_card)
        subtitle_label.bind("<Button-1>", click_card)

        return card

    # -------------------------------------------------
    # Login / Register screen
    # -------------------------------------------------
    def show_login_screen(self) -> None:
        self.clear_screen()
        self.create_background_scene()

        wrapper = tk.Frame(self.main_container, bg=BG_MAIN)
        wrapper.pack(fill="both", expand=True, padx=40, pady=40)

        left = tk.Frame(wrapper, bg=BG_MAIN)
        left.pack(side="left", fill="both", expand=True, padx=(0, 20))

        right = tk.Frame(
            wrapper,
            bg=BG_PANEL,
            highlightbackground=NEON_PINK,
            highlightthickness=2,
        )
        right.pack(side="right", fill="y", padx=(20, 0))
        right.configure(width=430)
        right.pack_propagate(False)

        logo = self.load_logo_widget(left, size=(320, 320))
        logo.pack(anchor="center", pady=(20, 10))

        tk.Label(
            left,
            text="INTERACT WITH THE FUTURE OF TRADE",
            bg=BG_MAIN,
            fg=NEON_PINK,
            font=("Bahnschrift SemiBold", 12, "italic"),
        ).pack(anchor="center", pady=(0, 8))

        ttk.Label(left, text="TRADE SMARTER. TRADE FAIRER.", style="Header.TLabel").pack(anchor="center", pady=(0, 8))
        ttk.Label(
            left,
            text=(
                "Cyber Xchange is a neon-styled barter platform prototype. "
                "Users create accounts, browse trade listings, compare value, "
                "and connect through a local inbox."
            ),
            style="SubHeader.TLabel",
            wraplength=620,
            justify="center",
        ).pack(anchor="center", pady=(0, 20))

        features = tk.Frame(left, bg=BG_MAIN)
        features.pack(anchor="center", pady=20)

        bullets = [
            "• Secure local account creation and login",
            "• Upload item listings for trade only",
            "• Browse other users by category",
            "• Compare estimated value vs desired trade value",
            "• Message traders through the app inbox",
        ]
        for item in bullets:
            tk.Label(
                features,
                text=item,
                bg=BG_MAIN,
                fg=TEXT_MAIN,
                font=("Bahnschrift SemiBold", 11),
                anchor="w",
                justify="left",
            ).pack(anchor="w", pady=3)

        form = tk.Frame(right, bg=BG_PANEL)
        form.pack(fill="both", expand=True, padx=30, pady=30)

        tk.Label(
            form,
            text="LOGIN / REGISTER",
            bg=BG_PANEL,
            fg=NEON_PINK,
            font=("Bahnschrift SemiBold", 24, "italic"),
        ).pack(anchor="w", pady=(10, 25))

        tk.Label(
            form,
            text="SIGNAL IN TO ENTER THE MARKET",
            bg=BG_PANEL,
            fg=TEXT_MUTED,
            font=("Bahnschrift SemiBold", 10, "italic"),
        ).pack(anchor="w", pady=(0, 16))

        tk.Label(form, text="Username", bg=BG_PANEL, fg=NEON_BLUE, font=("Bahnschrift SemiBold", 10)).pack(anchor="w")
        self.username_entry = ttk.Entry(form, width=30)
        self.username_entry.pack(fill="x", pady=(6, 16))

        tk.Label(form, text="Password", bg=BG_PANEL, fg=NEON_BLUE, font=("Bahnschrift SemiBold", 10)).pack(anchor="w")
        self.password_entry = ttk.Entry(form, width=30, show="*")
        self.password_entry.pack(fill="x", pady=(6, 20))

        btns = tk.Frame(form, bg=BG_PANEL)
        btns.pack(fill="x", pady=10)

        ttk.Button(
            btns,
            text="Login",
            command=self.handle_login,
            style="Accent.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ttk.Button(
            btns,
            text="Create Account",
            command=self.handle_register,
            style="Secondary.TButton",
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            form,
            text=(
                "Local prototype note:\n"
                "Accounts, listings, and inbox messages are stored in SQLite "
                "for demo purposes."
            ),
            bg=BG_PANEL,
            fg=TEXT_MUTED,
            font=("Consolas", 9),
            justify="left",
        ).pack(anchor="w", pady=(30, 0))

    def handle_register(self) -> None:
        """Create a new account but do not force the user into posting."""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password:
            messagebox.showwarning("Missing fields", "Enter a username and password.")
            return

        ok, msg = create_user(username, password)
        if ok:
            messagebox.showinfo("Success", f"{msg}\nYou can now log in.")
            self.username_entry.delete(0, "end")
            self.password_entry.delete(0, "end")
        else:
            messagebox.showerror("Error", msg)

    def handle_login(self) -> None:
        """Authenticate the user, then send them to the main hub screen."""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        ok, user_id = authenticate_user(username, password)

        if not ok or user_id is None:
            messagebox.showerror("Login failed", "Invalid username or password.")
            return

        self.current_user_id = user_id
        self.current_username = username
        self.user_agreement_acknowledged = False
        self.show_main_hub()

    def minimize_user_agreement(self, agreement_panel: tk.Widget) -> None:
        """Dismiss the agreement panel and unlock the rest of the hub."""
        self.user_agreement_acknowledged = True
        self.set_interaction_lock(False)
        self.stop_agreement_shine()

        if agreement_panel.winfo_exists():
            agreement_panel.destroy()

        self.agreement_button = None

    def build_user_agreement_panel(self, parent: tk.Widget) -> None:
        """Render the expandable agreement card shown on the main hub."""
        header = tk.Frame(parent, bg=BG_PANEL)
        header.pack(fill="x", padx=18, pady=(18, 10))

        tk.Label(
            header,
            text="USER AGREEMENT",
            bg=BG_PANEL,
            fg=NEON_PINK,
            font=("Consolas", 18, "bold"),
        ).pack(side="left")

        agreement_body = tk.Frame(parent, bg=BG_PANEL)
        agreement_body.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        self.agreement_button = tk.Button(
            header,
            text="MINIMIZE",
            command=lambda: self.minimize_user_agreement(parent),
            bg=BG_EDGE,
            fg=NEON_GOLD,
            activebackground=NEON_GOLD,
            activeforeground=BG_MAIN,
            font=("Consolas", 9, "bold"),
            relief="raised",
            bd=2,
            highlightbackground=NEON_GOLD,
            highlightcolor=NEON_GOLD,
            highlightthickness=2,
            cursor="hand2",
            padx=10,
            pady=4,
        )
        self.agreement_button.pack(side="right")

        if self.user_agreement_acknowledged:
            parent.destroy()
            return

        agreement_scroll = tk.Scrollbar(agreement_body)
        agreement_scroll.pack(side="right", fill="y")

        agreement_text = tk.Text(
            agreement_body,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 9),
            yscrollcommand=agreement_scroll.set,
            padx=12,
            pady=12,
            height=14,
        )
        agreement_text.pack(fill="both", expand=True)
        agreement_scroll.config(command=agreement_text.yview)

        agreement_text.insert(
            "end",
            (
                "By creating an account or using CyberXchange, you agree to the user agreement below "
                "and all policies on here. If you do not agree, you may not use our services. Thank you.\n\n"
                "USE AT YOUR OWN RISK!\n\n"
                "We are not liable for destruction, damage, or theft of your property or anyone else's property "
                "once you have traded it.\n\n"
                "We strongly recommend that meetups for trades be done in public safe spaces like police stations, "
                "market squares, and busy walkways.\n\n"
                "We are not liable for death or injury to you or your trader in any meetups connected to these services.\n\n"
                "CyberXchange grants you a limited, non-exclusive, non-transferable license to access the Services "
                "for your internal business purposes. You may not copy, modify, reverse engineer, or bypass security "
                "or usage limits.\n\n"
                "By trading on this site, you and your trader both agree that you will not be able to return or see "
                "your items ever again.\n\n"
                "::::::: Trading Warning System :::::::\n\n"
                "WARNING: BY ACCEPTING THIS OFFER YOU AGREE TO TRADE THIS ITEM FOREVER. "
                "NO RETURNS. NO EXCEPTIONS."
            ),
        )
        agreement_text.config(state="disabled")

        self.set_interaction_lock(True)
        self.agreement_shine_on = False
        self.stop_agreement_shine()
        self.animate_agreement_button()

    # -------------------------------------------------
    # Main hub screen
    # -------------------------------------------------
    def show_main_hub(self) -> None:
        self.clear_screen()
        self.create_background_scene()

        self.build_topbar("CYBER XCHANGE // MAIN HUB")

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=28, pady=20)

        hero = tk.Frame(outer, bg=BG_MAIN)
        hero.pack(fill="x", pady=(0, 20))

        left = tk.Frame(hero, bg=BG_MAIN)
        left.pack(side="left", fill="both", expand=True)

        right = None
        if not self.user_agreement_acknowledged:
            right = tk.Frame(
                hero,
                bg=BG_PANEL,
                highlightbackground=NEON_PINK,
                highlightthickness=2,
            )
            right.pack(side="right", anchor="n", padx=(20, 0))
            right.configure(width=350)
            right.pack_propagate(True)

        logo = self.load_logo_widget(left, size=(250, 250))
        logo.pack(anchor="w", pady=(0, 10))

        tk.Label(
            left,
            text=f"Welcome back, {self.current_username}",
            bg=BG_MAIN,
            fg=TEXT_MAIN,
            font=("Bahnschrift SemiBold", 34, "italic"),
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            left,
            text="LIVE NEON NETWORK // TRADE, MATCH, NEGOTIATE",
            bg=BG_MAIN,
            fg=NEON_PINK,
            font=("Bahnschrift SemiBold", 12, "italic"),
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            left,
            text=(
                "Choose how you want to interact with the trading network. "
                "Upload new trade posts, browse categories, check your inbox, "
                "or manage your profile and listings."
            ),
            bg=BG_MAIN,
            fg=TEXT_SOFT,
            font=("Bahnschrift SemiBold", 13),
            wraplength=620,
            justify="left",
        ).pack(anchor="w")

        if right is not None:
            self.build_user_agreement_panel(right)

        grid = tk.Frame(outer, bg=BG_MAIN)
        grid.pack(fill="both", expand=True)

        upload_card = self.make_card_button(
            grid,
            "UPLOAD TRADE",
            "Create a new barter listing with photo, condition, estimated value, and desired trade value.",
            NEON_BLUE,
            self.show_upload_screen,
        )
        browse_card = self.make_card_button(
            grid,
            "BROWSE TRADES",
            "View other users' postings by category and compare item trade values.",
            NEON_GREEN,
            self.show_browse_screen,
        )
        inbox_card = self.make_card_button(
            grid,
            "MESSAGING INBOX",
            "Read messages from other traders and keep track of incoming trade interest.",
            NEON_PINK,
            self.show_inbox_screen,
        )
        profile_card = self.make_card_button(
            grid,
            "MY PROFILE",
            "View your current identity in the app and review the trade posts you have uploaded.",
            NEON_PURPLE,
            self.show_profile_screen,
        )

        upload_card.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        browse_card.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        inbox_card.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        profile_card.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)
        self.add_quick_scan_button()

    # -------------------------------------------------
    # Upload screen
    # -------------------------------------------------
    def show_upload_screen(self) -> None:
        self.clear_screen()
        self.create_background_scene()
        self.build_topbar("UPLOAD TRADE", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        form = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        form.pack(fill="both", expand=True)

        inner = tk.Frame(form, bg=BG_CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=26)

        tk.Label(
            inner,
            text="CREATE A TRADE LISTING",
            bg=BG_CARD,
            fg=NEON_BLUE,
            font=("Consolas", 22, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 20))

        tk.Label(inner, text="Item Title", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.title_entry = ttk.Entry(inner)
        self.title_entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 15), pady=(0, 14))

        tk.Label(inner, text="Category", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=3, column=0, sticky="w", pady=(0, 6))
        self.category_combo = ttk.Combobox(
            inner,
            values=["Electronics", "Clothing", "Collectibles", "Home", "Tools", "Gaming", "Other"],
            state="readonly",
        )
        self.category_combo.grid(row=4, column=0, sticky="ew", padx=(0, 15), pady=(0, 14))
        self.category_combo.set("Other")

        tk.Label(inner, text="Condition", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=3, column=1, sticky="w", pady=(0, 6))
        self.condition_combo = ttk.Combobox(
            inner,
            values=["New", "Like New", "Good", "Fair", "Used"],
            state="readonly",
        )
        self.condition_combo.grid(row=4, column=1, sticky="ew", pady=(0, 14))
        self.condition_combo.set("Good")

        tk.Label(inner, text="Estimated Value ($)", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=5, column=0, sticky="w", pady=(0, 6))
        self.estimated_entry = ttk.Entry(inner)
        self.estimated_entry.grid(row=6, column=0, sticky="ew", padx=(0, 15), pady=(0, 14))

        tk.Label(inner, text="Desired Trade Value ($)", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=5, column=1, sticky="w", pady=(0, 6))
        self.desired_entry = ttk.Entry(inner)
        self.desired_entry.grid(row=6, column=1, sticky="ew", pady=(0, 14))

        tk.Label(inner, text="Description / Trade Notes", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).grid(row=7, column=0, sticky="w", pady=(0, 6))
        self.description_text = tk.Text(
            inner,
            height=8,
            wrap="word",
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            font=("Consolas", 10),
        )
        self.description_text.grid(row=8, column=0, columnspan=2, sticky="ew", padx=(0, 15), pady=(0, 16))

        upload_frame = tk.Frame(
            inner,
            bg=BG_ALT,
            highlightbackground=NEON_PINK,
            highlightthickness=1,
        )
        upload_frame.grid(row=1, column=2, rowspan=8, sticky="nsew")

        self.preview_label = tk.Label(
            upload_frame,
            text="No photo selected",
            bg=BG_INPUT,
            fg=TEXT_SOFT,
            width=28,
            height=14,
            relief="flat",
            font=("Consolas", 10),
        )
        self.preview_label.pack(pady=(14, 12), padx=12, fill="both", expand=False)

        ttk.Button(
            upload_frame,
            text="Upload Photo",
            command=self.select_photo,
            style="Secondary.TButton",
        ).pack(fill="x", padx=12, pady=(0, 10))

        ttk.Button(
            upload_frame,
            text="Save Listing",
            command=self.save_listing,
            style="Accent.TButton",
        ).pack(fill="x", padx=12, pady=(0, 10))

        self.feedback_var = tk.StringVar(value="Add your trade item details, then save.")
        tk.Label(
            inner,
            textvariable=self.feedback_var,
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 10, "italic"),
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(12, 0))

        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)
        inner.columnconfigure(2, weight=1)
        self.add_quick_scan_button()

    def run_upload_ai_search(self) -> None:
        """Run AI price lookup from the upload form search field."""
        term = self.upload_search_var.get().strip() if hasattr(self, "upload_search_var") else ""
        if not term:
            messagebox.showwarning("Missing search", "Type an item name to search.")
            return

        suggestion, details, _sources = self.lookup_market_value(term)
        self.autofill_estimated_value(suggestion)
        self.feedback_var.set(f"A.I suggested new price {suggestion}. {details}")

    def select_photo(self) -> None:
        """Open a file picker and preview the selected image if Pillow exists."""
        file_path = filedialog.askopenfilename(
            title="Choose item photo",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif *.webp")],
        )
        if not file_path:
            return

        self.selected_photo_path = file_path

        if Image and ImageTk:
            try:
                image = Image.open(file_path)
                image.thumbnail((260, 260))
                self.preview_photo = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=self.preview_photo, text="")
            except Exception:
                self.preview_label.configure(image="", text="Unable to preview image")
        else:
            self.preview_label.configure(text=os.path.basename(file_path))

    def save_listing(self) -> None:
        """Validate and save a barter listing, then return the user to the hub."""
        if self.current_user_id is None:
            messagebox.showerror("Error", "No user logged in.")
            return

        title = self.title_entry.get().strip()
        category = self.category_combo.get().strip()
        condition = self.condition_combo.get().strip()
        description = self.description_text.get("1.0", "end").strip()
        estimated_raw = self.estimated_entry.get().strip()
        desired_raw = self.desired_entry.get().strip()

        if not title or not estimated_raw or not desired_raw:
            messagebox.showwarning(
                "Missing information",
                "Title, estimated value, and desired trade value are required.",
            )
            return

        try:
            estimated_value = float(estimated_raw)
            desired_value = float(desired_raw)
        except ValueError:
            messagebox.showerror(
                "Invalid values",
                "Estimated value and desired trade value must be numbers.",
            )
            return

        saved_photo_path = ""
        if self.selected_photo_path:
            try:
                saved_photo_path = store_uploaded_photo(self.selected_photo_path)
            except OSError as exc:
                messagebox.showerror(
                    "Image save failed",
                    f"Could not save the selected image.\n{exc}",
                )
                return

        try:
            save_item(
                self.current_user_id,
                title,
                category,
                condition,
                description,
                estimated_value,
                desired_value,
                saved_photo_path,
            )
        except sqlite3.OperationalError as exc:
            messagebox.showerror(
                "Database busy",
                f"The listing could not be saved because the database is busy.\n{exc}\n\nClose extra app windows and try again.",
            )
            return

        label, score = fairness_score(estimated_value, desired_value)
        self.feedback_var.set(f"Listing saved. Trade balance reference: {label} ({score}%).")
        messagebox.showinfo("Saved", f"Listing saved.\nTrade balance reference: {label} ({score}%).")
        self.show_main_hub()

    # -------------------------------------------------
    # Browse screen
    # -------------------------------------------------
    def show_browse_screen(self) -> None:
        self.clear_screen()
        self.create_background_scene()
        self.build_topbar("BROWSE TRADES", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        top = tk.Frame(outer, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))

        tk.Label(
            top,
            text="Category Filter",
            bg=BG_MAIN,
            fg=NEON_GREEN,
            font=("Consolas", 11, "bold"),
        ).pack(side="left", padx=(0, 10))

        self.browse_category_combo = ttk.Combobox(
            top,
            values=["All", "Electronics", "Clothing", "Collectibles", "Home", "Tools", "Gaming", "Other"],
            state="readonly",
            width=18,
        )
        self.browse_category_combo.pack(side="left")
        self.browse_category_combo.set("All")

        ttk.Button(
            top,
            text="Load Category",
            command=self.refresh_browse_results,
            style="Accent.TButton",
        ).pack(side="left", padx=10)

        ai_search = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        ai_search.pack(fill="x", pady=(0, 14))

        tk.Label(
            ai_search,
            text="MARKET SEARCH",
            bg=BG_PANEL,
            fg=NEON_PINK,
            font=("Consolas", 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        controls = tk.Frame(ai_search, bg=BG_PANEL)
        controls.pack(fill="x", padx=16)

        self.browse_search_var = tk.StringVar()
        self.browse_search_entry = ttk.Entry(controls, textvariable=self.browse_search_var, width=34)
        self.browse_search_entry.pack(side="left", fill="x", expand=True)
        self.browse_search_entry.bind("<Return>", lambda event: self.run_browse_ai_search())

        ttk.Button(
            controls,
            text="Open Price Search",
            command=self.run_browse_ai_search,
            style="Accent.TButton",
        ).pack(side="left", padx=(10, 8))

        ttk.Button(
            controls,
            text="Filter Listings",
            command=self.refresh_browse_results,
            style="Secondary.TButton",
        ).pack(side="left")

        self.browse_ai_result_var = tk.StringVar(value="Type an item name to open live web price searches and filter listings.")
        tk.Label(
            ai_search,
            textvariable=self.browse_ai_result_var,
            bg=BG_PANEL,
            fg=NEON_GREEN,
            font=("Consolas", 10, "bold"),
            wraplength=1120,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(12, 4))

        self.browse_ai_detail_var = tk.StringVar(value="")
        tk.Label(
            ai_search,
            textvariable=self.browse_ai_detail_var,
            bg=BG_PANEL,
            fg=TEXT_MUTED,
            font=("Consolas", 9),
            wraplength=1120,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.browse_sources_frame = tk.Frame(ai_search, bg=BG_PANEL)
        self.browse_sources_frame.pack(fill="x", padx=16, pady=(0, 14))

        browse_frame = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_GREEN,
            highlightthickness=1,
        )
        browse_frame.pack(fill="both", expand=True)

        self.browse_text = tk.Text(
            browse_frame,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.browse_text.pack(fill="both", expand=True, padx=16, pady=16)
        self.browse_text.config(state="disabled")

        bottom = tk.Frame(outer, bg=BG_MAIN)
        bottom.pack(fill="x", pady=(14, 0))

        tk.Label(
            bottom,
            text="To start trade conversations, note the trader username and use Inbox to send a message.",
            bg=BG_MAIN,
            fg=TEXT_MUTED,
            font=("Consolas", 10),
        ).pack(side="left")

        self.refresh_browse_results()
        self.add_quick_scan_button()

    def run_browse_ai_search(self) -> None:
        """Run the browser-based price lookup from the browse screen."""
        term = self.browse_search_var.get().strip() if hasattr(self, "browse_search_var") else ""
        if not term:
            messagebox.showwarning("Missing search", "Type an item name to search.")
            return

        self.browse_ai_result_var.set("Opening live market price search...")
        self.browse_ai_detail_var.set("Opening browser pricing results and updating local listing matches.")
        self.main_container.update_idletasks()

        suggestion, details, sources = self.lookup_market_value(term)
        self.browse_ai_result_var.set(suggestion)
        self.browse_ai_detail_var.set(details)
        self.render_source_links(self.browse_sources_frame, sources)
        self.refresh_browse_results()

    def refresh_browse_results(self) -> None:
        """Load browse results based on the selected category."""
        if self.current_user_id is None:
            return

        category = self.browse_category_combo.get().strip() if hasattr(self, "browse_category_combo") else "All"
        items = get_other_items_by_category(self.current_user_id, category)
        search_term = self.browse_search_var.get().strip().lower() if hasattr(self, "browse_search_var") else ""
        if search_term:
            items = [
                item for item in items
                if search_term in item[2].lower()
                or search_term in item[3].lower()
                or search_term in item[4].lower()
                or search_term in item[5].lower()
                or search_term in (item[6] or "").lower()
            ]
        self.browse_image_refs = []

        self.browse_text.config(state="normal")
        self.browse_text.delete("1.0", "end")

        if not items:
            self.browse_text.insert(
                "end",
                "No trade posts found for this category yet.\n"
                "Try another category or create a second account for demo browsing."
            )
            self.browse_text.config(state="disabled")
            return

        for item in items:
            item_id, owner_id, owner_username, title, item_category, condition, description, estimated_value, desired_trade_value, photo_path, created_at, _status = item
            label, score = fairness_score(estimated_value, desired_trade_value)

            block = (
                f"{title}\n"
                f"Trader: {owner_username}\n"
                f"Category: {item_category}\n"
                f"Condition: {condition}\n"
                f"Estimated Value: ${estimated_value:,.2f}\n"
                f"Desired Trade Value: ${desired_trade_value:,.2f}\n"
                f"Trade Balance Reference: {label} ({score}%)\n"
                f"Notes: {description or 'No description provided.'}\n"
            )
            self.browse_text.insert("end", block)

            thumbnail = self.load_text_thumbnail(photo_path, size=(220, 220))
            if thumbnail is not None:
                self.browse_image_refs.append(thumbnail)
                self.browse_text.image_create("end", image=thumbnail)
                self.browse_text.insert("end", "\n")
            else:
                self.browse_text.insert("end", f"Photo: {photo_path or 'No photo uploaded'}\n")

            self.browse_text.insert("end", f"{'-' * 64}\n")

        self.browse_text.config(state="disabled")

    # -------------------------------------------------
    # Inbox screen
    # -------------------------------------------------
    def show_inbox_screen(self) -> None:
        self.clear_screen()
        self.create_background_scene()
        self.build_topbar("MESSAGING INBOX", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        left = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_PINK,
            highlightthickness=1,
        )
        left.pack(side="left", fill="y", padx=(0, 10))
        left.configure(width=300)
        left.pack_propagate(False)

        center = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_GREEN,
            highlightthickness=1,
        )
        center.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        right.pack(side="right", fill="y", padx=(10, 0))
        right.configure(width=360)
        right.pack_propagate(False)

        inbox_wrap = tk.Frame(left, bg=BG_CARD)
        inbox_wrap.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            inbox_wrap,
            text="MESSAGE REQUESTS",
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 15, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        self.request_listbox = tk.Listbox(
            inbox_wrap,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            selectbackground=BG_EDGE,
            selectforeground=NEON_BLUE,
            relief="flat",
            font=("Consolas", 10),
        )
        self.request_listbox.pack(fill="x")
        self.request_listbox.bind("<<ListboxSelect>>", self.handle_request_select)

        request_buttons = tk.Frame(inbox_wrap, bg=BG_CARD)
        request_buttons.pack(fill="x", pady=(10, 18))

        ttk.Button(
            request_buttons,
            text="Approve",
            command=self.approve_selected_request,
            style="Accent.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ttk.Button(
            request_buttons,
            text="Decline",
            command=self.decline_selected_request,
            style="Secondary.TButton",
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            inbox_wrap,
            text="CONVERSATIONS",
            bg=BG_CARD,
            fg=NEON_BLUE,
            font=("Consolas", 15, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        self.conversation_listbox = tk.Listbox(
            inbox_wrap,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            selectbackground=BG_EDGE,
            selectforeground=NEON_GREEN,
            relief="flat",
            font=("Consolas", 10),
        )
        self.conversation_listbox.pack(fill="both", expand=True)
        self.conversation_listbox.bind("<<ListboxSelect>>", self.handle_conversation_select)

        thread_wrap = tk.Frame(center, bg=BG_CARD)
        thread_wrap.pack(fill="both", expand=True, padx=18, pady=18)

        self.thread_title_var = tk.StringVar(value="Select a request or conversation")
        tk.Label(
            thread_wrap,
            textvariable=self.thread_title_var,
            bg=BG_CARD,
            fg=NEON_GREEN,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w")

        self.thread_status_var = tk.StringVar(value="No conversation selected.")
        tk.Label(
            thread_wrap,
            textvariable=self.thread_status_var,
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=("Consolas", 10),
        ).pack(anchor="w", pady=(4, 12))

        self.thread_text = tk.Text(
            thread_wrap,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.thread_text.pack(fill="both", expand=True)
        self.thread_text.config(state="disabled")

        reply_wrap = tk.Frame(thread_wrap, bg=BG_CARD)
        reply_wrap.pack(fill="x", pady=(14, 0))

        tk.Label(reply_wrap, text="Reply", bg=BG_CARD, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.reply_body_text = tk.Text(
            reply_wrap,
            height=5,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.reply_body_text.pack(fill="x", pady=(6, 10))

        self.reply_button = ttk.Button(
            reply_wrap,
            text="Send Reply",
            command=self.handle_reply_message,
            style="Accent.TButton",
        )
        self.reply_button.pack(fill="x")

        composer = tk.Frame(right, bg=BG_PANEL)
        composer.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(
            composer,
            text="SEND MESSAGE",
            bg=BG_PANEL,
            fg=NEON_BLUE,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        tk.Label(composer, text="Send To", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_user_combo = ttk.Combobox(composer, state="readonly")
        users = get_all_users_except(self.current_user_id) if self.current_user_id is not None else []
        self.user_lookup = {username: user_id for user_id, username in users}
        self.message_user_combo["values"] = list(self.user_lookup.keys())
        if users:
            self.message_user_combo.set(users[0][1])
        self.message_user_combo.pack(fill="x", pady=(6, 14))

        tk.Label(composer, text="Subject", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_subject_entry = ttk.Entry(composer)
        self.message_subject_entry.pack(fill="x", pady=(6, 14))

        tk.Label(composer, text="Message", bg=BG_PANEL, fg=NEON_GREEN, font=("Consolas", 10)).pack(anchor="w")
        self.message_body_text = tk.Text(
            composer,
            height=12,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.message_body_text.pack(fill="both", expand=True, pady=(6, 14))

        ttk.Button(
            composer,
            text="Send Message",
            command=self.handle_send_message,
            style="Accent.TButton",
        ).pack(fill="x")

        self.refresh_inbox()
        self.add_quick_scan_button()

    def refresh_inbox(self) -> None:
        """Reload message requests and conversations for the logged-in user."""
        if self.current_user_id is None:
            return

        requests = get_message_requests(self.current_user_id)
        conversations = get_visible_conversations(self.current_user_id)

        self.request_lookup = [row[0] for row in requests]
        self.request_listbox.delete(0, "end")
        if not requests:
            self.request_listbox.insert("end", "No requests")
        else:
            for _conversation_id, username, subject, body, created_at, _requested_by in requests:
                preview = (body[:24] + "...") if len(body) > 24 else body
                self.request_listbox.insert(
                    "end",
                    f"{username}  [{format_timestamp(created_at)}]\n{subject}: {preview}"
                )

        self.conversation_lookup = [row[0] for row in conversations]
        self.conversation_listbox.delete(0, "end")
        if not conversations:
            self.conversation_listbox.insert("end", "No chats yet")
        else:
            for _conversation_id, other_username, subject, body, created_at, status, unread_count in conversations:
                preview = (body[:26] + "...") if len(body) > 26 else body
                unread_tag = f" ({unread_count})" if unread_count else ""
                status_tag = " [Pending]" if status == "pending" else ""
                self.conversation_listbox.insert(
                    "end",
                    f"{other_username}{unread_tag}{status_tag}\n{subject}: {preview}"
                )

        valid_ids = set(self.request_lookup + self.conversation_lookup)
        if self.current_conversation_id in valid_ids:
            self.display_conversation(self.current_conversation_id)
        else:
            self.current_conversation_id = None
            self.thread_title_var.set("Select a request or conversation")
            self.thread_status_var.set("No conversation selected.")
            self.thread_text.config(state="normal")
            self.thread_text.delete("1.0", "end")
            self.thread_text.insert("end", "Choose a conversation on the left to read and reply.")
            self.thread_text.config(state="disabled")
            self.reply_body_text.delete("1.0", "end")
            self.reply_button.state(["disabled"])

    def handle_request_select(self, _event=None) -> None:
        """Load a pending request thread from the requests list."""
        if not self.request_lookup:
            return
        selection = self.request_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.request_lookup):
            return
        self.current_conversation_id = self.request_lookup[index]
        self.display_conversation(self.current_conversation_id)

    def handle_conversation_select(self, _event=None) -> None:
        """Load an existing conversation thread from the conversations list."""
        if not self.conversation_lookup:
            return
        selection = self.conversation_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.conversation_lookup):
            return
        self.current_conversation_id = self.conversation_lookup[index]
        self.display_conversation(self.current_conversation_id)

    def display_conversation(self, conversation_id: int) -> None:
        """Show one threaded conversation in the main message panel."""
        if self.current_user_id is None:
            return

        meta, messages = get_conversation_messages(self.current_user_id, conversation_id)
        if meta is None:
            return

        mark_conversation_read(self.current_user_id, conversation_id)

        _conv_id, status, requested_by, user_one_id, user_two_id, other_username = meta
        self.thread_other_user_id = user_two_id if user_one_id == self.current_user_id else user_one_id
        self.thread_title_var.set(other_username)

        if status == "pending" and requested_by != self.current_user_id:
            self.thread_status_var.set("Message request waiting for your approval.")
            self.reply_button.state(["disabled"])
        elif status == "pending":
            self.thread_status_var.set("Request sent. Waiting for them to accept before chatting.")
            self.reply_button.state(["disabled"])
        else:
            self.thread_status_var.set("Conversation active.")
            self.reply_button.state(["!disabled"])

        self.thread_text.config(state="normal")
        self.thread_text.delete("1.0", "end")

        last_subject = "Trade Reply"
        for _msg_id, sender_id, sender_username, subject, body, created_at, _is_read in messages:
            author = "You" if sender_id == self.current_user_id else sender_username
            last_subject = subject or last_subject
            block = (
                f"{author}  [{format_timestamp(created_at)}]\n"
                f"{body}\n"
                f"{'-' * 56}\n"
            )
            self.thread_text.insert("end", block)

        self.current_thread_subject = last_subject
        self.thread_text.config(state="disabled")
        self.refresh_inbox_lists_only()

    def refresh_inbox_lists_only(self) -> None:
        """Refresh sidebars after read-state changes without clearing the thread view."""
        if self.current_user_id is None:
            return
        requests = get_message_requests(self.current_user_id)
        conversations = get_visible_conversations(self.current_user_id)

        self.request_lookup = [row[0] for row in requests]
        self.request_listbox.delete(0, "end")
        if not requests:
            self.request_listbox.insert("end", "No requests")
        else:
            for _conversation_id, username, subject, body, created_at, _requested_by in requests:
                preview = (body[:24] + "...") if len(body) > 24 else body
                self.request_listbox.insert("end", f"{username}  [{format_timestamp(created_at)}]\n{subject}: {preview}")

        self.conversation_lookup = [row[0] for row in conversations]
        self.conversation_listbox.delete(0, "end")
        if not conversations:
            self.conversation_listbox.insert("end", "No chats yet")
        else:
            for _conversation_id, other_username, subject, body, _created_at, status, unread_count in conversations:
                preview = (body[:26] + "...") if len(body) > 26 else body
                unread_tag = f" ({unread_count})" if unread_count else ""
                status_tag = " [Pending]" if status == "pending" else ""
                self.conversation_listbox.insert("end", f"{other_username}{unread_tag}{status_tag}\n{subject}: {preview}")

    def approve_selected_request(self) -> None:
        """Approve the currently selected message request."""
        if self.current_user_id is None or self.current_conversation_id is None:
            return
        if approve_message_request(self.current_user_id, self.current_conversation_id):
            self.refresh_inbox()
            self.display_conversation(self.current_conversation_id)

    def decline_selected_request(self) -> None:
        """Decline the currently selected message request."""
        if self.current_user_id is None or self.current_conversation_id is None:
            return
        if decline_message_request(self.current_user_id, self.current_conversation_id):
            self.current_conversation_id = None
            self.refresh_inbox()

    def handle_reply_message(self) -> None:
        """Reply inside the selected conversation thread."""
        if self.current_user_id is None or self.current_conversation_id is None:
            messagebox.showwarning("No conversation", "Choose an active conversation first.")
            return

        body = self.reply_body_text.get("1.0", "end").strip()
        if not body:
            messagebox.showwarning("Missing message", "Enter a reply before sending.")
            return

        send_message(
            self.current_user_id,
            self.thread_other_user_id,
            getattr(self, "current_thread_subject", "Trade Reply"),
            body,
            conversation_id=self.current_conversation_id,
        )
        self.reply_body_text.delete("1.0", "end")
        self.display_conversation(self.current_conversation_id)

    def handle_send_message(self) -> None:
        """Send a new message that becomes a request or active chat thread."""
        if self.current_user_id is None:
            messagebox.showerror("Error", "No user logged in.")
            return

        username = self.message_user_combo.get().strip()
        subject = self.message_subject_entry.get().strip()
        body = self.message_body_text.get("1.0", "end").strip()

        if not username:
            messagebox.showwarning("Missing recipient", "Choose a user to message.")
            return

        if not subject or not body:
            messagebox.showwarning("Missing information", "Subject and message are required.")
            return

        receiver_id = self.user_lookup.get(username)
        if receiver_id is None:
            messagebox.showerror("Error", "Selected user not found.")
            return

        conversation_id, status = send_message(self.current_user_id, receiver_id, subject, body)

        self.message_subject_entry.delete(0, "end")
        self.message_body_text.delete("1.0", "end")
        self.current_conversation_id = conversation_id
        if status == "pending":
            messagebox.showinfo("Request sent", f"Your message request was sent to {username}.")
        else:
            messagebox.showinfo("Sent", f"Message sent to {username}.")
        self.refresh_inbox()
        self.display_conversation(conversation_id)

    # -------------------------------------------------
    # Profile screen
    # -------------------------------------------------
    def sync_profile_status_selection(self, event=None) -> None:
        """Mirror the selected listing's current status into the status picker."""
        selection = self.profile_item_combo.get().strip() if hasattr(self, "profile_item_combo") else ""
        item_meta = self.profile_status_lookup.get(selection)
        if item_meta is None or not hasattr(self, "profile_status_combo"):
            return
        _item_id, current_status = item_meta
        self.profile_status_combo.set(LISTING_STATUS_LABELS.get(current_status, "Active"))

    def handle_profile_status_update(self) -> None:
        """Apply a profile listing status change and refresh the profile view."""
        if self.current_user_id is None:
            messagebox.showerror("Error", "No user logged in.")
            return

        selection = self.profile_item_combo.get().strip() if hasattr(self, "profile_item_combo") else ""
        selected_status_label = self.profile_status_combo.get().strip() if hasattr(self, "profile_status_combo") else ""
        item_meta = self.profile_status_lookup.get(selection)
        if item_meta is None:
            messagebox.showwarning("Select listing", "Choose one of your listings first.")
            return

        reverse_labels = {label: key for key, label in LISTING_STATUS_LABELS.items()}
        next_status = reverse_labels.get(selected_status_label, "active")
        item_id, _current_status = item_meta
        if update_item_status(self.current_user_id, item_id, next_status):
            messagebox.showinfo("Listing updated", f"Listing status set to {LISTING_STATUS_LABELS[next_status]}.")
            self.show_profile_screen()
        else:
            messagebox.showerror("Update failed", "Could not update that listing status.")

    def show_profile_screen(self) -> None:
        self.clear_screen()
        self.create_background_scene()
        self.build_topbar("MY PROFILE", back_command=self.show_main_hub)

        outer = tk.Frame(self.main_container, bg=BG_MAIN)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        top_card = tk.Frame(
            outer,
            bg=BG_PANEL,
            highlightbackground=NEON_PURPLE,
            highlightthickness=1,
        )
        top_card.pack(fill="x", pady=(0, 16))

        tk.Label(
            top_card,
            text=self.current_username,
            bg=BG_PANEL,
            fg=NEON_BLUE,
            font=("Consolas", 24, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 8))

        user_items = get_user_items(self.current_user_id) if self.current_user_id is not None else []
        self.profile_status_lookup = {}
        active_count = sum(1 for item in user_items if item[9] == "active")
        pending_count = sum(1 for item in user_items if item[9] == "pending_trade")
        traded_count = sum(1 for item in user_items if item[9] == "traded")

        tk.Label(
            top_card,
            text=f"Active: {active_count}   Pending Trade: {pending_count}   Traded: {traded_count}",
            bg=BG_PANEL,
            fg=NEON_GREEN,
            font=("Consolas", 12, "bold"),
        ).pack(anchor="w", padx=20, pady=(0, 18))

        listings_card = tk.Frame(
            outer,
            bg=BG_CARD,
            highlightbackground=NEON_BLUE,
            highlightthickness=1,
        )
        listings_card.pack(fill="both", expand=True)

        tk.Label(
            listings_card,
            text="MY UPLOADED LISTINGS",
            bg=BG_CARD,
            fg=NEON_PINK,
            font=("Consolas", 18, "bold"),
        ).pack(anchor="w", padx=18, pady=(18, 12))

        if user_items:
            manager = tk.Frame(listings_card, bg=BG_CARD)
            manager.pack(fill="x", padx=18, pady=(0, 14))

            tk.Label(
                manager,
                text="Listing Status Manager",
                bg=BG_CARD,
                fg=NEON_BLUE,
                font=("Consolas", 11, "bold"),
            ).grid(row=0, column=0, sticky="w", pady=(0, 8), columnspan=3)

            item_options = [f"#{item[0]} - {item[1]}" for item in user_items]
            self.profile_status_lookup = {
                option: (item[0], item[9]) for option, item in zip(item_options, user_items)
            }

            self.profile_item_combo = ttk.Combobox(manager, state="readonly", values=item_options, width=34)
            self.profile_item_combo.grid(row=1, column=0, sticky="ew", padx=(0, 12))
            self.profile_item_combo.set(item_options[0])
            self.profile_item_combo.bind("<<ComboboxSelected>>", self.sync_profile_status_selection)

            self.profile_status_combo = ttk.Combobox(
                manager,
                state="readonly",
                values=[LISTING_STATUS_LABELS[key] for key in LISTING_STATUSES],
                width=18,
            )
            self.profile_status_combo.grid(row=1, column=1, sticky="ew", padx=(0, 12))

            ttk.Button(
                manager,
                text="Update Status",
                command=self.handle_profile_status_update,
                style="Secondary.TButton",
            ).grid(row=1, column=2, sticky="ew")

            manager.columnconfigure(0, weight=2)
            manager.columnconfigure(1, weight=1)
            manager.columnconfigure(2, weight=0)
            self.sync_profile_status_selection()

        profile_text = tk.Text(
            listings_card,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=NEON_BLUE,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        profile_text.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.profile_image_refs = []

        if not user_items:
            profile_text.insert(
                "end",
                "You do not have any uploaded trade listings yet.\n\nUse Upload Trade from the main hub to create one."
            )
        else:
            for item in user_items:
                item_id, title, category, condition, description, estimated_value, desired_trade_value, photo_path, created_at, status = item
                label, score = fairness_score(estimated_value, desired_trade_value)
                recommended_matches = build_trade_match_candidates(self.current_user_id, item) if self.current_user_id is not None else []

                block = (
                    f"{title}\n"
                    f"Status: {LISTING_STATUS_LABELS.get(status, 'Active')}\n"
                    f"Category: {category}\n"
                    f"Condition: {condition}\n"
                    f"Estimated Value: ${estimated_value:,.2f}\n"
                    f"Desired Trade Value: ${desired_trade_value:,.2f}\n"
                    f"Trade Balance Reference: {label} ({score}%)\n"
                    f"Notes: {description or 'No description provided.'}\n"
                    f"Created: {created_at[:19].replace('T', ' ')}\n"
                )
                profile_text.insert("end", block)

                thumbnail = self.load_text_thumbnail(photo_path, size=(220, 220))
                if thumbnail is not None:
                    self.profile_image_refs.append(thumbnail)
                    profile_text.image_create("end", image=thumbnail)
                    profile_text.insert("end", "\n")
                else:
                    profile_text.insert("end", f"Photo: {photo_path or 'No photo uploaded'}\n")

                if recommended_matches:
                    profile_text.insert("end", "\nRecommended Trade Matches\n")
                    for index, match in enumerate(recommended_matches, start=1):
                        reason_text = " ".join(match["reasons"])
                        profile_text.insert(
                            "end",
                            (
                                f"  {index}. {match['title']} by {match['owner_username']}\n"
                                f"     Match Score: {match['match_score']}%\n"
                                f"     Category: {match['category']} | Condition: {match['condition']}\n"
                                f"     Estimated Value: ${match['estimated_value']:,.2f}\n"
                                f"     Why it matches: {reason_text}\n"
                                f"     Trade outlook: {match['summary']}\n"
                            ),
                        )
                else:
                    profile_text.insert(
                        "end",
                        "\nRecommended Trade Matches\n  No strong matches yet. More community listings will improve suggestions.\n",
                    )

                profile_text.insert("end", f"{'-' * 64}\n")

        profile_text.config(state="disabled")
        self.add_quick_scan_button()


# -------------------------------------------------
# App entry point
# -------------------------------------------------
if __name__ == "__main__":
    init_db()
    app = SilkRouteApp()
    app.mainloop()
