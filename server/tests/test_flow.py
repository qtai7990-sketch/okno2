import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.crypto import bank_canonical, hmac_hex
from app.main import create_app

ADMIN = {"X-Admin-Token": "admin-test"}
PASSPORT = {"surname": "RAKHIMOV", "given": "FARRUKH", "cyr": "Рахимов Фаррух", "country": "TJK",
            "country_name": "Таджикистан", "number": "401234567", "dob": "1994-03-12", "sex": "M",
            "expiry": "2032-03-11", "has_ru_text": False, "source": "demo", "checks": []}
MIG = {"series": "4626", "number": "1234567", "entry_date": "2026-09-26", "kpp": "Жуковский", "purpose": "Работа",
       "stay_until": "2026-12-25"}
NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
def make_png(w=400, h=120, text=b"SIGNATURE-MARKER"):
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00" + text) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = make_png()
JPG = b"\xff\xd8\xff\xe0" + b"PAPER-FORM" * 10
FP_T = "a" * 64
FP_V = "b" * 64
BODY = "Я, клиент, даю согласие на обработку персональных данных в целях, указанных выше. " * 2


def settings(tmp_path, name="t.db", **kw):
    base = dict(database_url=f"sqlite:///{tmp_path}/{name}", data_key=Fernet.generate_key().decode(),
                dup_key="dup-" + secrets.token_hex(8), admin_token="admin-test", tz=ZoneInfo("Europe/Moscow"),
                token_ttl_sec=600, allow_demo_scan=True, bank_mtls=True, auto_create_schema=True)
    base.update(kw)
    return Settings(**base)


def add_text(c, recipient, bank_id=None, approved="Юрист И.И. Иванов, приказ №1"):
    r = c.post("/admin/v1/consent-texts", headers=ADMIN, json={
        "recipient": recipient, "bank_id": bank_id,
        "legal_name": {"simpad": "ООО «Окно»", "bank": f"Банк {bank_id}", "notary": "ООО «Бюро»", "insurer": "АО «Страховщик»"}[recipient],
        "inn": "7700000000", "purpose": f"Цель для {recipient}", "fields": ["ФИО", "паспорт"], "retention": "30 дней",
        "body": BODY, "version": "2026-10-v1", "approved_by": approved})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_bank(c, bid, mode, fp):
    b = c.post("/admin/v1/banks", json={"id": bid, "name": bid.upper(), "legal_name": f"АО {bid}", "inn": "7700000001",
                                        "sim_brand": "SIM", "mode": mode}, headers=ADMIN).json()
    c.patch(f"/admin/v1/banks/{bid}", json={"status_map": {"CARD_OK": "CARD_ISSUED"}, "cert_fingerprint": fp}, headers=ADMIN)
    b["fp"], b["id"] = fp, bid
    return b


def login(c, dev_key, code="op1", pin="246810"):
    r = c.post("/api/v1/login", json={"code": code, "pin": pin}, headers={"X-Device-Key": dev_key})
    assert r.status_code == 200, r.text
    return {"X-Device-Key": dev_key, "X-Session": r.json()["session"]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    c = TestClient(create_app(settings(tmp_path)))
    monkeypatch.setattr("app.services.Svc.hour_now", lambda self: 12.0)
    assert c.post("/admin/v1/stands", json={"id": "zia", "name": "Жуковский"}, headers=ADMIN).status_code == 200
    dev = c.post("/admin/v1/devices", json={"stand_id": "zia", "name": "Планшет 1"}, headers=ADMIN).json()
    assert c.post("/admin/v1/operators", json={"stand_id": "zia", "code": "op1", "name": "Оператор", "pin": "246810"},
                  headers=ADMIN).status_code == 200
    tb = make_bank(c, "tbank", "api", FP_T)
    vtb = make_bank(c, "vtb", "api", FP_V)
    texts = {"simpad": add_text(c, "simpad"), "tbank": add_text(c, "bank", "tbank"), "vtb": add_text(c, "bank", "vtb"),
             "notary": add_text(c, "notary"), "insurer": add_text(c, "insurer")}
    h = login(c, dev["device_key"])
    return {"c": c, "h": h, "tb": tb, "vtb": vtb, "texts": texts, "dev_key": dev["device_key"], "tmp": tmp_path}


def bank_post(c, bank, path, payload, ts=None, secret=None, nonce=None, fp=None, sign_path=None, key=None):
    body = json.dumps(payload).encode()
    ts = str(ts or int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    sig = hmac_hex(secret or bank["hmac_secret"], bank_canonical("POST", sign_path or path, ts, nonce, body))
    return c.post(path, content=body, headers={
        "X-Bank-Key": key or bank["bank_api_key"], "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
        "X-Client-Cert-Sha256": bank["fp"] if fp is None else fp, "Content-Type": "application/json"})


def consent(e, cid, recipient, text_key=None, method="paper", data=JPG, mime="image/jpeg"):
    return e["c"].post(f"/api/v1/cases/{cid}/consents", headers=e["h"], json={
        "recipient": recipient, "text_id": e["texts"][text_key or recipient], "method": method, "explained_lang": "tj",
        "evidence_mime": mime, "evidence_b64": base64.b64encode(data).decode()})


def ready_case(e, pkg="full", bank="tbank", ruid=False):
    c, h = e["c"], e["h"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    assert c.put(f"/api/v1/cases/{cid}/scan", json={"passport": PASSPORT, "migcard": MIG}, headers=h).status_code == 409
    assert consent(e, cid, "simpad", method="stylus", data=PNG, mime="image/png").status_code == 200
    assert c.put(f"/api/v1/cases/{cid}/scan", json={"passport": PASSPORT, "migcard": MIG}, headers=h).status_code == 200
    r = c.patch(f"/api/v1/cases/{cid}", json={"pkg": pkg, "bank_id": bank, "ruid": ruid}, headers=h)
    assert r.status_code == 200, r.text
    assert consent(e, cid, "bank", text_key=bank).status_code == 200
    assert consent(e, cid, "notary").status_code == 200
    assert consent(e, cid, "insurer").status_code == 200
    return cid


def sent_case(e, pkg="full"):
    cid = ready_case(e, pkg)
    qr = e["c"].post(f"/api/v1/cases/{cid}/handoff", headers=e["h"]).json()["qr"]
    r = bank_post(e["c"], e["tb"], "/bank/v1/handoff/redeem", {"qr": qr})
    assert r.status_code == 200, r.text
    return cid, r.json()["handoff_id"]


def ev(cid, hid, code, n, bcid="B-1"):
    return {"event_id": f"e{n}", "case_id": cid, "handoff_id": hid, "bank_case_id": bcid, "bank_code": code,
            "occurred_at": NOW()}


def view(e, cid):
    return e["c"].get(f"/api/v1/cases/{cid}", headers=e["h"]).json()


def step(v, key):
    return next(s for s in v["steps"] if s["key"] == key)


# ---------------- вход сотрудника ----------------
def test_device_and_operator_session_required(env):
    c = env["c"]
    assert c.get("/api/v1/cases").status_code == 401
    assert c.get("/api/v1/cases", headers={"X-Device-Key": env["dev_key"]}).status_code == 401  # ключ есть, сессии нет
    assert c.get("/api/v1/cases", headers={"X-Device-Key": "wrong", "X-Session": env["h"]["X-Session"]}).status_code == 401
    assert c.get("/api/v1/cases", headers=env["h"]).status_code == 200
    c.post("/api/v1/logout", headers=env["h"])
    assert c.get("/api/v1/cases", headers=env["h"]).status_code == 401


def test_pin_lockout(env):
    c = env["c"]
    for _ in range(5):
        assert c.post("/api/v1/login", json={"code": "op1", "pin": "000000"}, headers={"X-Device-Key": env["dev_key"]}).status_code == 401
    r = c.post("/api/v1/login", json={"code": "op1", "pin": "246810"}, headers={"X-Device-Key": env["dev_key"]})
    assert r.status_code == 429


# ---------------- персональные данные ----------------
def test_personal_data_and_evidence_encrypted_at_rest(env):
    ready_case(env)
    raw = (env["tmp"] / "t.db").read_bytes()
    assert b"RAKHIMOV" not in raw and b"401234567" not in raw
    assert b"SIGNATURE-MARKER" not in raw and b"PAPER-FORM" not in raw


def test_dup_key_is_keyed_hmac(env):
    ready_case(env)
    cid2 = ready_case(env)
    assert view(env, cid2)["dup_of"]
    plain = hashlib.sha256(f"401234567|1994-03-12|TJK".encode()).hexdigest().encode()
    assert plain not in (env["tmp"] / "t.db").read_bytes()


# ---------------- маршрут и RuID ----------------
def test_route_and_ruid_does_not_waive_translation(env):
    cid = ready_case(env, ruid=True)
    v = view(env, cid)
    keys = [s["key"] for s in v["steps"]]
    assert keys.index("snils") < keys.index("ebs") < keys.index("sim")
    assert "transl" in keys, "RuID не освобождает от перевода"
    assert v["purpose"] == "patent" and v["lang"] == "tj"


def test_policy_per_service(env):
    c = env["c"]
    c.put("/admin/v1/policies", headers=ADMIN, json=[
        {"bank_id": "tbank", "service": "snils", "country": "TJK", "rule": "no", "confirmed": True, "version": "письмо №2"}])
    cid = ready_case(env, pkg="sim")
    assert "transl" not in [s["key"] for s in view(env, cid)["steps"]]
    c.put("/admin/v1/policies", headers=ADMIN, json=[
        {"bank_id": "tbank", "service": "card", "country": "TJK", "rule": "before", "confirmed": True, "version": "письмо №1"}])
    cid2 = ready_case(env, pkg="card")
    v = view(env, cid2)
    assert "transl" in [s["key"] for s in v["steps"]]
    assert any("перевод" in b.lower() for b in step(v, "card")["blockers"])


# ---------------- согласия ----------------
def test_bank_consent_is_bound_to_chosen_bank(env):
    c, h = env["c"], env["h"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    assert consent(env, cid, "bank", text_key="tbank").status_code == 409  # сначала согласие на обработку
    consent(env, cid, "simpad")
    c.put(f"/api/v1/cases/{cid}/scan", json={"passport": PASSPORT, "migcard": MIG}, headers=h)
    assert consent(env, cid, "bank", text_key="tbank").status_code == 409  # банк не выбран
    c.patch(f"/api/v1/cases/{cid}", json={"pkg": "card", "bank_id": "tbank"}, headers=h)
    assert consent(env, cid, "bank", text_key="vtb").status_code == 422  # текст другого банка
    assert consent(env, cid, "bank", text_key="tbank").status_code == 200
    v = c.patch(f"/api/v1/cases/{cid}", json={"bank_id": "vtb"}, headers=h).json()
    assert not any(x["recipient"] == "bank" and x["valid"] for x in v["consents"])
    assert c.post(f"/api/v1/cases/{cid}/handoff", headers=h).status_code == 409


def test_consent_evidence_validation(env):
    c, h = env["c"], env["h"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    assert consent(env, cid, "simpad", method="stylus", data=JPG, mime="image/jpeg").status_code == 422  # стилус — только PNG
    assert consent(env, cid, "simpad", data=PNG, mime="image/jpeg").status_code == 422  # тип не совпал с содержимым
    assert consent(env, cid, "simpad").status_code == 200
    v = view(env, cid)
    k = v["consents"][0]
    assert k["legal_name"] == "ООО «Окно»" and len(k["text_sha256"]) == 64 and k["explained_lang"] == "tj"
    audit = c.get(f"/admin/v1/audit?case_id={cid}", headers=ADMIN).json()
    assert any(a["action"] == "consent.grant" for a in audit)


def test_unapproved_text_rejected_on_production(tmp_path):
    c = TestClient(create_app(settings(tmp_path, "p.db", allow_demo_scan=False)))
    c.post("/admin/v1/stands", json={"id": "zia", "name": "Ж"}, headers=ADMIN)
    key = c.post("/admin/v1/devices", json={"stand_id": "zia", "name": "П"}, headers=ADMIN).json()["device_key"]
    c.post("/admin/v1/operators", json={"stand_id": "zia", "code": "op1", "name": "О", "pin": "246810"}, headers=ADMIN)
    tid = add_text(c, "simpad", approved="")
    h = login(c, key)
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    r = c.post(f"/api/v1/cases/{cid}/consents", headers=h, json={
        "recipient": "simpad", "text_id": tid, "method": "paper", "explained_lang": "ru",
        "evidence_mime": "image/jpeg", "evidence_b64": base64.b64encode(JPG).decode()})
    assert r.status_code == 409 and "юрист" in r.json()["detail"]
    assert c.post(f"/admin/v1/consent-texts/{tid}/approve", headers=ADMIN, json={"approved_by": "Юрист, приказ №7"}).status_code == 200
    assert c.post(f"/admin/v1/consent-texts/{tid}/approve", headers=ADMIN, json={"approved_by": "Другой юрист"}).status_code == 409
    assert c.post(f"/api/v1/cases/{cid}/consents", headers=h, json={
        "recipient": "simpad", "text_id": tid, "method": "paper", "explained_lang": "ru",
        "evidence_mime": "image/jpeg", "evidence_b64": base64.b64encode(JPG).decode()}).status_code == 200
    # демо-сканер на рабочем сервере запрещён
    assert c.put(f"/api/v1/cases/{cid}/scan", json={"passport": PASSPORT}, headers=h).status_code == 400


def test_revoked_consent_blocks_handoff(env):
    cid = ready_case(env)
    env["c"].delete(f"/api/v1/cases/{cid}/consents/bank", headers=env["h"])
    r = env["c"].post(f"/api/v1/cases/{cid}/handoff", headers=env["h"])
    assert r.status_code == 409 and "банку" in r.json()["detail"]


# ---------------- передача в банк ----------------
def test_handoff_single_use_and_redeem(env):
    cid = ready_case(env)
    qr = env["c"].post(f"/api/v1/cases/{cid}/handoff", headers=env["h"]).json()["qr"]
    assert qr.startswith("OKNO1:") and "RAKHIMOV" not in qr
    r = bank_post(env["c"], env["tb"], "/bank/v1/handoff/redeem", {"qr": qr})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prefill"]["number"] == "401234567" and body["handoff_id"]
    assert body["consent"]["recipient"] == "Банк tbank" and len(body["consent"]["text_sha256"]) == 64
    assert bank_post(env["c"], env["tb"], "/bank/v1/handoff/redeem", {"qr": qr}).status_code == 410


def test_other_bank_cannot_redeem(env):
    cid = ready_case(env)
    qr = env["c"].post(f"/api/v1/cases/{cid}/handoff", headers=env["h"]).json()["qr"]
    assert bank_post(env["c"], env["vtb"], "/bank/v1/handoff/redeem", {"qr": qr}).status_code == 404


def test_bank_hours_block_handoff(env, monkeypatch):
    cid = ready_case(env)
    monkeypatch.setattr("app.services.Svc.hour_now", lambda self: 23.5)
    r = env["c"].post(f"/api/v1/cases/{cid}/handoff", headers=env["h"])
    assert r.status_code == 409 and "работает" in r.json()["detail"]
    assert "Повторный визит" in view(env, cid)["plan"]


# ---------------- аутентификация банка ----------------
def test_bank_auth_signature_time_nonce_mtls_path(env):
    c, tb = env["c"], env["tb"]
    p = {"qr": "OKNO1:nothing"}
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, secret="wrong").status_code == 401
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, ts=int(time.time()) - 3600).status_code == 401
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, fp="").status_code == 401  # без сертификата
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, fp=FP_V).status_code == 401  # чужой сертификат
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, sign_path="/bank/v1/events").status_code == 401  # подпись другого пути
    n = secrets.token_hex(16)
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, nonce=n).status_code == 404  # прошёл аутентификацию
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p, nonce=n).status_code == 401  # повтор nonce


def test_key_rotation(env):
    c, tb = env["c"], env["tb"]
    new = c.post("/admin/v1/banks/tbank/rotate", headers=ADMIN).json()
    p = {"qr": "OKNO1:x"}
    assert bank_post(c, tb, "/bank/v1/handoff/redeem", p).status_code == 404  # старый ключ ещё действует
    tb2 = {**tb, "bank_api_key": new["bank_api_key"], "hmac_secret": new["hmac_secret"]}
    assert bank_post(c, tb2, "/bank/v1/handoff/redeem", p).status_code == 404
    assert bank_post(c, tb2, "/bank/v1/handoff/redeem", p, secret=tb["hmac_secret"]).status_code == 401


# ---------------- события банка ----------------
def test_other_bank_cannot_post_events(env):
    """P0 из аудита: чужой банк не может менять статусы обращения."""
    cid, hid = sent_case(env)
    r = bank_post(env["c"], env["vtb"], "/bank/v1/events", ev(cid, hid, "CARD_ISSUED", 1))
    assert r.status_code == 403
    assert step(view(env, cid), "card")["status"] == "todo"
    rej = env["c"].get("/admin/v1/rejected-events", headers=ADMIN).json()
    assert rej and rej[0]["bank_id"] == "vtb"


def test_order_enforced_with_409(env):
    c, tb = env["c"], env["tb"]
    cid, hid = sent_case(env)
    r = bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "SIM_ISSUED", 1))
    assert r.status_code == 409 and "порядок" in r.json()["detail"]
    assert step(view(env, cid), "sim")["status"] == "todo"
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 2)).status_code == 409
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "ESIA_CONFIRMED", 3)).status_code == 200
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 4)).status_code == 200
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "SIM_ISSUED", 5)).status_code == 200
    v = view(env, cid)
    assert step(v, "sim")["status"] == "done" and step(v, "sim")["source"] == "bank"


def test_event_binding_idempotency_and_conflicts(env):
    c, tb = env["c"], env["tb"]
    cid, hid = sent_case(env)
    e1 = ev(cid, hid, "CARD_OK", 1)
    assert bank_post(c, tb, "/bank/v1/events", e1).json()["code"] == "CARD_ISSUED"
    assert bank_post(c, tb, "/bank/v1/events", e1).json()["status"] == "duplicate"
    assert bank_post(c, tb, "/bank/v1/events", {**e1, "note": "другое"}).status_code == 409  # event_id с другим телом
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, "wrong-handoff", "CARD_ACTIVE", 2)).status_code == 409
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "CARD_ACTIVE", 3, bcid="B-2")).status_code == 409
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "CARD_NOT_ISSUED", 4)).status_code == 409  # отказ после выдачи
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "WHATEVER", 5)).status_code == 422
    assert bank_post(c, tb, "/bank/v1/events", {**ev(cid, hid, "CARD_ACTIVE", 6), "occurred_at": datetime.now().replace(tzinfo=None).isoformat()}).status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    assert bank_post(c, tb, "/bank/v1/events", {**ev(cid, hid, "CARD_ACTIVE", 8), "occurred_at": future}).status_code == 422
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "CARD_ACTIVE", 7)).status_code == 200
    assert view(env, cid)["card_state"] == "active"


def test_event_for_service_outside_route_rejected(env):
    cid, hid = sent_case(env, pkg="card")
    r = bank_post(env["c"], env["tb"], "/bank/v1/events", ev(cid, hid, "ESIA_CONFIRMED", 1))
    assert r.status_code == 409 and "маршрут" in r.json()["detail"]


def test_operator_claim_does_not_change_steps_or_kpi(env):
    c, h = env["c"], env["h"]
    cid, _ = sent_case(env)
    v = c.post(f"/api/v1/cases/{cid}/bank-claims", json={"code": "SIM_ISSUED", "note": "со слов Иванова"}, headers=h).json()
    assert v["claims"][0]["code"] == "SIM_ISSUED"
    assert step(v, "sim")["status"] == "todo"
    rep = c.get("/api/v1/reports/shift", headers=h).json()
    assert rep["by_bank"]["tbank"]["sims"] == 0 and rep["unverified_claims"] == 1


# ---------------- режим «номер дела» и сверка ----------------
def test_case_mode_manual_handoff_and_reconciliation(env):
    c, h = env["c"], env["h"]
    c.patch("/admin/v1/banks/tbank", json={"mode": "case"}, headers=ADMIN)
    cid = ready_case(env, pkg="full")
    r = c.post(f"/api/v1/cases/{cid}/handoff/manual", headers=h)
    assert r.status_code == 200, r.text
    num = r.json()["case_number"]
    rows = [{"event_id": "r1", "case_number": num, "bank_case_id": "D-9", "bank_code": "SIM_ISSUED", "occurred_at": NOW()},
            {"event_id": "r2", "case_number": num, "bank_case_id": "D-9", "bank_code": "ESIA_CONFIRMED", "occurred_at": NOW()},
            {"event_id": "r3", "case_number": num, "bank_case_id": "D-9", "bank_code": "CARD_OK", "occurred_at": NOW()}]
    res = c.post("/admin/v1/banks/tbank/reconcile", headers=ADMIN, json={"source_doc": "Акт сверки №1 от 30.09.2026", "rows": rows}).json()
    assert res["accepted"] == 2 and res["rejected"][0]["event_id"] == "r1"
    v = view(env, cid)
    assert step(v, "snils")["status"] == "done" and step(v, "snils")["source"] == "bank_file"
    assert step(v, "sim")["status"] == "todo"
    # повторная загрузка того же файла не создаёт дублей
    res2 = c.post("/admin/v1/banks/tbank/reconcile", headers=ADMIN, json={"source_doc": "Акт сверки №1 от 30.09.2026", "rows": rows[1:]}).json()
    assert res2["duplicate"] == 2


def test_route_frozen_after_handoff_and_recon_requires_handoff(env):
    c, h = env["c"], env["h"]
    cid, _ = sent_case(env)
    assert c.patch(f"/api/v1/cases/{cid}", json={"pkg": "card"}, headers=h).status_code == 409
    c.patch("/admin/v1/banks/tbank", json={"mode": "case"}, headers=ADMIN)
    cid2 = ready_case(env, pkg="card")  # не передан в банк
    num = view(env, cid2)["number"]
    res = c.post("/admin/v1/banks/tbank/reconcile", headers=ADMIN, json={"source_doc": "Акт №2", "rows": [
        {"event_id": "x1", "case_number": num, "bank_case_id": "D-1", "bank_code": "CARD_OK", "occurred_at": NOW()}]}).json()
    assert res["accepted"] == 0 and res["rejected"]
    assert c.put("/admin/v1/policies", headers=ADMIN, json=[{"bank_id": "nobank", "country": "TJK", "rule": "no"}]).status_code == 404
    assert c.get("/api/v1/reports/shift?date=bad", headers=h).status_code == 422


def test_api_bank_has_no_manual_handoff(env):
    cid = ready_case(env)
    assert env["c"].post(f"/api/v1/cases/{cid}/handoff/manual", headers=env["h"]).status_code == 409


# ---------------- шаги оператора ----------------
def test_operator_cannot_mark_bank_steps_and_imei_validation(env):
    c, h = env["c"], env["h"]
    cid = ready_case(env)
    assert c.post(f"/api/v1/cases/{cid}/steps/sim", json={"status": "done"}, headers=h).status_code == 403
    assert c.post(f"/api/v1/cases/{cid}/steps/imei", json={"status": "done", "imei": "123"}, headers=h).status_code == 400
    assert c.post(f"/api/v1/cases/{cid}/steps/imei", json={"status": "done", "imei": "356789012345678"}, headers=h).status_code == 200


def test_dms_payment_by_inactive_card_blocked(env):
    c, h = env["c"], env["h"]
    cid = ready_case(env)
    c.patch(f"/api/v1/cases/{cid}", json={"pay": {"dms": "bankcard"}}, headers=h)
    r = c.post(f"/api/v1/cases/{cid}/steps/dms", json={"status": "done"}, headers=h)
    assert r.status_code == 409 and "не активна" in r.json()["detail"]
    c.patch(f"/api/v1/cases/{cid}", json={"pay": {"dms": "mir"}}, headers=h)
    assert c.post(f"/api/v1/cases/{cid}/steps/dms", json={"status": "done"}, headers=h).status_code == 200


def test_corrections_journal(env):
    cid = ready_case(env)
    v = env["c"].post(f"/api/v1/cases/{cid}/corrections", headers=env["h"], json={
        "section": "passport", "field": "cyr", "value": "Рахимов Фаррух Ш.", "reason": "Написание по нотариальному переводу"}).json()
    assert v["passport"]["cyr"] == "Рахимов Фаррух Ш."
    assert v["corrections"][0]["old"] == "Рахимов Фаррух" and v["corrections"][0]["reason"]
    assert env["c"].post(f"/api/v1/cases/{cid}/corrections", headers=env["h"], json={
        "section": "passport", "field": "source", "value": "chip", "reason": "Обязательное требование банка"}).status_code == 400
    # свободный текст причины не принимается — только причины из списка
    assert env["c"].post(f"/api/v1/cases/{cid}/corrections", headers=env["h"], json={
        "section": "passport", "field": "cyr", "value": "Х", "reason": "клиент Иванов сказал"}).status_code == 422


def test_memo_and_shift(env):
    cid = ready_case(env)
    m = env["c"].get(f"/api/v1/cases/{cid}/memo?lang=uz", headers=env["h"]).json()
    assert m["case_number"] and m["sections"]
    rep = env["c"].get("/api/v1/reports/shift", headers=env["h"]).json()
    assert rep["cases"] >= 1 and "подтверждённые" in rep["basis"]


def test_dup_key_secret_must_differ(tmp_path):
    s = settings(tmp_path, "x.db")
    with pytest.raises(RuntimeError):
        create_app(Settings(**{**s.__dict__, "dup_key": s.data_key}))


def test_reauth_after_client_mode(env):
    c, h = env["c"], env["h"]
    assert c.post("/api/v1/reauth", json={"pin": "246810"}, headers=h).status_code == 200
    for _ in range(4):
        assert c.post("/api/v1/reauth", json={"pin": "000000"}, headers=h).status_code == 403
    assert c.post("/api/v1/reauth", json={"pin": "000000"}, headers=h).status_code == 403  # пятая — блокировка
    assert c.get("/api/v1/cases", headers=h).status_code == 401  # сессия закрыта


# ---------------- по ревизии Manus v0.2 ----------------
def test_sim_not_issued_before_prerequisites_rejected(env):
    c, tb = env["c"], env["tb"]
    cid, hid = sent_case(env)
    r = bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "SIM_NOT_ISSUED", 1))
    assert r.status_code == 409
    assert step(view(env, cid), "sim")["status"] == "todo"
    assert any(x["code"] == "SIM_NOT_ISSUED" for x in c.get("/admin/v1/rejected-events", headers=ADMIN).json())


def test_prerequisites_checked_by_occurred_at(env):
    c, tb = env["c"], env["tb"]
    cid, hid = sent_case(env)
    now = datetime.now(timezone.utc)
    e = lambda code, n, dt: {**ev(cid, hid, code, n), "occurred_at": (now + dt).isoformat()}  # noqa: E731
    assert bank_post(c, tb, "/bank/v1/events", e("ESIA_CONFIRMED", 1, timedelta(0))).status_code == 200
    assert bank_post(c, tb, "/bank/v1/events", e("EBS_REGISTERED", 2, timedelta(seconds=30))).status_code == 200
    # SIM «произошла» за 4 минуты до ЕСИА — невозможная хронология
    r = bank_post(c, tb, "/bank/v1/events", e("SIM_ISSUED", 3, timedelta(minutes=-4)))
    assert r.status_code == 409 and step(view(env, cid), "sim")["status"] == "todo"
    assert bank_post(c, tb, "/bank/v1/events", e("SIM_ISSUED", 4, timedelta(minutes=1))).status_code == 200


def test_rejected_event_does_not_bind_bank_case_id(env):
    c, tb = env["c"], env["tb"]
    cid, hid = sent_case(env)
    # первое событие отклонено (нарушен порядок) — привязка номера дела не должна сохраниться
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "SIM_ISSUED", 1, bcid="WRONG")).status_code == 409
    assert view(env, cid)["bank_case_id"] is None
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "CARD_OK", 2, bcid="B-7")).status_code == 200
    assert bank_post(c, tb, "/bank/v1/events", ev(cid, hid, "CARD_ACTIVE", 3, bcid="B-8")).status_code == 409
    assert view(env, cid)["bank_case_id"] == "B-7"


def test_free_text_notes_not_stored_in_plaintext(env):
    c, h, tb = env["c"], env["h"], env["tb"]
    cid, hid = sent_case(env)
    assert bank_post(c, tb, "/bank/v1/events", {**ev(cid, hid, "CARD_OK", 1), "note": "BANK-NOTE-MARKER-7e4a"}).status_code == 200
    v = c.post(f"/api/v1/cases/{cid}/bank-claims", json={"code": "SIM_ISSUED", "note": "CLAIM-NOTE-MARKER-91c"}, headers=h).json()
    assert v["claims"][0]["note"] == "CLAIM-NOTE-MARKER-91c"
    raw = (env["tmp"] / "t.db").read_bytes()
    assert b"BANK-NOTE-MARKER" not in raw and b"CLAIM-NOTE-MARKER" not in raw


def test_mtls_cannot_be_disabled_on_production(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(settings(tmp_path, "m.db", allow_demo_scan=False, bank_mtls=False))


# ---------------- по ревизии Manus v0.2.3 ----------------
def test_night_bank_hours(env, monkeypatch):
    from app.rules import is_open
    assert is_open(23.0, (22, 6)) and is_open(2.0, (22, 6)) and not is_open(12.0, (22, 6))
    assert is_open(0.0, (0, 24)) and is_open(23.9, (0, 24))
    c, h = env["c"], env["h"]
    assert c.patch("/admin/v1/banks/tbank", json={"open_from": 22, "open_to": 6}, headers=ADMIN).status_code == 200
    assert c.patch("/admin/v1/banks/tbank", json={"open_from": 9, "open_to": 9}, headers=ADMIN).status_code == 422
    assert c.patch("/admin/v1/banks/tbank", json={"open_from": 0, "open_to": 24}, headers=ADMIN).status_code == 200
    assert c.patch("/admin/v1/banks/tbank", json={"open_from": 22, "open_to": 6}, headers=ADMIN).status_code == 200
    cid = ready_case(env)
    monkeypatch.setattr("app.services.Svc.hour_now", lambda self: 2.0)
    assert c.post(f"/api/v1/cases/{cid}/handoff", headers=h).status_code == 200
    monkeypatch.setattr("app.services.Svc.hour_now", lambda self: 12.0)
    r = c.post(f"/api/v1/cases/{cid}/handoff", headers=h)
    assert r.status_code == 409 and "22:00–06:00" in r.json()["detail"]


def test_case_number_collision_retried(env, monkeypatch):
    c, h = env["c"], env["h"]
    first = c.post("/api/v1/cases", headers=h).json()["number"]
    seq = iter([first, first, "ZZZZZZ"])
    monkeypatch.setattr("app.services.Svc._new_number", lambda self: next(seq))
    r = c.post("/api/v1/cases", headers=h)
    assert r.status_code == 201 and r.json()["number"] == "ZZZZZZ"


def test_damaged_png_rejected(env):
    c, h = env["c"], env["h"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    bad = make_png()[:-12]  # без блока IEND
    assert consent(env, cid, "simpad", method="stylus", data=bad, mime="image/png").status_code == 422
    tiny = make_png(w=10, h=10)
    assert consent(env, cid, "simpad", method="stylus", data=tiny, mime="image/png").status_code == 422


def test_healthz_checks_database(env):
    r = env["c"].get("/healthz")
    assert r.status_code == 200 and r.json()["db"] is True


def test_min_app_version_enforced(tmp_path):
    c = TestClient(create_app(settings(tmp_path, "v.db", min_app_version=120)))
    c.post("/admin/v1/stands", json={"id": "zia", "name": "Ж"}, headers=ADMIN)
    key = c.post("/admin/v1/devices", json={"stand_id": "zia", "name": "П"}, headers=ADMIN).json()["device_key"]
    c.post("/admin/v1/operators", json={"stand_id": "zia", "code": "op1", "name": "О", "pin": "246810"}, headers=ADMIN)
    body = {"code": "op1", "pin": "246810"}
    assert c.post("/api/v1/login", json=body, headers={"X-Device-Key": key}).status_code == 426
    assert c.post("/api/v1/login", json=body, headers={"X-Device-Key": key, "X-App-Version": "119"}).status_code == 426
    assert c.post("/api/v1/login", json=body, headers={"X-Device-Key": key, "X-App-Version": "120"}).status_code == 200
