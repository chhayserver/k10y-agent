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
