"""Шифрование персональных данных и работа с секретами."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class DataCipher:
    """Шифрует поля с персональными данными (паспорт, миграционная карта, доказательства согласий)."""

    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode())

    def enc(self, obj: Any) -> str | None:
        if obj is None:
            return None
        return self._f.encrypt(json.dumps(obj, ensure_ascii=False).encode()).decode()

    def dec(self, token: str | None) -> Any:
        if not token:
            return None
        return json.loads(self._raw_dec(token))

    def enc_bytes(self, data: bytes) -> str:
        return self._f.encrypt(data).decode()

    def dec_bytes(self, token: str) -> bytes:
        return self._raw_dec(token)

    def _raw_dec(self, token: str) -> bytes:
        try:
            return self._f.decrypt(token.encode())
        except InvalidToken as exc:  # ключ сменили или данные повреждены
            raise RuntimeError("Не удалось расшифровать данные: проверьте SIMPAD_DATA_KEY") from exc


def new_secret(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha256b(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def hmac_hex(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def bank_canonical(method: str, path: str, ts: str, nonce: str, body: bytes) -> bytes:
    """Строка, которую подписывает банк. Метод и путь входят в подпись — запрос нельзя переадресовать."""
    return "\n".join(["OKNO-HMAC-v2", method.upper(), path, ts, nonce, sha256b(body)]).encode()


# ---------- ПИН сотрудника: scrypt со случайной солью ----------
_N, _R, _P = 2 ** 14, 8, 1


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def norm_fingerprint(fp: str) -> str:
    return fp.replace(":", "").strip().lower()
