"""Named-user auth: users live in the PORTAL_USERS env var as name:bcrypt-hash
pairs, sessions are signed cookies. No database, no paid seats — add a user by
appending to the env var and redeploying nothing."""

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE = "portal_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days — long enough to stay logged in on a phone


class LoginRequired(Exception):
    """Raised by require_user; handled in main.py with a redirect to /login."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="portal-auth")


def parse_users() -> dict[str, str]:
    """Parse PORTAL_USERS into {name: bcrypt_hash}. Names are matched case-insensitively."""
    users = {}
    for entry in get_settings().portal_users.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, pw_hash = entry.split(":", 1)
        users[name.strip().lower()] = pw_hash.strip()
    return users


def verify_login(name: str, password: str) -> str | None:
    """Return the canonical user name if credentials are valid, else None."""
    users = parse_users()
    pw_hash = users.get(name.strip().lower())
    if not pw_hash:
        # Still do a bcrypt round so unknown vs wrong-password timing is identical
        bcrypt.checkpw(b"x", bcrypt.hashpw(b"y", bcrypt.gensalt(rounds=4)))
        return None
    if bcrypt.checkpw(password.encode(), pw_hash.encode()):
        return name.strip().lower()
    return None


def create_session_token(name: str) -> str:
    return _serializer().dumps({"user": name})


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user")


def require_user(request: Request) -> str:
    """FastAPI dependency: returns the logged-in user name or raises LoginRequired."""
    user = get_current_user(request)
    if not user:
        raise LoginRequired()
    return user
