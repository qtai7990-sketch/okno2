"""Памятка клиенту. Переводы на таджикский и узбекский — черновые, проверить носителем языка."""
from __future__ import annotations

T = {
    "ru": {"h": "Ваша памятка", "works": "Уже работает", "not": "Пока не работает", "dl": "Ваши сроки", "next": "Что дальше",
           "card": "Банковская карта выдана. Когда банк подтвердит активацию — можно платить. Мобильный банк и переводы — после российской SIM.",
           "dms": "Полис ДМС действует со дня оформления.", "snils": "СНИЛС и подтверждённая учётная запись Госуслуг.",
           "ebs": "Биометрия в Единой биометрической системе.", "sim": "Российская SIM: звонки, SMS, интернет.",
           "home": "SIM с родины: звонки работают; SMS и интернет включены после капчи.", "transfer": "Перевод на родину через Юнистрим.",
           "noSim": "Российской SIM пока нет.", "noCard": "Банковской карты пока нет.",
           "uchet": "Миграционный учёт: 7 рабочих дней со дня прибытия в место пребывания. Уведомление подаёт принимающая сторона.",
           "med": "Медосвидетельствование: 30 дней со дня въезда.",
           "pat": "Заявление на патент: 30 дней со дня въезда, ММЦ. Нужен полис ДМС, договор платных медуслуг или ОМС.",
           "amina": "Приложение «Амина» — после получения карты иностранного гражданина в ММЦ.",
           "revisit": "Повторный визит", "exec": "Исполнители"},
    "tj": {"h": "Ёддошти шумо", "works": "Аллакай кор мекунад", "not": "Ҳоло кор намекунад", "dl": "Мӯҳлатҳои шумо", "next": "Қадами навбатӣ",
           "card": "Корти бонкӣ дода шуд. Пас аз тасдиқи бонк метавонед пардохт кунед. Бонки мобилӣ ва интиқолҳо — пас аз SIM‑и русӣ.",
           "dms": "Полиси ДМС аз рӯзи расмикунӣ амал мекунад.", "snils": "СНИЛС ва ҳисоби тасдиқшудаи Госуслуги.",
           "ebs": "Биометрия дар Системаи ягонаи биометрӣ.", "sim": "SIM‑и русӣ: занг, SMS, интернет.",
           "home": "SIM‑и ватанӣ: занг кор мекунад; SMS ва интернет пас аз капча фаъол шуданд.", "transfer": "Интиқол ба ватан тавассути Юнистрим.",
           "noSim": "SIM‑и русӣ ҳоло нест.", "noCard": "Корти бонкӣ ҳоло нест.",
           "uchet": "Бақайдгирии муҳоҷиратӣ: 7 рӯзи корӣ аз рӯзи расидан ба ҷои будубош. Огоҳиномаро тарафи қабулкунанда медиҳад.",
           "med": "Муоинаи тиббӣ: 30 рӯз аз рӯзи воридшавӣ.",
           "pat": "Аризаи патент: 30 рӯз аз рӯзи воридшавӣ, ММЦ. Полиси ДМС, шартномаи хизматрасонии тиббӣ ё ОМС лозим аст.",
           "amina": "Барномаи «Амина» — пас аз гирифтани корти шаҳрванди хориҷӣ дар ММЦ.",
           "revisit": "Ташрифи такрорӣ", "exec": "Иҷрокунандагон"},
    "uz": {"h": "Sizning eslatmangiz", "works": "Allaqachon ishlaydi", "not": "Hozircha ishlamaydi", "dl": "Sizning muddatlaringiz", "next": "Keyingi qadam",
           "card": "Bank kartasi berildi. Bank faollashtirishni tasdiqlagach, toʻlash mumkin. Mobil bank va oʻtkazmalar — Rossiya SIM kartasidan keyin.",
           "dms": "DMS polisi rasmiylashtirilgan kundan boshlab amal qiladi.", "snils": "SNILS va tasdiqlangan Gosuslugi hisobi.",
           "ebs": "Yagona biometrik tizimda biometriya.", "sim": "Rossiya SIM kartasi: qoʻngʻiroq, SMS, internet.",
           "home": "Vatan SIM kartasi: qoʻngʻiroqlar ishlaydi; SMS va internet kapchadan keyin yoqildi.", "transfer": "Vatanga pul oʻtkazmasi Unistream orqali.",
           "noSim": "Rossiya SIM kartasi hozircha yoʻq.", "noCard": "Bank kartasi hozircha yoʻq.",
           "uchet": "Migratsion roʻyxat: yashash joyiga kelgan kundan 7 ish kuni. Xabarnomani qabul qiluvchi tomon beradi.",
           "med": "Tibbiy koʻrik: kirgan kundan 30 kun.",
           "pat": "Patent uchun ariza: kirgan kundan 30 kun, MMC. DMS polisi, pullik tibbiy xizmat shartnomasi yoki OMS kerak.",
           "amina": "«Amina» ilovasi — MMCda chet el fuqarosi kartasini olgandan keyin.",
           "revisit": "Qayta tashrif", "exec": "Ijrochilar"},
}


def build_memo(case, view: dict, bank_name: str | None, lang: str) -> dict:
    t = T.get(lang, T["ru"])
    st = {s["key"]: s["status"] for s in view["steps"]}
    works, nots = [], []
    if st.get("wifi") == "done":
        works.append(t["home"])
    works.append(t["card"]) if st.get("card") == "done" else nots.append(t["noCard"])
    for k in ("dms", "snils", "ebs"):
        if st.get(k) == "done":
            works.append(t[k])
    works.append(t["sim"]) if st.get("sim") == "done" else nots.append(t["noSim"])
    if st.get("transfer") == "done":
        works.append(t["transfer"])
    deadlines = [t["uchet"]]
    if case.purpose == "patent":
        deadlines += [t["med"], t["pat"]]
    p = view.get("passport") or {}
    return {
        "lang": lang, "title": t["h"], "name": p.get("cyr") or "", "case_number": case.number,
        "sections": [
            {"title": t["works"], "items": works},
            {"title": t["not"], "items": nots},
            {"title": t["dl"], "items": deadlines},
            *([{"title": t["next"], "items": [t["amina"]]}] if case.purpose == "patent" else []),
            *([{"title": t["revisit"], "items": [view["plan"]]}] if view.get("plan") else []),
        ],
        "executors": f"{t['exec']}: {bank_name or '—'} — карта, СНИЛС, Госуслуги, ЕБС, SIM; страховщик — ДМС; бюро и нотариус — перевод; единое окно — сопровождение.",
    }
