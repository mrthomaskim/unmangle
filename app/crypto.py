"""Encrypts Gmail refresh tokens at rest. The key lives in Secret Manager, never in Firestore."""
from cryptography.fernet import Fernet, InvalidToken

_fernet = None


def init(key: str):
    global _fernet
    if not key:
        raise RuntimeError("TOKEN_ENC_KEY is not set (generate with: python -c "
                           "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')")
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Stored token could not be decrypted (key rotated?)") from e
