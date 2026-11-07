#!/usr/bin/env python3
"""
TA Homework Collector Bot (v10.1.0)
------------------------------------
Production-ready async Telegram Bot for automated student homework collection,
archiving to Telegram Forum Topics, TA progress tracking, and full-featured
Telegram Mini App (TMA) TA Control Hub.

Capabilities & Architecture:
- Telegram Mini App (TMA) TA Control Hub with zero-trust HMAC-SHA256 authentication.
- Live Roster & Submission Matrix with Grade Synchronization.
- Broadcast Console (All Students, Missing HW 1..5, Single Student).
- Student Manager, 1-Click Telegram chat_id unbinding, and direct chat_id lockout unblocking.
- Admin Telegram commands: /unblock and /reset with auto-detection for Student ID and Chat ID.
- Two-stage student authentication with per-student passwords and zero-trust chat_id locking.
- Submission edit limits: Maximum 2 edits allowed after initial submission (total 3 submissions).
- Forum archive deduplication: Superseded submissions are automatically deleted from archive topics.
- Dual persistence: Supabase PostgreSQL (production) with local SQLite fallback.
"""

import csv
import io
import http.server
import json
import logging
import os
import socketserver

import sqlite3
try:
    import psycopg2
except ImportError:
    psycopg2 = None
from contextlib import contextmanager

POSTGRES_URL = os.environ.get("POSTGRES_URL")


class PostgresCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
    def execute(self, query, args=()):
        query = query.replace("?", "%s")
        return self._cursor.execute(query, args)
    def fetchone(self):
        return self._cursor.fetchone()
    def fetchall(self):
        return self._cursor.fetchall()
    def __getattr__(self, name):
        return getattr(self._cursor, name)

class PostgresConnWrapper:
    def __init__(self, conn):
        self._conn = conn
    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor())
    def commit(self):
        self._conn.commit()
    def close(self):
        self._conn.close()
    def __getattr__(self, name):
        return getattr(self._conn, name)

@contextmanager
def get_db_conn():
    if POSTGRES_URL:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            yield PostgresConnWrapper(conn)
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DATABASE_PATH)
        try:
            yield conn
        finally:
            conn.close()

def execute_query(conn, query, args=()):
    # Replace ? with %s if using Postgres
    if POSTGRES_URL:
        query = query.replace("?", "%s")
    cursor = conn.cursor()
    cursor.execute(query, args)
    return cursor

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TAHomeworkBot")

BASE_DIR = Path(__file__).resolve().parent
if os.environ.get("VERCEL") or not os.access(BASE_DIR, os.W_OK):
    DATABASE_PATH = Path("/tmp/homework.db")
else:
    DATABASE_PATH = BASE_DIR / "homework.db"
CSV_PATH = BASE_DIR / "students.csv"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
REGISTRATION_PASSWORD = os.getenv("REGISTRATION_PASSWORD", "CE401_Fall2026").strip()

raw_archive_id = os.getenv("ARCHIVE_GROUP_ID", "-1004495387404").strip()
ARCHIVE_GROUP_ID: Optional[int] = int(raw_archive_id) if raw_archive_id else None

# Maximum edits permitted after initial submission (total 3 submissions per HW)
MAX_EDITS_PER_HW: int = 2

# Map HW 1..5 to Topic (thread) IDs
TOPIC_MAP: Dict[int, Optional[int]] = {1: 5, 2: 6, 3: 4, 4: 3, 5: 12}

# Webhook Host for Telegram Mini App & Webhooks
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "").strip().rstrip("/")

# Parse ADMIN_CHAT_IDS
raw_admin_ids = os.getenv("ADMIN_CHAT_IDS", "").strip()
ADMIN_CHAT_IDS: Set[int] = set()
if raw_admin_ids:
    for item in raw_admin_ids.split(","):
        item = item.strip()
        if item.isdigit() or (item.startswith("-") and item[1:].isdigit()):
            ADMIN_CHAT_IDS.add(int(item))


# ---------------------------------------------------------------------------
# Timezone Utilities (Asia/Tehran, +03:30)
# ---------------------------------------------------------------------------
def get_tehran_now() -> datetime:
    """Return current datetime in Asia/Tehran timezone (UTC+03:30)."""
    try:
        return datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        tehran_tz = timezone(timedelta(hours=3, minutes=30), name="Tehran Time")
        return datetime.now(tehran_tz)


def format_tehran_timestamp(dt: Optional[datetime] = None) -> str:
    """Format datetime cleanly as 'YYYY-MM-DD HH:mm:ss Tehran Time'."""
    if dt is None:
        dt = get_tehran_now()
    elif dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Tehran"))
        except Exception:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=3, minutes=30)))
    else:
        try:
            dt = dt.astimezone(ZoneInfo("Asia/Tehran"))
        except Exception:
            dt = dt.astimezone(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime("%Y-%m-%d %H:%M:%S Tehran Time")


# ---------------------------------------------------------------------------
# Database Utilities
# ---------------------------------------------------------------------------

def init_db() -> None:
    with get_db_conn() as conn:
        cursor = conn.cursor()
        
        if POSTGRES_URL:
            # Postgres schemas
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS students (
                    chat_id BIGINT PRIMARY KEY,
                    student_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS submissions (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    student_id TEXT NOT NULL,
                    hw_num INTEGER NOT NULL,
                    message_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT,
                    submitted_at TEXT NOT NULL,
                    edit_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(student_id, hw_num)
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_state (
                    chat_id BIGINT PRIMARY KEY,
                    selected_hw INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    is_blacklisted BOOLEAN NOT NULL DEFAULT FALSE,
                    pending_student_id TEXT
                );
            ''')
            cursor.execute('ALTER TABLE user_state ADD COLUMN IF NOT EXISTS pending_student_id TEXT;')
        else:
            # SQLite schemas
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS students (
                    chat_id INTEGER PRIMARY KEY,
                    student_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    student_id TEXT NOT NULL,
                    hw_num INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT,
                    submitted_at TEXT NOT NULL,
                    edit_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(student_id, hw_num)
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_state (
                    chat_id INTEGER PRIMARY KEY,
                    selected_hw INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    is_blacklisted BOOLEAN NOT NULL DEFAULT 0,
                    pending_student_id TEXT
                );
            ''')
            # Backward-compatible migrations for existing databases
            cursor.execute("PRAGMA table_info(submissions)")
            columns = {row[1] for row in cursor.fetchall()}
            if "edit_count" not in columns:
                cursor.execute("ALTER TABLE submissions ADD COLUMN edit_count INTEGER NOT NULL DEFAULT 0")
            if "file_name" not in columns:
                cursor.execute("ALTER TABLE submissions ADD COLUMN file_name TEXT")
            cursor.execute("PRAGMA table_info(user_state)")
            state_columns = {row[1] for row in cursor.fetchall()}
            if "pending_student_id" not in state_columns:
                cursor.execute("ALTER TABLE user_state ADD COLUMN pending_student_id TEXT")
        conn.commit()
    logger.info("Database initialized")



def get_student_by_chat_id(chat_id: int) -> Optional[Tuple[int, str, str, str]]:
    """Retrieve student record by chat_id: (chat_id, student_id, name, registered_at)."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT chat_id, student_id, name, registered_at FROM students WHERE chat_id = ?", (chat_id,))
        return cursor.fetchone()


def get_student_by_student_id(student_id: str) -> Optional[Tuple[int, str, str, str]]:
    """Check if a student_id is already bound to a chat_id."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT chat_id, student_id, name, registered_at FROM students WHERE student_id = ?", (student_id,))
        return cursor.fetchone()


def save_student_registration(chat_id: int, student_id: str, name: str) -> None:
    """Register student chat_id, student_id, and name with Tehran timestamp."""
    now_tehran = format_tehran_timestamp()
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO students (chat_id, student_id, name, registered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                student_id=excluded.student_id,
                name=excluded.name,
                registered_at=excluded.registered_at
            """,
            (chat_id, student_id, name, now_tehran),
        )
        conn.commit()


def get_submission(student_id: str, hw_num: int) -> Optional[Tuple[int, int, str, str, int, Optional[str]]]:
    """Retrieve existing submission: (id, message_id, file_id, submitted_at, edit_count, file_name)."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, message_id, file_id, submitted_at, edit_count, file_name
            FROM submissions
            WHERE student_id = ? AND hw_num = ?
            """,
            (student_id, hw_num),
        )
        return cursor.fetchone()


def save_submission(
    chat_id: int,
    student_id: str,
    hw_num: int,
    message_id: int,
    file_id: str,
    file_name: str,
    edit_count: int,
) -> None:
    """Save or update homework submission record with Tehran timestamp and edit count."""
    now_tehran = format_tehran_timestamp()
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO submissions (chat_id, student_id, hw_num, message_id, file_id, file_name, submitted_at, edit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, hw_num) DO UPDATE SET
                chat_id=excluded.chat_id,
                message_id=excluded.message_id,
                file_id=excluded.file_id,
                file_name=excluded.file_name,
                submitted_at=excluded.submitted_at,
                edit_count=excluded.edit_count
            """,
            (chat_id, student_id, hw_num, message_id, file_id, file_name, now_tehran, edit_count),
        )
        conn.commit()


def get_submission_counts() -> Dict[int, int]:
    """Return dictionary of hw_num -> submission_count."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT hw_num, COUNT(DISTINCT student_id) FROM submissions GROUP BY hw_num")
        rows = cursor.fetchall()
        return {hw_num: count for hw_num, count in rows}


def get_submitted_student_ids_for_hw(hw_num: int) -> Set[str]:
    """Get all student_ids that have submitted a specific HW."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT student_id FROM submissions WHERE hw_num = ?", (hw_num,))
        rows = cursor.fetchall()
        return {r[0] for r in rows}


def get_student_submissions(student_id: str) -> Dict[int, Tuple[str, str, int]]:
    """Return dictionary of hw_num -> (file_name, submitted_at, edit_count) for a student."""
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT hw_num, file_name, submitted_at, edit_count FROM submissions WHERE student_id = ?", (student_id,))
        return {row[0]: (row[1] or "submission.pdf", row[2], row[3]) for row in cursor.fetchall()}


def get_student_grades(student_id: str) -> Dict[int, Dict[str, Any]]:
    """
    Retrieve graded assignments for a student by joining `grades`, `users`, and `assignments`.
    Resolves the UUID vs Academic Student ID discrepancy seamlessly.
    Returns: Dict[hw_num, {earned_score, max_score, graded_by, graded_at, title, assignment_code}]
    """
    if not student_id:
        return {}
    grades_map: Dict[int, Dict[str, Any]] = {}
    try:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            query = """
                SELECT a.short_code, a.title, a.total_max_score, g.scores, g.graded_by, g.graded_at
                FROM grades g
                JOIN users u ON u.id = g.student_id
                JOIN assignments a ON a.id = g.assignment_id
                WHERE u.student_id = ?
            """
            cursor = execute_query(conn, query, (student_id,))
            for row in cursor.fetchall():
                code, title, max_score, scores, grader, graded_at = row
                hw_match = re.search(r'\d+', code or "")
                hw_num = int(hw_match.group(0)) if hw_match else None
                if hw_num:
                    total_earned = 0.0
                    if isinstance(scores, dict):
                        total_earned = sum(float(v) for v in scores.values() if v is not None and isinstance(v, (int, float)))
                    elif isinstance(scores, str):
                        try:
                            s_dict = json.loads(scores)
                            total_earned = sum(float(v) for v in s_dict.values() if v is not None and isinstance(v, (int, float)))
                        except Exception:
                            pass
                    grades_map[hw_num] = {
                        "assignment_code": code,
                        "assignment_title": title,
                        "max_score": float(max_score) if max_score is not None else 0.0,
                        "earned_score": total_earned,
                        "graded_by": grader,
                        "graded_at": graded_at,
                    }
    except Exception as e:
        logger.debug("Could not fetch grades for student %s: %s", student_id, e)
    return grades_map


def get_all_student_grades_map() -> Dict[str, Dict[int, Dict[str, Any]]]:
    """
    Batch retrieve all student grades mapping: student_id -> {hw_num -> grade_info}.
    Executed in a single efficient query to prevent N+1 connection overhead.
    """
    grades_map: Dict[str, Dict[int, Dict[str, Any]]] = {}
    try:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            query = """
                SELECT u.student_id, a.short_code, a.title, a.total_max_score, g.scores, g.graded_by, g.graded_at
                FROM grades g
                JOIN users u ON u.id = g.student_id
                JOIN assignments a ON a.id = g.assignment_id
                WHERE u.student_id IS NOT NULL AND u.student_id != ''
            """
            cursor = execute_query(conn, query)
            for row in cursor.fetchall():
                sid, code, title, max_score, scores, grader, graded_at = row
                sid_str = str(sid).strip()
                hw_match = re.search(r'\d+', code or "")
                hw_num = int(hw_match.group(0)) if hw_match else None
                if sid_str and hw_num:
                    if sid_str not in grades_map:
                        grades_map[sid_str] = {}
                    total_earned = 0.0
                    if isinstance(scores, dict):
                        total_earned = sum(float(v) for v in scores.values() if v is not None and isinstance(v, (int, float)))
                    elif isinstance(scores, str):
                        try:
                            s_dict = json.loads(scores)
                            total_earned = sum(float(v) for v in s_dict.values() if v is not None and isinstance(v, (int, float)))
                        except Exception:
                            pass
                    grades_map[sid_str][hw_num] = {
                        "assignment_code": code,
                        "assignment_title": title,
                        "max_score": float(max_score) if max_score is not None else 0.0,
                        "earned_score": total_earned,
                        "graded_by": grader,
                        "graded_at": graded_at,
                    }
    except Exception as e:
        logger.debug("Could not batch fetch student grades: %s", e)
    return grades_map


# ---------------------------------------------------------------------------
# CSV Roster Utilities
# ---------------------------------------------------------------------------


def get_user_state(chat_id: int) -> Tuple[Optional[int], int, bool, Optional[str]]:
    with get_db_conn() as conn:
        cursor = execute_query(conn, "SELECT selected_hw, attempts, is_blacklisted, pending_student_id FROM user_state WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if not row:
            if POSTGRES_URL:
                execute_query(
                    conn,
                    "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, FALSE, NULL)",
                    (chat_id,)
                )
            else:
                execute_query(
                    conn,
                    "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, 0, NULL)",
                    (chat_id,)
                )
            conn.commit()
            return (None, 0, False, None)
        return (row[0], row[1], bool(row[2]), row[3])

def update_user_state_hw(chat_id: int, hw_num: Optional[int]):
    with get_db_conn() as conn:
        execute_query(conn, "UPDATE user_state SET selected_hw = ? WHERE chat_id = ?", (hw_num, chat_id))
        conn.commit()

def set_pending_student_id(chat_id: int, student_id: Optional[str]) -> None:
    with get_db_conn() as conn:
        cursor = execute_query(conn, "SELECT chat_id FROM user_state WHERE chat_id = ?", (chat_id,))
        if not cursor.fetchone():
            if POSTGRES_URL:
                execute_query(
                    conn,
                    "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, FALSE, ?)",
                    (chat_id, student_id),
                )
            else:
                execute_query(
                    conn,
                    "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, 0, ?)",
                    (chat_id, student_id),
                )
        else:
            execute_query(conn, "UPDATE user_state SET pending_student_id = ? WHERE chat_id = ?", (student_id, chat_id))
        conn.commit()

def clear_pending_student_id(chat_id: int) -> None:
    set_pending_student_id(chat_id, None)

def increment_attempts(chat_id: int) -> int:
    get_user_state(chat_id)
    with get_db_conn() as conn:
        execute_query(conn, "UPDATE user_state SET attempts = attempts + 1 WHERE chat_id = ?", (chat_id,))
        conn.commit()
        cursor = execute_query(conn, "SELECT attempts FROM user_state WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else 1

def blacklist_user(chat_id: int):
    get_user_state(chat_id)
    with get_db_conn() as conn:
        if POSTGRES_URL:
            execute_query(conn, "UPDATE user_state SET is_blacklisted = TRUE WHERE chat_id = ?", (chat_id,))
        else:
            execute_query(conn, "UPDATE user_state SET is_blacklisted = 1 WHERE chat_id = ?", (chat_id,))
        conn.commit()

def reset_attempts(chat_id: int):
    with get_db_conn() as conn:
        if POSTGRES_URL:
            execute_query(conn, "UPDATE user_state SET attempts = 0, is_blacklisted = FALSE WHERE chat_id = ?", (chat_id,))
        else:
            execute_query(conn, "UPDATE user_state SET attempts = 0, is_blacklisted = 0 WHERE chat_id = ?", (chat_id,))
        conn.commit()

def _parse_roster_csv_stream(stream) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parse CSV stream safely into (student_id -> name, student_id -> password) mappings."""
    roster: Dict[str, str] = {}
    passwords: Dict[str, str] = {}
    try:
        reader = csv.DictReader(stream)
        for raw_row in reader:
            if not raw_row:
                continue
            # Normalize keys and values (Rule 10: Zero-trust boundary data)
            row = {
                (k.strip().lower() if isinstance(k, str) else k): (v.strip() if isinstance(v, str) else "")
                for k, v in raw_row.items()
                if k is not None
            }
            sid = row.get("student_id", "")
            name = row.get("name", "")
            pwd = row.get("password", "")
            if sid and name:
                roster[sid] = name
                if pwd:
                    passwords[sid] = pwd
    except Exception as e:
        logger.error("Failed to parse roster CSV: %s", e)
    return roster, passwords


def _load_roster_from_env_or_disk() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Helper to load student roster and passwords from ROSTER_CSV or students.csv."""
    # 1. Environment Variable (Vercel Serverless Production)
    raw_csv_env = os.getenv("ROSTER_CSV") or os.getenv("STUDENTS_ROSTER_DATA")
    if raw_csv_env and raw_csv_env.strip():
        normalized_content = raw_csv_env.strip().replace("\\n", "\n")
        roster, passwords = _parse_roster_csv_stream(io.StringIO(normalized_content))
        if roster:
            return roster, passwords

    # 2. Local Disk File (Local Polling / VPS)
    if CSV_PATH.exists():
        try:
            with open(CSV_PATH, mode="r", encoding="utf-8-sig") as f:
                roster, passwords = _parse_roster_csv_stream(f)
                if roster:
                    return roster, passwords
        except Exception as e:
            logger.warning("Error reading students.csv from disk: %s", e)

    logger.warning("No student roster available (checked ROSTER_CSV env var and %s)", CSV_PATH)
    return {}, {}


def load_roster_data() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load student roster and password mappings.

    Hierarchy:
    1. Test / Environment Override: When ROSTER_CSV or STUDENTS_ROSTER_DATA is set, or CSV_PATH is customized.
    2. Supabase / PostgreSQL `users` table: When POSTGRES_URL is configured and active.
    3. Default local disk file: students.csv.
    """
    # 1. Environment variable override or customized CSV_PATH (e.g. test fixtures)
    raw_csv_env = os.getenv("ROSTER_CSV") or os.getenv("STUDENTS_ROSTER_DATA")
    is_custom_csv_path = CSV_PATH != (BASE_DIR / "students.csv")
    if (raw_csv_env and raw_csv_env.strip()) or is_custom_csv_path:
        return _load_roster_from_env_or_disk()

    # 2. Supabase PostgreSQL `users` table
    if POSTGRES_URL:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor = execute_query(
                    conn,
                    "SELECT student_id, full_name, raw_password FROM users WHERE student_id IS NOT NULL AND student_id != ''"
                )
                db_roster = {}
                db_passwords = {}
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    name = str(row[1]).strip() if row[1] else ""
                    pwd = str(row[2]).strip() if row[2] else ""
                    if sid and name:
                        db_roster[sid] = name
                        if pwd:
                            db_passwords[sid] = pwd
                if db_roster:
                    # Supplement missing passwords from CSV/ENV roster
                    _, file_pwds = _load_roster_from_env_or_disk()
                    for sid, pwd in file_pwds.items():
                        if sid not in db_passwords or not db_passwords[sid]:
                            db_passwords[sid] = pwd
                    return db_roster, db_passwords
        except Exception as e:
            logger.warning("Could not query student roster from Supabase users table: %s", e)

    return _load_roster_from_env_or_disk()


def load_students_roster() -> Dict[str, str]:
    """
    Load student roster mapping (student_id -> full_name).
    """
    roster, _ = load_roster_data()
    return roster


def load_students_passwords() -> Dict[str, str]:
    """
    Load student password mapping (student_id -> password).
    """
    _, passwords = load_roster_data()
    return passwords


def get_expected_student_password(student_id: str) -> str:
    """Return student-specific password if defined in roster, else fallback to REGISTRATION_PASSWORD."""
    passwords = load_students_passwords()
    student_pwd = passwords.get(student_id, "").strip()
    if student_pwd:
        return student_pwd
    return REGISTRATION_PASSWORD.strip()


# ---------------------------------------------------------------------------
# Keyboards & Helpers
# ---------------------------------------------------------------------------
def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Build persistent reply keyboard for bottom dock."""
    keyboard = [
        [
            KeyboardButton("📝 HW 1"),
            KeyboardButton("📝 HW 2"),
            KeyboardButton("📝 HW 3"),
        ],
        [
            KeyboardButton("📝 HW 4"),
            KeyboardButton("📝 HW 5"),
        ],
        [
            KeyboardButton("📊 My Status"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_hw_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for homework selection (HW 1 to HW 5) and status."""
    keyboard = [
        [
            InlineKeyboardButton("HW 1", callback_data="select_hw_1"),
            InlineKeyboardButton("HW 2", callback_data="select_hw_2"),
            InlineKeyboardButton("HW 3", callback_data="select_hw_3"),
        ],
        [
            InlineKeyboardButton("HW 4", callback_data="select_hw_4"),
            InlineKeyboardButton("HW 5", callback_data="select_hw_5"),
        ],
        [
            InlineKeyboardButton("📊 My Status", callback_data="status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)



import re

def normalize_digits(text: str) -> str:
    if not text: return ""
    text = re.sub(r'[٠-٩]', lambda m: str(ord(m.group(0)) - 1632), text)
    text = re.sub(r'[۰-۹]', lambda m: str(ord(m.group(0)) - 1776), text)
    return text

def extract_student_id(text: str) -> str:
    norm = normalize_digits(text)
    match = re.search(r'\d{5,12}', norm)
    return match.group(0) if match else ""

def parse_homework_selection(text: str) -> Optional[int]:
    """Parse homework number 1..5 from text (e.g. '📝 HW 1', 'HW 2', 'hw3', '1')."""
    if not text:
        return None
    clean = normalize_digits(text).strip().lower()
    if re.match(r"^[1-5]$", clean):
        return int(clean)
    match = re.search(r"(?:📝\s*)?(?:hw|homework)[_\s-]*([1-5])\b", clean)
    if match:
        return int(match.group(1))
    return None

def is_status_request(text: str) -> bool:
    """Check if message text is requesting submission status."""
    if not text:
        return False
    clean = text.strip().lower()
    return clean in ("status", "my status", "📊 my status", "/status") or bool(
        re.match(r"^(?:📊\s*)?(?:my\s*)?status$", clean)
    )

def format_hw_selection_prompt(student_id: str, hw_num: int) -> str:
    """Format assignment prompt message detailing submission/edit status."""
    prev_sub = get_submission(student_id, hw_num)
    if prev_sub:
        _, _, _, submitted_at, edit_count, file_name = prev_sub
        if edit_count >= MAX_EDITS_PER_HW:
            return (
                f"🔒 *Selected: Homework {hw_num} (LOCKED)*\n\n"
                f"⚠️ *Submission Limit Reached:*\n"
                f"You have already submitted the initial version and used all {MAX_EDITS_PER_HW} edits "
                f"(total 3 submissions) for HW {hw_num}.\n\n"
                f"📄 *Archived File:* `{file_name or 'submission.pdf'}`\n"
                f"🕒 *Submitted at:* `{submitted_at}`\n\n"
                f"⛔ Submissions for HW {hw_num} are permanently locked. No further uploads can be accepted."
            )
        else:
            remaining = MAX_EDITS_PER_HW - edit_count
            version_label = "Initial submission" if edit_count == 0 else f"Edit {edit_count}/{MAX_EDITS_PER_HW}"
            return (
                f"📝 *Selected: Homework {hw_num}*\n\n"
                f"ℹ️ *Notice:* Current version is `{version_label}` submitted on `{submitted_at}`.\n"
                f"⚠️ *Edit Limit:* You have **{remaining} edit(s) remaining**.\n"
                f"Uploading a new PDF will **replace** your previous submission in the archive.\n\n"
                f"📎 Please send your replacement PDF file now."
            )
    else:
        return (
            f"📝 *Selected: Homework {hw_num}*\n\n"
            f"📎 Please upload your homework as a **PDF file** (`.pdf`).\n"
            f"_(Note: After your initial submission, you may edit/replace your file up to 2 times.)_"
        )

def is_admin(chat_id: int) -> bool:
    """Check if the given chat_id is in ADMIN_CHAT_IDS."""
    return chat_id in ADMIN_CHAT_IDS


async def send_student_status(update: Update, student: Tuple[int, str, str, str]) -> None:
    """Format and send personal submission status to student or admin."""
    chat_id, student_id, name, registered_at = student
    student_subs = get_student_submissions(student_id)
    student_grades = get_student_grades(student_id)
    is_admin_viewer = bool(update.effective_chat and is_admin(update.effective_chat.id))
    title = "📊 *Student Submission Status (Admin View)*" if is_admin_viewer else "📊 *Your Homework Submission Status*"

    lines = [
        title,
        f"👤 *Student:* {name}",
        f"🆔 *Student ID:* `{student_id}`",
        f"🕒 *Current Time:* `{format_tehran_timestamp()}`",
    ]
    if is_admin_viewer:
        reg_status = f"`{registered_at}` (Chat ID: `{chat_id}`)" if chat_id else "⚠️ *Not registered in bot*"
        lines.append(f"📱 *Telegram Registration:* {reg_status}")
    lines.append("")

    for hw in range(1, 6):
        grade_info = student_grades.get(hw)
        grade_str = f" | 🎯 Graded: *{grade_info['earned_score']:.1f}/{grade_info['max_score']:.0f}*" if grade_info else ""
        if hw in student_subs:
            fname, s_at, e_cnt = student_subs[hw]
            if e_cnt >= MAX_EDITS_PER_HW:
                lines.append(f"• *HW {hw}:* 🔒 Locked (`{fname}`, {e_cnt}/{MAX_EDITS_PER_HW} edits used on `{s_at}`){grade_str}")
            elif e_cnt > 0:
                lines.append(f"• *HW {hw}:* ✅ Submitted (`{fname}`, Edit {e_cnt}/{MAX_EDITS_PER_HW}, {MAX_EDITS_PER_HW - e_cnt} remaining on `{s_at}`){grade_str}")
            else:
                lines.append(f"• *HW {hw}:* ✅ Submitted (`{fname}`, Initial, {MAX_EDITS_PER_HW} edits remaining on `{s_at}`){grade_str}")
        else:
            lines.append(f"• *HW {hw}:* ⏳ Not submitted{grade_str}")

    if not is_admin_viewer:
        lines.append("\nTo submit or update an assignment, select a homework below:")

    msg_text = "\n".join(lines)
    reply_markup = None if is_admin_viewer else get_main_reply_keyboard()
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(
            msg_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
    elif update.message:
        await update.message.reply_text(
            msg_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
    elif update.effective_chat:
        await update.effective_chat.send_message(
            msg_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )


# ---------------------------------------------------------------------------
# Command & Message Handlers
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    chat_id = update.effective_chat.id
    
    state = get_user_state(chat_id)
    if state[2]:
        await update.effective_chat.send_message("🚫 *ACCESS DENIED*\n\nYour account has been banned due to exceeding failed attempts. Please contact your Course TA.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)

    if student:
        _, student_id, name, _ = student
        await update.effective_chat.send_message(
            f"👋 Welcome back, *{name}* (`{student_id}`)!\n\n"
            "Please select a homework assignment below to upload your submission:\n"
            "_(Policy: Up to 2 edits allowed after initial submission per homework)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_reply_keyboard(),
        )
    else:
        clear_pending_student_id(chat_id)
        update_user_state_hw(chat_id, None)
        MAX_ATTEMPTS = 5
        remaining = MAX_ATTEMPTS - state[1]
        await update.effective_chat.send_message(
            f"👋 *Welcome to the TA Homework Collector Bot!*\n\n"
            f"To get started, please enter your **Student ID** (as listed in the course roster):\n\n"
            f"⚠️ _Notice: You have a maximum of {MAX_ATTEMPTS} attempts ({remaining} remaining)_",
            parse_mode=ParseMode.MARKDOWN,
        )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat: return
    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2]:
        await update.effective_chat.send_message(
            "🚫 *ACCESS DENIED*\n\nYour account has been banned due to exceeding failed attempts. Please contact your Course TA.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        clear_pending_student_id(chat_id)
        update_user_state_hw(chat_id, None)
        await update.effective_chat.send_message(
            "🔄 Registration input cleared.\n\nPlease enter your **Student ID** (as listed in the course roster) to begin registration:",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        update_user_state_hw(chat_id, None)
        await update.effective_chat.send_message(
            "🔄 Selection cancelled.\n\nPlease select a homework assignment below:",
            reply_markup=get_main_reply_keyboard(),
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text: return
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    state = get_user_state(chat_id)
    if state[2]:
        await update.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned due to exceeding failed attempts. Please contact your Course TA.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)

    if is_status_request(text):
        if not student:
            await update.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        else:
            await send_student_status(update, student)
        return

    hw_choice = parse_homework_selection(text)
    if hw_choice:
        if not student:
            await update.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
            return
        _, student_id, name, _ = student
        update_user_state_hw(chat_id, hw_choice)
        prompt_msg = format_hw_selection_prompt(student_id, hw_choice)
        await update.message.reply_text(
            prompt_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_reply_keyboard(),
        )
        return

    if student:
        _, student_id, name, _ = student
        selected_hw = state[0]
        if selected_hw:
            sub = get_submission(student_id, selected_hw)
            if sub and sub[4] >= MAX_EDITS_PER_HW:
                status_extra = f"\n\n🔒 *HW {selected_hw} is Locked:* You have reached the maximum {MAX_EDITS_PER_HW} edits limit."
            elif sub:
                remaining = MAX_EDITS_PER_HW - sub[4]
                status_extra = f"\n\nℹ️ You have **{remaining} edit(s) remaining** for HW {selected_hw}."
            else:
                status_extra = ""

            await update.message.reply_text(
                f"You currently have *HW {selected_hw}* selected.{status_extra}\n\n"
                "📎 Please upload your homework as a **PDF file** (`.pdf`), "
                "or choose a different assignment below:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_reply_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"Hello *{name}*! Please select which homework assignment you wish to submit:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_reply_keyboard(),
            )
        return

    MAX_ATTEMPTS = 5
    pending_student_id = state[3]

    # -----------------------------------------------------------------------
    # STAGE 2: Password Verification (when Student ID is pending)
    # -----------------------------------------------------------------------
    if pending_student_id:
        roster = load_students_roster()
        student_name = roster.get(pending_student_id, "Student")

        expected_password = get_expected_student_password(pending_student_id)
        if text == expected_password:
            # Race condition defense: Check if another chat registered this student ID in the interim
            existing = get_student_by_student_id(pending_student_id)
            if existing and existing[0] != chat_id:
                clear_pending_student_id(chat_id)
                await update.message.reply_text(
                    f"⚠️ Student ID `{pending_student_id}` is already registered with another chat.\n\n"
                    "If you switched Telegram accounts or believe this is an error, please contact your Course TA.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            save_student_registration(chat_id=chat_id, student_id=pending_student_id, name=student_name)
            clear_pending_student_id(chat_id)
            reset_attempts(chat_id)

            await update.message.reply_text(
                f"✅ *Registration Successful!*\n\n"
                f"👤 *Name:* {student_name}\n"
                f"🆔 *Student ID:* `{pending_student_id}`\n\n"
                "You can now submit assignments. Select a homework below:\n"
                "_(Up to 2 edits allowed after initial submission)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_reply_keyboard(),
            )
            return
        else:
            attempts = increment_attempts(chat_id)
            if attempts >= MAX_ATTEMPTS:
                blacklist_user(chat_id)
                # Note: Preserve pending_student_id so TAs can identify which student_id was locked
                await update.message.reply_text(
                    "🚫 *MAXIMUM ATTEMPTS EXCEEDED (5/5)*\n\n"
                    "Your account has been placed on the *Blacklist* due to multiple failed password attempts. "
                    "Please contact your Course TA to unblock your account.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                remaining = MAX_ATTEMPTS - attempts
                await update.message.reply_text(
                    f"❌ *Incorrect Course Registration Password!*\n\n"
                    f"Please enter the correct password provided by your Course TA for Student ID `{pending_student_id}`.\n\n"
                    f"⚠️ _Notice: You have {remaining} attempt(s) remaining._\n"
                    f"_(Tip: Send /cancel if you entered the wrong Student ID)_",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

    # -----------------------------------------------------------------------
    # STAGE 1: Student ID Entry & Validation
    # -----------------------------------------------------------------------
    clean_id = extract_student_id(text)
    roster = load_students_roster()
    
    if not roster:
        await update.message.reply_text("⚠️ The course roster is currently empty or unavailable. Please notify your TA.")
        return

    if not clean_id or clean_id not in roster:
        attempts = increment_attempts(chat_id)
        if attempts >= MAX_ATTEMPTS:
            blacklist_user(chat_id)
            await update.message.reply_text("🚫 *MAXIMUM ATTEMPTS EXCEEDED (5/5)*\n\nYour account has been placed on the *Blacklist*.", parse_mode=ParseMode.MARKDOWN)
        else:
            remaining = MAX_ATTEMPTS - attempts
            await update.message.reply_text(
                f"❌ *Invalid Student ID!*\nThat ID is not in the roster.\n\n"
                f"⚠️ _Notice: You have a maximum of {MAX_ATTEMPTS} attempts ({remaining} remaining)_",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    existing = get_student_by_student_id(clean_id)
    if existing and existing[0] != chat_id:
        await update.message.reply_text(
            f"⚠️ Student ID `{clean_id}` is already registered with another chat.\n\n"
            "If you switched Telegram accounts or need a reset, please contact your Course TA.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    student_name = roster[clean_id]
    set_pending_student_id(chat_id, clean_id)
    await update.message.reply_text(
        f"👤 *Student Found:* {student_name} (`{clean_id}`)\n\n"
        "🔐 Please enter the **Course Registration Password** to verify your identity and link your Telegram account:\n\n"
        "_(Tip: Send /cancel to restart if you entered the wrong Student ID)_",
        parse_mode=ParseMode.MARKDOWN,
    )

async def handle_hw_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    if not update.effective_chat: return
    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2]:
        await query.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        await query.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        return

    _, student_id, name, _ = student
    data = query.data or ""
    if not data.startswith("select_hw_"): return

    hw_num = int(data.split("_")[-1])
    update_user_state_hw(chat_id, hw_num)

    msg = format_hw_selection_prompt(student_id, hw_num)
    await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_reply_keyboard())

async def handle_status_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    if not update.effective_chat: return
    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2]:
        await query.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        await query.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        return

    await send_student_status(update, student)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.document: return
    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2]:
        await update.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        await update.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        return

    _, student_id, student_name, _ = student
    selected_hw = state[0]
    if not selected_hw:
        await update.message.reply_text("⚠️ Please select which homework assignment you are submitting first:", reply_markup=get_main_reply_keyboard())
        return

    doc = update.message.document
    file_name = doc.file_name or "submission.pdf"
    mime_type = doc.mime_type or ""

    is_pdf = mime_type == "application/pdf" or file_name.lower().endswith(".pdf")
    if not is_pdf:
        await update.message.reply_text(
            f"❌ *Invalid File Format*\n\nReceived: `{file_name}` ({mime_type or 'unknown type'})\n"
            "Only **PDF** files (`.pdf`) are accepted. Please re-send your file as a PDF.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    prev_sub = get_submission(student_id, selected_hw)
    if prev_sub:
        _, prev_msg_id, _, submitted_at, edit_count, prev_file_name = prev_sub
        if edit_count >= MAX_EDITS_PER_HW:
            await update.message.reply_text(
                f"🔒 *Submission Locked for Homework {selected_hw}*\n\n"
                f"You have already submitted the initial version and used all {MAX_EDITS_PER_HW} allowed edits (total 3 submissions).\n\n"
                f"📄 *Archived Version:* `{prev_file_name or 'submission.pdf'}`\n🕒 *Submitted at:* `{submitted_at}`\n\n"
                f"⛔ *No further uploads can be accepted.* Future submissions for HW {selected_hw} are permanently locked.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    if not ARCHIVE_GROUP_ID:
        logger.error("ARCHIVE_GROUP_ID is not configured in .env!")
        await update.message.reply_text("⚠️ Bot configuration error: Archive group is not configured. Please contact the TA.")
        return

    topic_id = TOPIC_MAP.get(selected_hw)

    if prev_sub:
        prev_msg_id = prev_sub[1]
        try:
            await context.bot.delete_message(chat_id=ARCHIVE_GROUP_ID, message_id=prev_msg_id)
            logger.info("Deleted old archive message %s for student %s on HW %s", prev_msg_id, student_id, selected_hw)
        except TelegramError as e:
            logger.warning("Failed to delete previous message %s from archive: %s", prev_msg_id, e)

    new_edit_count = (prev_sub[4] + 1) if prev_sub else 0
    timestamp_str = format_tehran_timestamp()

    version_str = "Initial Submission" if new_edit_count == 0 else f"Edit {new_edit_count}/{MAX_EDITS_PER_HW}"
    caption = (
        f"📚 *Homework {selected_hw} Submission*\n"
        f"👤 *Student:* {student_name}\n"
        f"🆔 *Student ID:* `{student_id}`\n"
        f"🕒 *Submitted:* `{timestamp_str}`\n"
        f"📝 *Version:* {version_str}"
    )

    try:
        kwargs = {
            "chat_id": ARCHIVE_GROUP_ID,
            "document": doc.file_id,
            "caption": caption,
            "parse_mode": ParseMode.MARKDOWN,
        }
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id

        archive_msg = await context.bot.send_document(**kwargs)
    except TelegramError as e:
        logger.error("Failed to forward document to archive group: %s", e)
        await update.message.reply_text("❌ Failed to archive your document. Please verify the archive group settings with the TA.")
        return

    save_submission(
        chat_id=chat_id,
        student_id=student_id,
        hw_num=selected_hw,
        message_id=archive_msg.message_id,
        file_id=doc.file_id,
        file_name=file_name,
        edit_count=new_edit_count,
    )
    
    update_user_state_hw(chat_id, None)

    remaining_edits = MAX_EDITS_PER_HW - new_edit_count
    if new_edit_count == 0:
        edit_info = f"ℹ️ *Edit Allowance:* You have **{remaining_edits} edit(s) remaining** if you need to replace this file."
    elif new_edit_count < MAX_EDITS_PER_HW:
        edit_info = (
            f"🔄 *Edit {new_edit_count}/{MAX_EDITS_PER_HW} Recorded!*\n"
            f"Previous submission was removed from the archive.\n"
            f"⚠️ You have **{remaining_edits} edit remaining** before HW {selected_hw} is locked."
        )
    else:
        edit_info = (
            f"🔒 *Final Edit {new_edit_count}/{MAX_EDITS_PER_HW} Recorded!*\n"
            f"Previous submission was removed from the archive.\n"
            f"⛔ You have used all {MAX_EDITS_PER_HW} allowed edits. **HW {selected_hw} is now locked** against future submissions."
        )

    await update.message.reply_text(
        f"✅ *Homework {selected_hw} Successfully Submitted!*\n\n"
        f"📄 *File:* `{file_name}`\n"
        f"🕒 *Recorded at:* `{timestamp_str}`\n\n"
        f"Your file has been delivered to the TA archive.\n\n"
        f"{edit_info}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_reply_keyboard(),
    )

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    if not update.effective_chat: return
    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2]:
        await query.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        await query.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        return

    update_user_state_hw(chat_id, None)
    await query.message.reply_text(
        "Please select a homework assignment:",
        reply_markup=get_main_reply_keyboard(),
    )

async def handle_non_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message: return
    chat_id = update.effective_chat.id
    state = get_user_state(chat_id)
    if state[2]:
        await update.message.reply_text("🚫 *ACCESS DENIED*\n\nYour account has been banned.", parse_mode=ParseMode.MARKDOWN)
        return

    student = get_student_by_chat_id(chat_id)
    if not student:
        await update.message.reply_text("⚠️ You are not registered. Please send your Student ID to register.", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(
        "❌ Only **PDF documents** are accepted for homework submissions. Please upload a `.pdf` file.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Diagnostic helper command:
    Replies with chat ID, chat type, thread/topic ID, and user ID.
    Essential for configuring ARCHIVE_GROUP_ID and TOPIC_HW1..5 in .env.
    """
    if not update.effective_chat or not update.message:
        return

    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    thread_id = update.message.message_thread_id
    user_id = update.effective_user.id if update.effective_user else "N/A"

    msg = (
        "🔍 *Telegram Diagnostic Info*\n\n"
        f"• *Chat ID:* `{chat_id}`\n"
        f"• *Chat Type:* `{chat_type}`\n"
        f"• *Topic (Thread) ID:* `{thread_id if thread_id else 'None (General / Direct)'}`\n"
        f"• *Your User ID:* `{user_id}`\n\n"
        "_Tip: Copy the Chat ID into `ARCHIVE_GROUP_ID` and Topic ID into `TOPIC_HWx` in your `.env`._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show submission summary: Admin gets aggregate stats or single student query, student gets personal submission status."""
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    state = get_user_state(chat_id)
    if state[2] and not is_admin(chat_id):
        await update.effective_chat.send_message(
            "🚫 *ACCESS DENIED*\n\nYour account has been banned due to exceeding failed attempts. Please contact your Course TA.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not is_admin(chat_id):
        student = get_student_by_chat_id(chat_id)
        if not student:
            await update.effective_chat.send_message(
                "⚠️ You are not registered. Please send your Student ID to register.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        # Regular students are strictly bound to their own chat_id - command arguments are ignored
        await send_student_status(update, student)
        return

    # Admin execution path
    if context.args:
        target_id = extract_student_id(context.args[0])
        roster = load_students_roster()
        if not target_id or target_id not in roster:
            await update.effective_chat.send_message(
                f"❌ Student ID `{context.args[0]}` not found in course roster.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        target_name = roster[target_id]
        student_record = get_student_by_student_id(target_id)
        registered_chat = student_record[0] if student_record else 0
        registered_at = student_record[3] if student_record else "Not registered"
        await send_student_status(update, (registered_chat, target_id, target_name, registered_at))
        return

    roster = load_students_roster()
    total_roster = len(roster)
    counts = get_submission_counts()

    # Total registered in bot
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = execute_query(conn, "SELECT COUNT(*) FROM students")
        registered_count = cursor.fetchone()[0]

    lines = [
        "📊 *Homework Submissions Status (Admin View)*",
        f"👥 *Total in Roster:* {total_roster}",
        f"👤 *Registered in Bot:* {registered_count} / {total_roster}",
        f"🕒 *Current Time:* `{format_tehran_timestamp()}`",
        "",
    ]

    for hw in range(1, 6):
        count = counts.get(hw, 0)
        pct = (count / total_roster * 100) if total_roster > 0 else 0
        topic_configured = "✓" if TOPIC_MAP.get(hw) else "no topic"
        lines.append(f"• *HW {hw}* ({topic_configured}): {count} / {total_roster} submitted ({pct:.1f}%)")

    lines.append("\n💡 _Tip: Use `/status <student_id>` to view an individual student's submissions._")
    await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def missing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: List students who have not submitted a specific HW."""
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.effective_chat.send_message("⛔ You are not authorized to use admin commands.")
        return

    args = context.args
    if not args or not args[0].isdigit() or not (1 <= int(args[0]) <= 5):
        await update.effective_chat.send_message(
            "⚠️ Usage: `/missing <hw_num>`\nExample: `/missing 1`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    hw_num = int(args[0])
    roster = load_students_roster()
    if not roster:
        await update.effective_chat.send_message("⚠️ Course roster is empty.")
        return

    submitted_ids = get_submitted_student_ids_for_hw(hw_num)
    missing = [
        (sid, name)
        for sid, name in sorted(roster.items())
        if sid not in submitted_ids
    ]

    total_roster = len(roster)
    missing_count = len(missing)

    header = (
        f"📋 *Missing Submissions: HW {hw_num}*\n"
        f"Missing: *{missing_count}* / {total_roster}\n\n"
    )

    if not missing:
        await update.effective_chat.send_message(
            f"🎉 Great news! All {total_roster} students have submitted HW {hw_num}!"
        )
        return

    # Chunk into multiple messages if list is long (> 3500 chars)
    chunk = header
    for sid, name in missing:
        line = f"• `{sid}` - {name}\n"
        if len(chunk) + len(line) > 3800:
            await update.effective_chat.send_message(chunk, parse_mode=ParseMode.MARKDOWN)
            chunk = ""
        chunk += line

    if chunk:
        await update.effective_chat.send_message(chunk, parse_mode=ParseMode.MARKDOWN)


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to unblock a student by student_id or telegram chat_id."""
    if not update.effective_chat or not update.message:
        return
    sender_chat_id = update.effective_chat.id
    if not is_admin(sender_chat_id):
        await update.message.reply_text("⛔ *Unauthorized:* This command is reserved for Course Teaching Assistants.", parse_mode=ParseMode.MARKDOWN)
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ *Usage:* `/unblock <student_id or chat_id>`\n\n"
            "Examples:\n"
            "• `/unblock 401001` (unblock by Student ID)\n"
            "• `/unblock 6103177673` (unblock by Telegram Chat ID)\n\n"
            "_(Clears lockout & resets failed password attempts so the student can use the bot)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target = args[0].strip()
    try:
        from tma_admin import admin_unblock_user
        if target.isdigit() and len(target) >= 8 and len(target) <= 12:
            res = admin_unblock_user(chat_id=int(target))
        else:
            res = admin_unblock_user(student_id=target)

        if res.get("found"):
            target_desc = res.get("student_id") or res.get("chat_id")
            await update.message.reply_text(
                f"✅ *Student Unblocked Successfully*\n\n"
                f"👤 *Target:* `{target_desc}`\n"
                f"💬 *Chat ID:* `{res.get('chat_id')}`\n"
                f"🔓 Lockout state and failed attempts have been cleared. Student can now interact with the bot.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"⚠️ *Not Found:* `{target}` was not found in registered students or active sessions.\n\n"
                "If the student failed registration, please unblock using their Telegram Chat ID:\n"
                "`/unblock <chat_id>`",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        logger.error("Admin /unblock error: %s", e)
        await update.message.reply_text(f"❌ Failed to unblock: {e}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to unbind student session and clear lockouts."""
    if not update.effective_chat or not update.message:
        return
    sender_chat_id = update.effective_chat.id
    if not is_admin(sender_chat_id):
        await update.message.reply_text("⛔ *Unauthorized:* This command is reserved for Course Teaching Assistants.", parse_mode=ParseMode.MARKDOWN)
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ *Usage:* `/reset <student_id or chat_id>`\n\n"
            "Unbinds student's Telegram session and clears failed attempts so they can re-register from scratch.\n"
            "_(Existing homework submissions are safely preserved in the database)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target = args[0].strip()
    try:
        from tma_admin import admin_reset_student
        if target.isdigit() and len(target) >= 8 and len(target) <= 12:
            res = admin_reset_student(chat_id=int(target))
        else:
            res = admin_reset_student(student_id=target)

        if res.get("found"):
            target_desc = res.get("student_id") or res.get("chat_id")
            await update.message.reply_text(
                f"✅ *Student Session Reset Successfully*\n\n"
                f"👤 *Target:* `{target_desc}`\n"
                f"💬 *Chat ID:* `{res.get('chat_id')}`\n"
                f"🔓 Registration unbound & lockout cleared. The student can now send /start to register again.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"⚠️ *Not Found:* `{target}` was not found in registered students or active sessions.",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        logger.error("Admin /reset error: %s", e)
        await update.message.reply_text(f"❌ Failed to reset: {e}")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: Open the Telegram Mini App TA Control Hub."""
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.effective_chat.send_message("⛔ You are not authorized to use admin commands.")
        return

    webhook_host = (WEBHOOK_HOST if WEBHOOK_HOST is not None else os.getenv("WEBHOOK_HOST", "")).strip().rstrip("/")
    if webhook_host:
        app_url = f"{webhook_host}/app"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Open TA Control Hub", web_app=WebAppInfo(url=app_url))]
        ])
        text = (
            "🎛 *TA Control Hub Mini App*\n\n"
            "Welcome, Administrator. Tap below to launch your management dashboard:\n\n"
            "• 📢 *Broadcast Console* — Target announcements & missing HW alerts\n"
            "• 📊 *Roster & Submission Matrix* — Live student submission tracking\n"
            "• ⚙️ *Student Management* — 1-click unblock & authentication resets"
        )
        await update.effective_chat.send_message(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        text = (
            "🎛 *TA Control Hub Mini App*\n\n"
            "⚠️ *Mini App URL Not Configured*\n"
            "The `WEBHOOK_HOST` environment variable is not currently set.\n\n"
            "To enable the Telegram Mini App dashboard:\n"
            "1. Set `WEBHOOK_HOST=https://your-domain.vercel.app` in your `.env`\n"
            "2. Restart the bot or redeploy to Vercel\n\n"
            "In the meantime, you can use CLI admin commands:\n"
            "• `/status` — View aggregate submission metrics\n"
            "• `/status <student_id>` — Query individual student record\n"
            "• `/missing <hw_num>` — List students missing a homework"
        )
        await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled exception."""
    logger.error("Exception while handling an update: %s", context.error, exc_info=context.error)


# ---------------------------------------------------------------------------
# Background HTTP Health-Check Server (for Koyeb / Render Free Tiers)
# ---------------------------------------------------------------------------
class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","service":"ta-homework-bot","version":"10.1.0"}')

    def log_message(self, format: str, *args: object) -> None:
        # Suppress periodic health-check access logs
        pass


def run_health_check_server() -> None:
    """Run lightweight HTTP health-check server on PORT (default 8000)."""
    raw_port = os.getenv("PORT", "8000").strip()
    port = int(raw_port) if raw_port.isdigit() else 8000
    try:
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            logger.info("Health check server active on port %s", port)
            httpd.serve_forever()
    except Exception as e:
        logger.warning("Could not bind health-check server on port %s: %s", port, e)


# ---------------------------------------------------------------------------
# Main Application Entrypoint
# ---------------------------------------------------------------------------
def create_app(token: str | None = None) -> Application:
    init_db()
    effective_token = token or BOT_TOKEN or "123456789:AAH_DummyTestTokenForOfflineTesting12345"

    async def post_init(application: Application) -> None:
        try:
            await application.bot.set_my_commands([
                BotCommand("start", "Start bot & view assignments"),
                BotCommand("status", "Check submission & edit status"),
                BotCommand("cancel", "Cancel current input or selection"),
                BotCommand("id", "Show your Telegram Chat ID"),
                BotCommand("admin", "Open TA Control Hub Mini App"),
                BotCommand("unblock", "Admin: Unblock student lockout"),
                BotCommand("reset", "Admin: Reset student session"),
            ])
            logger.info("Bot commands successfully registered with Telegram.")
        except Exception as e:
            logger.warning("Could not register bot commands with Telegram: %s", e)

    app = Application.builder().token(effective_token).post_init(post_init).build()
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('cancel', cancel_command))
    app.add_handler(CommandHandler('id', id_command))
    app.add_handler(CommandHandler('status', status_command))
    app.add_handler(CommandHandler('missing', missing_command))
    app.add_handler(CommandHandler('admin', admin_command))
    app.add_handler(CommandHandler('unblock', unblock_command))
    app.add_handler(CommandHandler('reset', reset_command))
    app.add_handler(CallbackQueryHandler(handle_hw_button, pattern=r'^select_hw_\d+$'))
    app.add_handler(CallbackQueryHandler(handle_status_button, pattern=r'^status$'))
    app.add_handler(CallbackQueryHandler(handle_main_menu, pattern=r'^main_menu$'))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    media_filter = filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Sticker.ALL | filters.ANIMATION
    app.add_handler(MessageHandler(media_filter, handle_non_pdf))
    app.add_error_handler(error_handler)
    return app

def main() -> None:
    """Initialize database and start bot polling."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing! Please set BOT_TOKEN in your .env file.")
        raise ValueError("BOT_TOKEN is required to run the bot.")

    app = create_app()
    server_thread = threading.Thread(target=run_health_check_server, daemon=True)
    server_thread.start()

    logger.info("Bot starting in polling mode...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
