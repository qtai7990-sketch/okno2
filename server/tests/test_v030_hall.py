"""v0.3.0: агент 1 этажа — услуги → согласие → паспорт → талон; на 2 этаже открывается то же обращение."""
import base64

from tests.test_flow import ADMIN, PNG, add_text
from tests.test_queue import q  # noqa: F401  (фикстура)

PASSPORT = {"surname": "RAKHIMOV", "given": "FARRUKH", "cyr": "Рахимов Фаррух", "country": "TJK",
            "country_name": "Таджикистан", "number": "401234567", "dob": "1994-03-12", "sex": "M",
            "expiry": "2032-03-11", "source": "demo"}


def hall_case(q, who="hall1", pkg="full"):
    r = q["c"].post("/api/v1/hall/cases", json={"pkg": pkg, "opts": {"dms": True}}, headers=q["h"][who])
    assert r.status_code == 201, r.text
    return r.json()


def sign(q, cid, who="hall1"):
    tid = q["c"].get("/api/v1/hall/setup", headers=q["h"][who]).json()["texts"][0]["id"]
    return q["c"].post(f"/api/v1/hall/cases/{cid}/consent", headers=q["h"][who], json={
        "recipient": "simpad", "text_id": tid, "method": "stylus", "explained_lang": "tj",
        "evidence_mime": "image/png", "evidence_b64": base64.b64encode(PNG).decode()})


def test_hall_intake_order_and_handover_to_desk(q):
    c, h = q["c"], q["h"]
    add_text(c, "simpad")
    v = hall_case(q)
    assert v["pkg"] == "full" and not v["consent"] and v["passport"] is None
    # без согласия ни скана, ни талона
    assert c.put(f"/api/v1/hall/cases/{v['id']}/scan", json={"passport": PASSPORT}, headers=h["hall1"]).status_code == 409
    assert c.post("/api/v1/tickets", json={"case_id": v["id"]}, headers=h["hall1"]).status_code == 409
    assert sign(q, v["id"]).json()["consent"]
    s = c.put(f"/api/v1/hall/cases/{v['id']}/scan", json={"passport": PASSPORT}, headers=h["hall1"]).json()
    # на 1 этаже — только латиница; язык талона — по гражданству
    assert s["lang"] == "tj" and s["passport"]["surname"] == "RAKHIMOV" and "cyr" not in s["passport"] and "dob" not in s["passport"]
    t = c.post("/api/v1/tickets", json={"lang": "ru", "case_id": v["id"]}, headers=h["hall1"]).json()
    assert t["lang"] == "tj" and t["number"] == "А001"
    # повтор того же запроса — тот же талон; новый запрос — отказ, второй талон к обращению не выдаётся
    again = c.post("/api/v1/tickets", json={"case_id": v["id"]}, headers=h["hall1"])
    assert again.status_code == 409
    # после талона зал обращение больше не видит
    assert c.get(f"/api/v1/hall/cases/{v['id']}", headers=h["hall1"]).status_code == 404
    # 2 этаж: приёмная → вызов → «клиент подошёл» открывает то же обращение с согласием и паспортом
    assert c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"]).status_code == 200
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    view = c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"]).json()
    assert view["id"] == v["id"] and view["passport"]["cyr"] == "Рахимов Фаррух" and view["pkg"] == "full"
    assert any(x["recipient"] == "simpad" and x["valid"] for x in view["consents"])


def test_hall_idempotent_ticket_for_case(q):
    c, h = q["c"], q["h"]
    add_text(c, "simpad")
    v = hall_case(q)
    sign(q, v["id"])
    c.put(f"/api/v1/hall/cases/{v['id']}/scan", json={"passport": PASSPORT}, headers=h["hall1"])
    k = {**h["hall1"], "Idempotency-Key": "hallkey-0001"}
    a = c.post("/api/v1/tickets", json={"case_id": v["id"]}, headers=k)
    b = c.post("/api/v1/tickets", json={"case_id": v["id"]}, headers=k)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"] and b.json()["replay"]


def test_hall_sees_only_own_cases_and_only_hall_actions(q):
    c, h = q["c"], q["h"]
    add_text(c, "simpad")
    v = hall_case(q)
    assert c.get(f"/api/v1/hall/cases/{v['id']}", headers=h["hall2"]).status_code == 404  # чужой планшет зала
    assert c.get("/api/v1/cases", headers=h["hall1"]).status_code == 403  # список обращений залу закрыт
    assert c.get(f"/api/v1/cases/{v['id']}", headers=h["hall1"]).status_code == 403
    assert c.post("/api/v1/hall/cases", json={}, headers=h["desk7"]).status_code == 403
    assert c.post("/api/v1/tickets", json={"case_id": v["id"]}, headers=h["rec"]).status_code == 403
    # банк в зале не выбирается, согласие банку — только на 2 этаже
    assert c.patch(f"/api/v1/hall/cases/{v['id']}", json={"bank_id": "x"}, headers=h["hall1"]).json().get("pkg") == "full"
    tid = c.get("/api/v1/hall/setup", headers=h["hall1"]).json()["texts"][0]["id"]
    r = c.post(f"/api/v1/hall/cases/{v['id']}/consent", headers=h["hall1"], json={
        "recipient": "bank", "text_id": tid, "method": "stylus", "explained_lang": "ru",
        "evidence_mime": "image/png", "evidence_b64": base64.b64encode(PNG).decode()})
    assert r.status_code == 403


def test_hall_respects_services_off(q):
    c, h = q["c"], q["h"]
    assert c.put("/admin/v1/settings/services_off", json=["dms", "sim"], headers=ADMIN).status_code == 200
    assert c.get("/api/v1/hall/setup", headers=h["hall1"]).json()["services_off"] == ["dms", "sim"]
    assert c.post("/api/v1/hall/cases", json={"pkg": "full"}, headers=h["hall1"]).status_code == 409
    assert c.post("/api/v1/hall/cases", json={"pkg": "card", "opts": {"dms": True}}, headers=h["hall1"]).status_code == 409
    assert c.post("/api/v1/hall/cases", json={"pkg": "card"}, headers=h["hall1"]).status_code == 201
