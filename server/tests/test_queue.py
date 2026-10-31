"""Электронная очередь: талон в зале → приёмная → вызов на табло → обслуживание → окно банка."""
import base64
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_flow import ADMIN, add_text, login, make_bank, settings, FP_T


def dev(c, name, role="desk", station="", series=""):
    r = c.post("/admin/v1/devices", headers=ADMIN, json={"stand_id": "zia", "name": name, "role": role,
                                                          "station": station, "series": series})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def q(tmp_path, monkeypatch):
    c = TestClient(create_app(settings(tmp_path)))
    monkeypatch.setattr("app.services.Svc.hour_now", lambda self: 12.0)
    assert c.post("/admin/v1/stands", json={"id": "zia", "name": "Жуковский"}, headers=ADMIN).status_code == 200
    for code in ("floor1", "hall1", "hall2", "rec1", "desk7", "desk8"):
        assert c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": code, "name": code,
                                                                   "pin": "246810"}).status_code == 200
    d = {"floor1": dev(c, "Агент 1 этажа", "floor1", series="E"), "hall1": dev(c, "Зал 1", "hall", series="A"), "hall2": dev(c, "Зал 2", "hall", series="B"),
         "rec": dev(c, "Приёмная", "reception", series="P"), "desk7": dev(c, "Стойка 7", "desk", station="7"),
         "desk8": dev(c, "Стойка 8", "desk", station="8"), "board": dev(c, "Табло", "board")}
    h = {"floor1": login(c, d["floor1"]["device_key"], "floor1"), "hall1": login(c, d["hall1"]["device_key"], "hall1"), "hall2": login(c, d["hall2"]["device_key"], "hall2"),
         "rec": login(c, d["rec"]["device_key"], "rec1"), "desk7": login(c, d["desk7"]["device_key"], "desk7"),
         "desk8": login(c, d["desk8"]["device_key"], "desk8"), "board": {"X-Device-Key": d["board"]["device_key"]}}
    return {"c": c, "d": d, "h": h}


def issue(q, who="hall1", lang="tj"):
    r = q["c"].post("/api/v1/tickets", json={"lang": lang}, headers=q["h"][who])
    assert r.status_code == 201, r.text
    return r.json()


def test_hall_ticket_has_no_personal_data_and_series_numbering(q):
    a1, a2, b1 = issue(q), issue(q, lang="ky"), issue(q, "hall2", "uz")
    assert (a1["number"], a2["number"], b1["number"]) == ("А001", "А002", "В001")  # кириллица на талоне
    assert a1["qr"].isdigit() and len(a1["qr"]) == 16 and a1["status"] == "issued"
    assert "Рақами шумо" == a1["print"]["your"] and "кабатка" in a2["print"]["route"]
    assert set(a1) >= {"number", "qr", "valid_until", "print"} and not {"name", "passport", "phone"} & set(a1)


def test_series_required_and_unique(q):
    c = q["c"]
    r = c.post("/admin/v1/devices", headers=ADMIN, json={"stand_id": "zia", "name": "x", "role": "hall"})
    assert r.status_code == 422
    r = c.post("/admin/v1/devices", headers=ADMIN, json={"stand_id": "zia", "name": "x", "role": "hall", "series": "A"})
    assert r.status_code == 409
    r = c.post("/admin/v1/devices", headers=ADMIN, json={"stand_id": "zia", "name": "x", "role": "hall", "series": "Z"})
    assert r.status_code == 422  # только буквы, одинаковые в латинице и кириллице


def test_roles_are_enforced(q):
    c, h = q["c"], q["h"]
    assert c.post("/api/v1/cases", headers=h["hall1"]).status_code == 403  # зал не открывает обращения
    assert c.post("/api/v1/tickets/checkin", json={"code": "A001"}, headers=h["hall1"]).status_code == 403
    assert c.post("/api/v1/cases", headers=h["rec"]).status_code == 403
    assert c.post("/api/v1/queue/next", headers=h["rec"]).status_code == 403
    assert c.post("/api/v1/login", json={"code": "desk7", "pin": "246810"}, headers=h["board"]).status_code == 403
    assert c.get("/api/v1/board", headers=h["board"]).status_code == 200
    assert c.get("/api/v1/board", headers={"X-Device-Key": q["d"]["hall1"]["device_key"]}).status_code == 403
    assert c.get("/api/v1/cases", headers=h["floor1"]).status_code == 403
    assert c.post("/api/v1/tickets", json={"lang": "ru"}, headers=h["floor1"]).status_code == 403


def test_floor1_pre_case_reaches_second_floor_without_duplicate(q):
    """У агента 1-го этажа только его pre-case, паспортная латиница и талон без ПДн."""
    from tests.test_flow import PASSPORT, PNG

    c, h = q["c"], q["h"]
    text_id = add_text(c, "simpad")
    assert c.get("/api/v1/floor1/consent-texts", headers=h["floor1"]).json()[0]["id"] == text_id
    opened = c.post("/api/v1/floor1/cases", headers=h["floor1"])
    assert opened.status_code == 201, opened.text
    pre = opened.json()
    assert pre["passport"] is None and pre["consent_signed"] is False and "cyr" not in pre
    cid = pre["id"]
    assert c.post(f"/api/v1/floor1/cases/{cid}/consents", headers=h["floor1"], json={
        "recipient": "simpad", "text_id": text_id, "method": "stylus", "explained_lang": "ru",
        "evidence_mime": "image/png", "evidence_b64": base64.b64encode(PNG).decode()}).status_code == 200
    scan = c.put(f"/api/v1/floor1/cases/{cid}/scan", headers=h["floor1"], json={"passport": PASSPORT})
    assert scan.status_code == 200, scan.text
    preview = scan.json()
    assert preview["passport"]["surname"] == "RAKHIMOV" and "cyr" not in preview["passport"]
    assert c.put(f"/api/v1/floor1/cases/{cid}/scan", headers=h["floor1"], json={"passport": PASSPORT, "migcard": {}}).status_code == 422
    first = c.post(f"/api/v1/floor1/cases/{cid}/ticket", headers={**h["floor1"], "Idempotency-Key": "floor1-ticket-0001"})
    assert first.status_code == 201, first.text
    out = first.json()
    ticket = out["ticket"]
    assert ticket["number"] == "Е001" and ticket["source"] == "floor1" and not {"name", "passport", "phone"} & set(ticket)
    replay = c.post(f"/api/v1/floor1/cases/{cid}/ticket", headers={**h["floor1"], "Idempotency-Key": "floor1-ticket-0002"})
    assert replay.status_code == 200 and replay.json()["ticket"]["id"] == ticket["id"] and replay.json()["replay"] is True
    assert c.post(f"/api/v1/tickets/{ticket['id']}/printed", headers=h["floor1"]).status_code == 200
    assert c.post("/api/v1/tickets/checkin", json={"code": ticket["qr"]}, headers=h["rec"]).status_code == 200
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    arrived = c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"])
    assert arrived.status_code == 201, arrived.text
    case = arrived.json()
    assert case["id"] == cid and case["ticket"]["number"] == "Е001" and case["passport"]["cyr"] == "Рахимов Фаррух"
    report = c.get("/api/v1/reports/queue", headers=h["rec"]).json()
    assert report["issued_floor1"] == 1 and report["printed_floor1"] == 1


def test_queue_order_is_by_arrival_not_by_number(q):
    c, h = q["c"], q["h"]
    t1, t2 = issue(q), issue(q)
    # второй дошёл первым; номер можно набрать кириллицей и с пробелом
    assert c.post("/api/v1/tickets/checkin", json={"code": "а 002"}, headers=h["rec"]).json()["position"] == 1
    r = c.post("/api/v1/tickets/checkin", json={"code": t1["qr"]}, headers=h["rec"]).json()
    assert r["position"] == 2 and r["status"] == "waiting"
    # повторное сканирование того же талона не двигает его в конец
    assert c.post("/api/v1/tickets/checkin", json={"code": t2["qr"]}, headers=h["rec"]).json()["position"] == 1
    first = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    assert first["number"] == "А002" and first["station"] == "7" and first["status"] == "called"
    second = c.post("/api/v1/queue/next", headers=h["desk8"]).json()
    assert second["number"] == "А001" and second["station"] == "8"
    assert c.post("/api/v1/queue/next", headers=h["desk7"]).status_code == 404  # очередь пуста
    board = c.get("/api/v1/board", headers=h["board"]).json()
    assert [x["number"] for x in board["calls"]] == ["А001", "А002"] and board["waiting"] == 0


def test_bad_codes(q):
    c, h = q["c"], q["h"]
    assert c.post("/api/v1/tickets/checkin", json={"code": "А999"}, headers=h["rec"]).status_code == 404
    assert c.post("/api/v1/tickets/checkin", json={"code": "1234567890123456"}, headers=h["rec"]).status_code == 404
    assert c.post("/api/v1/tickets/checkin", json={"code": "привет"}, headers=h["rec"]).status_code == 422


def test_reception_walk_in_goes_straight_to_queue(q):
    c, h = q["c"], q["h"]
    t = issue(q, "rec", "ru")
    assert t["number"] == "Р001" and t["status"] == "waiting" and t["source"] == "reception"
    assert c.post("/api/v1/queue/next", headers=h["desk7"]).json()["number"] == "Р001"


def test_full_cycle_recall_noshow_serve_bank_done(q):
    c, h = q["c"], q["h"]
    t = issue(q, lang="ky")
    c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"])
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    again = c.post(f"/api/v1/tickets/{called['id']}/recall", headers=h["desk7"]).json()
    assert again["call_count"] == 2
    assert c.post(f"/api/v1/tickets/{called['id']}/noshow", headers=h["desk7"]).json()["status"] == "noshow"
    # вернулся — снова в очередь, в конец
    assert c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"]).json()["status"] == "waiting"
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    case = c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"])
    assert case.status_code == 201, case.text
    case = case.json()
    assert case["ticket"]["number"] == "А001" and case["lang"] == "ky"
    assert c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"]).status_code == 409  # не дважды
    # к окну банка — только после передачи в банк
    assert c.post(f"/api/v1/cases/{case['id']}/to-bank", headers=h["desk7"]).status_code == 409
    assert c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"]).status_code == 409  # обслужен
    c.post(f"/api/v1/cases/{case['id']}/close", headers=h["desk7"])
    rep = c.get("/api/v1/reports/queue", headers=h["rec"]).json()
    assert rep["issued"] == 1 and rep["came"] == 1 and rep["served"] == 1
    assert rep["by_hall_operator"]["hall1"] == {"name": "hall1", "issued": 1, "came": 1, "served": 1}


def test_to_bank_announces_bank_window(q, tmp_path):
    c, h = q["c"], q["h"]
    from tests.test_flow import JPG, MIG, PASSPORT, PNG, consent  # noqa: F401
    make_bank(c, "tbank", "case", FP_T)
    assert c.patch("/admin/v1/banks/tbank", json={"window": "12"}, headers=ADMIN).status_code == 200
    texts = {"simpad": add_text(c, "simpad"), "tbank": add_text(c, "bank", "tbank")}
    t = issue(q)
    c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"])
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    cid = c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"]).json()["id"]
    e = {"c": c, "h": h["desk7"], "texts": texts}
    assert consent(e, cid, "simpad", method="stylus", data=PNG, mime="image/png").status_code == 200
    assert c.put(f"/api/v1/cases/{cid}/scan", json={"passport": PASSPORT, "migcard": MIG}, headers=h["desk7"]).status_code == 200
    assert c.patch(f"/api/v1/cases/{cid}", json={"pkg": "card", "bank_id": "tbank"}, headers=h["desk7"]).status_code == 200
    assert consent(e, cid, "bank", text_key="tbank").status_code == 200
    r = c.post(f"/api/v1/cases/{cid}/handoff/manual", headers=h["desk7"])
    assert r.status_code == 200, r.text
    r = c.post(f"/api/v1/cases/{cid}/to-bank", headers=h["desk7"]).json()
    assert r["status"] == "bank" and r["station"] == "12" and r["station_kind"] == "bank"
    assert c.get("/api/v1/board", headers=h["board"]).json()["calls"][0] == {**c.get("/api/v1/board", headers=h["board"]).json()["calls"][0], "number": "А001", "station": "12", "kind": "bank"}
    c.post(f"/api/v1/cases/{cid}/close", headers=h["desk7"])
    assert c.post(f"/api/v1/cases/{cid}/to-bank", headers=h["desk7"]).status_code == 409  # закрыто — повторного вызова нет


def test_desk_without_station_cannot_call(q):
    c = q["c"]
    d = dev(c, "Без номера", "desk")
    assert c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": "x1", "name": "x",
                                                               "pin": "246810"}).status_code == 200
    hh = login(c, d["device_key"], "x1")
    assert c.post("/api/v1/queue/next", headers=hh).status_code == 409


def test_ticket_texts_override(q):
    c = q["c"]
    r = c.put("/admin/v1/ticket-texts/ru", headers=ADMIN, json={"route": "Офис на 2 этаже, над кафе. Ждите вызова."})
    assert r.status_code == 200
    assert issue(q, lang="ru")["print"]["route"] == "Офис на 2 этаже, над кафе. Ждите вызова."


def test_parallel_calls_never_take_the_same_ticket(tmp_path, monkeypatch):
    """Два рабочих места нажимают «Вызвать» одновременно — каждый талон достаётся ровно одному."""
    app = create_app(settings(tmp_path, name="par.db"))
    c = TestClient(app)
    c.post("/admin/v1/stands", json={"id": "zia", "name": "Ж"}, headers=ADMIN)
    hall = dev(c, "Зал", "hall", series="A")
    c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": "hh", "name": "h", "pin": "246810"})
    hh = login(c, hall["device_key"], "hh")
    rec = dev(c, "Приёмная", "reception", series="P")
    c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": "rr", "name": "r", "pin": "246810"})
    rh = login(c, rec["device_key"], "rr")
    for _ in range(6):
        t = c.post("/api/v1/tickets", json={"lang": "ru"}, headers=hh).json()
        c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=rh)
    desks = []
    for i in range(6):
        d = dev(c, f"С{i}", "desk", station=str(i + 1))
        c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": f"d{i}", "name": "d", "pin": "246810"})
        desks.append(login(c, d["device_key"], f"d{i}"))
    got, lock = [], threading.Lock()

    def worker(hdr):
        r = TestClient(app).post("/api/v1/queue/next", headers=hdr)
        with lock:
            got.append((r.status_code, r.json().get("number")))

    ths = [threading.Thread(target=worker, args=(hd,)) for hd in desks]
    [t.start() for t in ths]
    [t.join() for t in ths]
    numbers = [n for st, n in got if st == 200]
    assert len(numbers) == len(set(numbers)) == 6


def test_other_desk_cannot_touch_my_call_and_stations_unique(q):
    c, h = q["c"], q["h"]
    t = issue(q)
    c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"])
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    for act in ("noshow", "recall", "start"):
        r = c.post(f"/api/v1/tickets/{called['id']}/{act}", headers=h["desk8"])
        assert r.status_code == 409 and "стойке 7" in r.json()["detail"], (act, r.text)
    r = c.post("/admin/v1/devices", headers=ADMIN, json={"stand_id": "zia", "name": "Дубль", "role": "desk", "station": "7"})
    assert r.status_code == 409


def test_to_bank_after_close_is_rejected(q):
    c, h = q["c"], q["h"]
    t = issue(q)
    c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"])
    called = c.post("/api/v1/queue/next", headers=h["desk7"]).json()
    cid = c.post(f"/api/v1/tickets/{called['id']}/start", headers=h["desk7"]).json()["id"]
    c.post(f"/api/v1/cases/{cid}/close", headers=h["desk7"])
    from app.queue import Queue  # noqa: F401  — прямой вызов to_bank: обращение закрыто
    r = c.post(f"/api/v1/cases/{cid}/to-bank", headers=h["desk7"])
    assert r.status_code == 409
    assert c.get("/api/v1/board", headers=h["board"]).json()["calls"] == []


def test_times_carry_timezone(q):
    t = issue(q)
    assert t["issued_at"].endswith("+00:00")


def test_board_works_with_min_app_version(tmp_path):
    c = TestClient(create_app(settings(tmp_path, name="mv.db", min_app_version=5)))
    c.post("/admin/v1/stands", json={"id": "zia", "name": "Ж"}, headers=ADMIN)
    b = dev(c, "Табло", "board")
    assert c.get("/api/v1/board", headers={"X-Device-Key": b["device_key"]}).status_code == 200
    d = dev(c, "Стойка", "desk", station="1")
    assert c.get("/api/v1/me", headers={"X-Device-Key": d["device_key"]}).status_code == 426  # планшет — проверяется


def test_same_idempotency_key_returns_same_ticket(q):
    c, h = q["c"], q["h"]
    hdr = {**h["hall1"], "Idempotency-Key": "tap-0001-abcdef"}
    r1 = c.post("/api/v1/tickets", json={"lang": "tj"}, headers=hdr)
    r2 = c.post("/api/v1/tickets", json={"lang": "tj"}, headers=hdr)
    assert r1.status_code == 201 and r2.status_code == 200
    a, b = r1.json(), r2.json()
    assert (a["id"], a["number"], a["qr"]) == (b["id"], b["number"], b["qr"]) and b["replay"] is True
    r3 = c.post("/api/v1/tickets", json={"lang": "tj"}, headers={**h["hall1"], "Idempotency-Key": "tap-0002-abcdef"})
    assert r3.json()["number"] == "А002"
    # тот же ключ на другом планшете — это другой запрос
    r4 = c.post("/api/v1/tickets", json={"lang": "tj"}, headers={**h["hall2"], "Idempotency-Key": "tap-0001-abcdef"})
    assert r4.json()["number"] == "В001"
    assert c.post("/api/v1/tickets", json={"lang": "tj"}, headers={**h["hall1"], "Idempotency-Key": "bad key!"}).status_code == 422
    rep = c.get("/api/v1/reports/queue", headers=h["rec"]).json()
    assert rep["issued"] == 3


def test_parallel_same_key_gives_one_ticket(tmp_path):
    app = create_app(settings(tmp_path, name="idem.db"))
    c = TestClient(app)
    c.post("/admin/v1/stands", json={"id": "zia", "name": "Ж"}, headers=ADMIN)
    hall = dev(c, "Зал", "hall", series="A")
    c.post("/admin/v1/operators", headers=ADMIN, json={"stand_id": "zia", "code": "hh", "name": "h", "pin": "246810"})
    hh = {**login(c, hall["device_key"], "hh"), "Idempotency-Key": "same-key-123456"}
    got, lock = [], threading.Lock()

    def worker():
        r = TestClient(app).post("/api/v1/tickets", json={"lang": "ru"}, headers=hh)
        with lock:
            got.append((r.status_code, r.json().get("id"), r.json().get("qr")))

    ths = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert all(s in (200, 201) for s, _, _ in got), got
    assert len({(i, qr) for s, i, qr in got}) == 1


def test_printed_mark_only_by_issuing_device(q):
    c, h = q["c"], q["h"]
    t = issue(q)
    assert c.post(f"/api/v1/tickets/{t['id']}/printed", headers=h["hall2"]).status_code == 403
    assert c.post(f"/api/v1/tickets/{t['id']}/printed", headers=h["hall1"]).status_code == 200
    assert c.get("/api/v1/reports/queue", headers=h["rec"]).json()["printed_hall"] == 1
    # QR талона стабилен: по нему находится тот же талон
    assert c.post("/api/v1/tickets/checkin", json={"code": t["qr"]}, headers=h["rec"]).json()["id"] == t["id"]


def test_position_ties_break_by_seq(q):
    """Два прихода в одну и ту же микросекунду: позиции 1 и 2, как и порядок вызова."""
    import sqlite3
    c, h = q["c"], q["h"]
    t1, t2 = issue(q), issue(q)
    c.post("/api/v1/tickets/checkin", json={"code": t1["qr"]}, headers=h["rec"])
    c.post("/api/v1/tickets/checkin", json={"code": t2["qr"]}, headers=h["rec"])
    con = sqlite3.connect(c.app.state.settings.database_url.replace("sqlite:///", ""))
    con.execute("UPDATE tickets SET checked_in_at=(SELECT checked_in_at FROM tickets WHERE seq=1)")
    con.commit(); con.close()
    p1 = c.post("/api/v1/tickets/checkin", json={"code": t1["qr"]}, headers=h["rec"]).json()["position"]
    p2 = c.post("/api/v1/tickets/checkin", json={"code": t2["qr"]}, headers=h["rec"]).json()["position"]
    assert (p1, p2) == (1, 2)
