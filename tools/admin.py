"""Администрирование сервера «Единого окна». Запускается НА СЕРВЕРЕ через обёртку:  cd server && bash adm.sh <команда> ...
Токен администратора берётся из окружения контейнера — копировать его никуда не нужно.

Команды:
  health
  bootstrap-demo                                   учебный сервер: стойка, планшет, старший смены, 3 банка, черновики согласий
  bootstrap-prod                                   рабочий сервер: то же без черновиков согласий
  device-add "<Имя>" [desk <№ стойки> | hall <серия> | reception <серия> | board]
                                                   выдаёт ключ устройства (один раз). Серия — одна буква из ABEKMHPCTX
  device-set <id> поле=значение ...                name, role, station, series
  devices                                          список устройств
  device-off <id>                                  отключить потерянный планшет
  operator-add <код> "<ФИО>" <ПИН 6–8 цифр> [senior]
  operator-pin <код> <новый ПИН>                   смена ПИН, все сессии закрываются
  operator-off <код>                               увольнение
  bank-add <код> "<Название>" "<SIM-бренд>"        выдаёт ключ API и секрет подписи (один раз)
  bank-set <код> поле=значение ...                 legal_name, inn, mode(api|case), open_from, open_to,
                                                   cert_fingerprint, sim_authority_confirmed(true|false),
                                                   status_map='{"КОД_БАНКА":"КОД_СЛОВАРЯ"}', active(true|false),
                                                   window (номер окна банка для вызова на табло)
  bank-rotate <код>                                новый ключ и секрет; старые действуют 24 часа
  policy <банк> <card|snils|ebs|sim> <страна ISO3> <no|before|final|unknown> "<письмо № … от …>"
  text-add <файл.json>                             загрузить тексты согласий (образец docs/consent_texts.example.json)
  text-approve <id> "<ФИО юриста, приказ № …>"
  reconcile <банк> <файл.json> "<акт сверки № … от …>"   строки: event_id, case_number, bank_case_id, bank_code, occurred_at
  rejected                                         отклонённые события банков
  audit [id обращения]
  purge <дней>                                     удалить закрытые обращения старше N дней
  ticket-text <ru|tj|uz|ky> "<как пройти в офис>"  текст маршрута на талоне
  queue-report [ГГГГ-ММ-ДД]                        талоны за день: выдано, пришли, обслужены, ожидание, по сотрудникам зала
"""
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SIMPAD_ADMIN_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("SIMPAD_ADMIN_TOKEN", "")
BANKS = [("tbank", "Т‑Банк", "Т‑Мобайл"), ("mts", "МТС Банк", "МТС"), ("vtb", "ВТБ", "ВТБ Мобайл")]
COUNTRIES = ("TJK", "UZB", "KGZ", "KAZ", "ARM", "AZE")


def read_json(path):
    """Файл из аргумента: переданный обёрткой adm.sh (FILES) или локальный."""
    import base64
    files = globals().get("FILES") or {}
    if path in files:
        return json.loads(base64.b64decode(files[path]).decode("utf-8"))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Admin-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"Ошибка {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Сервер недоступен: {e.reason}")


def show(x):
    print(json.dumps(x, ensure_ascii=False, indent=1))


def val(v):
    if v in ("true", "false"):
        return v == "true"
    if v.lstrip("-").isdigit():
        return int(v)
    if v.startswith("{"):
        return json.loads(v)
    return v


def bootstrap(demo):
    call("POST", "/admin/v1/stands", {"id": "zia", "name": "Аэропорт Жуковский, зал прилёта"})
    dev = call("POST", "/admin/v1/devices", {"stand_id": "zia", "name": "Стойка 1", "role": "desk", "station": "1"})
    hall = call("POST", "/admin/v1/devices", {"stand_id": "zia", "name": "Зал 1", "role": "hall", "series": "A"})
    board = call("POST", "/admin/v1/devices", {"stand_id": "zia", "name": "Табло офиса", "role": "board"})
    pin = f"{secrets.randbelow(10 ** 6):06d}"
    call("POST", "/admin/v1/operators", {"stand_id": "zia", "code": "senior1", "name": "Старший смены", "pin": pin, "role": "senior"})
    print("=== ЗАПИШИТЕ В МЕНЕДЖЕР ПАРОЛЕЙ. Показывается один раз ===")
    print("Ключ планшета «Стойка 1» (офис, стойка № 1):", dev["device_key"])
    print("Ключ планшета «Зал 1» (талоны серии А):     ", hall["device_key"])
    print("Ключ табло офиса (браузер на экране):       ", board["device_key"])
    print("Старший смены: код senior1, ПИН", pin)
    for i, (bid, name, sim) in enumerate(BANKS):
        k = call("POST", "/admin/v1/banks", {"id": bid, "name": name, "sim_brand": sim, "mode": "case", "open_from": 9, "open_to": 21})
        call("PATCH", f"/admin/v1/banks/{bid}", {"window": str(11 + i)})
        print(f"{name}: ключ API {k['bank_api_key']}  секрет подписи {k['hmac_secret']}")
        call("PUT", "/admin/v1/policies", [{"bank_id": bid, "service": s, "country": c, "rule": "unknown"}
                                           for s in ("card", "snils", "ebs", "sim") for c in COUNTRIES])
    call("PUT", "/admin/v1/settings/notary_hours", [9, 20])
    if demo:
        body = ("ЧЕРНОВИК ДЛЯ ОБУЧЕНИЯ. Не является юридическим текстом согласия. "
                "Рабочий текст готовит юрист компании по 152‑ФЗ: оператор, цель, перечень данных, действия, срок, порядок отзыва.")
        drafts = [("simpad", None, "Оператор единого окна (черновик)"), ("notary", None, "Бюро переводов (черновик)"),
                  ("insurer", None, "Страховщик ДМС (черновик)")] + [("bank", b, f"{n} (черновик)") for b, n, _ in BANKS]
        for rcp, bid, legal in drafts:
            call("POST", "/admin/v1/consent-texts", {
                "recipient": rcp, "bank_id": bid, "legal_name": legal, "inn": "0000000000", "purpose": "Учебный сценарий",
                "fields": ["ФИО", "реквизиты паспорта", "реквизиты миграционной карты"], "retention": "30 дней",
                "body": body, "version": "draft-1", "approved_by": ""})
        print("Черновики согласий созданы (только для учебного сервера).")
    print("Готово.")


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    if not TOKEN:
        sys.exit("Нет SIMPAD_ADMIN_TOKEN — запускайте через bash adm.sh")
    cmd, args = a[0], a[1:]
    if cmd == "health":
        show(call("GET", "/healthz"))
    elif cmd == "bootstrap-demo":
        bootstrap(True)
    elif cmd == "bootstrap-prod":
        bootstrap(False)
    elif cmd == "device-add":
        body = {"stand_id": "zia", "name": args[0], "role": args[1] if len(args) > 1 else "desk"}
        if body["role"] == "desk" and len(args) > 2:
            body["station"] = args[2]
        elif body["role"] in ("hall", "reception"):
            body["series"] = args[2].upper().translate(str.maketrans("АВЕКМНРСТХ", "ABEKMHPCTX"))
        show(call("POST", "/admin/v1/devices", body))
    elif cmd == "device-set":
        body = {}
        for kv in args[1:]:
            k, _, v = kv.partition("=")
            body[k] = v.upper().translate(str.maketrans("АВЕКМНРСТХ", "ABEKMHPCTX")) if k == "series" else v
        show(call("PATCH", f"/admin/v1/devices/{int(args[0])}", body))
    elif cmd == "devices":
        show(call("GET", "/admin/v1/devices"))
    elif cmd == "device-off":
        show(call("DELETE", f"/admin/v1/devices/{int(args[0])}"))
    elif cmd == "operator-add":
        role = "senior" if len(args) > 3 and args[3] == "senior" else "operator"
        show(call("POST", "/admin/v1/operators", {"stand_id": "zia", "code": args[0], "name": args[1], "pin": args[2], "role": role}))
    elif cmd == "operator-pin":
        show(call("PATCH", f"/admin/v1/operators/{args[0]}", {"pin": args[1]}))
    elif cmd == "operator-off":
        show(call("PATCH", f"/admin/v1/operators/{args[0]}", {"active": False}))
    elif cmd == "bank-add":
        show(call("POST", "/admin/v1/banks", {"id": args[0], "name": args[1], "sim_brand": args[2] if len(args) > 2 else "", "mode": "case"}))
    elif cmd == "bank-set":
        body = {}
        for kv in args[1:]:
            k, _, v = kv.partition("=")
            body[k] = v if k in ("window", "inn", "legal_name") else val(v)
        show(call("PATCH", f"/admin/v1/banks/{args[0]}", body))
    elif cmd == "bank-rotate":
        show(call("POST", f"/admin/v1/banks/{args[0]}/rotate"))
    elif cmd == "policy":
        bank, svc, country, rule, ver = args[:5]
        show(call("PUT", "/admin/v1/policies", [{"bank_id": bank, "service": svc, "country": country.upper(), "rule": rule,
                                                 "confirmed": rule != "unknown", "version": ver}]))
    elif cmd == "text-add":
        for t in read_json(args[0]):
            r = call("POST", "/admin/v1/consent-texts", t)
            print(f"id={r['id']}  {r['recipient']} {r.get('bank_id') or ''}  версия {r['version']}  sha256 {r['sha256'][:16]}…  утверждён: {r['approved']}")
    elif cmd == "text-approve":
        show(call("POST", f"/admin/v1/consent-texts/{int(args[0])}/approve", {"approved_by": args[1]}))
    elif cmd == "reconcile":
        rows = read_json(args[1])
        show(call("POST", f"/admin/v1/banks/{args[0]}/reconcile", {"source_doc": args[2], "rows": rows}))
    elif cmd == "rejected":
        show(call("GET", "/admin/v1/rejected-events"))
    elif cmd == "audit":
        show(call("GET", "/admin/v1/audit" + (f"?case_id={args[0]}" if args else "")))
    elif cmd == "purge":
        show(call("POST", f"/admin/v1/purge?days={int(args[0])}"))
    elif cmd == "ticket-text":
        show(call("PUT", f"/admin/v1/ticket-texts/{args[0]}", {"route": args[1]}))
    elif cmd == "queue-report":
        show(call("GET", "/admin/v1/reports/queue?stand_id=zia" + (f"&date={args[0]}" if args else "")))
    else:
        sys.exit(f"Неизвестная команда {cmd}. Список: bash adm.sh help")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (IndexError, ValueError):
        sys.exit("Не хватает или неверные параметры. Список: bash adm.sh help")
