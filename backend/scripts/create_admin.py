"""Create the first administrator.

There is no registration endpoint (spec §40) — this script is the only way an
account is created.

    py scripts\\create_admin.py
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.modules.auth.repositories.user_repository import UserRepository  # noqa: E402
from app.modules.auth.services.auth_service import AuthService  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 12


def main() -> int:
    db = SessionLocal()
    try:
        repo = UserRepository(db)

        email = input("Email: ").strip().lower()
        if not EMAIL_RE.match(email):
            print("That is not a valid email address.")
            return 1

        if repo.get_by_email(email) is not None:
            print(f"An account already exists for {email}.")
            return 1

        full_name = input("Full name: ").strip()
        if not full_name:
            print("Full name is required.")
            return 1

        # getpass so the password never appears on screen or in shell history.
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return 1

        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            return 1

        user = AuthService(db).create_admin(
            email=email, password=password, full_name=full_name
        )
        print(f"\nAdministrator created: {user.email}  ({user.id})")
        return 0

    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
