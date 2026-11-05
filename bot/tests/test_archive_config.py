import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Clear the environment variable to simulate Vercel's failure to inject it
if "ARCHIVE_GROUP_ID" in os.environ:
    del os.environ["ARCHIVE_GROUP_ID"]

# Mock external dependencies if running in a bare test environment
for mod in ["psycopg2", "telegram", "telegram.constants", "telegram.error", "telegram.ext", "dotenv"]:
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            import unittest.mock
            sys.modules[mod] = unittest.mock.MagicMock()

import bot

def test_archive_group_id():
    expected = -1004495387404
    actual = bot.ARCHIVE_GROUP_ID
    
    print("Testing ARCHIVE_GROUP_ID routing logic...")
    print(f"Expected ID: {expected}")
    print(f"Actual ID:   {actual}")
    
    assert actual == expected, f"QC FAILED! Expected {expected} but got {actual}"
    print("QC PASSED: ARCHIVE_GROUP_ID is mathematically verified to route to the correct group.")

if __name__ == "__main__":
    test_archive_group_id()
