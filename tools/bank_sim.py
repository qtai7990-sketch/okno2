"""Имитатор банка: так же должен работать настоящий банк. Схема подписи v2 (docs/BANK_API.md).

  python3 tools/bank_sim.py redeem https://bank-okno.example.ru KEY SECRET CERT.crt CERT.key "OKNO1:..."
  python3 tools/bank_sim.py event  https://bank-okno.example.ru KEY SECRET CERT.crt CERT.key CASE_ID HANDOFF_ID BANK_CASE_ID CODE

Переменная SIMPAD_CA=путь — корневой сертификат для проверки сервера (для учебного стенда с собственным CA).
"""
import hashlib
import hmac
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


def post(base, key, secret, cert, keyfile, path, payload):
    body = json.dumps(payload).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    canon = "\n".join(["OKNO-HMAC-v2", "POST", path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()
    sig = hmac.new(secret.encode(), canon, hashlib.sha256).hexdigest()
    ctx = ssl.create_default_context(cafile=os.environ.get("SIMPAD_CA"))
    ctx.load_cert_chain(cert, keyfile)
    req = urllib.request.Request(base.rstrip("/") + path, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Bank-Key": key, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig})
    try:
        with urllib.request.urlopen(req, context=ctx) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


if __name__ == "__main__":
    if len(sys.argv) < 7:
        print(__doc__)
        sys.exit(1)
    cmd, base, key, secret, cert, keyfile = sys.argv[1:7]
    if cmd == "redeem":
        print(post(base, key, secret, cert, keyfile, "/bank/v1/handoff/redeem", {"qr": sys.argv[7]}))
    elif cmd == "event":
        case_id, handoff_id, bank_case_id, code = sys.argv[7:11]
        print(post(base, key, secret, cert, keyfile, "/bank/v1/events", {
            "event_id": str(uuid.uuid4()), "case_id": case_id, "handoff_id": handoff_id, "bank_case_id": bank_case_id,
            "bank_code": code, "occurred_at": datetime.now(timezone.utc).isoformat(), "schema_version": "2"}))
    else:
        print(__doc__)
