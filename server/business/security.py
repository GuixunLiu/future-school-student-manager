"""Password hashes and revocable opaque tokens; no JWT or client-supplied roles."""

import hashlib
import hmac
import re
import secrets

ITERATIONS = 600_000
ROLES = frozenset({"admin", "teacher", "parent"})


def identifier(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("Use 3-64 lowercase letters, digits, dots, underscores or hyphens")
    return value


def check_password(value: str) -> str:
    if not 15 <= len(value) <= 128 or len(value.encode("utf-8")) > 1024:
        raise ValueError("Password must be 15-128 characters")
    return value


def password_hash(value: str) -> str:
    check_password(value)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(value: str, encoded: str | None) -> bool:
    if not isinstance(value, str) or len(value) > 128 or len(value.encode()) > 1024:
        return False
    # Unknown accounts do the same hash work, without a real password to disclose.
    if encoded is None:
        encoded = f"pbkdf2_sha256${ITERATIONS}${'00' * 16}${'00' * 32}"
    try:
        scheme, iterations, salt, expected = encoded.split("$")
        count = int(iterations)
        if scheme != "pbkdf2_sha256" or count != ITERATIONS or len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), count).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_token(kind: str) -> str:
    return ("wxu_" if kind == "user" else "wxd_") + secrets.token_urlsafe(32)


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_token(value: str) -> bool:
    return bool(re.fullmatch(r"wx[ud]_[A-Za-z0-9_-]{43}", value))
