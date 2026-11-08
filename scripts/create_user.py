"""Create a user, or reset an existing user's password and role.

uv run python scripts/create_user.py alice --role supervisor      # prompts for the password
"""

import argparse
import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.settings import get_settings
from core.db.models import Role, User
from core.db.session import make_engine

MIN_PASSWORD = 10


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username")
    parser.add_argument("--role", type=Role, choices=list(Role), default=Role.OPERATOR)
    parser.add_argument("--password", help="avoid on shared machines: it ends up in shell history")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    if len(password) < MIN_PASSWORD:
        raise SystemExit(f"error: password must be at least {MIN_PASSWORD} characters")

    engine = make_engine(get_settings().database_url)
    with Session(engine) as session, session.begin():
        user = session.scalar(select(User).where(User.username == args.username))
        if user is None:
            session.add(User(username=args.username, password_hash=hash_password(password), role=args.role))
            print(f"created {args.username} ({args.role})")
        else:
            user.password_hash, user.role, user.is_active = hash_password(password), args.role, True
            print(f"updated {args.username} ({args.role})")
    engine.dispose()


if __name__ == "__main__":
    main()
