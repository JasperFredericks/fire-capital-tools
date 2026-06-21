#!/usr/bin/env python3
"""
One-time admin setup script.
Run this to generate a bcrypt password hash, then paste the output into .env.

Usage:
    python create_admin.py
"""
import getpass
import bcrypt


def main() -> None:
    print("FIRE Capital Tools — Admin Password Setup")
    print("=" * 44)
    password = getpass.getpass("Enter password for admin account: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("\n[Error] Passwords do not match. Please try again.")
        raise SystemExit(1)

    if len(password) < 8:
        print("\n[Error] Password must be at least 8 characters.")
        raise SystemExit(1)

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    print("\n" + "=" * 44)
    print("Add the following line to your .env file:")
    print("=" * 44)
    print(f"ADMIN_PASSWORD_HASH={hashed}")
    print("=" * 44)


if __name__ == "__main__":
    main()
