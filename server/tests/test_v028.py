"""v0.2.8: подтверждение ручных шагов документом, канал СНИЛС, выключенные услуги, связь с банками, киргизская памятка."""
from datetime import datetime, timedelta, timezone

from tests.test_flow import ADMIN, MIG, PASSPORT, bank_post, consent, env, ev, ready_case, sent_case, step, view  # noqa: F401 (env — фикстура)


def mark(e, cid, key, status, **kw):
    return e["c"].post(f"/api/v1/cases/{cid}/steps/{key}", json={"status": status, **kw}, headers=e["h"])


def test_manual_done_needs_document_and_fail_needs_reason(env):
    cid = ready_case(env)
    env["c"].patch(f"/api/v1/cases/{cid}", json={"opts": {"transfer": True}}, headers=env["h"])
    # без номера документа «выполнено» не ставится — ни по полису, ни по переводу у нотариуса
    assert mark(env, cid, "dms", "done").status_code == 422
    assert mark(env, cid, "transl", "done", doc="—").status_code == 422
    r = mark(env, cid, "dms", "done", doc="ДМС-2026/0457")
    assert r.status_code == 200, r.text
    assert "ДМС-2026/0457" in step(r.json(), "dms")["note"]
    assert step(r.json(), "dms")["doc_label"] == "Номер полиса ДМС"
    # «не удалось» — только с причиной
    assert mark(env, cid, "transl", "fail").status_code == 422
    assert mark(env, cid, "transl", "fail", note="ок").status_code == 422
    assert mark(env, cid, "transl", "fail", note="Нотариус не принял паспорт: повреждена страница").status_code == 200
    # номер документа не попадает в открытый журнал
    audit = env["c"].get(f"/admin/v1/audit?case_id={cid}", headers=ADMIN).json()
    assert not any("0457" in (a.get("detail") or "") for a in audit)
    # номер документа без отметки «выполнено» не принимается
    assert mark(env, cid, "wifi", "work", doc="12345").status_code == 422
    # банковские шаги по-прежнему нельзя отметить вручную
    assert mark(env, cid, "card", "done", doc="123456").status_code == 403


def test_snils_channel_sfr(env):
    c, h = env["c"], env["h"]
    assert c.patch("/admin/v1/banks/tbank", json={"snils_channel": "sfr"}, headers=ADMIN).status_code == 200
    assert c.patch("/admin/v1/banks/tbank", json={"snils_channel": "fns"}, headers=ADMIN).status_code == 422
    cid, hid = sent_case(env)
    v = view(env, cid)
    sn = step(v, "snils")
    assert sn["owner"] == "sfr" and sn["operator_can_mark"] and not sn["auto"]
    assert v["snils_channel"] == "sfr"
    # банк не может прислать статус СНИЛС, если СНИЛС идёт через Соцфонд
    r = bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "ESIA_CONFIRMED", 1))
    assert r.status_code == 409 and "Соцфонд" in r.json()["detail"]
    # ЕБС раньше подтверждённого СНИЛС не принимается
    r = bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 2))
    assert r.status_code == 409 and "СНИЛС" in r.json()["detail"]
    # СНИЛС отмечает сотрудник по уведомлению: контрольная сумма проверяется, целиком номер не хранится
    assert mark(env, cid, "transl", "done", doc="50/77-н/77-2026-1-123").status_code == 200
    assert mark(env, cid, "snils", "done", doc="112-233-445 96").status_code == 422
    r = mark(env, cid, "snils", "done", doc="112-233-445 95")
    assert r.status_code == 200, r.text
    note = step(r.json(), "snils")["note"]
    assert "445 95" in note and "112" not in note and step(r.json(), "snils")["source"] == "sfr"
    r = bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 3))
    assert r.status_code == 200, r.text
    m = c.get(f"/api/v1/cases/{cid}/memo?lang=ru", headers=h).json()
    assert "Соцфонд — СНИЛС" in m["executors"]


def test_snils_channel_bank_unchanged(env):
    cid, hid = sent_case(env)
    assert step(view(env, cid), "snils")["owner"] == "bank"
    assert mark(env, cid, "snils", "done", doc="112-233-445 95").status_code == 403
    r = bank_post(env["c"], env["tb"], "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 1))
    assert r.status_code == 409  # без ESIA_CONFIRMED от банка — как раньше


def test_redeem_services_exclude_snils_when_sfr(env):
    c = env["c"]
    c.patch("/admin/v1/banks/tbank", json={"snils_channel": "sfr"}, headers=ADMIN)
    cid = ready_case(env)
    qr = c.post(f"/api/v1/cases/{cid}/handoff", headers=env["h"]).json()["qr"]
    body = bank_post(c, env["tb"], "/bank/v1/handoff/redeem", {"qr": qr}).json()
    assert "snils" not in body["services"] and "sim" in body["services"]


def test_services_off(env):
    c, h = env["c"], env["h"]
    assert c.put("/admin/v1/settings/services_off", json=["dms", "nope"], headers=ADMIN).status_code == 422
    assert c.put("/admin/v1/settings/services_off", json=["dms", "transfer", "sim"], headers=ADMIN).status_code == 200
    assert c.get("/api/v1/banks", headers=h).json()["services_off"] == ["dms", "sim", "transfer"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    assert view(env, cid)["opts"]["dms"] is False
    assert c.patch(f"/api/v1/cases/{cid}", json={"opts": {"dms": True}}, headers=h).status_code == 409
    assert c.patch(f"/api/v1/cases/{cid}", json={"opts": {"phone": True}}, headers=h).status_code == 200
    assert c.patch(f"/api/v1/cases/{cid}", json={"pkg": "full"}, headers=h).status_code == 409
    assert c.patch(f"/api/v1/cases/{cid}", json={"pkg": "card"}, headers=h).status_code == 200
    assert c.put("/admin/v1/settings/services_off", json=[], headers=ADMIN).json()["services_off"] == []


def test_bank_link_state(env, monkeypatch):
    c, h = env["c"], env["h"]
    banks = {b["id"]: b for b in c.get("/api/v1/banks", headers=h).json()["banks"]}
    assert banks["tbank"]["link"] == "none" and banks["tbank"]["link_text"] == "не подключён"
    # любой подписанный запрос банка — признак связи (даже если сам запрос отклонён по смыслу)
    bank_post(c, env["tb"], "/bank/v1/handoff/redeem", {"qr": "OKNO1:nothing"})
    banks = {b["id"]: b for b in c.get("/api/v1/banks", headers=h).json()["banks"]}
    assert banks["tbank"]["link"] == "ok" and banks["vtb"]["link"] == "none"
    later = datetime.now(timezone.utc) + timedelta(hours=30)
    monkeypatch.setattr("app.services.utcnow", lambda: later)
    banks = {b["id"]: b for b in c.get("/api/v1/banks", headers=h).json()["banks"]}
    assert banks["tbank"]["link"] == "wait"
    # неподписанный запрос связь не «зажигает»
    bank_post(c, env["vtb"], "/bank/v1/handoff/redeem", {"qr": "x"}, secret="wrong")
    assert {b["id"]: b for b in c.get("/api/v1/banks", headers=h).json()["banks"]}["vtb"]["link"] == "none"


def test_shift_counts_cases_without_ticket(env):
    ready_case(env)
    sh = env["c"].get("/api/v1/reports/shift", headers=env["h"]).json()
    assert sh["without_ticket"] == 1 and sh["with_ticket"] == 0


def test_kyrgyz_memo_and_eaeu_deadlines(env):
    c, h = env["c"], env["h"]
    cid = c.post("/api/v1/cases", headers=h).json()["id"]
    consent(env, cid, "simpad")
    kg = {**PASSPORT, "country": "KGZ", "country_name": "Кыргызстан", "cyr": "Токтогулов Эмиль"}
    assert c.put(f"/api/v1/cases/{cid}/scan", json={"passport": kg, "migcard": MIG}, headers=h).status_code == 200
    v = view(env, cid)
    assert v["lang"] == "ky" and v["eaeu"] is True
    m = c.get(f"/api/v1/cases/{cid}/memo?lang=ky", headers=h).json()
    text = " ".join(i for s in m["sections"] for i in s["items"])
    assert m["title"] == "Сиздин эскертмеңиз"
    assert "30 күн" in text and "7 жумуш" not in text and "Патентке арыз" not in text
    ru = c.get(f"/api/v1/cases/{cid}/memo?lang=ru", headers=h).json()
    assert not any("патент" in i.lower() and "Заявление" in i for s in ru["sections"] for i in s["items"])
    # для Таджикистана — как раньше: 7 рабочих дней и патент
    cid2 = ready_case(env)
    t = " ".join(i for s in c.get(f"/api/v1/cases/{cid2}/memo?lang=ru", headers=h).json()["sections"] for i in s["items"])
    assert "7 рабочих дней" in t and "патент" in t


def test_snils_channel_fixed_at_handoff(env):
    c = env["c"]
    cid, hid = sent_case(env)  # передано при канале «банк»
    c.patch("/admin/v1/banks/tbank", json={"snils_channel": "sfr"}, headers=ADMIN)
    # переключение банка не меняет уже переданное обращение: СНИЛС по-прежнему от банка
    assert step(view(env, cid), "snils")["owner"] == "bank"
    assert bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "ESIA_CONFIRMED", 1)).status_code == 200
    assert bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 2)).status_code == 200
    assert mark(env, cid, "snils", "fail", note="Проверка попытки перезаписи").status_code == 403


def test_sfr_snils_locked_after_done(env):
    c = env["c"]
    c.patch("/admin/v1/banks/tbank", json={"snils_channel": "sfr"}, headers=ADMIN)
    cid, hid = sent_case(env)
    c.patch("/admin/v1/banks/tbank", json={"snils_channel": "bank"}, headers=ADMIN)  # после передачи — не влияет
    assert step(view(env, cid), "snils")["owner"] == "sfr"
    assert mark(env, cid, "transl", "done", doc="50/77-н/77-2026-1-124").status_code == 200
    assert mark(env, cid, "snils", "done", doc="112-233-445 95").status_code == 200
    assert mark(env, cid, "snils", "fail", note="Попытка отменить подтверждённый СНИЛС").status_code == 409
    assert bank_post(c, env["tb"], "/bank/v1/events", ev(cid, hid, "EBS_REGISTERED", 1)).status_code == 200
