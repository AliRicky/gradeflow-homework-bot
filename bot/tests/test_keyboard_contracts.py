import os
import sys
import unittest.mock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock external dependencies if running in a bare test environment
for mod in ["psycopg2", "telegram", "telegram.constants", "telegram.error", "telegram.ext", "dotenv"]:
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            import unittest.mock
            sys.modules[mod] = unittest.mock.MagicMock()

# If telegram is mocked, supply lightweight dataclasses for Reply/Inline keyboards to allow deep inspection
import telegram
if isinstance(telegram.ReplyKeyboardMarkup, unittest.mock.MagicMock):
    class FakeReplyKeyboardMarkup:
        def __init__(self, keyboard, resize_keyboard=False, is_persistent=False, **kwargs):
            self.keyboard = keyboard
            self.resize_keyboard = resize_keyboard
            self.is_persistent = is_persistent

    class FakeInlineKeyboardMarkup:
        def __init__(self, inline_keyboard, **kwargs):
            self.inline_keyboard = inline_keyboard

    class FakeKeyboardButton:
        def __init__(self, text, **kwargs):
            self.text = text

    class FakeInlineKeyboardButton:
        def __init__(self, text, callback_data=None, **kwargs):
            self.text = text
            self.callback_data = callback_data

    telegram.ReplyKeyboardMarkup = FakeReplyKeyboardMarkup
    telegram.InlineKeyboardMarkup = FakeInlineKeyboardMarkup
    telegram.KeyboardButton = FakeKeyboardButton
    telegram.InlineKeyboardButton = FakeInlineKeyboardButton

import importlib
import bot
importlib.reload(bot)

def test_reply_keyboard_contract():
    """Verify persistent reply keyboard structure and Telegram 6.7+ attributes."""
    kb = bot.get_main_reply_keyboard()
    
    # Assert persistence and resizing flags
    assert getattr(kb, "resize_keyboard", None) is True, "Expected resize_keyboard=True"
    assert getattr(kb, "is_persistent", None) is True, "Expected is_persistent=True"
    
    rows = kb.keyboard
    assert len(rows) == 3, f"Expected 3 rows in reply keyboard, got {len(rows)}"
    
    # Row 1: HW 1, HW 2, HW 3
    row1_texts = [btn.text if hasattr(btn, "text") else str(btn) for btn in rows[0]]
    assert row1_texts == ["📝 HW 1", "📝 HW 2", "📝 HW 3"], f"Unexpected Row 1: {row1_texts}"
    
    # Row 2: HW 4, HW 5
    row2_texts = [btn.text if hasattr(btn, "text") else str(btn) for btn in rows[1]]
    assert row2_texts == ["📝 HW 4", "📝 HW 5"], f"Unexpected Row 2: {row2_texts}"
    
    # Row 3: 📊 My Status
    row3_texts = [btn.text if hasattr(btn, "text") else str(btn) for btn in rows[2]]
    assert row3_texts == ["📊 My Status"], f"Unexpected Row 3: {row3_texts}"
    print("PASS: test_reply_keyboard_contract")


def test_inline_keyboard_contract():
    """Verify inline keyboard structure and Status button presence."""
    kb = bot.get_hw_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == 3, f"Expected 3 rows in inline keyboard, got {len(rows)}"
    
    # Row 1: HW 1..3
    r1 = [(btn.text, btn.callback_data) for btn in rows[0]]
    assert r1 == [
        ("HW 1", "select_hw_1"),
        ("HW 2", "select_hw_2"),
        ("HW 3", "select_hw_3"),
    ], f"Unexpected inline row 1: {r1}"
    
    # Row 2: HW 4..5
    r2 = [(btn.text, btn.callback_data) for btn in rows[1]]
    assert r2 == [
        ("HW 4", "select_hw_4"),
        ("HW 5", "select_hw_5"),
    ], f"Unexpected inline row 2: {r2}"
    
    # Row 3: 📊 My Status with callback 'status'
    r3 = [(btn.text, btn.callback_data) for btn in rows[2]]
    assert r3 == [("📊 My Status", "status")], f"Unexpected inline row 3: {r3}"
    print("PASS: test_inline_keyboard_contract")


def test_homework_selection_parsing():
    """Verify robust parsing of text-based homework button clicks."""
    assert bot.parse_homework_selection("📝 HW 1") == 1
    assert bot.parse_homework_selection("📝 HW 2") == 2
    assert bot.parse_homework_selection("📝 HW 3") == 3
    assert bot.parse_homework_selection("📝 HW 4") == 4
    assert bot.parse_homework_selection("📝 HW 5") == 5
    
    # Case and spacing tolerance
    assert bot.parse_homework_selection("hw 1") == 1
    assert bot.parse_homework_selection("HW-2") == 2
    assert bot.parse_homework_selection("homework 3") == 3
    assert bot.parse_homework_selection("4") == 4
    assert bot.parse_homework_selection("5") == 5
    
    # Persian digits support
    assert bot.parse_homework_selection("HW ۱") == 1
    assert bot.parse_homework_selection("۲") == 2
    
    # Negative cases / non-collisions with student IDs
    assert bot.parse_homework_selection("4002019015") is None
    assert bot.parse_homework_selection("Hello world") is None
    assert bot.parse_homework_selection("status") is None
    print("PASS: test_homework_selection_parsing")


def test_status_intent_detection():
    """Verify status intent detection covers all user and button permutations."""
    assert bot.is_status_request("status") is True
    assert bot.is_status_request("Status") is True
    assert bot.is_status_request("/status") is True
    assert bot.is_status_request("my status") is True
    assert bot.is_status_request("My Status") is True
    assert bot.is_status_request("📊 my status") is True
    assert bot.is_status_request("📊 My Status") is True
    assert bot.is_status_request("  📊 My Status  ") is True
    
    # Negative cases
    assert bot.is_status_request("📝 HW 1") is False
    assert bot.is_status_request("4002019015") is False
    assert bot.is_status_request("what is the status of my life?") is False
    print("PASS: test_status_intent_detection")


def test_app_handlers_and_post_init():
    """Verify application builder registers all necessary callbacks and command menu."""
    app = bot.create_app()
    
    # Check post_init registration
    if hasattr(bot.Application, "builder") and isinstance(bot.Application.builder, unittest.mock.MagicMock):
        builder = bot.Application.builder.return_value
        token_builder = builder.token.return_value
        assert token_builder.post_init.called, "Expected post_init to be called on ApplicationBuilder"
        
        # Verify CallbackQueryHandler patterns
        patterns = [call.kwargs.get("pattern") for call in bot.CallbackQueryHandler.call_args_list]
        assert "^status$" in patterns, f"Expected ^status$ callback handler in patterns: {patterns}"
        assert "^select_hw_\\d+$" in patterns, f"Expected ^select_hw_\\d+$ callback handler in patterns: {patterns}"
        assert "^main_menu$" in patterns, f"Expected ^main_menu$ callback handler in patterns: {patterns}"
    else:
        assert getattr(app, "post_init", None) is not None, "Expected post_init callback to be registered"
        patterns = []
        for handler_list in app.handlers.values():
            for handler in handler_list:
                if hasattr(handler, "pattern") and handler.pattern:
                    patterns.append(handler.pattern.pattern)
        assert r"^status$" in patterns, f"Expected ^status$ in patterns: {patterns}"
    print("PASS: test_app_handlers_and_post_init")


if __name__ == "__main__":
    test_reply_keyboard_contract()
    test_inline_keyboard_contract()
    test_homework_selection_parsing()
    test_status_intent_detection()
    test_app_handlers_and_post_init()
    print("ALL KEYBOARD CONTRACT TESTS PASSED!")
