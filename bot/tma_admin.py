"""
TA Homework Collector Bot - Telegram Mini App (TMA) Administration Module
-------------------------------------------------------------------------
Provides Telegram Mini App endpoints, cryptographic initData HMAC-SHA256 validation,
submission matrix queries, student unbind / lockout resets, and targeted broadcast dispatch.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import bot

logger = logging.getLogger("TMAAdmin")

# Path to template file
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "tma_app.html"


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class BroadcastRequest(BaseModel):
    target: str = Field(..., description="Target audience: 'all', 'missing_1'..'missing_5', 'single'")
    message: str = Field(..., min_length=1, description="Message text in Markdown format")
    student_id: Optional[str] = Field(None, description="Student ID if target is 'single'")


class ResetRequest(BaseModel):
    student_id: Optional[str] = Field(None, description="Student ID to unbind and reset")
    chat_id: Optional[int] = Field(None, description="Telegram chat ID to reset")


# ---------------------------------------------------------------------------
# Telegram WebApp Cryptographic Validation (HMAC-SHA256)
# ---------------------------------------------------------------------------

def get_all_admin_chat_ids() -> Set[int]:
    """Return all administrator chat IDs from bot.ADMIN_CHAT_IDS plus Supabase users with admin/ta roles."""
    admin_ids = set(bot.ADMIN_CHAT_IDS)
    try:
        with bot.get_db_conn() as conn:
            cursor = bot.execute_query(
                conn,
                """
                SELECT s.chat_id 
                FROM students s 
                JOIN users u ON u.student_id = s.student_id 
                WHERE u.role IN ('admin', 'ta', 'instructor')
                """
            )
            for row in cursor.fetchall():
                if row[0]:
                    admin_ids.add(int(row[0]))
    except Exception as e:
        logger.debug("Could not supplement admin IDs from database: %s", e)
    return admin_ids


def validate_init_data(
    init_data_str: str,
    bot_token: str,
    admin_chat_ids: Set[int],
    max_age_seconds: int = 604800,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Validate Telegram WebApp initData string using HMAC-SHA256 and verify admin privileges.

    Telegram Mini App Cryptographic Protocol:
    1. Parse query-string into key-value pairs.
    2. Extract and remove 'hash'.
    3. Sort pairs alphabetically by key and concatenate as 'key=value\\n'.
    4. Compute secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token).
    5. Compute signature = HMAC_SHA256(key=secret_key, msg=data_check_string).hexdigest().
    6. Verify signature match with constant-time comparison.
    7. Verify auth_date freshness against max_age_seconds.
    8. Parse user object and verify user['id'] in admin_chat_ids.
    """
    if not init_data_str or not init_data_str.strip():
        return False, "Missing initData", None

    if not bot_token:
        return False, "BOT_TOKEN is not configured on server", None

    try:
        parsed_pairs = urllib.parse.parse_qsl(init_data_str, keep_blank_values=True, strict_parsing=False)
    except Exception as e:
        return False, f"Malformed initData query string: {e}", None

    params = dict(parsed_pairs)
    received_hash = params.pop("hash", None)
    if not received_hash:
        return False, "Missing hash in initData", None

    # Step 3: Sort alphabetically and construct data_check_string
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items(), key=lambda x: x[0]))

    # Step 4 & 5: HMAC-SHA256 computation
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    # Step 6: Constant-time signature comparison
    if not hmac.compare_digest(calculated_hash, received_hash):
        return False, "Invalid initData signature", None

    # Step 7: auth_date freshness check
    auth_date_raw = params.get("auth_date")
    if not auth_date_raw or not auth_date_raw.isdigit():
        return False, "Missing or invalid auth_date in initData", None

    auth_date = int(auth_date_raw)
    now = int(time.time())
    if max_age_seconds > 0:
        if (now - auth_date) > max_age_seconds:
            return False, "Expired initData: auth_date exceeds max age", None
        if auth_date > (now + 600):
            return False, "Invalid initData: auth_date is in the future", None

    # Step 8: Parse user and check admin authorization
    user_raw = params.get("user")
    if not user_raw:
        return False, "Missing user parameter in initData", None

    try:
        user_data = json.loads(user_raw)
    except Exception as e:
        return False, f"Invalid JSON in user parameter: {e}", None

    user_id = user_data.get("id")
    if user_id is None:
        return False, "Missing user id in initData user object", None

    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return False, "Invalid user id format in initData", None

    if user_id_int not in admin_chat_ids:
        return False, f"Unauthorized: User ID {user_id_int} is not an administrator", user_data

    return True, "Authorized", user_data


def extract_init_data_from_request(
    request: Request,
    init_data_header: Optional[str] = None,
    authorization: Optional[str] = None,
) -> str:
    """Extract initData query string from header, Authorization header, or query parameters."""
    if init_data_header and init_data_header.strip():
        return init_data_header.strip()

    if authorization and authorization.strip():
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() in ("tma", "bearer"):
            return parts[1].strip()
        return authorization.strip()

    qp = (
        request.query_params.get("initData")
        or request.query_params.get("init_data")
        or request.query_params.get("tgWebAppData")
    )
    if qp and qp.strip():
        return qp.strip()

    return ""


async def verify_admin_request(
    request: Request,
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
    authorization: Optional[str] = Header(None),
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    """FastAPI dependency to authenticate and verify administrator privileges via Telegram WebApp initData or admin secret."""
    # 1. Admin Secret / Token check (for web browser TA dashboard access & integrations)
    secret_candidate = (
        x_admin_secret
        or request.query_params.get("token")
        or request.query_params.get("admin_key")
    )
    valid_secrets = {
        os.getenv("ADMIN_SECRET", ""),
        os.getenv("DB_PASSWORD", ""),
        bot.BOT_TOKEN,
    }
    valid_secrets.discard("")
    if secret_candidate and secret_candidate.strip() in valid_secrets:
        return {"id": 0, "first_name": "Admin", "role": "admin", "via": "secret"}

    # 2. Telegram WebApp initData verification
    init_data_str = extract_init_data_from_request(
        request=request,
        init_data_header=x_telegram_init_data,
        authorization=authorization,
    )
    if not init_data_str:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: Telegram WebApp initData missing",
        )

    all_admins = get_all_admin_chat_ids()
    is_valid, msg, user_data = validate_init_data(
        init_data_str=init_data_str,
        bot_token=bot.BOT_TOKEN,
        admin_chat_ids=all_admins,
    )
    if not is_valid:
        if user_data and "not an administrator" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=401, detail=msg)

    return user_data or {}


# ---------------------------------------------------------------------------
# Core Database Operations for TMA Admin
# ---------------------------------------------------------------------------

def get_admin_metrics() -> Dict[str, Any]:
    """Calculate summary statistics for administrative oversight."""
    roster = bot.load_students_roster()
    enrolled_count = len(roster)
    submission_counts = bot.get_submission_counts()

    hw_counts = {str(hw): submission_counts.get(hw, 0) for hw in range(1, 6)}
    total_submissions = sum(submission_counts.values())

    with bot.get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = bot.execute_query(conn, "SELECT COUNT(*) FROM students")
        registered_count = cursor.fetchone()[0]

        # Locked out or blacklisted count
        if bot.POSTGRES_URL:
            cursor = bot.execute_query(
                conn,
                "SELECT COUNT(*) FROM user_state WHERE is_blacklisted = TRUE OR attempts >= 3",
            )
        else:
            cursor = bot.execute_query(
                conn,
                "SELECT COUNT(*) FROM user_state WHERE is_blacklisted = 1 OR attempts >= 3",
            )
        locked_count = cursor.fetchone()[0]

    return {
        "enrolled_count": enrolled_count,
        "registered_count": registered_count,
        "locked_count": locked_count,
        "submission_counts": hw_counts,
        "total_submissions": total_submissions,
        "server_time": bot.format_tehran_timestamp(),
    }


def get_admin_roster() -> List[Dict[str, Any]]:
    """Generate complete course roster submission and status matrix."""
    roster = bot.load_students_roster()

    registered_map: Dict[str, Tuple[int, str, str]] = {}
    with bot.get_db_conn() as conn:
        cursor = conn.cursor()
        cursor = bot.execute_query(conn, "SELECT chat_id, student_id, name, registered_at FROM students")
        for row in cursor.fetchall():
            registered_map[str(row[1])] = (row[0], row[2], row[3])

        # Submissions map: sid -> {hw_num: info}
        submissions_map: Dict[str, Dict[int, Dict[str, Any]]] = {}
        cursor = bot.execute_query(
            conn,
            "SELECT student_id, hw_num, file_name, submitted_at, edit_count FROM submissions",
        )
        for row in cursor.fetchall():
            sid, hw_num, fname, sub_at, ed_count = str(row[0]), row[1], row[2], row[3], row[4]
            if sid not in submissions_map:
                submissions_map[sid] = {}
            submissions_map[sid][hw_num] = {
                "submitted": True,
                "file_name": fname or f"HW{hw_num}.pdf",
                "submitted_at": sub_at,
                "edit_count": ed_count,
            }

        # User state map: chat_id -> (attempts, is_blacklisted)
        # Also map pending_student_id -> (chat_id, attempts, is_blacklisted) for unregistered students
        user_state_map: Dict[int, Tuple[int, bool]] = {}
        user_state_by_pending: Dict[str, Tuple[int, int, bool]] = {}
        cursor = bot.execute_query(conn, "SELECT chat_id, attempts, is_blacklisted, pending_student_id FROM user_state")
        for row in cursor.fetchall():
            cid = row[0]
            att = row[1]
            blk = bool(row[2])
            user_state_map[cid] = (att, blk)
            if row[3]:
                user_state_by_pending[str(row[3]).strip()] = (cid, att, blk)

    # Merge roster and registered students
    all_sids = list(roster.keys())
    for sid in registered_map:
        if sid not in roster:
            all_sids.append(sid)

    # Batch fetch all student grades in a single query
    all_grades_map = bot.get_all_student_grades_map() if hasattr(bot, "get_all_student_grades_map") else {}

    matrix: List[Dict[str, Any]] = []
    for sid in all_sids:
        name = roster.get(sid) or registered_map.get(sid, (0, "Unknown", ""))[1]
        reg_info = registered_map.get(sid)
        is_registered = reg_info is not None
        chat_id = reg_info[0] if reg_info else None
        registered_at = reg_info[2] if reg_info else None

        attempts = 0
        is_blacklisted = False
        if chat_id and chat_id in user_state_map:
            attempts, is_blacklisted = user_state_map[chat_id]
        elif not is_registered and sid in user_state_by_pending:
            # Student attempted to register, link their pending chat_id and lockout state!
            chat_id, attempts, is_blacklisted = user_state_by_pending[sid]

        student_subs = submissions_map.get(sid, {})
        student_grades = all_grades_map.get(sid, {})
        subs: Dict[str, Dict[str, Any]] = {}
        for hw in range(1, 6):
            grade_info = student_grades.get(hw)
            if hw in student_subs:
                sub_data = dict(student_subs[hw])
                sub_data["grade"] = grade_info
                subs[str(hw)] = sub_data
            else:
                subs[str(hw)] = {
                    "submitted": False,
                    "file_name": None,
                    "submitted_at": None,
                    "edit_count": 0,
                    "grade": grade_info,
                }

        matrix.append({
            "student_id": sid,
            "name": name,
            "is_registered": is_registered,
            "chat_id": chat_id,
            "registered_at": registered_at,
            "attempts": attempts,
            "is_blacklisted": is_blacklisted,
            "is_locked": is_blacklisted or attempts >= 3,
            "submissions": subs,
        })

    # Append any standalone locked Telegram chat IDs not mapped to known roster students
    mapped_chat_ids = {item["chat_id"] for item in matrix if item.get("chat_id")}
    for cid, (att, blk) in user_state_map.items():
        if (blk or att >= 3) and cid not in mapped_chat_ids:
            matrix.append({
                "student_id": f"TG-{cid}",
                "name": f"Unregistered User ({cid})",
                "is_registered": False,
                "chat_id": cid,
                "registered_at": None,
                "attempts": att,
                "is_blacklisted": blk,
                "is_locked": True,
                "submissions": {
                    str(hw): {
                        "submitted": False,
                        "file_name": None,
                        "submitted_at": None,
                        "edit_count": 0,
                        "grade": None,
                    } for hw in range(1, 6)
                },
            })

    return matrix


def admin_unblock_user(
    student_id: Optional[str] = None,
    chat_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Clear lockout state (attempts = 0, is_blacklisted = FALSE) in `user_state`
    WITHOUT deleting the student from `students` table.
    """
    if not student_id and not chat_id:
        raise ValueError("Either student_id or chat_id must be provided")

    # If student_id looks like a Telegram chat_id (e.g. 8-12 digits) and chat_id is None
    if student_id and not chat_id:
        sid_str = str(student_id).strip()
        if sid_str.isdigit() and len(sid_str) >= 8 and len(sid_str) <= 12:
            chat_id = int(sid_str)
            student_id = None

    resolved_chat_id = chat_id
    resolved_student_id = student_id
    found = False

    with bot.get_db_conn() as conn:
        if student_id:
            cursor = bot.execute_query(
                conn,
                "SELECT chat_id, student_id, name FROM students WHERE student_id = ?",
                (str(student_id).strip(),),
            )
            row = cursor.fetchone()
            if row:
                found = True
                resolved_chat_id = row[0]
                resolved_student_id = row[1]
            else:
                cursor = bot.execute_query(
                    conn,
                    "SELECT chat_id FROM user_state WHERE pending_student_id = ?",
                    (str(student_id).strip(),),
                )
                p_row = cursor.fetchone()
                if p_row:
                    found = True
                    resolved_chat_id = p_row[0]

        if chat_id:
            found = True
            cursor = bot.execute_query(
                conn,
                "SELECT student_id, name FROM students WHERE chat_id = ?",
                (chat_id,),
            )
            row = cursor.fetchone()
            if row:
                resolved_student_id = row[0]
            else:
                cursor = bot.execute_query(
                    conn,
                    "SELECT pending_student_id FROM user_state WHERE chat_id = ?",
                    (chat_id,),
                )
                p_row = cursor.fetchone()
                if p_row and p_row[0]:
                    resolved_student_id = p_row[0]

        if resolved_chat_id:
            c = bot.execute_query(conn, "SELECT chat_id FROM user_state WHERE chat_id = ?", (resolved_chat_id,))
            if c.fetchone():
                if bot.POSTGRES_URL:
                    bot.execute_query(
                        conn,
                        "UPDATE user_state SET attempts = 0, is_blacklisted = FALSE, selected_hw = NULL WHERE chat_id = ?",
                        (resolved_chat_id,),
                    )
                else:
                    bot.execute_query(
                        conn,
                        "UPDATE user_state SET attempts = 0, is_blacklisted = 0, selected_hw = NULL WHERE chat_id = ?",
                        (resolved_chat_id,),
                    )
            else:
                if bot.POSTGRES_URL:
                    bot.execute_query(
                        conn,
                        "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, FALSE, NULL)",
                        (resolved_chat_id,),
                    )
                else:
                    bot.execute_query(
                        conn,
                        "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, 0, NULL)",
                        (resolved_chat_id,),
                    )
            conn.commit()
            return {
                "status": "ok",
                "message": f"Lockout cleared for {resolved_student_id or resolved_chat_id}. Account is unblocked and attempts reset.",
                "student_id": resolved_student_id,
                "chat_id": resolved_chat_id,
                "found": True,
            }

        conn.commit()

    return {
        "status": "not_found",
        "message": f"Target '{student_id or chat_id}' was not found in registered students or active sessions. Please provide their Telegram Chat ID to unblock directly.",
        "student_id": student_id,
        "chat_id": chat_id,
        "found": False,
    }


def admin_reset_student(
    student_id: Optional[str] = None,
    chat_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Unbind student from `students` and reset `attempts` / `is_blacklisted` in `user_state`.
    Allows the student to re-register with their student ID or clears lockout states.
    """
    if not student_id and not chat_id:
        raise ValueError("Either student_id or chat_id must be provided")

    # If student_id looks like a Telegram chat_id (e.g. 8-12 digits) and chat_id is None
    if student_id and not chat_id:
        sid_str = str(student_id).strip()
        if sid_str.isdigit() and len(sid_str) >= 8 and len(sid_str) <= 12:
            chat_id = int(sid_str)
            student_id = None

    resolved_chat_id = chat_id
    resolved_student_id = student_id
    found = False
    unbound = False
    unblocked = False

    with bot.get_db_conn() as conn:
        if student_id:
            cursor = bot.execute_query(
                conn,
                "SELECT chat_id, student_id, name FROM students WHERE student_id = ?",
                (str(student_id).strip(),),
            )
            row = cursor.fetchone()
            if row:
                found = True
                unbound = True
                resolved_chat_id = row[0]
                resolved_student_id = row[1]
                bot.execute_query(conn, "DELETE FROM students WHERE student_id = ?", (row[1],))
            else:
                # Check user_state pending_student_id if not yet registered
                cursor = bot.execute_query(
                    conn,
                    "SELECT chat_id FROM user_state WHERE pending_student_id = ?",
                    (str(student_id).strip(),),
                )
                p_row = cursor.fetchone()
                if p_row:
                    found = True
                    resolved_chat_id = p_row[0]

        if chat_id:
            found = True
            cursor = bot.execute_query(
                conn,
                "SELECT chat_id, student_id, name FROM students WHERE chat_id = ?",
                (chat_id,),
            )
            row = cursor.fetchone()
            if row:
                unbound = True
                resolved_student_id = row[1]
                bot.execute_query(conn, "DELETE FROM students WHERE chat_id = ?", (chat_id,))
            else:
                cursor = bot.execute_query(
                    conn,
                    "SELECT pending_student_id FROM user_state WHERE chat_id = ?",
                    (chat_id,),
                )
                p_row = cursor.fetchone()
                if p_row and p_row[0]:
                    resolved_student_id = p_row[0]

        if resolved_chat_id:
            c = bot.execute_query(conn, "SELECT chat_id FROM user_state WHERE chat_id = ?", (resolved_chat_id,))
            if c.fetchone():
                if bot.POSTGRES_URL:
                    bot.execute_query(
                        conn,
                        "UPDATE user_state SET attempts = 0, is_blacklisted = FALSE, selected_hw = NULL, pending_student_id = NULL WHERE chat_id = ?",
                        (resolved_chat_id,),
                    )
                else:
                    bot.execute_query(
                        conn,
                        "UPDATE user_state SET attempts = 0, is_blacklisted = 0, selected_hw = NULL, pending_student_id = NULL WHERE chat_id = ?",
                        (resolved_chat_id,),
                    )
            else:
                if bot.POSTGRES_URL:
                    bot.execute_query(
                        conn,
                        "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, FALSE, NULL)",
                        (resolved_chat_id,),
                    )
                else:
                    bot.execute_query(
                        conn,
                        "INSERT INTO user_state (chat_id, selected_hw, attempts, is_blacklisted, pending_student_id) VALUES (?, NULL, 0, 0, NULL)",
                        (resolved_chat_id,),
                    )
            unblocked = True
        elif not found:
            conn.commit()
            return {
                "status": "not_found",
                "message": f"Student '{student_id or chat_id}' was not found in registered students or active sessions. Please provide their Telegram Chat ID to unblock directly.",
                "student_id": student_id,
                "chat_id": chat_id,
                "found": False,
                "unbound": False,
                "unblocked": False,
            }

        conn.commit()

    actions = []
    if unbound:
        actions.append("session unbound")
    if unblocked:
        actions.append("lockout cleared")
    action_str = " and ".join(actions) if actions else "state reset"

    return {
        "status": "ok",
        "message": f"Student {resolved_student_id or resolved_chat_id} ({action_str}) completed successfully.",
        "student_id": resolved_student_id,
        "chat_id": resolved_chat_id,
        "found": True,
        "unbound": unbound,
        "unblocked": unblocked,
    }


async def admin_broadcast(
    bot_instance: Any,
    target: str,
    message: str,
    student_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dispatch targeted broadcast messages with rate limiting and comprehensive error tracking.
    Catches Forbidden (bot blocked by user) and returns detailed statistics.
    """
    if not message or not message.strip():
        raise ValueError("Broadcast message cannot be empty")

    roster_matrix = get_admin_roster()
    candidates: List[Dict[str, Any]] = []

    if target == "all":
        candidates = [s for s in roster_matrix if s["is_registered"] and s["chat_id"]]
    elif target.startswith("missing_"):
        try:
            hw_num = str(int(target.split("_")[1]))
            candidates = [
                s for s in roster_matrix
                if s["is_registered"] and s["chat_id"] and not s["submissions"].get(hw_num, {}).get("submitted", False)
            ]
        except Exception as e:
            raise ValueError(f"Invalid missing HW target '{target}': {e}")
    elif target == "single":
        if not student_id:
            raise ValueError("student_id is required when target is 'single'")
        candidates = [
            s for s in roster_matrix
            if s["student_id"] == str(student_id).strip() and s["is_registered"] and s["chat_id"]
        ]
        if not candidates:
            unreg = [s for s in roster_matrix if s["student_id"] == str(student_id).strip()]
            if unreg:
                raise ValueError(f"Student {student_id} is in roster but not registered in bot (no chat_id)")
            raise ValueError(f"Student ID {student_id} not found in course roster")
    else:
        raise ValueError(f"Unknown broadcast target: {target}")

    total = len(candidates)
    sent = 0
    failed = 0
    details: List[Dict[str, Any]] = []

    # Import telegram error types defensively
    try:
        from telegram.constants import ParseMode
        from telegram.error import Forbidden, TelegramError
    except ImportError:
        class TelegramError(Exception): pass
        class Forbidden(TelegramError): pass
        class ParseMode: MARKDOWN = "Markdown"

    for c in candidates:
        cid = c["chat_id"]
        sid = c["student_id"]
        sname = c["name"]
        try:
            try:
                await bot_instance.send_message(
                    chat_id=cid,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError as te:
                if "can't parse entities" in str(te).lower() or "markdown" in str(te).lower():
                    await bot_instance.send_message(chat_id=cid, text=message)
                else:
                    raise te
            sent += 1
            details.append({"student_id": sid, "chat_id": cid, "name": sname, "status": "sent"})
            await asyncio.sleep(0.04)  # Rate limiting protection
        except Forbidden:
            failed += 1
            details.append({
                "student_id": sid,
                "chat_id": cid,
                "name": sname,
                "status": "failed",
                "reason": "Forbidden: Bot was blocked by user",
            })
        except Exception as e:
            failed += 1
            details.append({
                "student_id": sid,
                "chat_id": cid,
                "name": sname,
                "status": "failed",
                "reason": str(e),
            })

    return {
        "total": total,
        "sent": sent,
        "failed": failed,
        "details": details,
    }


def get_app_html() -> str:
    """Load Mini App single-page application HTML."""
    if TEMPLATE_PATH.exists():
        try:
            return TEMPLATE_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read %s: %s", TEMPLATE_PATH, e)
    return "<h1>TA Control Hub</h1><p>Template file not found.</p>"


# ---------------------------------------------------------------------------
# FastAPI Router Factory
# ---------------------------------------------------------------------------

def create_admin_router(get_bot_func: Optional[Callable[[], Any]] = None) -> APIRouter:
    """Create and configure FastAPI router for Telegram Mini App admin operations."""
    router = APIRouter()

    @router.get("/app", response_class=HTMLResponse)
    async def serve_mini_app():
        """Serve the sleek single-page Telegram Mini App dashboard."""
        return HTMLResponse(content=get_app_html(), status_code=200)

    @router.get("/api/app", response_class=HTMLResponse)
    async def serve_mini_app_api():
        """Alias for /app when accessed via API prefix."""
        return HTMLResponse(content=get_app_html(), status_code=200)

    @router.get("/api/admin/metrics")
    async def get_metrics(admin_user: Dict[str, Any] = Depends(verify_admin_request)):
        """Return summary metrics for the Mini App dashboard."""
        try:
            return get_admin_metrics()
        except Exception as e:
            logger.error("Failed to fetch admin metrics: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/admin/roster")
    async def get_roster(admin_user: Dict[str, Any] = Depends(verify_admin_request)):
        """Return full matrix of students, submission progress, and lockout state."""
        try:
            return get_admin_roster()
        except Exception as e:
            logger.error("Failed to fetch admin roster: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/admin/broadcast")
    async def post_broadcast(
        req: BroadcastRequest,
        admin_user: Dict[str, Any] = Depends(verify_admin_request),
    ):
        """Dispatch broadcast messages to target student cohort."""
        if get_bot_func is None:
            raise HTTPException(status_code=500, detail="Bot dispatch function not initialized")

        bot_inst = get_bot_func()
        if bot_inst is None:
            raise HTTPException(status_code=500, detail="Bot instance unavailable")

        try:
            result = await admin_broadcast(
                bot_instance=bot_inst,
                target=req.target,
                message=req.message,
                student_id=req.student_id,
            )
            return result
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error("Broadcast dispatch failure: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/admin/reset")
    async def post_reset(
        req: ResetRequest,
        admin_user: Dict[str, Any] = Depends(verify_admin_request),
    ):
        """Unbind student from `students` and reset `attempts` / `is_blacklisted` in `user_state`."""
        if not req.student_id and not req.chat_id:
            raise HTTPException(status_code=400, detail="Either student_id or chat_id is required")

        try:
            result = admin_reset_student(student_id=req.student_id, chat_id=req.chat_id)
            if not result.get("found"):
                raise HTTPException(status_code=404, detail=result.get("message", "Target not found"))
            return result
        except HTTPException:
            raise
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error("Student reset failure: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/admin/unblock")
    async def post_unblock(
        req: ResetRequest,
        admin_user: Dict[str, Any] = Depends(verify_admin_request),
    ):
        """Unblock student in `user_state` without unbinding their student registration."""
        if not req.student_id and not req.chat_id:
            raise HTTPException(status_code=400, detail="Either student_id or chat_id is required")

        try:
            result = admin_unblock_user(student_id=req.student_id, chat_id=req.chat_id)
            if not result.get("found"):
                raise HTTPException(status_code=404, detail=result.get("message", "Target not found"))
            return result
        except HTTPException:
            raise
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error("Student unblock failure: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return router
