"""Passwords, sessions and encryption of stored secrets."""

import base64
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

_hasher = PasswordHasher()
MIN_PASSWORD = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password_policy(password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if password.isdigit() or password.isalpha():
        return "Use a mix of letters and numbers or symbols."
    return None


class Crypto:
    """Fernet encryption keyed by K10Y_SECRET_KEY (or a generated key file)."""

    def __init__(self, secret: str, data_dir: str):
        if not secret:
            path = Path(data_dir) / "secret.key"
            if path.exists():
                secret = path.read_text().strip()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                secret = secrets.token_urlsafe(48)
                path.write_text(secret)
                path.chmod(0o600)
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self.fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            raise RuntimeError("cannot decrypt a stored secret: K10Y_SECRET_KEY changed or the data is corrupt")


class LoginLimiter:
    """At most `limit` failed logins per (username, ip) per window."""

    def __init__(self, limit: int = 5, window_s: int = 300):
        self.limit, self.window = limit, window_s
        self.fails: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def _key(self, username: str, ip: str) -> str:
        return f"{username.lower()}|{ip}"

    def blocked(self, username: str, ip: str) -> bool:
        with self.lock:
            q = self.fails[self._key(username, ip)]
            now = time.monotonic()
            while q and now - q[0] > self.window:
                q.popleft()
            return len(q) >= self.limit

    def failed(self, username: str, ip: str) -> None:
        with self.lock:
            self.fails[self._key(username, ip)].append(time.monotonic())

    def reset(self, username: str, ip: str) -> None:
        with self.lock:
            self.fails.pop(self._key(username, ip), None)


# --- two-factor login (TOTP, RFC 6238: 6 digits, 30 s, SHA-1, as every authenticator app expects) -------------

TOTP_STEP = 30


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    import hmac
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    return f"{(int.from_bytes(mac[offset:offset + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret: str, code: str, last_step: Optional[int] = None, now: Optional[float] = None) -> Optional[int]:
    """The time step `code` belongs to (allowing one step of clock drift), or None. A step at or before
    `last_step` is refused, so a code can't be used twice."""
    import hmac
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    current = int((time.time() if now is None else now) // TOTP_STEP)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp_code(secret, step), code) and (last_step is None or step > last_step):
            return step
    return None


def totp_uri(secret: str, username: str, issuer: str = "k10y") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}&digits=6&period={TOTP_STEP}"


def new_recovery_codes(n: int = 10) -> list:
    """Single-use codes like 7f3k-q9wm, for when the phone is lost. Only their digests are stored."""
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(n)]


def recovery_digest(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()
