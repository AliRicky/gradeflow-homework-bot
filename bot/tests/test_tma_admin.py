"""
Unit & Integration Test Suite for Telegram Mini App (TMA) Administration
------------------------------------------------------------------------
Validates cryptographic initData HMAC-SHA256 authentication, admin FastAPI endpoints,
rate-limited targeted broadcasts with Telegram error handling, student lockout resets,
and bot /admin command contracts.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import unittest.mock
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import tma_admin
from api.index import app

# Constants for testing
TEST_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_test_token"
ADMIN_USER_ID = 987654321
NON_ADMIN_USER_ID = 112233445


def generate_init_data(
    bot_token: str,
    user_id: int,
    auth_date: int = None,
    tampered_sig: bool = False,
    tampered_payload: bool = False,
    include_hash: bool = True,
    include_user: bool = True,
    first_name: str = "AdminTester",
) -> str:
    """Generate cryptographically valid or intentionally corrupted Telegram WebApp initData query string."""
    if auth_date is None:
        auth_date = int(time.time())

    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
    }
    if include_user:
        params["user"] = json.dumps({
            "id": user_id,
            "first_name": first_name,
            "username": "adm_user",
        })

    # Sort alphabetically and build check string
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(params.items(), key=lambda x: x[0]))

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, check_str.encode("utf-8"), hashlib.sha256).hexdigest()

    if tampered_sig:
        calc_hash = "deadbeef" * 8

    if include_hash:
        params["hash"] = calc_hash

    if tampered_payload:
        params["auth_date"] = str(auth_date + 100)  # modified after hash was computed

    return urllib.parse.urlencode(params)


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path: Path, monkeypatch):
    """Set up isolated SQLite database, test tokens, and mock roster for test execution."""
    test_db = tmp_path / "test_tma_admin.db"
    monkeypatch.setattr(bot, "DATABASE_PATH", test_db)
    monkeypatch.setattr(bot, "POSTGRES_URL", None)
    monkeypatch.setattr(bot, "BOT_TOKEN", TEST_BOT_TOKEN)
    monkeypatch.setattr(bot, "ADMIN_CHAT_IDS", {ADMIN_USER_ID})
    monkeypatch.setattr(bot, "WEBHOOK_HOST", "https://test-homework-bot.vercel.app")

    # Mock roster
    mock_roster = {
        "4001": "Alice Test",
        "4002": "Bob Test",
        "4003": "Charlie Test",
    }
    monkeypatch.setattr(bot, "load_students_roster", lambda: mock_roster)
    monkeypatch.setattr(bot, "load_students_passwords", lambda: {"4001": "pwd1", "4002": "pwd2", "4003": "pwd3"})

    bot.init_db()


# ---------------------------------------------------------------------------
# 1. Cryptographic initData Validation Tests
# ---------------------------------------------------------------------------

def test_validate_init_data_valid_admin():
    """Verify that an authentic HMAC signature for an administrator passes validation."""
    init_data = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
    )
    assert is_valid is True
    assert msg == "Authorized"
    assert user is not None
    assert user["id"] == ADMIN_USER_ID


def test_validate_init_data_tampered_signature():
    """Verify that an altered HMAC signature is rejected."""
    init_data = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, tampered_sig=True)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
    )
    assert is_valid is False
    assert "Invalid initData signature" in msg
    assert user is None


def test_validate_init_data_tampered_payload():
    """Verify that payload modification after signature generation invalidates check."""
    init_data = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, tampered_payload=True)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
    )
    assert is_valid is False
    assert "Invalid initData signature" in msg


def test_validate_init_data_expired_auth_date():
    """Verify that an initData token older than max_age_seconds is rejected."""
    old_timestamp = int(time.time()) - 90000  # older than 86400s (24h)
    init_data = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, auth_date=old_timestamp)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
        max_age_seconds=86400,
    )
    assert is_valid is False
    assert "Expired initData" in msg


def test_validate_init_data_future_auth_date():
    """Verify that an initData token with an auth_date far in the future is rejected."""
    future_timestamp = int(time.time()) + 1000
    init_data = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, auth_date=future_timestamp)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
    )
    assert is_valid is False
    assert "Invalid initData: auth_date is in the future" in msg


def test_validate_init_data_non_admin_user():
    """Verify that a validly signed token from a non-admin user is rejected with authorization notice."""
    init_data = generate_init_data(TEST_BOT_TOKEN, NON_ADMIN_USER_ID)
    is_valid, msg, user = tma_admin.validate_init_data(
        init_data_str=init_data,
        bot_token=TEST_BOT_TOKEN,
        admin_chat_ids={ADMIN_USER_ID},
    )
    assert is_valid is False
    assert f"User ID {NON_ADMIN_USER_ID} is not an administrator" in msg
    assert user is not None
    assert user["id"] == NON_ADMIN_USER_ID


def test_validate_init_data_missing_parameters():
    """Verify robust handling of missing initData, missing hash, and missing user parameter."""
    # Empty string
    valid, msg, _ = tma_admin.validate_init_data("", TEST_BOT_TOKEN, {ADMIN_USER_ID})
    assert valid is False and "Missing initData" in msg

    # Missing hash
    no_hash = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, include_hash=False)
    valid, msg, _ = tma_admin.validate_init_data(no_hash, TEST_BOT_TOKEN, {ADMIN_USER_ID})
    assert valid is False and "Missing hash" in msg

    # Missing user
    no_user = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, include_user=False)
    valid, msg, _ = tma_admin.validate_init_data(no_user, TEST_BOT_TOKEN, {ADMIN_USER_ID})
    assert valid is False and "Missing user" in msg


# ---------------------------------------------------------------------------
# 2. FastAPI Mini App & Admin Endpoints Integration Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def test_get_app_serves_html(client):
    """Verify that GET /app and GET /api/app serve the Mini App HTML dashboard."""
    res = client.get("/app")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "TA Control Hub" in res.text
    assert "telegram-web-app.js" in res.text

    # Alias /api/app
    res_api = client.get("/api/app")
    assert res_api.status_code == 200
    assert "TA Control Hub" in res_api.text


def test_admin_metrics_unauthorized_and_forbidden(client):
    """Verify that GET /api/admin/metrics rejects unauthenticated and non-admin requests."""
    # No auth header -> 401
    res = client.get("/api/admin/metrics")
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]

    # Tampered signature -> 401
    tampered_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, tampered_sig=True)
    res_bad = client.get("/api/admin/metrics", headers={"X-Telegram-Init-Data": tampered_init})
    assert res_bad.status_code == 401

    # Non-admin user -> 403 Forbidden
    non_admin_init = generate_init_data(TEST_BOT_TOKEN, NON_ADMIN_USER_ID)
    res_forbidden = client.get("/api/admin/metrics", headers={"X-Telegram-Init-Data": non_admin_init})
    assert res_forbidden.status_code == 403
    assert "not an administrator" in res_forbidden.json()["detail"]


def test_admin_metrics_authorized(client):
    """Verify that GET /api/admin/metrics returns comprehensive stats when authorized."""
    # Seed one registered student and one submission
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")
    bot.save_submission(
        chat_id=1001,
        student_id="4001",
        hw_num=1,
        message_id=555,
        file_id="fid_123",
        file_name="HW1_Alice.pdf",
        edit_count=0,
    )

    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.get("/api/admin/metrics", headers={"X-Telegram-Init-Data": admin_init})
    assert res.status_code == 200
    data = res.json()
    assert data["enrolled_count"] == 3  # Alice, Bob, Charlie in mock roster
    assert data["registered_count"] == 1
    assert data["submission_counts"]["1"] == 1
    assert data["submission_counts"]["2"] == 0
    assert data["total_submissions"] == 1
    assert "server_time" in data


def test_admin_roster_matrix(client):
    """Verify that GET /api/admin/roster returns accurate full submission matrix."""
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")
    bot.save_submission(
        chat_id=1001,
        student_id="4001",
        hw_num=1,
        message_id=555,
        file_id="fid_123",
        file_name="HW1_Alice.pdf",
        edit_count=1,
    )

    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.get("/api/admin/roster", headers={"X-Telegram-Init-Data": admin_init})
    assert res.status_code == 200
    roster_list = res.json()
    assert len(roster_list) >= 3

    alice = next(s for s in roster_list if s["student_id"] == "4001")
    assert alice["is_registered"] is True
    assert alice["chat_id"] == 1001
    assert alice["submissions"]["1"]["submitted"] is True
    assert alice["submissions"]["1"]["edit_count"] == 1
    assert alice["submissions"]["1"]["file_name"] == "HW1_Alice.pdf"
    assert alice["submissions"]["2"]["submitted"] is False

    bob = next(s for s in roster_list if s["student_id"] == "4002")
    assert bob["is_registered"] is False
    assert bob["chat_id"] is None
    assert bob["submissions"]["1"]["submitted"] is False


# ---------------------------------------------------------------------------
# 3. Student Reset & Unblock Endpoint Tests
# ---------------------------------------------------------------------------

def test_admin_reset_student(client):
    """Verify that POST /api/admin/reset unbinds student from database and clears lockout state."""
    chat_id = 1002
    student_id = "4002"
    bot.save_student_registration(chat_id=chat_id, student_id=student_id, name="Bob Test")
    
    # Simulate failed password attempts and lockout in user_state
    bot.increment_attempts(chat_id)
    bot.increment_attempts(chat_id)
    bot.increment_attempts(chat_id)
    bot.blacklist_user(chat_id)

    # Verify locked state
    state = bot.get_user_state(chat_id)
    assert state[1] == 3  # attempts
    assert state[2] is True  # is_blacklisted

    # Call reset via API
    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.post(
        "/api/admin/reset",
        headers={"X-Telegram-Init-Data": admin_init},
        json={"student_id": student_id},
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "ok"
    assert res_data["found"] is True

    # Assert student unbound from students table
    assert bot.get_student_by_student_id(student_id) is None
    assert bot.get_student_by_chat_id(chat_id) is None

    # Assert lockout cleared in user_state
    cleared_state = bot.get_user_state(chat_id)
    assert cleared_state[1] == 0  # attempts reset to 0
    assert cleared_state[2] is False  # is_blacklisted reset to False


def test_admin_reset_by_chat_id(client):
    """Verify that POST /api/admin/reset works when chat_id is supplied directly."""
    chat_id = 9999
    bot.increment_attempts(chat_id)
    bot.blacklist_user(chat_id)

    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.post(
        "/api/admin/reset",
        headers={"X-Telegram-Init-Data": admin_init},
        json={"chat_id": chat_id},
    )
    assert res.status_code == 200
    state = bot.get_user_state(chat_id)
    assert state[1] == 0
    assert state[2] is False


def test_admin_reset_missing_body(client):
    """Verify that POST /api/admin/reset rejects requests without student_id or chat_id."""
    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.post(
        "/api/admin/reset",
        headers={"X-Telegram-Init-Data": admin_init},
        json={},
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# 4. Broadcast Endpoint & Rate-Limiting Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_admin_broadcast_dispatch_all():
    """Verify broadcast to all registered students with mocked send_message."""
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")
    bot.save_student_registration(chat_id=1002, student_id="4002", name="Bob Test")

    mock_bot = unittest.mock.AsyncMock()
    mock_bot.send_message = unittest.mock.AsyncMock()

    result = await tma_admin.admin_broadcast(
        bot_instance=mock_bot,
        target="all",
        message="*Notice:* Class tomorrow at 9 AM.",
    )

    assert result["total"] == 2
    assert result["sent"] == 2
    assert result["failed"] == 0
    assert mock_bot.send_message.call_count == 2


@pytest.mark.anyio
async def test_admin_broadcast_missing_hw():
    """Verify broadcast targets only students who have not submitted a specific homework."""
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")
    bot.save_student_registration(chat_id=1002, student_id="4002", name="Bob Test")

    # Alice submitted HW 1, Bob has not
    bot.save_submission(1001, "4001", 1, 111, "fid", "alice.pdf", 0)

    mock_bot = unittest.mock.AsyncMock()
    mock_bot.send_message = unittest.mock.AsyncMock()

    result = await tma_admin.admin_broadcast(
        bot_instance=mock_bot,
        target="missing_1",
        message="⚠️ Reminder to submit HW 1",
    )

    assert result["total"] == 1
    assert result["sent"] == 1
    assert result["details"][0]["student_id"] == "4002"
    assert mock_bot.send_message.call_count == 1
    mock_bot.send_message.assert_called_once_with(
        chat_id=1002,
        text="⚠️ Reminder to submit HW 1",
        parse_mode=unittest.mock.ANY,
    )


@pytest.mark.anyio
async def test_admin_broadcast_handles_forbidden_blocked_bot():
    """Verify that when a user has blocked the bot (Forbidden), broadcast catches it gracefully and tracks failure."""
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")
    bot.save_student_registration(chat_id=1002, student_id="4002", name="Bob Test")

    from telegram.error import Forbidden

    mock_bot = unittest.mock.AsyncMock()
    # First call succeeds, second call raises Forbidden
    mock_bot.send_message = unittest.mock.AsyncMock(
        side_effect=[
            unittest.mock.MagicMock(),
            Forbidden("Bot was blocked by the user"),
        ]
    )

    result = await tma_admin.admin_broadcast(
        bot_instance=mock_bot,
        target="all",
        message="Hello class",
    )

    assert result["total"] == 2
    assert result["sent"] == 1
    assert result["failed"] == 1
    failed_item = next(d for d in result["details"] if d["status"] == "failed")
    assert failed_item["student_id"] == "4002"
    assert "Forbidden" in failed_item["reason"]


def test_admin_broadcast_api_endpoint(client, monkeypatch):
    """Verify POST /api/admin/broadcast integration with FastAPI TestClient."""
    bot.save_student_registration(chat_id=1001, student_id="4001", name="Alice Test")

    mock_bot = unittest.mock.AsyncMock()
    mock_bot.send_message = unittest.mock.AsyncMock()
    
    # Inject mock bot into router get_bot_func
    monkeypatch.setattr("api.index.get_tg_bot", lambda: mock_bot)

    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.post(
        "/api/admin/broadcast",
        headers={"X-Telegram-Init-Data": admin_init},
        json={
            "target": "single",
            "student_id": "4001",
            "message": "Personal TA consultation note.",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["sent"] == 1
    assert data["failed"] == 0


# ---------------------------------------------------------------------------
# 5. Bot /admin Command Tests
# ---------------------------------------------------------------------------

class MockChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.messages = []

    async def send_message(self, text, parse_mode=None, reply_markup=None):
        self.messages.append({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
        mock_msg = unittest.mock.MagicMock()
        mock_msg.message_id = 999
        return mock_msg


class MockUpdate:
    def __init__(self, chat_id):
        self.effective_chat = MockChat(chat_id)


@pytest.mark.anyio
async def test_admin_command_unauthorized():
    """Verify that non-admin chat ID is refused access to /admin command."""
    update = MockUpdate(chat_id=NON_ADMIN_USER_ID)
    context = unittest.mock.MagicMock()

    await bot.admin_command(update, context)

    assert len(update.effective_chat.messages) == 1
    msg = update.effective_chat.messages[0]
    assert "⛔ You are not authorized to use admin commands." in msg["text"]


@pytest.mark.anyio
async def test_admin_command_with_webhook_host(monkeypatch):
    """Verify that administrator receives WebApp button pointing to configured URL."""
    monkeypatch.setattr(bot, "WEBHOOK_HOST", "https://ta-collector.vercel.app")
    update = MockUpdate(chat_id=ADMIN_USER_ID)
    context = unittest.mock.MagicMock()

    await bot.admin_command(update, context)

    assert len(update.effective_chat.messages) == 1
    msg = update.effective_chat.messages[0]
    assert "TA Control Hub Mini App" in msg["text"]
    assert msg["reply_markup"] is not None
    # Check that inline keyboard contains WebAppInfo button
    kb = msg["reply_markup"]
    btn = kb.inline_keyboard[0][0]
    assert "Open TA Control Hub" in btn.text
    assert btn.web_app is not None
    assert btn.web_app.url == "https://ta-collector.vercel.app/app"


@pytest.mark.anyio
async def test_admin_command_without_webhook_host(monkeypatch):
    """Verify that administrator receives fallback instructions if WEBHOOK_HOST is unset."""
    monkeypatch.setattr(bot, "WEBHOOK_HOST", "")
    update = MockUpdate(chat_id=ADMIN_USER_ID)
    context = unittest.mock.MagicMock()

    await bot.admin_command(update, context)

    assert len(update.effective_chat.messages) == 1
    msg = update.effective_chat.messages[0]
    assert "Mini App URL Not Configured" in msg["text"]
    assert "WEBHOOK_HOST" in msg["text"]
    assert msg["reply_markup"] is None


def test_auth_via_authorization_header(client):
    """Verify authentication using standard Authorization: tma <initData> header."""
    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.get("/api/admin/metrics", headers={"Authorization": f"tma {admin_init}"})
    assert res.status_code == 200
    assert "enrolled_count" in res.json()


def test_auth_via_query_parameter(client):
    """Verify authentication using query parameter ?initData=<initData>."""
    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.get(f"/api/admin/metrics?initData={urllib.parse.quote(admin_init)}")
    assert res.status_code == 200
    assert "enrolled_count" in res.json()


def test_admin_broadcast_validation_failures(client, monkeypatch):
    """Verify broadcast validation rejections for empty messages and invalid targets."""
    mock_bot = unittest.mock.AsyncMock()
    monkeypatch.setattr("api.index.get_tg_bot", lambda: mock_bot)
    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)

    # Empty message
    res_empty = client.post(
        "/api/admin/broadcast",
        headers={"X-Telegram-Init-Data": admin_init},
        json={"target": "all", "message": ""},
    )
    assert res_empty.status_code == 422  # Pydantic min_length validation

    # Invalid target
    res_inv = client.post(
        "/api/admin/broadcast",
        headers={"X-Telegram-Init-Data": admin_init},
        json={"target": "non_existent_target", "message": "Test"},
    )
    assert res_inv.status_code == 400
    assert "Unknown broadcast target" in res_inv.json()["detail"]


def test_admin_reset_pending_student(client):
    """Verify that resetting a student who only reached pending authentication state clears user_state."""
    chat_id = 7777
    student_id = "4003"
    # Seed pending student in user_state
    bot.set_pending_student_id(chat_id, student_id)
    bot.increment_attempts(chat_id)
    bot.blacklist_user(chat_id)

    admin_init = generate_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    res = client.post(
        "/api/admin/reset",
        headers={"X-Telegram-Init-Data": admin_init},
        json={"student_id": student_id},
    )
    assert res.status_code == 200
    state = bot.get_user_state(chat_id)
    assert state[1] == 0
    assert state[2] is False
    assert state[3] is None


def test_get_app_html_fallback(tmp_path, monkeypatch):
    """Verify fallback behavior when template file is not present."""
    missing_path = tmp_path / "non_existent.html"
    monkeypatch.setattr(tma_admin, "TEMPLATE_PATH", missing_path)
    html = tma_admin.get_app_html()
    assert "TA Control Hub" in html
    assert "not found" in html.lower()
