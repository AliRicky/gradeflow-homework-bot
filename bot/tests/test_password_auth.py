import asyncio
import os
import sys
import unittest.mock
from pathlib import Path
import pytest

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock external dependencies if missing
for mod in ["psycopg2", "telegram", "telegram.constants", "telegram.error", "telegram.ext", "dotenv"]:
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            sys.modules[mod] = unittest.mock.MagicMock()

import bot

class MockChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.messages = []

    async def send_message(self, text, parse_mode=None, reply_markup=None):
        self.messages.append({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
        mock_msg = unittest.mock.MagicMock()
        mock_msg.message_id = 999
        return mock_msg

class MockMessage:
    def __init__(self, text, chat):
        self.text = text
        self.chat = chat
        self.replies = []
        self.document = None

    async def reply_text(self, text, parse_mode=None, reply_markup=None):
        self.replies.append({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
        mock_msg = unittest.mock.MagicMock()
        mock_msg.message_id = 999
        return mock_msg

class MockUpdate:
    def __init__(self, chat_id, text=""):
        self.effective_chat = MockChat(chat_id)
        self.message = MockMessage(text, self.effective_chat)
        self.callback_query = None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Path, monkeypatch):
    """Isolate database and mock roster for test suite, ensuring clean tear-down."""
    test_db = tmp_path / "test_auth.db"
    monkeypatch.setattr(bot, "DATABASE_PATH", test_db)
    monkeypatch.setattr(bot, "POSTGRES_URL", None)
    monkeypatch.setattr(bot, "REGISTRATION_PASSWORD", "CE401_Fall2026")
    
    # Mock roster with known test students
    test_roster = {
        "401001": "Alice Smith",
        "401002": "Bob Johnson",
        "401003": "Charlie Brown",
    }
    monkeypatch.setattr(bot, "load_students_roster", lambda: test_roster)
    
    bot.init_db()
    yield test_db


def test_registration_happy_path():
    """
    Test Phase 2/3 Happy Path:
    1. Unregistered user sends valid Student ID from roster.
    2. Bot places user into pending_student_id state and prompts for password.
    3. User provides correct password.
    4. Registration is committed to students table with name and timestamp.
    5. pending_student_id is cleared and persistent keyboard is sent.
    """
    chat_id = 10001
    context = unittest.mock.MagicMock()

    # Step 1: Send valid Student ID
    up1 = MockUpdate(chat_id, "401001")
    asyncio.run(bot.handle_text(up1, context))

    assert len(up1.message.replies) == 1
    reply1 = up1.message.replies[0]["text"]
    assert "Alice Smith" in reply1
    assert "Course Registration Password" in reply1

    # Verify pending_student_id in user_state
    state = bot.get_user_state(chat_id)
    assert state[3] == "401001"
    assert bot.get_student_by_chat_id(chat_id) is None  # Not yet registered!

    # Step 2: Send correct registration password
    up2 = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up2, context))

    assert len(up2.message.replies) == 1
    reply2 = up2.message.replies[0]["text"]
    assert "Registration Successful" in reply2
    assert "Alice Smith" in reply2

    # Verify state and registration in DB
    student = bot.get_student_by_chat_id(chat_id)
    assert student is not None
    assert student[1] == "401001"
    assert student[2] == "Alice Smith"

    state_after = bot.get_user_state(chat_id)
    assert state_after[3] is None  # pending_student_id must be cleared!
    assert state_after[1] == 0     # attempts reset to 0


def test_password_failure_and_lockout():
    """
    Test Phase 2/3 Password Failure & Lockout:
    1. User enters valid Student ID.
    2. User enters wrong password 4 times (receives decrementing remaining attempts).
    3. User enters wrong password 5th time -> blacklisted (is_blacklisted=True).
    4. Subsequent interactions receive ACCESS DENIED.
    """
    chat_id = 10002
    context = unittest.mock.MagicMock()

    # Step 1: Enter valid Student ID
    up_id = MockUpdate(chat_id, "401002")
    asyncio.run(bot.handle_text(up_id, context))
    assert bot.get_user_state(chat_id)[3] == "401002"

    # Step 2: 4 failed password attempts
    for i in range(1, 5):
        up_fail = MockUpdate(chat_id, f"WrongPass_{i}")
        asyncio.run(bot.handle_text(up_fail, context))
        state = bot.get_user_state(chat_id)
        assert state[1] == i
        assert not state[2]  # Not blacklisted yet
        assert f"{5 - i} attempt(s) remaining" in up_fail.message.replies[0]["text"]

    # Step 3: 5th failed attempt -> Lockout!
    up_fail5 = MockUpdate(chat_id, "WrongPass_5")
    asyncio.run(bot.handle_text(up_fail5, context))
    state5 = bot.get_user_state(chat_id)
    assert state5[1] == 5
    assert state5[2] is True  # Blacklisted!
    assert state5[3] is None  # pending_student_id cleared
    assert "MAXIMUM ATTEMPTS EXCEEDED" in up_fail5.message.replies[0]["text"]
    assert "Blacklist" in up_fail5.message.replies[0]["text"]

    # Step 4: Subsequent message is completely denied
    up_denied = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_denied, context))
    assert "ACCESS DENIED" in up_denied.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_id) is None


def test_duplicate_registration_prevention():
    """
    Test Duplicate Registration Prevention:
    Student A registers ID 401001.
    Student B attempts to enter ID 401001.
    Student B is immediately rejected without password prompt.
    """
    chat_a = 20001
    chat_b = 20002
    context = unittest.mock.MagicMock()

    # Register Student A
    up_a1 = MockUpdate(chat_a, "401001")
    asyncio.run(bot.handle_text(up_a1, context))
    up_a2 = MockUpdate(chat_a, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_a2, context))
    assert bot.get_student_by_chat_id(chat_a)[1] == "401001"

    # Student B attempts to claim 401001
    up_b = MockUpdate(chat_b, "401001")
    asyncio.run(bot.handle_text(up_b, context))

    reply_b = up_b.message.replies[0]["text"]
    assert "already registered with another chat" in reply_b
    assert "contact your Course TA" in reply_b

    # Verify Student B was not assigned pending state for 401001
    assert bot.get_user_state(chat_b)[3] is None


def test_impersonation_prevention_before_registration():
    """
    Test Impersonation Prevention:
    Student B (attacker) enters Student A's unassigned Student ID,
    but does NOT know the Course Registration Password.
    Attacker is locked out and never registered as Student A.
    """
    attacker_chat = 30001
    victim_id = "401003"
    context = unittest.mock.MagicMock()

    # Attacker enters victim's ID
    up1 = MockUpdate(attacker_chat, victim_id)
    asyncio.run(bot.handle_text(up1, context))
    assert bot.get_user_state(attacker_chat)[3] == victim_id

    # Attacker enters guessed passwords until locked out
    for _ in range(5):
        up_guess = MockUpdate(attacker_chat, "Secret123")
        asyncio.run(bot.handle_text(up_guess, context))

    # Attacker is banned
    assert bot.get_user_state(attacker_chat)[2] is True
    # Victim ID remains completely unbound in students table!
    assert bot.get_student_by_student_id(victim_id) is None


def test_ta_manual_reset_and_submission_continuity():
    """
    Test TA Manual Reset & Submission Continuity:
    1. Student registers (chat_id 40001, student_id 401001) and submits HW 1 with edit_count=1.
    2. TA manually deletes student from 'students' table in Supabase/Postgres.
    3. Verify 'submissions' table remains 100% intact with previous submission file and edit count.
    4. Student connects with NEW Telegram account (chat_id 40002).
    5. Student enters student_id 401001 + Course Password -> Successfully registers.
    6. Student selects HW 1 or requests status -> Past submission and edit count are immediately accessible!
    """
    old_chat = 40001
    new_chat = 40002
    sid = "401001"
    context = unittest.mock.MagicMock()

    # Step 1: Initial Registration
    up1 = MockUpdate(old_chat, sid)
    asyncio.run(bot.handle_text(up1, context))
    up2 = MockUpdate(old_chat, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up2, context))
    assert bot.get_student_by_chat_id(old_chat)[1] == sid

    # Step 2: Student submits HW 1 (edit_count=1, file_name='assignment1_v2.pdf')
    bot.save_submission(
        chat_id=old_chat,
        student_id=sid,
        hw_num=1,
        message_id=5001,
        file_id="tg_file_abc",
        file_name="assignment1_v2.pdf",
        edit_count=1,
    )
    sub = bot.get_submission(sid, 1)
    assert sub is not None
    assert sub[4] == 1  # edit_count=1
    assert sub[5] == "assignment1_v2.pdf"

    # Step 3: TA simulates manual deletion in Supabase: DELETE FROM students WHERE student_id = '401001';
    with bot.get_db_conn() as conn:
        conn.cursor().execute("DELETE FROM students WHERE student_id = ?", (sid,))
        conn.commit()

    # Verify student is removed from students table, but submissions table is INTACT
    assert bot.get_student_by_student_id(sid) is None
    sub_persisted = bot.get_submission(sid, 1)
    assert sub_persisted is not None
    assert sub_persisted[4] == 1
    assert sub_persisted[5] == "assignment1_v2.pdf"

    # Step 4: Student re-registers using new chat_id
    up_new1 = MockUpdate(new_chat, sid)
    asyncio.run(bot.handle_text(up_new1, context))
    assert bot.get_user_state(new_chat)[3] == sid

    up_new2 = MockUpdate(new_chat, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_new2, context))
    assert "Registration Successful" in up_new2.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(new_chat)[1] == sid

    # Step 5: Check submission continuity - prompt for HW 1 reflects prior submission and remaining edits!
    prompt_msg = bot.format_hw_selection_prompt(sid, 1)
    assert "Edit 1/2" in prompt_msg
    assert "1 edit(s) remaining" in prompt_msg

    # Check student submissions dictionary
    student_subs = bot.get_student_submissions(sid)
    assert 1 in student_subs
    assert student_subs[1][0] == "assignment1_v2.pdf"
    assert student_subs[1][2] == 1


def test_existing_registered_student_continuity():
    """
    Test Existing Registered Student Continuity:
    Already registered students interact normally without ever seeing a password prompt.
    """
    chat_id = 50001
    sid = "401002"
    name = "Bob Johnson"
    context = unittest.mock.MagicMock()

    # Pre-register student in DB
    bot.save_student_registration(chat_id, sid, name)
    assert bot.get_student_by_chat_id(chat_id) is not None

    # Sending /start gives welcome back message
    up_start = MockUpdate(chat_id)
    asyncio.run(bot.start_command(up_start, context))
    start_msg = up_start.effective_chat.messages[0]["text"]
    assert "Welcome back" in start_msg
    assert "Bob Johnson" in start_msg
    assert "Password" not in start_msg

    # Selecting HW 1 via text "📝 HW 1"
    up_hw = MockUpdate(chat_id, "📝 HW 1")
    asyncio.run(bot.handle_text(up_hw, context))
    hw_reply = up_hw.message.replies[0]["text"]
    assert "Selected: Homework 1" in hw_reply
    assert "Password" not in hw_reply

    # Checking status via text "📊 My Status"
    up_status = MockUpdate(chat_id, "📊 My Status")
    asyncio.run(bot.handle_text(up_status, context))
    status_reply = up_status.message.replies[0]["text"]
    assert "Your Homework Submission Status" in status_reply
    assert "Bob Johnson" in status_reply


def test_cancel_command_flow():
    """
    Test /cancel command resets pending_student_id so user can re-enter student ID.
    """
    chat_id = 60001
    context = unittest.mock.MagicMock()

    # Enter Student ID
    up1 = MockUpdate(chat_id, "401001")
    asyncio.run(bot.handle_text(up1, context))
    assert bot.get_user_state(chat_id)[3] == "401001"

    # Send /cancel
    up_cancel = MockUpdate(chat_id)
    asyncio.run(bot.cancel_command(up_cancel, context))
    assert bot.get_user_state(chat_id)[3] is None
    cancel_msg = up_cancel.effective_chat.messages[0]["text"]
    assert "Registration input cleared" in cancel_msg
    assert "Student ID" in cancel_msg

    # User can now enter a different Student ID
    up2 = MockUpdate(chat_id, "401002")
    asyncio.run(bot.handle_text(up2, context))
    assert bot.get_user_state(chat_id)[3] == "401002"
    assert "Bob Johnson" in up2.message.replies[0]["text"]


def test_custom_password_via_env():
    """
    Verify REGISTRATION_PASSWORD environment variable overrides default.
    """
    chat_id = 70001
    context = unittest.mock.MagicMock()

    bot.REGISTRATION_PASSWORD = "CustomUltraSecretPass2026!"

    # Enter Student ID
    up1 = MockUpdate(chat_id, "401001")
    asyncio.run(bot.handle_text(up1, context))

    # Old default fails
    up_old = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_old, context))
    assert "Incorrect Course Registration Password" in up_old.message.replies[0]["text"]

    # New custom password succeeds
    up_new = MockUpdate(chat_id, "CustomUltraSecretPass2026!")
    asyncio.run(bot.handle_text(up_new, context))
    assert "Registration Successful" in up_new.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_id)[1] == "401001"


def test_per_student_password_unique_authentication(monkeypatch):
    """
    Verify that when a student has a unique password in the roster,
    only that specific password authenticates them.
    """
    roster_mock = {
        "401001": "Alice Smith",
        "401002": "Bob Johnson",
    }
    passwords_mock = {
        "401001": "AliceSecretPass1",
        "401002": "BobSecretPass2",
    }
    monkeypatch.setattr(bot, "load_students_roster", lambda: roster_mock)
    monkeypatch.setattr(bot, "load_students_passwords", lambda: passwords_mock)

    chat_id = 80001
    context = unittest.mock.MagicMock()

    # Step 1: Send Student ID
    up1 = MockUpdate(chat_id, "401001")
    asyncio.run(bot.handle_text(up1, context))
    assert bot.get_user_state(chat_id)[3] == "401001"

    # Step 2: Global password fails because Alice has an individual password
    up_global = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_global, context))
    assert "Incorrect Course Registration Password" in up_global.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_id) is None

    # Step 3: Alice's individual password succeeds
    up_correct = MockUpdate(chat_id, "AliceSecretPass1")
    asyncio.run(bot.handle_text(up_correct, context))
    assert "Registration Successful" in up_correct.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_id)[1] == "401001"


def test_per_student_cross_password_rejection(monkeypatch):
    """
    Verify that another student with a different password cannot use
    the first student's password to authenticate.
    """
    roster_mock = {
        "401001": "Alice Smith",
        "401002": "Bob Johnson",
    }
    passwords_mock = {
        "401001": "AliceSecretPass1",
        "401002": "BobSecretPass2",
    }
    monkeypatch.setattr(bot, "load_students_roster", lambda: roster_mock)
    monkeypatch.setattr(bot, "load_students_passwords", lambda: passwords_mock)

    chat_bob = 80002
    context = unittest.mock.MagicMock()

    # Bob enters his own Student ID
    up1 = MockUpdate(chat_bob, "401002")
    asyncio.run(bot.handle_text(up1, context))
    assert bot.get_user_state(chat_bob)[3] == "401002"

    # Bob attempts to enter Alice's password
    up_wrong = MockUpdate(chat_bob, "AliceSecretPass1")
    asyncio.run(bot.handle_text(up_wrong, context))
    assert "Incorrect Course Registration Password" in up_wrong.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_bob) is None

    # Bob enters his own password -> Succeeds!
    up_correct = MockUpdate(chat_bob, "BobSecretPass2")
    asyncio.run(bot.handle_text(up_correct, context))
    assert "Registration Successful" in up_correct.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_bob)[1] == "401002"


def test_per_student_password_fallback_to_global(monkeypatch):
    """
    Verify fallback to REGISTRATION_PASSWORD when a student or roster
    has no individual password specified.
    """
    roster_mock = {
        "401001": "Alice Smith",
        "401003": "Charlie Brown",
    }
    passwords_mock = {
        "401001": "AliceSecretPass1",
        # Charlie has no entry in passwords_mock
    }
    monkeypatch.setattr(bot, "load_students_roster", lambda: roster_mock)
    monkeypatch.setattr(bot, "load_students_passwords", lambda: passwords_mock)

    chat_charlie = 80003
    context = unittest.mock.MagicMock()

    # Charlie enters his Student ID
    up1 = MockUpdate(chat_charlie, "401003")
    asyncio.run(bot.handle_text(up1, context))
    assert bot.get_user_state(chat_charlie)[3] == "401003"

    # Charlie tries an incorrect password
    up_wrong = MockUpdate(chat_charlie, "WrongPass123")
    asyncio.run(bot.handle_text(up_wrong, context))
    assert "Incorrect Course Registration Password" in up_wrong.message.replies[0]["text"]

    # Charlie enters global REGISTRATION_PASSWORD ("CE401_Fall2026") -> Succeeds!
    up_global = MockUpdate(chat_charlie, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_global, context))
    assert "Registration Successful" in up_global.message.replies[0]["text"]
    assert bot.get_student_by_chat_id(chat_charlie)[1] == "401003"


# ===========================================================================
# Zero-Trust Chat ID Locking & Student Isolation QA Suite
# ===========================================================================

class MockCallbackQuery:
    def __init__(self, chat_id, data):
        self.data = data
        self.message = MockMessage("", MockChat(chat_id))
    async def answer(self):
        pass


class MockDocument:
    def __init__(self, file_name="homework1.pdf", mime_type="application/pdf"):
        self.file_id = "test_doc_file_id_123"
        self.file_name = file_name
        self.mime_type = mime_type


def get_last_reply(update: MockUpdate) -> str:
    if update.callback_query and update.callback_query.message.replies:
        return update.callback_query.message.replies[-1]["text"]
    if update.message and update.message.replies:
        return update.message.replies[-1]["text"]
    if update.effective_chat and update.effective_chat.messages:
        return update.effective_chat.messages[-1]["text"]
    return ""


def test_unregistered_status_denied():
    """Verify that any unregistered or deleted chat_id is strictly denied from viewing status."""
    unregistered_chat = 90001
    context = unittest.mock.MagicMock()

    # 1. /status command
    up_cmd = MockUpdate(unregistered_chat)
    asyncio.run(bot.status_command(up_cmd, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_cmd)

    # 2. Text message "📊 My Status"
    up_txt = MockUpdate(unregistered_chat, "📊 My Status")
    asyncio.run(bot.handle_text(up_txt, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_txt)

    # 3. Callback query "status"
    up_cb = MockUpdate(unregistered_chat)
    up_cb.callback_query = MockCallbackQuery(unregistered_chat, "status")
    asyncio.run(bot.handle_status_button(up_cb, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_cb)


def test_unregistered_hw_selection_denied():
    """Verify that any unregistered chat_id cannot select homework assignments."""
    unregistered_chat = 90002
    context = unittest.mock.MagicMock()

    # 1. Text selection "📝 HW 1"
    up_txt1 = MockUpdate(unregistered_chat, "📝 HW 1")
    asyncio.run(bot.handle_text(up_txt1, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_txt1)
    assert bot.get_user_state(unregistered_chat)[0] is None

    # 2. Text selection "HW 2"
    up_txt2 = MockUpdate(unregistered_chat, "HW 2")
    asyncio.run(bot.handle_text(up_txt2, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_txt2)

    # 3. Callback selection "select_hw_1"
    up_cb = MockUpdate(unregistered_chat)
    up_cb.callback_query = MockCallbackQuery(unregistered_chat, "select_hw_1")
    asyncio.run(bot.handle_hw_button(up_cb, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_cb)
    assert bot.get_user_state(unregistered_chat)[0] is None


def test_unregistered_document_and_media_upload_denied():
    """Verify that any unregistered chat_id cannot upload documents or media."""
    unregistered_chat = 90003
    context = unittest.mock.MagicMock()

    # 1. PDF Document upload
    up_doc = MockUpdate(unregistered_chat)
    up_doc.message.document = MockDocument("my_hw.pdf", "application/pdf")
    asyncio.run(bot.handle_document(up_doc, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_doc)

    # Verify no submission entered in DB
    with bot.get_db_conn() as conn:
        cursor = bot.execute_query(conn, "SELECT COUNT(*) FROM submissions WHERE chat_id = ?", (unregistered_chat,))
        assert cursor.fetchone()[0] == 0

    # 2. Non-PDF media upload
    up_media = MockUpdate(unregistered_chat)
    asyncio.run(bot.handle_non_pdf(up_media, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_media)


def test_instant_revocation_on_student_deletion():
    """
    Verify that deleting a student from 'students' table instantly revokes all
    submission, status, and selection capabilities from that chat_id, while
    preserving submission history in the database.
    """
    chat_id = 90004
    sid = "401001"
    context = unittest.mock.MagicMock()

    # 1. Register student
    up1 = MockUpdate(chat_id, sid)
    asyncio.run(bot.handle_text(up1, context))
    up2 = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up2, context))
    assert bot.get_student_by_chat_id(chat_id) is not None

    # 2. Student submits HW 1
    bot.save_submission(
        chat_id=chat_id,
        student_id=sid,
        hw_num=1,
        message_id=777,
        file_id="tg_file_777",
        file_name="alice_hw1.pdf",
        edit_count=0,
    )
    assert bot.get_submission(sid, 1) is not None

    # 3. TA simulates manual deletion in Supabase / Postgres
    with bot.get_db_conn() as conn:
        conn.cursor().execute("DELETE FROM students WHERE student_id = ?", (sid,))
        conn.commit()

    assert bot.get_student_by_chat_id(chat_id) is None

    # 4. Immediate revocation assertion:
    # A. /status command
    up_status = MockUpdate(chat_id)
    asyncio.run(bot.status_command(up_status, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_status)

    # B. HW selection via text
    up_hw = MockUpdate(chat_id, "📝 HW 1")
    asyncio.run(bot.handle_text(up_hw, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_hw)

    # C. HW selection via callback
    up_hw_cb = MockUpdate(chat_id)
    up_hw_cb.callback_query = MockCallbackQuery(chat_id, "select_hw_1")
    asyncio.run(bot.handle_hw_button(up_hw_cb, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_hw_cb)

    # D. Document upload
    up_doc = MockUpdate(chat_id)
    up_doc.message.document = MockDocument("replacement.pdf", "application/pdf")
    asyncio.run(bot.handle_document(up_doc, context))
    assert "You are not registered. Please send your Student ID to register." in get_last_reply(up_doc)

    # 5. Verify historical submission record remains 100% intact
    persisted_sub = bot.get_submission(sid, 1)
    assert persisted_sub is not None
    assert persisted_sub[5] == "alice_hw1.pdf"

    # 6. Re-registration restores full access
    up_re1 = MockUpdate(chat_id, sid)
    asyncio.run(bot.handle_text(up_re1, context))
    up_re2 = MockUpdate(chat_id, "CE401_Fall2026")
    asyncio.run(bot.handle_text(up_re2, context))
    assert "Registration Successful" in get_last_reply(up_re2)

    # 7. Capabilities restored - /status now works and reflects past submission
    up_restored_status = MockUpdate(chat_id)
    asyncio.run(bot.status_command(up_restored_status, context))
    reply = get_last_reply(up_restored_status)
    assert "Your Homework Submission Status" in reply
    assert "alice_hw1.pdf" in reply


def test_cross_student_spoofing_impossible():
    """
    Verify that regular students can never query another student's status,
    submit for another student, or alter their own bound student ID.
    """
    chat_alice = 91001
    chat_bob = 91002
    sid_alice = "401001"
    sid_bob = "401002"
    context = unittest.mock.MagicMock()

    # Register Alice and Bob
    bot.save_student_registration(chat_alice, sid_alice, "Alice Smith")
    bot.save_student_registration(chat_bob, sid_bob, "Bob Johnson")

    # Bob has submitted HW 1
    bot.save_submission(chat_bob, sid_bob, 1, 888, "bob_file", "bob_hw1.pdf", 0)

    # 1. Alice attempts to query Bob's status via `/status 401002`
    up_alice_status = MockUpdate(chat_alice)
    context.args = ["401002"]
    asyncio.run(bot.status_command(up_alice_status, context))
    reply_alice = get_last_reply(up_alice_status)

    # Alice ONLY sees her own status, NEVER Bob's!
    assert "Alice Smith" in reply_alice
    assert "401001" in reply_alice
    assert "Bob Johnson" not in reply_alice
    assert "bob_hw1.pdf" not in reply_alice

    # 2. Alice tries to send Bob's student ID in chat to re-identify
    up_alice_txt = MockUpdate(chat_alice, sid_bob)
    asyncio.run(bot.handle_text(up_alice_txt, context))
    reply_txt = get_last_reply(up_alice_txt)
    assert "Alice" in reply_txt
    # Identity remains locked to Alice
    assert bot.get_student_by_chat_id(chat_alice)[1] == sid_alice


def test_admin_status_query_capabilities():
    """
    Verify that an admin can:
    1. View aggregate course statistics via `/status`.
    2. View an individual student's submissions via `/status <student_id>`.
    3. Receive an error when querying a student ID not in the roster.
    """
    admin_chat = 99999
    bot.ADMIN_CHAT_IDS.add(admin_chat)
    context = unittest.mock.MagicMock()

    # Pre-register Alice and add a submission
    bot.save_student_registration(91001, "401001", "Alice Smith")
    bot.save_submission(91001, "401001", 1, 991, "alice_file_id", "alice_hw1.pdf", 0)

    # 1. Admin runs /status with no args -> Aggregate view
    up_admin_agg = MockUpdate(admin_chat)
    context.args = []
    asyncio.run(bot.status_command(up_admin_agg, context))
    agg_msg = get_last_reply(up_admin_agg)
    assert "Homework Submissions Status (Admin View)" in agg_msg
    assert "Total in Roster" in agg_msg

    # 2. Admin queries specific student /status 401001
    up_admin_student = MockUpdate(admin_chat)
    context.args = ["401001"]
    asyncio.run(bot.status_command(up_admin_student, context))
    student_msg = get_last_reply(up_admin_student)
    assert "Student Submission Status (Admin View)" in student_msg
    assert "Alice Smith" in student_msg
    assert "401001" in student_msg
    assert "alice_hw1.pdf" in student_msg

    # 3. Admin queries non-existent student ID
    up_admin_invalid = MockUpdate(admin_chat)
    context.args = ["999999"]
    asyncio.run(bot.status_command(up_admin_invalid, context))
    invalid_msg = get_last_reply(up_admin_invalid)
    assert "not found in course roster" in invalid_msg
