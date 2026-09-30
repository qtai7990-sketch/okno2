"""Правила маршрута. Единственный источник истины о порядке шагов и блокировках.

Порядок маршрута на стойке: СНИЛС → подтверждённая ЕСИА → ЕБС → IMEI → SIM (подсказки и блокировки интерфейса).
Приём событий банка: SIM‑события принимаются только после подтверждённых банком ЕСИА и ЕБС (EVENT_PREREQ).
IMEI в приём событий не входит: IMEI фиксирует банк в договоре связи (ст. 45.1 «О связи»); отметка IMEI на стойке —
вспомогательная и доказательством не является.
RuID — добровольная процедура и ни от чего не освобождает: маршрут строится так, будто RuID нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STEP_ORDER = ["screen", "wifi", "card", "dms", "transl", "snils", "ebs", "imei", "sim", "transfer", "phone"]

STEPS: dict[str, dict] = {
    "screen": {"title": "Сканирование и согласия", "owner": "simpad", "auto": False},
    "wifi": {"title": "Wi‑Fi и SIM с родины", "owner": "simpad", "auto": False},
    "card": {"title": "Банковская карта", "owner": "bank", "auto": True},
    "dms": {"title": "Полис ДМС", "owner": "insurer", "auto": False},
    "transl": {"title": "Нотариальный перевод паспорта", "owner": "notary", "auto": False},
    "snils": {"title": "СНИЛС + подтверждённые Госуслуги", "owner": "bank", "auto": True},
    "ebs": {"title": "Биометрия в ЕБС", "owner": "bank", "auto": True},
    "imei": {"title": "IMEI телефона", "owner": "simpad", "auto": False},
    "sim": {"title": "SIM + первый месяц", "owner": "bank", "auto": True},
    "transfer": {"title": "Первый перевод на родину", "owner": "transfer", "auto": False},
    "phone": {"title": "Android‑смартфон под «Амину»", "owner": "simpad", "auto": False},
}

# Шаги, статус которых сотрудник стойки может отметить сам (с записью в журнал).
# Шаги банка в пилоте «номер дела» оператор отмечает только как нейтральный статус со слов банка.
OPERATOR_STEPS = {"wifi", "imei", "phone", "dms", "transl", "transfer"}

# «Выполнено» по шагам исполнителей без интеграции — только с номером документа, который это подтверждает.
# СНИЛС здесь только в режиме «через Соцфонд»: тогда шаг подтверждает сотрудник по уведомлению о регистрации.
DOC_STEPS = {
    "dms": "Номер полиса ДМС",
    "transl": "Номер в реестре нотариальных действий",
    "transfer": "Контрольный номер перевода",
    "snils": "Номер СНИЛС из уведомления о регистрации",
}
# Как подтверждается СНИЛС и подтверждённая учётная запись Госуслуг:
#   bank — событиями банка (только если у точки банка есть такие полномочия и договор);
#   sfr  — клиент оформляет через Соцфонд/МФЦ, сотрудник отмечает по уведомлению о регистрации.
SNILS_CHANNELS = ("bank", "sfr")
# Услуги, которые можно выключить настройкой сервера (нет договора — сотрудник их не видит и не предлагает)
SWITCHABLE = ("card", "sim", "dms", "transfer", "phone")

POLICY_RULES = {"no", "before", "final", "unknown"}
PAY_METHODS = {"bankcard", "mir", "employer", "transfer"}
RECIPIENTS = {"simpad", "bank", "notary", "insurer"}

# Нормализованные коды словаря статусов -> действие над шагом маршрута
CODES = {
    "BANK_CASE_ACCEPTED": None,
    "CARD_ISSUED": ("card", "done"),
    "CARD_ACTIVE": ("card", "done"),
    "CARD_NOT_ISSUED": ("card", "fail"),
    "SNILS_CREATED": ("snils", "work"),
    "ESIA_CONFIRMED": ("snils", "done"),
    "SNILS_ERROR": ("snils", "fail"),
    "EBS_REGISTERED": ("ebs", "done"),
    "REVISIT_REQUIRED": None,
    "SIM_CONTRACT_SIGNED": ("sim", "work"),
    "SIM_ISSUED": ("sim", "done"),
    "SIM_NOT_ISSUED": ("sim", "fail"),
}
# Услуга, к которой относится код: событие по услуге вне маршрута клиента отклоняется
CODE_SERVICE = {
    "CARD_ISSUED": "card", "CARD_ACTIVE": "card", "CARD_NOT_ISSUED": "card",
    "SNILS_CREATED": "snils", "ESIA_CONFIRMED": "snils", "SNILS_ERROR": "snils",
    "EBS_REGISTERED": "ebs",
    "SIM_CONTRACT_SIGNED": "sim", "SIM_ISSUED": "sim", "SIM_NOT_ISSUED": "sim",
}
# Обязательный порядок. Предпосылки — только ПОДТВЕРЖДЁННЫЕ события того же банка по тому же обращению.
# IMEI сюда не входит: банк фиксирует IMEI в своём договоре связи, наша отметка IMEI — не доказательство.
EVENT_PREREQ = {
    "CARD_ACTIVE": ["CARD_ISSUED"],
    "EBS_REGISTERED": ["ESIA_CONFIRMED"],
    "SIM_CONTRACT_SIGNED": ["ESIA_CONFIRMED", "EBS_REGISTERED"],
    "SIM_ISSUED": ["ESIA_CONFIRMED", "EBS_REGISTERED"],
    # отказ по SIM до ЕСИА и ЕБС тоже не принимается: SIM‑ветка не может закрыться раньше предпосылок
    "SIM_NOT_ISSUED": ["ESIA_CONFIRMED", "EBS_REGISTERED"],
}
# Допустимое расхождение часов банка при проверке хронологии предпосылок
CLOCK_SKEW_SEC = 120

SERVICES = ("card", "snils", "ebs", "sim")
# Если банк не подтвердил правило письменно: для карты — предупреждение, для СНИЛС — перевод считаем нужным
# (осторожный вариант: без перевода СФР может отказать, клиент потеряет визит).
UNKNOWN_DEFAULT = {"card": "unknown", "snils": "before", "ebs": "no", "sim": "no"}


@dataclass
class Ctx:
    pkg: str | None
    opts: dict
    pay: dict
    has_ru_text: bool
    doc_rules: dict[str, str]  # услуга -> правило банка (no|before|final|unknown)
    card_state: str
    consents: set[str]
    status: dict[str, str]
    bank_name: str | None
    bank_mode: str | None
    bank_open: tuple[int, int] | None
    notary_open: tuple[int, int]
    hour: float
    sent: bool
    online: bool = True
    scanned: bool = False
    snils_channel: str = "bank"
    extra: dict = field(default_factory=dict)


def bank_services(pkg: str | None) -> list[str]:
    out = []
    if pkg in ("card", "full"):
        out.append("card")
    if pkg in ("sim", "full"):
        out += ["snils", "ebs", "sim"]
    return out


def bank_services_ctx(ctx: Ctx) -> list[str]:
    """Услуги, статус которых приходит от банка. СНИЛС через Соцфонд банку не принадлежит."""
    return [s for s in bank_services(ctx.pkg) if not (s == "snils" and ctx.snils_channel == "sfr")]


def owner(ctx: Ctx, key: str) -> str:
    if key == "snils" and ctx.snils_channel == "sfr":
        return "sfr"
    return STEPS[key]["owner"]


def operator_can_mark(ctx: Ctx, key: str) -> bool:
    return key in OPERATOR_STEPS or (key == "snils" and ctx.snils_channel == "sfr")


def transl_rule(ctx: Ctx, svc: str) -> str:
    """Правило перевода для услуги. RuID НЕ отменяет перевод: решает только политика банка и русский текст в паспорте."""
    if ctx.has_ru_text:
        return "no"
    return ctx.doc_rules.get(svc, "unknown")


def effective_rule(ctx: Ctx, svc: str) -> str:
    r = transl_rule(ctx, svc)
    return UNKNOWN_DEFAULT[svc] if r == "unknown" else r


def need_transl(ctx: Ctx) -> bool:
    services = bank_services(ctx.pkg) or list(SERVICES)
    return any(effective_rule(ctx, s) in ("before", "final") for s in services)


def steps_for(ctx: Ctx) -> list[str]:
    if not ctx.pkg:
        return ["screen", "wifi"]
    want = {"screen", "wifi", *bank_services(ctx.pkg)}
    if "sim" in want:
        want.add("imei")
    if need_transl(ctx):
        want.add("transl")
    if ctx.opts.get("dms"):
        want.add("dms")
    if ctx.opts.get("transfer"):
        want.add("transfer")
    if ctx.opts.get("phone") and "sim" in want:
        want.add("phone")
    return [k for k in STEP_ORDER if k in want]


def deps(ctx: Ctx, key: str) -> list[str]:
    s = steps_for(ctx)
    table = {
        "screen": [],
        "wifi": ["screen"],
        "card": ["transl"] if (effective_rule(ctx, "card") == "before" and "transl" in s) else ["screen"],
        "dms": ["screen"],
        "transl": ["screen"],
        "snils": ["transl"] if (effective_rule(ctx, "snils") == "before" and "transl" in s) else ["screen"],
        "ebs": ["snils"],
        "imei": ["screen"],
        "sim": ["snils", "ebs", "imei"],
        "transfer": ["card"],
        "phone": ["sim"],
    }
    return table.get(key, [])


def is_open(hour: float, span: tuple[int, int]) -> bool:
    """Часы работы. Поддерживается ночной интервал через полночь: (22, 6) — открыт с 22:00 до 06:00.
    (0, 24) — круглосуточно. Равные границы — закрыт (запрещено при вводе)."""
    start, end = span
    if start < end:
        return start <= hour < end
    if start > end:
        return hour >= start or hour < end
    return False


def span_text(span: tuple[int, int]) -> str:
    return "круглосуточно" if span == (0, 24) else f"{span[0]:02d}:00–{span[1] % 24:02d}:00"


_open = is_open  # прежнее имя


def blockers(ctx: Ctx, key: str) -> list[str]:
    st = ctx.status.get(key, "todo")
    if st in ("done", "fail"):
        return []
    out: list[str] = []
    if key == "screen":
        if not ctx.scanned:
            out.append("Паспорт и миграционная карта не отсканированы")
        if "simpad" not in ctx.consents:
            out.append("Нет согласия клиента на обработку данных")
    for d in deps(ctx, key):
        if ctx.status.get(d, "todo") != "done":
            out.append(f"Ждёт «{STEPS[d]['title']}»")
    own = owner(ctx, key)
    if own == "bank":
        if not ctx.bank_name:
            out.append("Банк не выбран")
        else:
            if ctx.bank_open and not _open(ctx.hour, ctx.bank_open):
                out.append(f"{ctx.bank_name} на стойке работает {span_text(ctx.bank_open)}")
            if "bank" not in ctx.consents:
                out.append("Нет согласия клиента на передачу данных банку")
            elif not ctx.sent:
                out.append("Обращение не передано в банк")
    if own == "notary":
        if "notary" not in ctx.consents:
            out.append("Нет согласия на передачу скана бюро переводов")
        if not _open(ctx.hour, ctx.notary_open):
            out.append(f"Нотариус работает {ctx.notary_open[0]}:00–{ctx.notary_open[1]}:00")
    if own == "insurer" and "insurer" not in ctx.consents:
        out.append("Нет согласия на передачу данных страховщику")
    if key == "dms" and ctx.pay.get("dms") == "bankcard" and ctx.card_state != "active":
        out.append("Оплата выбрана новой картой, а карта ещё не активна — смените способ оплаты")
    if key == "sim" and ctx.pay.get("sim") == "bankcard" and ctx.card_state != "active":
        out.append("Первый месяц SIM выбран новой картой, а карта ещё не активна")
    if not ctx.online and own == "bank":
        out.append("Нет связи — обращение сохранено как черновик")
    return out


def warnings(ctx: Ctx) -> list[dict]:
    s = steps_for(ctx)
    w: list[dict] = []
    if "card" in s and transl_rule(ctx, "card") == "unknown":
        w.append({"level": "md", "title": "Требование к переводу для карты уточняется",
                  "text": "Банк не подтвердил правило для этого гражданства письменно. Карта запускается, но банк может потребовать перевод."})
    for svc in bank_services(ctx.pkg):
        if transl_rule(ctx, svc) == "final" and ctx.status.get("transl") != "done":
            w.append({"level": "md", "title": f"{STEPS[svc]['title']}: перевод нужен до выдачи",
                      "text": "Банк начнёт оформление, но выдаст результат только после нотариального перевода."})
    for svc, label in (("sim", "первого месяца SIM"), ("dms", "ДМС")):
        if svc in s and ctx.pay.get(svc) == "bankcard" and "card" not in s:
            w.append({"level": "md", "title": f"Оплата {label} новой картой невозможна",
                      "text": "Карты в пакете нет. Выберите другой способ оплаты."})
    if ctx.opts.get("patent_hint") and not ctx.opts.get("dms"):
        w.append({"level": "lo", "title": "Цель — работа, ДМС не выбран",
                  "text": "Для патента нужен ДМС, договор платных медуслуг или ОМС."})
    return w


def plan(ctx: Ctx) -> str | None:
    closed = []
    if ctx.bank_name and ctx.bank_open and not _open(ctx.hour, ctx.bank_open):
        closed.append(f"{ctx.bank_name} — с {ctx.bank_open[0]}:00")
    s = steps_for(ctx)
    if "transl" in s and ctx.status.get("transl") != "done" and not _open(ctx.hour, ctx.notary_open):
        closed.append(f"нотариус — с {ctx.notary_open[0]}:00")
    if not closed:
        return None
    return "Сегодня полный пакет за один визит не оформить. Повторный визит: " + "; ".join(closed) + "."
