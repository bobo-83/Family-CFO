"""A Fernet key for tests.

Generated per run rather than committed: a hardcoded key-shaped literal trips
every secret scanner forever (#32), and a test key that lives in git is
indistinguishable from a real one to anybody reading the repo. Module-level so
all tests in a session share one value, which is what the old constant gave.
"""

from cryptography.fernet import Fernet

TEST_FERNET_KEY = Fernet.generate_key().decode()
