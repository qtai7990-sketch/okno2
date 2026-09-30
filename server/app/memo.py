"""Памятка клиенту. Переводы на таджикский, узбекский и киргизский — черновые, проверить носителем языка.

Граждане ЕАЭС (Киргизия, Казахстан, Армения, Беларусь) работают без патента; миграционный учёт — 30 дней
(Договор о ЕАЭС, ст. 97). Для них памятка не говорит о патенте и 7 днях учёта."""
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
           "revisit": "Повторный визит", "exec": "Исполнители",
           "uchet_eaeu": "Миграционный учёт: 30 дней со дня въезда. Уведомление подаёт принимающая сторона (обычно работодатель).",
           "med_eaeu": "Медосвидетельствование и дактилоскопия: 30 дней со дня въезда. Патент гражданам ЕАЭС не нужен.",
           "snils_sfr": "СНИЛС и подтверждение Госуслуг — в Соцфонде или МФЦ, с паспортом и уведомлением.",
           "exec_bank": "карта, ЕБС, SIM", "exec_snils": "СНИЛС, Госуслуги", "exec_sfr": "Соцфонд — СНИЛС", "exec_ins": "страховщик — ДМС", "exec_not": "бюро и нотариус — перевод", "exec_office": "единое окно — сопровождение"},
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
           "revisit": "Ташрифи такрорӣ", "exec": "Иҷрокунандагон",
           "uchet_eaeu": "Бақайдгирии муҳоҷиратӣ: 30 рӯз аз рӯзи воридшавӣ. Огоҳиномаро тарафи қабулкунанда медиҳад.",
           "med_eaeu": "Муоинаи тиббӣ ва дактилоскопия: 30 рӯз аз рӯзи воридшавӣ.",
           "snils_sfr": "СНИЛС ва тасдиқи Госуслуги — дар Фонди иҷтимоӣ ё МФЦ, бо шиноснома ва огоҳинома.",
           "exec_bank": "корт, ЕБС, SIM", "exec_snils": "СНИЛС, Госуслуги", "exec_sfr": "Фонди иҷтимоӣ — СНИЛС", "exec_ins": "суғуртакунанда — ДМС", "exec_not": "бюро ва нотариус — тарҷума", "exec_office": "равзанаи ягона — ҳамроҳӣ"},
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
           "revisit": "Qayta tashrif", "exec": "Ijrochilar",
           "uchet_eaeu": "Migratsion roʻyxat: kirgan kundan 30 kun. Xabarnomani qabul qiluvchi tomon beradi.",
           "med_eaeu": "Tibbiy koʻrik va daktiloskopiya: kirgan kundan 30 kun.",
           "snils_sfr": "SNILS va Gosuslugi tasdigʻi — Ijtimoiy fondda yoki MFCda, pasport va xabarnoma bilan.",
           "exec_bank": "karta, EBS, SIM", "exec_snils": "SNILS, Gosuslugi", "exec_sfr": "Ijtimoiy fond — SNILS", "exec_ins": "sugʻurtachi — DMS", "exec_not": "byuro va notarius — tarjima", "exec_office": "yagona oyna — hamrohlik"},
    "ky": {"h": "Сиздин эскертмеңиз", "works": "Иштеп жатат", "not": "Азырынча иштебейт", "dl": "Сиздин мөөнөттөрүңүз", "next": "Кийинки кадам",
           "card": "Банк картасы берилди. Банк иштетүүнү тастыктагандан кийин төлөсөңүз болот. Мобилдик банк жана которуулар — орус SIM-картасынан кийин.",
           "dms": "ДМС полиси таризделген күндөн тартып күчүндө.", "snils": "СНИЛС жана тастыкталган Госуслуги аккаунту.",
           "ebs": "Бирдиктүү биометрикалык системадагы биометрия.", "sim": "Орус SIM-картасы: чалуу, SMS, интернет.",
           "home": "Мекендеги SIM-карта: чалуулар иштейт; SMS жана интернет капчадан кийин күйгүзүлдү.", "transfer": "Мекенге акча которуу Юнистрим аркылуу.",
           "noSim": "Орус SIM-картасы азырынча жок.", "noCard": "Банк картасы азырынча жок.",
           "uchet": "Миграциялык каттоо: жашаган жерге келген күндөн тартып 7 жумуш күнү. Билдирүүнү кабыл алуучу тарап берет.",
           "med": "Медициналык күбөлөндүрүү: кирген күндөн тартып 30 күн.",
           "pat": "Патентке арыз: кирген күндөн тартып 30 күн, ММЦ. ДМС полиси, акы төлөнүүчү медициналык кызмат келишими же ОМС керек.",
           "amina": "«Амина» тиркемеси — ММЦда чет өлкөлүк жарандын картасын алгандан кийин.",
           "revisit": "Кайра келүү", "exec": "Аткаруучулар",
           "uchet_eaeu": "Миграциялык каттоо: кирген күндөн тартып 30 күн. Билдирүүнү кабыл алуучу тарап (адатта иш берүүчү) берет.",
           "med_eaeu": "Медициналык күбөлөндүрүү жана дактилоскопия: кирген күндөн тартып 30 күн. ЕАЭБ жарандарына патент керек эмес.",
           "snils_sfr": "СНИЛС жана Госуслугини тастыктоо — Социалдык фонддо же МФЦда, паспорт жана билдирүү менен.",
           "exec_bank": "карта, ЕБС, SIM", "exec_snils": "СНИЛС, Госуслуги", "exec_sfr": "Социалдык фонд — СНИЛС", "exec_ins": "камсыздандыруучу — ДМС", "exec_not": "бюро жана нотариус — котормо", "exec_office": "бирдиктүү терезе — коштоо"},
}


EAEU = {"KGZ", "KAZ", "ARM", "BLR"}


def build_memo(case, view: dict, bank_name: str | None, lang: str, snils_channel: str = "bank") -> dict:
    t = T.get(lang, T["ru"])
    st = {s["key"]: s["status"] for s in view["steps"]}
    eaeu = (case.country or "") in EAEU
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
    nxt = []
    if "snils" in st and st["snils"] != "done" and snils_channel == "sfr":
        nxt.append(t["snils_sfr"])
    if eaeu:
        deadlines = [t["uchet_eaeu"]] + ([t["med_eaeu"]] if case.purpose == "patent" else [])
    else:
        deadlines = [t["uchet"]]
        if case.purpose == "patent":
            deadlines += [t["med"], t["pat"]]
            nxt.append(t["amina"])
    p = view.get("passport") or {}
    snils_by = t["exec_sfr"] if snils_channel == "sfr" else ""
    bank_part = t["exec_bank"] if snils_channel == "sfr" else t["exec_snils"] + ", " + t["exec_bank"]
    executors = f"{t['exec']}: {bank_name or '—'} — {bank_part}; " + (f"{snils_by}; " if snils_by else "") + \
        f"{t['exec_ins']}; {t['exec_not']}; {t['exec_office']}."
    return {
        "lang": lang, "title": t["h"], "name": p.get("cyr") or "", "case_number": case.number,
        "sections": [
            {"title": t["works"], "items": works},
            {"title": t["not"], "items": nots},
            {"title": t["dl"], "items": deadlines},
            *([{"title": t["next"], "items": nxt}] if nxt else []),
            *([{"title": t["revisit"], "items": [view["plan"]]}] if view.get("plan") else []),
        ],
        "executors": executors,
    }
