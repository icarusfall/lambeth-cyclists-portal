"""Generate a PORTAL_USERS entry.

Usage:  python scripts/hash_password.py <name>
Prompts for a password and prints the name:hash pair to append to PORTAL_USERS.
"""

import getpass
import sys

import bcrypt


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/hash_password.py <name>")
        sys.exit(1)
    name = sys.argv[1].strip().lower()
    password = getpass.getpass(f"Password for '{name}': ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    print("\nAdd this to PORTAL_USERS (comma-separated with other users):\n")
    print(f"{name}:{pw_hash}")


if __name__ == "__main__":
    main()
