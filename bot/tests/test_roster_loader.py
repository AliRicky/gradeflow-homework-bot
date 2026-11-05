import os
import sys
import tempfile
from pathlib import Path

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

import bot

def test_load_from_env_var():
    csv_data = "student_id,name\n401001,John Doe\n401002,Jane Smith"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        roster = bot.load_students_roster()
        assert "401001" in roster, "Expected 401001 in roster"
        assert roster["401001"] == "John Doe", f"Expected John Doe, got {roster.get('401001')}"
        assert roster["401002"] == "Jane Smith", f"Expected Jane Smith, got {roster.get('401002')}"
        print("PASS: test_load_from_env_var")
    finally:
        del os.environ["ROSTER_CSV"]

def test_load_from_env_var_escaped_newlines():
    csv_data = "student_id,name\\n401001,John Doe\\n401002,Jane Smith"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        roster = bot.load_students_roster()
        assert roster.get("401001") == "John Doe"
        assert roster.get("401002") == "Jane Smith"
        print("PASS: test_load_from_env_var_escaped_newlines")
    finally:
        del os.environ["ROSTER_CSV"]

def test_load_from_disk_fallback():
    # Ensure env var is empty
    if "ROSTER_CSV" in os.environ:
        del os.environ["ROSTER_CSV"]
    if "STUDENTS_ROSTER_DATA" in os.environ:
        del os.environ["STUDENTS_ROSTER_DATA"]
    
    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write("student_id,name\n999001,Local Student\n")
        temp_path = Path(f.name)

    old_csv_path = bot.CSV_PATH
    try:
        bot.CSV_PATH = temp_path
        roster = bot.load_students_roster()
        assert roster.get("999001") == "Local Student"
        print("PASS: test_load_from_disk_fallback")
    finally:
        bot.CSV_PATH = old_csv_path
        temp_path.unlink(missing_ok=True)

def test_whitespace_and_case_tolerance():
    csv_data = " Student_ID ,  Name \n  401099  ,   Alice Wonderland   \n"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        roster = bot.load_students_roster()
        assert "401099" in roster
        assert roster["401099"] == "Alice Wonderland"
        print("PASS: test_whitespace_and_case_tolerance")
    finally:
        del os.environ["ROSTER_CSV"]

def test_graceful_fallback_when_empty():
    if "ROSTER_CSV" in os.environ:
        del os.environ["ROSTER_CSV"]
    if "STUDENTS_ROSTER_DATA" in os.environ:
        del os.environ["STUDENTS_ROSTER_DATA"]

    old_csv_path = bot.CSV_PATH
    try:
        bot.CSV_PATH = Path("/nonexistent/path/students.csv")
        roster = bot.load_students_roster()
        assert roster == {}, f"Expected empty dict, got {roster}"
        print("PASS: test_graceful_fallback_when_empty")
    finally:
        bot.CSV_PATH = old_csv_path

def test_malformed_data_handling():
    csv_data = "header_only\n1,2,3,4\n\n\n,,,,"
    os.environ["ROSTER_CSV"] = csv_data
    old_csv_path = bot.CSV_PATH
    try:
        bot.CSV_PATH = Path("/nonexistent/path/students.csv")
        roster = bot.load_students_roster()
        assert roster == {}
        print("PASS: test_malformed_data_handling")
    finally:
        del os.environ["ROSTER_CSV"]
        bot.CSV_PATH = old_csv_path

def test_load_with_individual_passwords():
    csv_data = "student_id,name,password\n401001,John Doe,AlphaPass10\n401002,Jane Smith,BetaPass10"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        roster = bot.load_students_roster()
        passwords = bot.load_students_passwords()
        assert roster.get("401001") == "John Doe"
        assert roster.get("401002") == "Jane Smith"
        assert passwords.get("401001") == "AlphaPass10"
        assert passwords.get("401002") == "BetaPass10"
        assert bot.get_expected_student_password("401001") == "AlphaPass10"
        assert bot.get_expected_student_password("401002") == "BetaPass10"
        print("PASS: test_load_with_individual_passwords")
    finally:
        del os.environ["ROSTER_CSV"]

def test_fallback_to_registration_password_when_no_password_column():
    csv_data = "student_id,name\n401001,John Doe\n401002,Jane Smith"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        passwords = bot.load_students_passwords()
        assert passwords == {}
        assert bot.get_expected_student_password("401001") == bot.REGISTRATION_PASSWORD.strip()
        assert bot.get_expected_student_password("401002") == bot.REGISTRATION_PASSWORD.strip()
        print("PASS: test_fallback_to_registration_password_when_no_password_column")
    finally:
        del os.environ["ROSTER_CSV"]

def test_mixed_passwords_in_roster():
    csv_data = "student_id,name,password\n401001,John Doe,SpecificPass1\n401002,Jane Smith,"
    os.environ["ROSTER_CSV"] = csv_data
    try:
        passwords = bot.load_students_passwords()
        assert passwords.get("401001") == "SpecificPass1"
        assert "401002" not in passwords
        assert bot.get_expected_student_password("401001") == "SpecificPass1"
        assert bot.get_expected_student_password("401002") == bot.REGISTRATION_PASSWORD.strip()
        print("PASS: test_mixed_passwords_in_roster")
    finally:
        del os.environ["ROSTER_CSV"]

def test_load_from_database_users_table(monkeypatch):
    """Test priority 1 database users table roster loading."""
    monkeypatch.setattr(bot, "POSTGRES_URL", "postgresql://fake:fake@fake:6543/postgres")
    
    class FakeCursor:
        def execute(self, query, args=()):
            pass
        def fetchall(self):
            return [
                ("4042019010", "Arman Khalaj", "PassArman123"),
                ("4042019011", "Hannane Dareyni", "PassHannane123"),
            ]
    class FakeConn:
        def cursor(self):
            return FakeCursor()
        def commit(self):
            pass
        def close(self):
            pass
    from contextlib import contextmanager
    @contextmanager
    def fake_get_db_conn():
        yield FakeConn()
        
    monkeypatch.setattr(bot, "get_db_conn", fake_get_db_conn)
    monkeypatch.setattr(bot, "execute_query", lambda conn, q, args=(): FakeCursor())
    
    roster, passwords = bot.load_roster_data()
    assert roster.get("4042019010") == "Arman Khalaj"
    assert roster.get("4042019011") == "Hannane Dareyni"
    assert passwords.get("4042019010") == "PassArman123"
    assert passwords.get("4042019011") == "PassHannane123"

def test_get_student_grades_mapping(monkeypatch):
    """Test get_student_grades joining users and grades to resolve UUID."""
    class FakeCursor:
        def execute(self, query, args=()):
            pass
        def fetchall(self):
            return [
                ("HW1", "Linear Regression", 40.0, {"q1": 10, "q2": 15}, "Lead TA", "2026-11-02T10:00:00Z"),
                ("HW2", "Classification", 30.0, '{"q1": 20, "q2": 8}', "Grader TA", "2026-11-04T10:00:00Z"),
            ]
    class FakeConn:
        def cursor(self):
            return FakeCursor()
        def close(self):
            pass
    from contextlib import contextmanager
    @contextmanager
    def fake_get_db_conn():
        yield FakeConn()

    monkeypatch.setattr(bot, "get_db_conn", fake_get_db_conn)
    monkeypatch.setattr(bot, "execute_query", lambda conn, q, args=(): FakeCursor())

    grades = bot.get_student_grades("4042019010")
    assert 1 in grades
    assert grades[1]["assignment_code"] == "HW1"
    assert grades[1]["earned_score"] == 25.0
    assert grades[1]["max_score"] == 40.0
    assert grades[1]["graded_by"] == "Lead TA"

    assert 2 in grades
    assert grades[2]["assignment_code"] == "HW2"
    assert grades[2]["earned_score"] == 28.0
    assert grades[2]["max_score"] == 30.0


if __name__ == "__main__":
    test_load_from_env_var()
    test_load_from_env_var_escaped_newlines()
    test_load_from_disk_fallback()
    test_whitespace_and_case_tolerance()
    test_graceful_fallback_when_empty()
    test_malformed_data_handling()
    test_load_with_individual_passwords()
    test_fallback_to_registration_password_when_no_password_column()
    test_mixed_passwords_in_roster()
    print("ALL TESTS PASSED SUCCESSFULLY!")
