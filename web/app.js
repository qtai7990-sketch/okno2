/* Единое окно — интерфейс стойки. Демонстрационный прототип v0.2.
 * В Android-приложении все запросы к серверу идут через нативный мост: ключ планшета хранится в Android Keystore
 * и в JavaScript не попадает. В браузере (только для проверки) ключ живёт в памяти вкладки до перезагрузки.
 * Все решения о маршруте принимает сервер; интерфейс только показывает и отправляет действия. */
(function () {
  "use strict";

  /* ================= мост к планшету ================= */
  const N = window.SimpadNative || null;
  const pending = {};
  function newId() { return "r" + Date.now() + Math.random().toString(36).slice(2, 8); }
  function waitNative(id, ms, what) {
    return new Promise((resolve, reject) => {
      pending[id] = { resolve, reject };
      setTimeout(() => { if (pending[id]) { delete pending[id]; reject(new Error(what + " не ответил вовремя")); } }, ms);
    });
  }
  function settle(reqId, json) {
    const p = pending[reqId]; if (!p) return; delete pending[reqId];
    try { const r = JSON.parse(json); r.ok ? p.resolve(r.data) : p.reject(new Error(r.error || "Операция не удалась")); }
    catch (e) { p.reject(e); }
  }
  window.__simpadScanResult = settle;
  window.__simpadPhotoResult = settle;
  window.__simpadPrintResult = settle;
  window.__simpadHttp = function (reqId, status, text) {
    const p = pending[reqId]; if (!p) return; delete pending[reqId];
    p.resolve({ status, text });
  };

  function nativeScan(kind) { const id = newId(); const w = waitNative(id, 90000, "Сканер"); N.startScan(kind, id); return w; }
  const isDemoBuild = () => (N ? !!N.isDemo() : true);

  /* Демо-сканер: только для обучения. Рабочий сервер такие данные отклоняет. */
  const DEMO = [
    { surname: "RAKHIMOV", given: "FARRUKH", cyr: "Рахимов Фаррух", country: "TJK", country_name: "Таджикистан", number: "401234567", dob: "1994-03-12", sex: "M", expiry: "2032-03-11", has_ru_text: false },
    { surname: "KARIMOV", given: "JASUR", cyr: "Каримов Жасур", country: "UZB", country_name: "Узбекистан", number: "FA5512907", dob: "1992-01-17", sex: "M", expiry: "2033-01-16", has_ru_text: false },
    { surname: "TOKTOGULOV", given: "EMIL", cyr: "Токтогулов Эмиль", country: "KGZ", country_name: "Кыргызстан", number: "AC7700312", dob: "2000-04-05", sex: "M", expiry: "2030-04-04", has_ru_text: true }
  ];
  async function scan(kind) {
    if (N) return nativeScan(kind);
    const d = new Date();
    if (kind === "passport") return { ...DEMO[Math.floor(Math.random() * DEMO.length)], source: "demo", checks: ["демо‑данные"] };
    if (kind === "migcard") { const to = new Date(d); to.setDate(to.getDate() + 90); return { series: "4626", number: String(1000000 + Math.floor(Math.random() * 8999999)), entry_date: d.toISOString().slice(0, 10), kpp: "Жуковский (аэропорт)", purpose: "Работа", stay_until: to.toISOString().slice(0, 10) }; }
    if (kind === "imei") return { imei: "35" + String(Math.floor(Math.random() * 1e13)).padStart(13, "0") };
    if (kind === "ruid") return { ok: true };
    throw new Error("Неизвестный тип сканирования");
  }

  /* Фото подписанного бумажного бланка: камера планшета (Android) или выбор файла (браузер). */
  function takePhoto() {
    if (N) { const id = newId(); const w = waitNative(id, 180000, "Камера"); N.takePhoto(id); return w; }
    return new Promise((resolve, reject) => {
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = "image/jpeg,image/png,application/pdf"; inp.setAttribute("capture", "environment");
      inp.onchange = () => {
        const f = inp.files && inp.files[0]; if (!f) { reject(new Error("Файл не выбран")); return; }
        const rd = new FileReader();
        rd.onload = () => resolve({ mime: f.type || "image/jpeg", b64: String(rd.result).split(",")[1] });
        rd.onerror = () => reject(new Error("Не удалось прочитать файл"));
        rd.readAsDataURL(f);
      };
      inp.click();
    });
  }

  /* ================= состояние ================= */
  const S = { server: N ? (N.getServer() || "") : "", devKey: null, session: null, me: null, banks: [], notary: null, hour: null,
    cases: [], sel: null, view: null, tab: "cs", texts: [], shift: null, online: true, busy: false, memoLang: null, memo: null,
    lastAction: Date.now() };
  const configured = () => (N ? !!N.isConfigured() : !!(S.server && S.devKey));
  const IDLE_MS = 14 * 60 * 1000; // на минуту раньше сервера — экран не останется открытым с чужой сессией

  /* ================= утилиты ================= */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hhmm = (iso) => { if (!iso) return ""; const d = new Date(iso); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  const OWN = { simpad: "Единое окно", bank: "Банк", insurer: "Страховщик", notary: "Бюро и нотариус", transfer: "Юнистрим" };
  const OWNCLS = { simpad: "o-simpad", bank: "o-bank", insurer: "o-ins", notary: "o-not", transfer: "o-tr" };
  const PAY = { bankcard: "Новая карта банка‑партнёра (когда активна)", mir: "Карта «Мир» клиента на терминале", employer: "Аванс работодателя / предоплата из страны исхода", transfer: "Перевод через Юнистрим" };
  const PRULE = { no: "перевод не нужен", before: "перевод до начала", final: "перевод до выдачи", unknown: "не подтверждено банком" };
  const SVC = { card: "Карта", snils: "СНИЛС и Госуслуги", ebs: "ЕБС", sim: "SIM" };
  const CODES = ["BANK_CASE_ACCEPTED", "CARD_ISSUED", "CARD_ACTIVE", "CARD_NOT_ISSUED", "SNILS_CREATED", "ESIA_CONFIRMED", "SNILS_ERROR", "EBS_REGISTERED", "REVISIT_REQUIRED", "SIM_CONTRACT_SIGNED", "SIM_ISSUED", "SIM_NOT_ISSUED"];
  const REASONS = ["Низкая уверенность распознавания", "Расхождение оригинала и OCR", "Обязательное требование банка", "Написание по нотариальному переводу"];
  const RECIP = { simpad: "Единое окно — сопровождение", bank: "Банк‑партнёр", notary: "Бюро переводов и нотариус", insurer: "Страховщик ДМС" };
  const LANGS = { ru: "русский", tj: "таджикский", uz: "узбекский", ky: "киргизский" };
  const MEMO_LANGS = ["ru", "tj", "uz"];  // памятки на киргизском пока нет — показывается русская
  // Кнопки выдачи талона: подпись на языке клиента, чтобы он сам показал, на каком говорит
  const TICKET_LANGS = [["tj", "Тоҷикӣ", "таджикский"], ["uz", "Oʻzbekcha", "узбекский"], ["ky", "Кыргызча", "киргизский"], ["ru", "Русский", "русский"]];

  class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

  async function rawRequest(method, path, body, extra) {
    const headers = { ...(S.session ? { "X-Session": S.session } : {}), ...(body ? { "Content-Type": "application/json" } : {}), ...(extra || {}) };
    const data = body ? JSON.stringify(body) : "";
    if (N) {
      const id = newId(); const w = waitNative(id, 60000, "Сервер");
      N.http(id, method, path, JSON.stringify(headers), data);
      return w;
    }
    const r = await fetch(S.server.replace(/\/$/, "") + path, { method, headers: { ...headers, "X-Device-Key": S.devKey }, body: body ? data : undefined });
    return { status: r.status, text: await r.text() };
  }
  async function api(method, path, body, extra) {
    if (!configured()) throw new Error("Планшет не настроен");
    let r;
    try { r = await rawRequest(method, path, body, extra); } catch (e) { r = { status: 0, text: "" }; }
    if (r.status === 0) { S.online = false; renderTop(); throw new ApiError("Нет связи с сервером. Ничего не отправлено — повторите, когда связь появится.", 0); }
    S.online = true;
    let data = null; try { data = r.text ? JSON.parse(r.text) : null; } catch (e) { data = { detail: r.text }; }
    if (r.status === 401 && S.session) { logoutLocal("Сессия закончилась — войдите снова"); throw new ApiError("Сессия закончилась", 401); }
    if (r.status < 200 || r.status >= 300) {
      const d = data && data.detail;
      throw new ApiError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : "Ошибка сервера " + r.status, r.status);
    }
    return data;
  }

  function toast(msg, bad) {
    const t = document.createElement("div");
    t.className = "banner toast " + (bad ? "off" : "demo");
    t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), bad ? 6000 : 3000);
  }
  async function act(fn) {
    if (S.busy) return; S.busy = true;
    try { await fn(); } catch (e) { if (e.status !== 401) toast(e.message, true); } finally { S.busy = false; if (S.me) render(); }
  }
  function applyWidths(root) { (root || document).querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; }); }

  /* ================= загрузка данных ================= */
  async function refreshAll() {
    S.me = await api("GET", "/api/v1/me");
    if (S.me.role === "hall") return;  // планшет зала только выдаёт талоны
    if (S.me.role === "reception") { S.q = await api("GET", "/api/v1/queue"); return; }
    const [b, cases, texts, q] = await Promise.all([api("GET", "/api/v1/banks"), api("GET", "/api/v1/cases"), api("GET", "/api/v1/consent-texts"), api("GET", "/api/v1/queue")]);
    S.banks = b.banks; S.notary = b.notary; S.hour = b.hour; S.cases = cases; S.texts = texts; S.q = q;
    if (S.sel) S.view = await api("GET", "/api/v1/cases/" + S.sel);
    S.shift = await api("GET", "/api/v1/reports/shift");
  }
  async function refreshCase() {
    if (!S.sel) return;
    S.view = await api("GET", "/api/v1/cases/" + S.sel);
    S.cases = await api("GET", "/api/v1/cases");
  }
  function setView(v) { S.view = v; S.sel = v.id; }
  function logoutLocal(msg) {
    S.session = null; S.me = null; S.view = null; S.sel = null; S.cases = []; S.memo = null;
    exitClientMode();
    $("modalRoot").innerHTML = "";
    loginScreen(msg);
  }

  /* ================= экраны входа и настройки ================= */
  function setupScreen(err) {
    const fixed = N && N.isServerFixed && N.isServerFixed();
    const managed = N && N.isManaged && N.isManaged();
    $("root").innerHTML = `<div class="setup panel">
      <h2>Настройка планшета</h2>
      <p class="muted">${fixed ? "Рабочая сборка: адрес сервера задан при сборке приложения и на планшете не меняется." : "Адрес сервера и ключ планшета выдаёт администратор системы. При смене адреса сервера обязательно вводится новый ключ планшета."} Ключ хранится в защищённом хранилище планшета и больше нигде не показывается.</p>
      ${fixed ? `<div class="field"><label>Адрес сервера</label><p class="mono">${esc(S.server)}</p></div>`
        : `<div class="field"><label for="cfgS">Адрес сервера</label><input id="cfgS" value="${esc(S.server)}" placeholder="https://okno.example.ru"></div>`}
      ${managed ? `<div class="note g">Ключ планшета задан системой управления планшетами.</div>`
        : `<div class="field"><label for="cfgK">Ключ планшета</label><input id="cfgK" type="password" value="" autocomplete="off" placeholder="${configured() ? "оставьте пустым, чтобы не менять" : ""}"></div>`}
      ${err ? `<div class="note r">${esc(err)}</div>` : ""}
      <div class="row end">${configured() ? `<button class="btn" data-act="toLogin">Отмена</button>` : ""}<button class="btn primary" data-act="saveCfg" ${managed && fixed ? "disabled" : ""}>Сохранить</button></div>
      ${N && N.listPrinters ? `<div class="field"><label>Принтер талонов (Bluetooth)</label>
        <p>${N.printerName && N.printerName() ? esc(N.printerName()) + " · " + (N.printerDots() === 384 ? "58 мм" : "80 мм") : "не выбран"}</p>
        <div class="row">${!N.canPickPrinter || N.canPickPrinter() ? `<button class="btn" data-act="pickPrinter">Выбрать принтер</button>` : ""}${N.hasPrinter && N.hasPrinter() ? `<button class="btn" data-act="testPrint">Пробная печать</button>` : ""}</div>
        <p class="est">${!N.canPickPrinter || N.canPickPrinter() ? "Учебная сборка: сопрягите принтер в настройках Android (Bluetooth) и выберите его здесь." : "Рабочая сборка: принтер задаёт администратор через систему управления планшетами (адрес Bluetooth). Сотрудник смены его не меняет."}</p></div>` : ""}
      <p class="est">${N ? "Приложение " + esc(N.appVersion()) + (N.isDemo() ? " · УЧЕБНАЯ сборка" : "") : "Режим браузера — только для проверки. Сканер работает в демо‑режиме."}</p></div>`;
  }
  function loginScreen(err) {
    $("root").innerHTML = `<div class="setup panel">
      <h2>Вход сотрудника</h2>
      <p class="muted">Код сотрудника и ПИН выдаёт старший смены. Сессия закрывается после 15 минут бездействия.</p>
      <div class="field"><label for="lgC">Код сотрудника</label><input id="lgC" autocomplete="off" autocapitalize="off"></div>
      <div class="field"><label for="lgP">ПИН</label><input id="lgP" type="password" inputmode="numeric" autocomplete="off"></div>
      ${err ? `<div class="note r">${esc(err)}</div>` : ""}
      <div class="row end"><button class="btn" data-act="toSetup">Настройка планшета</button><button class="btn primary" data-act="login">Войти</button></div>
      ${N && N.isKioskReady && !N.isKioskReady() ? `<div class="note r">Планшет не в режиме киоска. Рабочая сборка без киоска не работает — обратитесь к администратору (система управления планшетами).</div>` : ""}
      <p class="est">Сервер: ${esc(S.server || "не задан")}</p></div>`;
    const c = $("lgC"); if (c) c.focus();
  }

  /* ================= основной экран ================= */
  function render() {
    if (!S.me) return;
    if (S.me.role === "hall") { renderHall(); return; }
    if (S.me.role === "reception") { renderReception(); return; }
    const root = $("root");
    if (!root.querySelector(".app")) {
      root.innerHTML = `<div class="bar" id="topbar"></div>
        ${S.me.demo_scan || isDemoBuild() ? `<div class="banner demo">ДЕМОНСТРАЦИОННЫЙ ПРОТОТИП v0.2: тестовые данные, демо‑сканер. Реальные паспорта не вводить.</div>` : ""}
        <div id="netBanner"></div>
        <div class="app"><aside class="panel" id="queuePanel"></aside><section class="panel" id="work"></section><aside class="panel side" id="sidePanel"></aside></div>`;
    }
    renderTop(); renderQueue(); renderWork(); renderSide(); applyWidths();
  }

  function renderTop() {
    const tb = $("topbar"); if (!tb || !S.me) return;
    const chips = S.banks.map((b) => `<span class="chip ${b.open_now ? "on" : "off"}"><i></i>${esc(b.name)}</span>`).join("")
      + (S.notary ? `<span class="chip ${S.notary.open_now ? "on" : "off"}"><i></i>Нотариус</span>` : "");
    const h = S.hour != null ? String(Math.floor(S.hour)).padStart(2, "0") + ":" + String(Math.round((S.hour % 1) * 60)).padStart(2, "0") : "--:--";
    tb.innerHTML = `<div class="brand"><b>Единое окно</b><span>${esc(S.me.stand.name)} · ${esc(S.me.device)} · ${esc(S.me.operator.name)}</span></div>
      <div class="chips">${chips}</div>
      <div class="clock"><span class="eyebrow">Время сервера</span><output>${h}</output>
        <span class="chip ${S.online ? "on" : "off"}"><i></i>${S.online ? "Связь есть" : "Нет связи"}</span>
        <button class="btn small" data-act="logout">Выйти</button></div>`;
    const nb = $("netBanner");
    if (nb) nb.innerHTML = S.online ? "" : `<div class="banner off">Нет связи с сервером. Передача в банк недоступна. Ничего не отправляется автоматически: после восстановления связи сервер перепроверит согласия и дубли.</div>`;
  }

  function renderQueue() {
    const p = $("queuePanel"); if (!p) return;
    p.innerHTML = `${deskQueueBlock()}<div class="qhead"><h3>Обращения</h3><button class="btn small" data-act="newCase">+ Без талона</button></div>
      <div class="qlist">${S.cases.map((c) => `<button class="qitem ${c.id === S.sel ? "sel" : ""}" data-act="open" data-id="${esc(c.id)}">
        <b>${esc(c.name || "Новый клиент")}</b>
        <span class="qmeta"><span>${esc(c.country)} ${c.pkg ? "· " + ({ card: "Только карта", sim: "Только SIM", full: "Полный пакет" })[c.pkg] : ""}</span><span class="mono">${hhmm(c.created_at)}</span></span>
        <span class="bar2"><span data-w="${c.total ? Math.round(c.done / c.total * 100) : 0}"></span></span>
        <span class="qmeta"><span>${c.done} из ${c.total}</span><span class="mono">${esc(c.number)}</span></span>
        ${c.dup_of ? `<span class="qmeta warntext">возможный дубль ${esc(c.dup_of)}</span>` : ""}</button>`).join("") || `<p class="muted">Открытых обращений нет</p>`}</div>`;
  }

  const hasConsent = (v, r) => v.consents.some((c) => c.recipient === r && c.valid);
  function renderWork() {
    const w = $("work"); if (!w) return;
    const v = S.view;
    if (!v) { w.innerHTML = `<div class="empty">Нажмите «Вызвать следующего» — клиент с талоном появится здесь. Без талона — «+ Без талона».</div>`; return; }
    const P = v.passport, M = v.migcard;
    const done = v.steps.filter((s) => s.status === "done").length;
    const tabs = [["cs", "Согласие на обработку", hasConsent(v, "simpad")], ["scan", "Сканирование", !!(P && M)], ["pkg", "Пакет и банк", !!(v.pkg && v.bank_id)],
      ["consent", "Согласия получателям", hasConsent(v, "bank")], ["route", "Маршрут", v.steps.length > 2 && done === v.steps.length], ["memo", "Памятка", false]];
    let h = `<div class="whead"><div><span class="eyebrow">Обращение ${esc(v.number)}${v.ticket ? " · талон " + esc(v.ticket.number) : ""} · с ${hhmm(v.created_at)}</span>
      <h2>${P ? esc(P.cyr || P.surname + " " + P.given) : "Новый клиент"}</h2>
      <p class="muted">${P ? esc(P.country_name) + " · паспорт " + esc(P.number) : "документы не отсканированы"}${M ? " · цель: " + esc(M.purpose) : ""}</p></div>
      <div class="meter"><span>Шагов<br><b>${done}/${v.steps.length}</b></span><span>Ручные правки<br><b class="${v.corrections.length ? "nz" : "zero"}">${v.corrections.length}</b></span>
      <button class="btn small" data-act="closeCase">Закрыть обращение</button></div></div>
      ${v.dup_of ? `<div class="note w"><b>Возможный дубль</b> обращения ${esc(v.dup_of)}: тот же паспорт и дата рождения. Проверьте, не повторный ли визит. Автоматического отказа нет.</div>` : ""}
      <nav class="tabs">${tabs.map((t, i) => `<button class="tab ${S.tab === t[0] ? "act" : ""} ${t[2] ? "done" : ""}" data-act="tab" data-tab="${t[0]}"><span class="n">${t[2] ? "✓" : i + 1}</span>${t[1]}</button>`).join("")}</nav>`;
    h += S.tab === "cs" ? simpadConsentView(v) : S.tab === "scan" ? scanView(v) : S.tab === "pkg" ? pkgView(v) : S.tab === "consent" ? consentView(v) : S.tab === "route" ? routeView(v) : memoView(v);
    w.innerHTML = h;
  }

  function kv(rows) { return `<dl class="kv">${rows.map((r) => `<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd><span class="src ${r[3] || ""}">${esc(r[2])}</span>`).join("")}</dl>`; }

  function consentRow(v, r) {
    const c = v.consents.filter((x) => x.recipient === r && x.valid)[0];
    if (c) return `<div class="check"><span class="grow"><b>${esc(RECIP[r])}: ${esc(c.legal_name)}</b> — подписано (${c.method === "paper" ? "бумажный бланк, фото в деле" : "стилус"}, ${hhmm(c.granted_at)}, объяснено: ${esc(LANGS[c.explained_lang] || c.explained_lang)})<br>
      <span class="est">${esc(c.purpose)}. Данные: ${esc(c.fields.join(", "))}. Текст ${esc(c.text_version)} · ${esc(c.text_sha256.slice(0, 12))}</span></span>
      ${r !== "simpad" && !(r === "bank" && v.sent_at) ? `<button class="btn small bad" data-act="revoke" data-r="${r}">Отозвать</button>` : ""}</div>`;
    const texts = S.texts.filter((t) => t.recipient === r && (r !== "bank" || t.bank_id === v.bank_id));
    if (r === "bank" && !v.bank_id) return `<div class="check"><span class="grow"><b>${esc(RECIP[r])}</b><br><span class="est">Сначала выберите банк: согласие даётся конкретному банку.</span></span></div>`;
    if (!texts.length) return `<div class="check"><span class="grow"><b>${esc(RECIP[r])}</b><br><span class="est">Нет утверждённого текста согласия — обратитесь к администратору.</span></span></div>`;
    return `<div class="check"><span class="grow"><b>${esc(RECIP[r])}</b><br><span class="est">${texts.map((t) => esc(t.legal_name)).join(" / ")}${texts.some((t) => !t.approved) ? " · ЧЕРНОВИК, не утверждён юристом" : ""}</span></span>
      <button class="btn small primary" data-act="consentStart" data-r="${r}">Оформить</button></div>`;
  }
  function simpadConsentView(v) {
    return `<p class="est">Первым — согласие на обработку данных: без него нельзя сканировать документы. Юридический текст — на русском; сотрудник объясняет его клиенту на понятном языке и отмечает язык объяснения.</p>
      <div class="opts one">${consentRow(v, "simpad")}</div>
      <div class="row end"><button class="btn primary" data-act="tab" data-tab="scan" ${hasConsent(v, "simpad") ? "" : "disabled"}>Дальше: сканирование</button></div>`;
  }
  function consentView(v) {
    return `<p class="est">Отдельное согласие на каждого получателя. Подпись — стилусом на экране или ручкой на бумажном бланке (фото бланка сохраняется в деле зашифрованным). Подпись пальцем не принимается.</p>
      <div class="opts one">${["bank", "notary", "insurer"].map((r) => consentRow(v, r)).join("")}</div>
      <div class="row end"><button class="btn primary" data-act="tab" data-tab="route">Дальше: маршрут</button></div>`;
  }

  function scanView(v) {
    const P = v.passport, M = v.migcard, sent = !!v.sent_at, ok = hasConsent(v, "simpad");
    const srcP = P ? ({ chip: "чип", mrz: "MRZ", ocr: "OCR", demo: "демо" })[P.source] || P.source : "";
    return `${ok ? "" : `<div class="note r">Нет согласия на обработку данных — сканирование недоступно.</div>`}<div class="scanrow">
      <div class="scan ${P ? "ok" : ""}"><h3>Паспорт <span class="auto">ЧИП + MRZ</span></h3>
        ${P ? kv([["Фамилия, имя", P.surname + " " + P.given, srcP], ["Кириллицей", P.cyr, "авто", "rule"], ["Гражданство", P.country_name + " (" + P.country + ")", srcP], ["Номер", P.number, srcP], ["Дата рождения", P.dob, srcP], ["Действует до", P.expiry, srcP], ["Русский текст в паспорте", P.has_ru_text ? "есть" : "нет", "справочник", "rule"]])
          + `<div class="checks">${(P.checks || []).map((x) => `<span>✓ ${esc(x)}</span>`).join("")}</div><p class="est">Внутренний риск‑индикатор. Не заключение МВД о подлинности.</p>`
          : `<p class="muted">Положите паспорт разворотом с фото, затем приложите к задней панели планшета для чтения чипа.</p>`}
        <button class="btn ${P ? "" : "primary"}" data-act="scanPassport" ${sent || !ok ? "disabled" : ""}>${P ? "Пересканировать" : "Сканировать паспорт"}</button></div>
      <div class="scan ${M ? "ok" : ""}"><h3>Миграционная карта <span class="auto">OCR</span></h3>
        ${M ? kv([["Серия, номер", M.series + " " + M.number, "ocr", "ocr"], ["Дата въезда", M.entry_date, "ocr", "ocr"], ["КПП", M.kpp, "ocr", "ocr"], ["Цель визита", M.purpose, "ocr", "ocr"], ["Срок пребывания", M.stay_until, "ocr", "ocr"]])
          + `<p class="est">Рукописные поля распознаются с ошибками — сверьте с бланком.</p>` : `<p class="muted">После паспорта — миграционная карта.</p>`}
        <button class="btn ${M || !P ? "" : "primary"}" data-act="scanMig" ${P && !sent && ok ? "" : "disabled"}>${M ? "Пересканировать" : "Сканировать миграционную карту"}</button></div>
    </div>
    <div class="scanrow"><div class="scan ${v.ruid ? "ok" : ""}"><h3>RuID <span class="auto">СПРАВОЧНО</span></h3>
      <p class="est">RuID — добровольная процедура. Отметка только для справки: маршрут, перевод и документы от неё не зависят.</p>
      ${v.ruid ? `<p class="sstate dn">Клиент подавал RuID</p>` : `<button class="btn" data-act="scanRuid" ${P && !sent ? "" : "disabled"}>Отметить по QR RuID</button>`}</div></div>
    ${P && !sent ? `<div class="row"><button class="btn small" data-act="fix">Исправить поле</button><span class="est">Только при ошибке распознавания. Правка попадает в журнал с причиной.</span></div>` : ""}
    <div class="row end"><button class="btn primary" data-act="tab" data-tab="pkg" ${P && M ? "" : "disabled"}>Дальше: пакет и банк</button></div>`;
  }

  function pkgView(v) {
    const P = [["card", "Только карта"], ["sim", "Только SIM"], ["full", "Полный пакет"]];
    const locked = !!v.sent_at;
    const dr = v.doc_rules || {};
    return `${v.migcard ? `<div class="note b">Цель по миграционной карте: <b>${esc(v.migcard.purpose)}</b>. ${v.purpose === "patent" ? "Для патента нужен ДМС, договор платных медуслуг или ОМС." : ""}</div>` : ""}
      <h3>Что нужно человеку</h3>
      <div class="pkgs">${P.map((x) => `<button class="pkg ${v.pkg === x[0] ? "sel" : ""}" data-act="pkg" data-v="${x[0]}" ${locked ? "disabled" : ""}><b>${x[1]}</b></button>`).join("")}</div>
      <div class="opts">
        ${[["dms", "Полис ДМС (добровольно)"], ["transfer", "Первый перевод на родину (Юнистрим)"], ["phone", "Android‑смартфон под «Амину»"]].map((o) => `<label class="check"><input type="checkbox" data-opt="${o[0]}" ${v.opts[o[0]] ? "checked" : ""}><span>${o[1]}</span></label>`).join("")}
      </div>
      <h3>Банк — выбирает клиент</h3>
      <div class="banks">${S.banks.map((b) => `<button class="bank ${v.bank_id === b.id ? "sel" : ""}" data-act="bank" data-v="${esc(b.id)}" ${locked ? "disabled" : ""}><b>${esc(b.name)}</b>
        <dl><dt>SIM</dt><dd>${esc(b.sim_brand)}${b.sim_authority_confirmed ? "" : " · полномочия не подтверждены"}</dd><dt>Часы</dt><dd>${b.open_from}:00–${b.open_to}:00 · ${b.open_now ? "открыт" : "закрыт"}</dd><dt>Передача</dt><dd>${b.mode === "api" ? "QR, данные по API" : "номер дела, банк сканирует сам"}</dd></dl></button>`).join("")}</div>
      ${hasConsent(v, "bank") ? "" : v.bank_id ? `<div class="note w">Смена банка отменяет согласие, данное прежнему банку.</div>` : ""}
      ${v.bank_id && v.passport ? `<div class="note ${Object.values(dr).some((x) => x.rule === "unknown") ? "w" : "b"}"><b>Нотариальный перевод по правилам банка:</b><br>
        ${Object.keys(SVC).map((k) => `${SVC[k]} — ${esc(PRULE[(dr[k] || {}).rule || "unknown"])}${dr[k] && dr[k].confirmed && dr[k].version ? " (" + esc(dr[k].version) + ")" : ""}`).join("<br>")}
        ${Object.values(dr).some((x) => x.rule === "unknown") ? "<br>Где правило не подтверждено: для СНИЛС перевод считаем нужным, для карты — предупреждаем." : ""}</div>` : ""}
      <h3>Чем платит клиент</h3>
      <div class="grid2">${["dms", "sim"].map((k) => `<div class="field"><label for="pay-${k}">${k === "dms" ? "ДМС" : "Первый месяц SIM"}</label><select id="pay-${k}" data-pay="${k}">${Object.entries(PAY).map(([m, t]) => `<option value="${m}" ${v.pay[k] === m ? "selected" : ""}>${t}</option>`).join("")}</select></div>`).join("")}</div>
      <p class="est">Новая карта — не гарантированный способ оплаты: банк сам решает, когда она активна. Наличные на стойке не принимаются.</p>
      <div class="row end"><button class="btn primary" data-act="tab" data-tab="consent" ${v.pkg && v.bank_id ? "" : "disabled"}>Дальше: согласия получателям</button></div>`;
  }

  function routeView(v) {
    if (!v.pkg || !v.bank_id) return `<div class="note w">Сначала выберите пакет и банк.</div>`;
    const b = S.banks.find((x) => x.id === v.bank_id) || { name: v.bank_id, mode: "case", open_now: false };
    const canSend = S.online && b.open_now && hasConsent(v, "bank");
    const handoff = v.sent_at
      ? `<div class="note g"><b>${esc(b.name)}: обращение передано</b> в ${hhmm(v.sent_at)} (${v.sent_mode === "api" ? "данные забраны банком по API" : v.sent_mode === "recon" ? "по файлу сверки банка" : "номер дела, банк сканирует оригиналы сам"}).${v.bank_case_id ? " Номер в банке: " + esc(v.bank_case_id) + "." : ""}</div>`
      : b.mode === "api"
        ? `<div class="note w"><b>${esc(b.name)}: передача по API.</b> Проверьте, что уйдёт в банк, и покажите QR сотруднику банка.
            <button class="btn small primary" data-act="preview" ${canSend ? "" : "disabled"}>Проверить и передать</button></div>`
        : `<div class="note w"><b>${esc(b.name)}: режим «номер дела».</b> Персональные данные в банк из системы не уходят — банк сканирует оригиналы своим сканером.
            <button class="btn small primary" data-act="manualHandoff" ${canSend ? "" : "disabled"}>Номер дела передан в банк</button></div>`;
    const steps = v.steps.map((s, i) => {
      let state, cls = s.status;
      const src = s.source === "bank" ? " · подтверждено банком" : s.source === "bank_file" ? " · по файлу сверки банка" : "";
      if (s.status === "done") state = `<p class="sstate dn">Выполнено в ${hhmm(s.updated_at)}${s.key === "card" ? (v.card_state === "active" ? " · активна" : " · активация не подтверждена") : ""}${src}</p>`;
      else if (s.status === "fail") state = `<p class="sstate fl">Отказ${s.note ? ": " + esc(s.note) : ""}${src}</p>`;
      else if (s.blockers.length) { cls += " blocked"; state = `<p class="sstate bl">${s.blockers.map(esc).join(" · ")}</p>`; }
      else if (s.status === "work") state = `<p class="sstate">В работе с ${hhmm(s.updated_at)}${src}</p>`;
      else state = `<p class="sstate">Можно выполнять</p>`;
      let a = "";
      const can = !s.blockers.length;
      if (s.operator_can_mark && s.status !== "done") {
        if (s.key === "imei") a = `<button class="btn small primary" data-act="imei" ${can ? "" : "disabled"}>Сканировать IMEI</button>`;
        else a = `<button class="btn small ok" data-act="mark" data-k="${s.key}" data-s="done" ${can ? "" : "disabled"}>Выполнено</button>
          ${s.status !== "work" ? `<button class="btn small" data-act="mark" data-k="${s.key}" data-s="work" ${can ? "" : "disabled"}>В работе</button>` : ""}
          <button class="btn small bad" data-act="mark" data-k="${s.key}" data-s="fail">Не удалось</button>`;
      } else if (s.owner === "bank") a = `<span class="auto">ТОЛЬКО ОТ БАНКА</span>`;
      return `<div class="step ${cls}"><span class="ix">${s.status === "done" ? "✓" : s.status === "fail" ? "✕" : i + 1}</span>
        <div><h3>${esc(s.title)} <span class="owner ${OWNCLS[s.owner]}">${esc(s.owner === "bank" ? b.name : OWN[s.owner])}</span></h3>${state}</div>
        <div class="sact">${a}</div></div>`;
    }).join("");
    const claims = v.claims && v.claims.length
      ? `<div class="note w"><b>Со слов сотрудника банка (не подтверждено):</b> ${v.claims.map((c) => esc(c.code) + " в " + hhmm(c.at) + (c.note ? " — " + esc(c.note) : "")).join("; ")}. Не учитывается в маршруте, KPI и расчётах с банком.</div>` : "";
    return `${handoff}
      ${v.plan ? `<div class="note r"><b>${esc(v.plan)}</b> Номер обращения: ${esc(v.number)}.</div>` : ""}
      ${v.warnings.map((x) => `<div class="note ${x.level === "hi" ? "r" : x.level === "md" ? "w" : "b"}"><b>${esc(x.title)}.</b> ${esc(x.text)}</div>`).join("")}
      <div class="steps">${steps}</div>
      ${claims}
      <div class="row end">${v.sent_at && v.ticket ? `<button class="btn small primary" data-act="toBank">${v.ticket.station_kind === "bank" ? "Повторить вызов к окну банка" : "Вызвать к окну банка на табло"}</button>` : ""}
        ${v.sent_at ? `<button class="btn small" data-act="claim">Записать слова сотрудника банка</button>` : ""}<button class="btn primary" data-act="tab" data-tab="memo">Памятка клиенту</button></div>`;
  }

  function memoView(v) {
    const m = S.memo;
    return `<div class="row no-print"><span class="eyebrow">Язык</span><div class="lang">${MEMO_LANGS.map((l) => `<button data-act="memoLang" data-l="${l}" class="${memoLangOf(v) === l ? "act" : ""}">${l.toUpperCase()}</button>`).join("")}</div>
      <button class="btn small" data-act="print">Печать</button></div>
      ${v.lang === "ky" && !S.memoLang ? `<div class="note w no-print">Памятки на киргизском пока нет — показана русская.</div>` : ""}
      ${memoLangOf(v) !== "ru" ? `<div class="note w no-print">Перевод памятки черновой — проверить носителем языка до запуска.</div>` : ""}
      ${m && m.case_number === v.number ? `<div class="memo"><h3>${esc(m.title)}</h3><p><b>${esc(m.name)}</b> · обращение ${esc(m.case_number)}</p>
        ${m.sections.map((s) => `<div><span class="eyebrow">${esc(s.title)}</span><ul>${s.items.map((i) => `<li>${esc(i)}</li>`).join("") || "<li>—</li>"}</ul></div>`).join("")}
        <p class="est">${esc(m.executors)}</p></div>` : `<p class="muted">Загружаю памятку…</p>`}`;
  }

  function renderSide() {
    const p = $("sidePanel"); if (!p) return;
    const v = S.view, sh = S.shift;
    p.innerHTML = `<h3>Смена</h3>
      ${sh ? `<div class="kpis">
        <div class="kpi"><b>${sh.cases}</b><span>обращений сегодня</span></div>
        <div class="kpi"><b>${sh.reached_bank_pct}%</b><span>дошли до банка</span></div>
        <div class="kpi"><b>${sh.sim_chain_pct == null ? "—" : sh.sim_chain_pct + "%"}</b><span>SIM выдана (по данным банка)</span></div>
        <div class="kpi"><b>${sh.corrections}</b><span>ручных правок</span></div>
        <div class="kpi"><b>${sh.unverified_claims}</b><span>статусов «со слов», не в KPI</span></div></div>
        <table><thead><tr><th>Банк</th><th class="r">Обр.</th><th class="r">Карт</th><th class="r">SIM</th></tr></thead><tbody>
        ${Object.entries(sh.by_bank).map(([id, r]) => `<tr><td>${esc((S.banks.find((b) => b.id === id) || {}).name || id)}</td><td class="r">${r.cases}</td><td class="r">${r.cards}</td><td class="r">${r.sims}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">пока нет</td></tr>`}
        </tbody></table><p class="est">${esc(sh.basis)}</p>` : `<p class="muted">Загрузка…</p>`}
      <button class="btn small" data-act="refreshShift">Обновить</button>
      ${v ? `<h3>Журнал правок</h3>${v.corrections.length ? `<table><thead><tr><th>Время</th><th>Поле</th><th>Было → стало</th><th>Причина</th></tr></thead><tbody>${v.corrections.map((c) => `<tr><td>${hhmm(c.at)}</td><td>${esc(c.field)}</td><td>${esc(c.old)} → ${esc(c.new)}</td><td>${esc(c.reason)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted small">Правок нет.</p>`}` : ""}`;
  }

  const memoLangOf = (v) => S.memoLang || (MEMO_LANGS.includes(v.lang) ? v.lang : "ru");

  /* ================= электронная очередь ================= */
  const QST = { waiting: "ждёт", called: "вызван", serving: "у стойки", bank: "у банка", issued: "в пути" };
  const mins = (iso) => (iso ? Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)) : 0);
  const langShort = (l) => ({ tj: "ТДЖ", uz: "УЗБ", ky: "КГЗ", ru: "РУС" })[l] || l;

  /* Рабочее место: «Вызвать следующего», вызванный талон и приём талона без приёмной. */
  function deskQueueBlock() {
    const q = S.q || { waiting: [], called: [], on_the_way: 0 };
    const mine = q.called.find((t) => t.station === S.me.station);
    const cur = mine ? `<div class="ticketcard"><span class="eyebrow">Вызван к стойке ${esc(mine.station)} · вызовов: ${mine.call_count}</span>
        <p class="tnum">${esc(mine.number)}</p><p class="est">${esc(LANGS[mine.lang] || mine.lang)}</p>
        <div class="row"><button class="btn small primary" data-act="tStart" data-id="${esc(mine.id)}">Клиент подошёл</button>
        <button class="btn small" data-act="tRecall" data-id="${esc(mine.id)}">Повторить вызов</button>
        <button class="btn small bad" data-act="tNoshow" data-id="${esc(mine.id)}">Не подошёл</button></div></div>` : "";
    return `<div class="qhead"><h3>Очередь</h3><span class="est">${S.me.station ? "стойка " + esc(S.me.station) : "номер стойки не задан"}</span></div>
      <button class="btn primary wide" data-act="callNext" ${q.waiting.length && !mine && S.me.station ? "" : "disabled"}>Вызвать следующего · ждут ${q.waiting.length}</button>
      <p class="est">В пути из зала: ${q.on_the_way}</p>
      ${cur}
      <div class="field"><label for="ckIn">Пришёл с талоном (если нет приёмной)</label>
        <div class="row"><input id="ckIn" class="grow" inputmode="text" autocomplete="off" placeholder="QR сканером или номер А317"><button class="btn small" data-act="checkin">Отметить</button></div></div>`;
  }

  /* Талон на бумаге: рисуется здесь (кириллица, таджикские и киргизские буквы — шрифтами системы),
     в принтер уходит готовая картинка. Персональных данных на талоне нет. */
  function ticketCanvas(t, dots) {
    const W = dots || 576, pad = Math.round(W * 0.05), cw = W - pad * 2;
    const c = document.createElement("canvas"); c.width = W; c.height = 2000;
    const g = c.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, W, c.height); g.fillStyle = "#000"; g.textAlign = "center"; g.textBaseline = "top";
    let y = pad;
    const font = (px, bold) => { g.font = `${bold ? "700 " : ""}${Math.round(px)}px sans-serif`; };
    const wrap = (text, px, bold) => {
      font(px, bold); const words = String(text).split(/\s+/); let line = "";
      const flush = () => { if (line) { g.fillText(line, W / 2, y); y += Math.round(px * 1.3); line = ""; } };
      words.forEach((w) => { const tryL = line ? line + " " + w : w; if (g.measureText(tryL).width > cw && line) { flush(); line = w; } else line = tryL; });
      flush();
    };
    wrap("ЕДИНОЕ ОКНО", W * 0.05, true);
    if (t.print.kind) wrap(t.print.kind, W * 0.032);
    y += Math.round(W * 0.01);
    wrap(t.print.your, W * 0.055);
    font(W * 0.26, true); g.fillText(t.number, W / 2, y); y += Math.round(W * 0.29);
    const q = window.qrcode(0, "M"); q.addData(t.qr); q.make();
    const n = q.getModuleCount(), cell = Math.floor((W * 0.5) / (n + 8)), size = cell * (n + 8), x0 = Math.round((W - size) / 2);
    for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (q.isDark(r, k)) g.fillRect(x0 + (k + 4) * cell, y + (r + 4) * cell, cell, cell);
    y += size + Math.round(W * 0.01);
    wrap(t.print.route, W * 0.048, true);
    y += Math.round(W * 0.015);
    wrap(t.print.valid, W * 0.04);
    const d = new Date(t.issued_at);
    wrap(d.toLocaleDateString("ru-RU") + " " + hhmm(t.issued_at), W * 0.04);
    y += pad * 2;
    const out = document.createElement("canvas"); out.width = W; out.height = y;
    out.getContext("2d").drawImage(c, 0, 0);
    return out;
  }
  async function printAndMark(t) {
    S.printErr = null;
    if (!canPrint()) return;
    try { await printTicket(t); } catch (e) { S.printErr = e.message; return; }
    api("POST", `/api/v1/tickets/${t.id}/printed`).catch(() => { /* отметка для отчёта, печать уже прошла */ });
  }
  const canPrint = () => !!(N && N.printTicket && N.hasPrinter && N.hasPrinter());
  function printTicket(t) {
    if (!canPrint()) return Promise.reject(new Error("Принтер не подключён"));
    const png = ticketCanvas(t, N.printerDots()).toDataURL("image/png").split(",")[1];
    const id = newId(); const w = waitNative(id, 30000, "Принтер"); N.printTicket(id, png); return w;
  }

  /* Планшет зала: одно касание — язык клиента; талон печатается и остаётся на экране (клиент может сфотографировать). */
  function renderHall() {
    const t = S.lastTicket;
    $("root").innerHTML = `<div class="bar"><div class="brand"><b>Единое окно</b><span>Зал · ${esc(S.me.device)} · ${esc(S.me.operator.name)}</span></div>
        <div class="clock"><span class="chip ${S.online ? "on" : "off"}"><i></i>${S.online ? "Связь есть" : "Нет связи"}</span>
        <span class="chip ${canPrint() ? "on" : "off"}"><i></i>${canPrint() ? "Принтер: " + esc(N.printerName()) : "Принтер не подключён"}</span>
        <button class="btn small" data-act="logout">Выйти</button></div></div>
      ${S.me.demo_scan || isDemoBuild() ? `<div class="banner demo">ДЕМОНСТРАЦИОННЫЙ ПРОТОТИП: учебный сервер.</div>` : ""}
      <div class="hall">
        <section class="panel hallgo"><h2>Выдать талон</h2><p class="est">Коснитесь языка клиента — талон выдаётся сразу. Паспорт и телефон не нужны.</p>
          <div class="langgrid">${TICKET_LANGS.map(([l, own, ru]) => `<button class="btn langbtn" data-act="issue" data-l="${l}"><b>${own}</b><span>${ru}</span></button>`).join("")}</div>
          <p class="est">Выдано с этого планшета за вход: <b>${S.issued || 0}</b></p></section>
        <section class="panel hallticket">${t ? `<span class="eyebrow">${esc(t.print.your)}</span><p class="tnum big">${esc(t.number)}</p>
          <div class="qrwrap">${qrSvg(t.qr)}</div><p class="route">${esc(t.print.route)}</p><p class="est">${esc(t.print.valid)} · в очереди сейчас: ${t.ahead}</p>
          <div class="note ${S.printErr ? "w" : "g"}">${S.printErr ? "Талон не напечатан: " + esc(S.printErr) + ". Попросите клиента сфотографировать экран." : canPrint() ? "Талон напечатан." : "Принтера нет — клиент фотографирует экран."}</div>
          ${canPrint() ? `<div class="row end"><button class="btn small" data-act="reprint">Напечатать ещё раз</button></div>` : ""}`
          : `<div class="empty">Здесь появится талон</div>`}</section></div>`;
  }

  /* Приёмная офиса: сканер QR работает как клавиатура — вводит цифры и Enter. */
  function renderReception() {
    const q = S.q || { waiting: [], called: [], serving: [], bank: [], on_the_way: 0 };
    const r = S.ckRes;
    $("root").innerHTML = `<div class="bar"><div class="brand"><b>Единое окно</b><span>Приёмная · ${esc(S.me.device)} · ${esc(S.me.operator.name)}</span></div>
        <div class="clock"><span class="chip ${S.online ? "on" : "off"}"><i></i>${S.online ? "Связь есть" : "Нет связи"}</span><button class="btn small" data-act="logout">Выйти</button></div></div>
      <div class="hall">
        <section class="panel"><h2>Талон клиента</h2>
          <div class="field"><label for="ckIn">Отсканируйте QR талона или введите номер</label><input id="ckIn" class="bigin" autocomplete="off" placeholder="А317"></div>
          <div class="row end"><button class="btn primary" data-act="checkin">В очередь</button></div>
          ${r ? `<div class="note ${r.ok ? "g" : "r"} big">${esc(r.text)}</div>` : ""}
          <h3>Без талона</h3><div class="langgrid small">${TICKET_LANGS.map(([l, own]) => `<button class="btn" data-act="issue" data-l="${l}">${own}</button>`).join("")}</div>
          ${S.lastTicket ? `<p class="est">Последний выданный: <b>${esc(S.lastTicket.number)}</b>${S.printErr ? " · не напечатан: " + esc(S.printErr) : ""}</p>` : ""}</section>
        <section class="panel"><h2>Очередь · ждут ${q.waiting.length} · в пути ${q.on_the_way}</h2>
          <table><thead><tr><th>Талон</th><th>Язык</th><th>Состояние</th><th class="r">Мин.</th></tr></thead><tbody>
          ${[...q.called, ...q.waiting].map((t) => `<tr><td class="mono"><b>${esc(t.number)}</b></td><td>${langShort(t.lang)}</td><td>${esc(QST[t.status] || t.status)}${t.station ? " → " + (t.station_kind === "bank" ? "окно " : "стойка ") + esc(t.station) : ""}</td><td class="r">${mins(t.checked_in_at)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Никого нет</td></tr>`}
          </tbody></table></section></div>`;
    const inp = $("ckIn"); if (inp && !document.getElementById("scrim")) inp.focus();
  }
  async function doCheckin() {
    const inp = $("ckIn"); const code = inp ? inp.value.trim() : ""; if (!code) return;
    try {
      const r = await api("POST", "/api/v1/tickets/checkin", { code });
      S.ckRes = { ok: true, text: r.status === "waiting" ? `${r.number} — в очереди, ${r.position}-й` : `${r.number} — уже вызван${r.station ? " к стойке " + r.station : ""}` };
    } catch (e) { S.ckRes = { ok: false, text: e.message }; }
    S.q = await api("GET", "/api/v1/queue").catch(() => S.q);
    if (inp) inp.value = "";
    if (S.me.role !== "reception") toast(S.ckRes.text, !S.ckRes.ok);
  }

  /* ================= модальные окна ================= */
  function modal(html, bind, wide) {
    const r = $("modalRoot");
    r.innerHTML = `<div class="scrim" id="scrim"><div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div></div>`;
    const close = () => { r.innerHTML = ""; if (S._qrTimer) { clearInterval(S._qrTimer); S._qrTimer = null; } };
    $("scrim").addEventListener("click", (e) => { if (e.target.id === "scrim") close(); });
    bind && bind(close);
  }

  /* Подпись стилусом: принимаются только события пера (pointerType === "pen"). Касания пальцем игнорируются. */
  function signaturePad(canvas, onChange) {
    const ctx = canvas.getContext("2d");
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    ctx.scale(ratio, ratio); ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#0b1b2b";
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, rect.width, rect.height);
    let drawing = false, strokes = 0, pts = 0;
    const pos = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "pen") { onChange(strokes, "Подпись принимается только стилусом"); return; }
      drawing = true; strokes++; try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* синтетические события */ }
      const [x, y] = pos(e); ctx.beginPath(); ctx.moveTo(x, y); e.preventDefault();
    });
    canvas.addEventListener("pointermove", (e) => { if (!drawing || e.pointerType !== "pen") return; const [x, y] = pos(e); ctx.lineTo(x, y); ctx.stroke(); pts++; e.preventDefault(); });
    const end = () => { if (drawing) { drawing = false; onChange(strokes, ""); } };
    canvas.addEventListener("pointerup", end); canvas.addEventListener("pointercancel", end);
    return {
      clear() { ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, rect.width, rect.height); strokes = 0; pts = 0; onChange(0, ""); },
      ok() { return strokes > 0 && pts > 15; },
      png() { return canvas.toDataURL("image/png").split(",")[1]; }
    };
  }

  function consentBody(x) {
    return `<div class="ctext"><p><b>${esc(x.legal_name)}</b>, ИНН ${esc(x.inn)}${x.address ? ", " + esc(x.address) : ""}</p>
      <p><b>Цель:</b> ${esc(x.purpose)}</p><p><b>Данные:</b> ${esc(x.fields.join(", "))}</p><p><b>Срок:</b> ${esc(x.retention)}</p>
      <p class="pre">${esc(x.body)}</p><p class="est">Версия ${esc(x.version)} · контрольная сумма ${esc(x.sha256.slice(0, 16))}…${x.approved ? " · утверждено: " + esc(x.approved_by) : ""}</p>
      ${x.approved ? "" : `<div class="note r">ЧЕРНОВИК — не утверждён юристом. Рабочий сервер его не примет.</div>`}</div>`;
  }

  /* Экран сотрудника: выбор текста, языка объяснения и способа подписи. */
  function consentModal(v, r) {
    const texts = S.texts.filter((t) => t.recipient === r && (r !== "bank" || t.bank_id === v.bank_id));
    const pick = (id) => texts.find((t) => String(t.id) === String(id)) || texts[0];
    let t = texts[0], evidence = null;
    modal(`<h3>Согласие: ${esc(RECIP[r])}</h3>
      ${texts.length > 1 ? `<div class="field"><label for="ctT">Получатель</label><select id="ctT">${texts.map((x) => `<option value="${x.id}">${esc(x.legal_name)}</option>`).join("")}</select></div>` : ""}
      <div id="ctBody">${consentBody(t)}</div>
      <div class="field"><label for="ctL">Язык, на котором текст объяснён клиенту</label><select id="ctL">${Object.entries(LANGS).map(([k, n]) => `<option value="${k}" ${v.lang === k ? "selected" : ""}>${n}</option>`).join("")}</select></div>
      <div class="row"><label class="check"><input type="radio" name="ctM" value="stylus" checked> Стилус на экране</label>
        <label class="check"><input type="radio" name="ctM" value="paper"> Бумажный бланк (фото)</label></div>
      <div id="ctStylus"><p class="est">Планшет переходит в клиентский режим: клиент видит только этот текст и поле подписи. Очередь и данные других клиентов скрыты. Вернуть рабочий экран — только по ПИН сотрудника.</p></div>
      <div id="ctPaper" hidden><div class="row"><button class="btn" id="ctPhoto">Сфотографировать подписанный бланк</button><span class="est" id="ctPh">Фото не сделано</span></div></div>
      <div class="row end"><button class="btn" id="mNo">Отмена</button><button class="btn primary" id="mClient">Передать планшет клиенту</button><button class="btn primary" id="mYes" hidden disabled>Сохранить согласие</button></div>`, (close) => {
      const yes = $("mYes"), cl = $("mClient");
      const method = () => document.querySelector("input[name=ctM]:checked").value;
      const refresh = () => {
        const st = method() === "stylus";
        $("ctStylus").hidden = !st; $("ctPaper").hidden = st; cl.hidden = !st; yes.hidden = st; yes.disabled = !evidence;
      };
      if ($("ctT")) $("ctT").onchange = () => { t = pick($("ctT").value); $("ctBody").innerHTML = consentBody(t); };
      document.querySelectorAll("input[name=ctM]").forEach((x) => { x.onchange = refresh; });
      $("ctPhoto").onclick = async () => {
        try { evidence = await takePhoto(); $("ctPh").textContent = "Фото бланка готово (" + Math.round(evidence.b64.length * 0.75 / 1024) + " КБ). Проверьте, что подпись видна."; }
        catch (e) { toast(e.message, true); }
        refresh();
      };
      $("mNo").onclick = close;
      cl.onclick = () => { const lang = $("ctL").value; close(); clientMode(v, r, t, lang); };
      yes.onclick = () => act(async () => {
        setView(await api("POST", `/api/v1/cases/${v.id}/consents`, { recipient: r, text_id: t.id, method: "paper", explained_lang: $("ctL").value, evidence_mime: evidence.mime, evidence_b64: evidence.b64 }));
        close();
      });
    }, true);
  }

  /* Клиентский режим: на экране только согласие и поле подписи. 90 секунд без действий — экран блокируется.
     Возврат к рабочему экрану — только по ПИН сотрудника (проверяет сервер). */
  const CLIENT_IDLE_MS = 90 * 1000;
  function exitClientMode() {
    S._lockHook = null;
    if (S._clientTimer) { clearTimeout(S._clientTimer); S._clientTimer = null; }
    document.body.classList.remove("client");
    const cr = $("clientRoot"); if (cr) cr.innerHTML = "";
  }
  function clientMode(v, r, t, lang) {
    const cr = $("clientRoot");
    document.body.classList.add("client");
    let evidence = null;
    const arm = () => { S.lastAction = Date.now(); if (S._clientTimer) clearTimeout(S._clientTimer); S._clientTimer = setTimeout(() => lock(), CLIENT_IDLE_MS); };
    cr.innerHTML = `<div class="clientwrap"><h2>Согласие на обработку персональных данных</h2>
      ${consentBody(t)}
      <p class="est">Распишитесь стилусом в рамке. Подпись пальцем не принимается.</p>
      <canvas id="clPad" class="sigpad big"></canvas>
      <div class="row"><span class="est grow" id="clHint"></span><button class="btn" id="clClr">Очистить</button><button class="btn primary" id="clOk" disabled>Подписать</button></div></div>`;
    const pad = signaturePad($("clPad"), (n, msg) => { $("clHint").textContent = msg || (n ? "Подпись есть" : ""); $("clOk").disabled = !pad.ok(); arm(); });
    ["pointerdown", "scroll", "touchstart", "keydown"].forEach((ev) => cr.addEventListener(ev, arm, { passive: true, capture: true }));
    $("clClr").onclick = () => pad.clear();
    $("clOk").onclick = () => { evidence = { mime: "image/png", b64: pad.png() }; lock(); };
    arm();
    S._lockHook = () => { if (!evidence) pad.clear(); lock(); };

    function lock(err) {
      if (S._clientTimer) { clearTimeout(S._clientTimer); S._clientTimer = null; }
      cr.innerHTML = `<div class="clientwrap lockscr"><h2>${evidence ? "Спасибо. Верните планшет сотруднику" : "Экран заблокирован"}</h2>
        <div class="field"><label for="clPin">ПИН сотрудника</label><input id="clPin" type="password" inputmode="numeric" autocomplete="off"></div>
        ${err ? `<div class="note r">${esc(err)}</div>` : ""}
        <div class="row end"><button class="btn primary" id="clBack">Вернуться к работе</button></div></div>`;
      const pin = $("clPin"); pin.focus();
      const go = async () => {
        try { await api("POST", "/api/v1/reauth", { pin: pin.value.trim() }); }
        catch (e) { if (e.status !== 401) lock(e.message); return; }
        exitClientMode();
        if (!evidence) { toast("Подпись не сохранена: клиент не подписал или истекло время. Повторите.", true); render(); return; }
        act(async () => {
          setView(await api("POST", `/api/v1/cases/${v.id}/consents`, { recipient: r, text_id: t.id, method: "stylus", explained_lang: lang, evidence_mime: evidence.mime, evidence_b64: evidence.b64 }));
          toast("Согласие сохранено");
        });
      };
      $("clBack").onclick = go;
      pin.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    }
  }

  /* Блокировка экрана, когда приложение ушло с экрана (вызывает Android): вернуться — только по ПИН. */
  function screenLock(err) {
    const cr = $("clientRoot");
    document.body.classList.add("client");
    $("modalRoot").innerHTML = "";
    cr.innerHTML = `<div class="clientwrap lockscr"><h2>Экран заблокирован</h2>
      <div class="field"><label for="slPin">ПИН сотрудника</label><input id="slPin" type="password" inputmode="numeric" autocomplete="off"></div>
      ${err ? `<div class="note r">${esc(err)}</div>` : ""}
      <div class="row end"><button class="btn primary" id="slBack">Вернуться к работе</button></div></div>`;
    const pin = $("slPin"); pin.focus();
    const go = async () => {
      try { await api("POST", "/api/v1/reauth", { pin: pin.value.trim() }); }
      catch (e) { if (e.status !== 401) screenLock(e.message); return; }
      exitClientMode(); render();
    };
    $("slBack").onclick = go;
    pin.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }
  window.__simpadLock = function () {
    if (!S.session) return;
    if (S._lockHook) { S._lockHook(); return; }
    screenLock();
  };

  function fixModal(v) {
    const fields = [["passport", "cyr", "ФИО кириллицей"], ["passport", "surname", "Фамилия (латиница)"], ["passport", "given", "Имя (латиница)"], ["passport", "number", "Номер паспорта"], ["passport", "dob", "Дата рождения"], ["passport", "expiry", "Срок действия"], ["migcard", "series", "Серия миграционной карты"], ["migcard", "number", "Номер миграционной карты"], ["migcard", "entry_date", "Дата въезда"]]
      .filter((f) => (f[0] === "passport" ? v.passport : v.migcard));
    const cur = (f) => ((f[0] === "passport" ? v.passport : v.migcard) || {})[f[1]] || "";
    modal(`<h3>Исправить поле</h3>
      <div class="field"><label for="fxF">Поле</label><select id="fxF">${fields.map((f, i) => `<option value="${i}">${f[2]}</option>`).join("")}</select></div>
      <div class="field"><label for="fxV">Правильное значение</label><input id="fxV" value="${esc(cur(fields[0]))}"></div>
      <div class="field"><label for="fxR">Причина</label><select id="fxR">${REASONS.map((r) => `<option>${r}</option>`).join("")}</select></div>
      <div class="row end"><button class="btn" id="mNo">Отмена</button><button class="btn primary" id="mYes">Сохранить</button></div>`, (close) => {
      $("fxF").onchange = () => { $("fxV").value = cur(fields[+$("fxF").value]); };
      $("mNo").onclick = close;
      $("mYes").onclick = () => act(async () => {
        const f = fields[+$("fxF").value];
        setView(await api("POST", `/api/v1/cases/${v.id}/corrections`, { section: f[0], field: f[1], value: $("fxV").value.trim(), reason: $("fxR").value }));
        close();
      });
    });
  }
  async function previewModal(v) {
    const pr = await api("GET", `/api/v1/cases/${v.id}/handoff/preview`);
    const rows = Object.entries(pr.fields || {});
    modal(`<h3>Что уйдёт в ${esc(pr.bank.name)}</h3>
      ${rows.length ? `<dl class="kv two">${rows.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${esc(val)}</dd>`).join("")}</dl>` : `<p>Номер обращения <b>${esc(pr.case_number)}</b> и маршрут.</p>`}
      <div class="note b">${esc(pr.note)}</div>
      <div class="row end"><button class="btn" id="mNo">Отмена</button><button class="btn primary" id="mYes">Выпустить QR для банка</button></div>`, (close) => {
      $("mNo").onclick = close;
      $("mYes").onclick = () => act(async () => { const h = await api("POST", `/api/v1/cases/${v.id}/handoff`); close(); qrModal(v, h); });
    });
  }
  function qrSvg(text) {
    const q = window.qrcode(0, "M"); q.addData(text); q.make();
    return q.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
  }
  function qrModal(v, h) {
    const exp = new Date(h.expires_at).getTime();
    modal(`<h3>QR для ${esc(h.bank)}</h3><div class="qrwrap">${qrSvg(h.qr)}</div>
      <p class="est center">Действует до ${hhmm(h.expires_at)} · осталось <b id="qrLeft">—</b></p>
      <div class="note b">В QR только одноразовый код. Паспорта, фото и СНИЛС в нём нет. Сотрудник банка сканирует его своим планшетом.</div>
      <div class="row end"><button class="btn" id="mNo">Закрыть</button></div>`, (close) => {
      $("mNo").onclick = close;
      S._qrTimer = setInterval(async () => {
        const left = Math.max(0, Math.round((exp - Date.now()) / 1000));
        const el = $("qrLeft"); if (el) el.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
        if (left === 0) { close(); toast("Срок QR истёк. Выпустите новый.", true); return; }
        try { const nv = await api("GET", "/api/v1/cases/" + v.id); if (nv.sent_at) { setView(nv); close(); toast("Банк принял обращение"); render(); } } catch (e) { /* покажется в баннере */ }
      }, 3000);
    });
  }
  function manualModal(v) {
    modal(`<h3>Передать номер дела</h3>
      <p class="bignum">${esc(v.number)}</p>
      <p class="est">Назовите или покажите номер сотруднику банка. Банк сканирует оригиналы паспорта и миграционной карты своим сканером. Нажимайте «Передано», только когда сотрудник банка принял номер.</p>
      <div class="row end"><button class="btn" id="mNo">Отмена</button><button class="btn primary" id="mYes">Передано</button></div>`, (close) => {
      $("mNo").onclick = close;
      $("mYes").onclick = () => act(async () => { const r = await api("POST", `/api/v1/cases/${v.id}/handoff/manual`); setView(r.view); close(); });
    });
  }
  function claimModal(v) {
    modal(`<h3>Слова сотрудника банка</h3>
      <p class="est">Запись для памяти смены. Она НЕ меняет маршрут, KPI и расчёты с банком — для этого нужны подписанные события банка или его официальный файл сверки.</p>
      <div class="field"><label for="bsC">Что сказал сотрудник</label><select id="bsC">${CODES.map((c) => `<option>${c}</option>`).join("")}</select></div>
      <div class="field"><label for="bsN">Комментарий</label><input id="bsN" placeholder="например, ФИО сотрудника банка"></div>
      <div class="row end"><button class="btn" id="mNo">Отмена</button><button class="btn primary" id="mYes">Записать</button></div>`, (close) => {
      $("mNo").onclick = close;
      $("mYes").onclick = () => act(async () => { setView(await api("POST", `/api/v1/cases/${v.id}/bank-claims`, { code: $("bsC").value, note: $("bsN").value })); close(); });
    });
  }

  /* ================= действия ================= */
  async function loadMemo() {
    if (!S.view) return;
    S.memo = await api("GET", `/api/v1/cases/${S.view.id}/memo?lang=${memoLangOf(S.view)}`);
  }
  const handlers = {
    saveCfg() {
      const server = $("cfgS") ? $("cfgS").value.trim() : S.server;
      const key = $("cfgK") ? $("cfgK").value.trim() : "";
      if (!/^https:\/\//.test(server) && !(!N && /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/.test(server))) { setupScreen("Адрес сервера должен начинаться с https://"); return; }
      if (N) {
        if (!N.saveConfig(server, key)) { setupScreen("Не сохранено: при смене адреса сервера нужен новый ключ планшета; адрес — только https://"); return; }
        S.server = N.getServer();
      } else {
        if (server !== S.server && !key) { setupScreen("При смене адреса сервера введите новый ключ планшета"); return; }
        if (!key && !S.devKey) { setupScreen("Введите ключ планшета"); return; }
        if (key) S.devKey = key;
        S.server = server;
      }
      loginScreen();
    },
    toSetup() { setupScreen(); },
    pickPrinter() {
      let r; try { r = JSON.parse(N.listPrinters()); } catch (e) { r = { error: "Не удалось получить список устройств" }; }
      if (r.error) { setupScreen(r.error); return; }
      const devs = r.devices || [];
      modal(`<h3>Принтер талонов</h3>
        ${devs.length ? `<div class="field"><label for="prD">Сопряжённое устройство</label><select id="prD">${devs.map((d, i) => `<option value="${i}">${esc(d.name)} · ${esc(d.mac)}</option>`).join("")}</select></div>
        <div class="field"><label for="prW">Ширина бумаги</label><select id="prW"><option value="576">80 мм (MPRINT E300 и подобные)</option><option value="384">58 мм</option></select></div>`
        : `<div class="note w">Нет сопряжённых устройств. Включите принтер, сопрягите его в настройках Android → Bluetooth и повторите.</div>`}
        <div class="row end"><button class="btn" id="mNo">Отмена</button>${devs.length ? `<button class="btn primary" id="mYes">Сохранить</button>` : ""}</div>`, (close) => {
        $("mNo").onclick = close;
        if (devs.length) $("mYes").onclick = () => { const d = devs[+$("prD").value]; const ok = N.setPrinter(d.mac, d.name, +$("prW").value); close(); setupScreen(ok ? "" : "Принтер не сохранён"); };
      });
    },
    async testPrint() {
      const t = { number: "А000", qr: "0000000000000000", issued_at: new Date().toISOString(),
        print: { your: "Пробная печать", route: "Поднимитесь на 2 этаж. Ҷ ҷ Ӯ ӯ Қ қ Ғ ғ Ҳ ҳ Ң ң Ө ө Ү ү", valid: "Проверьте, что все буквы видны." } };
      try { await printTicket(t); toast("Отправлено на принтер"); } catch (e) { toast(e.message, true); }
    },
    toLogin() { loginScreen(); },
    async login() {
      try {
        const r = await api("POST", "/api/v1/login", { code: $("lgC").value.trim(), pin: $("lgP").value.trim() });
        S.session = r.session; S.lastAction = Date.now();
        await refreshAll(); $("root").innerHTML = ""; render();
      } catch (e) { S.session = null; loginScreen(e.status === 401 && /устройств/i.test(e.message) ? e.message + ". Проверьте настройку планшета." : e.message); }
    },
    async logout() { try { await api("POST", "/api/v1/logout"); } catch (e) { /* всё равно выходим */ } logoutLocal(); },
    async newCase() { const v = await api("POST", "/api/v1/cases"); setView(v); S.tab = "cs"; S.memo = null; S.memoLang = null; S.cases = await api("GET", "/api/v1/cases"); S.shift = await api("GET", "/api/v1/reports/shift"); },
    async open(el) {
      S.sel = el.dataset.id; const v = S.view = await api("GET", "/api/v1/cases/" + S.sel);
      S.tab = !hasConsent(v, "simpad") ? "cs" : !v.passport ? "scan" : !v.bank_id ? "pkg" : !hasConsent(v, "bank") ? "consent" : "route";
      S.memo = null; S.memoLang = null;
    },
    async tab(el) { S.tab = el.dataset.tab; if (S.tab === "memo") await loadMemo(); },
    async scanPassport() { const p = await scan("passport"); setView(await api("PUT", `/api/v1/cases/${S.sel}/scan`, { passport: p })); S.cases = await api("GET", "/api/v1/cases"); },
    async scanMig() { const m = await scan("migcard"); setView(await api("PUT", `/api/v1/cases/${S.sel}/scan`, { migcard: m })); },
    async scanRuid() { await scan("ruid"); setView(await api("PATCH", `/api/v1/cases/${S.sel}`, { ruid: true })); },
    fix() { fixModal(S.view); },
    consentStart(el) { consentModal(S.view, el.dataset.r); },
    async revoke(el) { setView(await api("DELETE", `/api/v1/cases/${S.sel}/consents/${el.dataset.r}`)); },
    async pkg(el) { setView(await api("PATCH", `/api/v1/cases/${S.sel}`, { pkg: el.dataset.v })); },
    async bank(el) { setView(await api("PATCH", `/api/v1/cases/${S.sel}`, { bank_id: el.dataset.v })); },
    async preview() { await previewModal(S.view); },
    manualHandoff() { manualModal(S.view); },
    async mark(el) { setView(await api("POST", `/api/v1/cases/${S.sel}/steps/${el.dataset.k}`, { status: el.dataset.s, note: "" })); },
    async imei() { const r = await scan("imei"); setView(await api("POST", `/api/v1/cases/${S.sel}/steps/imei`, { status: "done", imei: r.imei })); },
    claim() { claimModal(S.view); },
    async memoLang(el) { S.memoLang = el.dataset.l; await loadMemo(); },
    print() { if (N) N.print(); else window.print(); },
    async closeCase() { await api("POST", `/api/v1/cases/${S.sel}/close`); S.sel = null; S.view = null; S.cases = await api("GET", "/api/v1/cases"); },
    async refreshShift() { S.shift = await api("GET", "/api/v1/reports/shift"); },
    async issue(el) {
      const lang = el.dataset.l;
      // Ключ повтора: если прошлый запрос не дошёл или ответ потерялся, повторное касание того же языка
      // в течение 2 минут отправляет тот же ключ — сервер вернёт тот же талон, а не выдаст второй.
      const p = S.pendingIssue;
      const key = p && p.lang === lang && Date.now() - p.at < 120000 ? p.key : "t" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
      S.pendingIssue = { lang, key, at: Date.now() };
      const t = await api("POST", "/api/v1/tickets", { lang }, { "Idempotency-Key": key });
      S.pendingIssue = null;
      if (!t.replay) S.issued = (S.issued || 0) + 1;
      S.lastTicket = t; S.printErr = null;
      await printAndMark(t);
      if (S.me.role === "reception") S.q = await api("GET", "/api/v1/queue");
    },
    async reprint() { await printAndMark(S.lastTicket); },
    async checkin() { await doCheckin(); },
    async callNext() { await api("POST", "/api/v1/queue/next"); S.q = await api("GET", "/api/v1/queue"); },
    async tRecall(el) { await api("POST", `/api/v1/tickets/${el.dataset.id}/recall`); toast("Вызов повторён на табло"); },
    async tNoshow(el) { await api("POST", `/api/v1/tickets/${el.dataset.id}/noshow`); S.q = await api("GET", "/api/v1/queue"); },
    async tStart(el) {
      const v = await api("POST", `/api/v1/tickets/${el.dataset.id}/start`);
      setView(v); S.tab = "cs"; S.memo = null; S.memoLang = null;
      [S.cases, S.q] = await Promise.all([api("GET", "/api/v1/cases"), api("GET", "/api/v1/queue")]);
    },
    async toBank() { await api("POST", `/api/v1/cases/${S.sel}/to-bank`); await refreshCase(); toast("Клиент вызван к окну банка"); }
  };
  const DIRECT = new Set(["saveCfg", "toSetup", "toLogin", "login", "fix", "claim", "consentStart", "manualHandoff", "pickPrinter", "testPrint"]);
  document.addEventListener("click", (e) => {
    S.lastAction = Date.now();
    const el = e.target.closest("[data-act]"); if (!el || el.disabled) return;
    const fn = handlers[el.dataset.act]; if (!fn) return;
    if (DIRECT.has(el.dataset.act)) { fn(el); return; }
    act(() => fn(el));
  });
  document.addEventListener("keydown", (e) => {
    S.lastAction = Date.now();
    if (e.key === "Enter" && $("lgP") && document.activeElement === $("lgP")) handlers.login();
    if (e.key === "Enter" && $("ckIn") && document.activeElement === $("ckIn")) { e.preventDefault(); act(doCheckin); }
  });
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.opt) act(async () => setView(await api("PATCH", `/api/v1/cases/${S.sel}`, { opts: { [el.dataset.opt]: el.checked } })));
    if (el.dataset.pay) act(async () => setView(await api("PATCH", `/api/v1/cases/${S.sel}`, { pay: { [el.dataset.pay]: el.value } })));
  });

  /* ================= запуск ================= */
  function boot() {
    if (!configured()) setupScreen(); else loginScreen();
    setInterval(() => {
      if (!S.session) return;
      if (Date.now() - S.lastAction > IDLE_MS) { handlers.logout(); return; }
      if (S.busy || !S.me || document.getElementById("scrim") || document.body.classList.contains("client")) return;
      if (S.me.role === "hall") return;
      // не перерисовываем, пока сотрудник набирает номер талона
      if ($("ckIn") && document.activeElement === $("ckIn") && $("ckIn").value) return;
      const jobs = [api("GET", "/api/v1/queue").then((q) => { S.q = q; })];
      if (S.me.role === "desk") jobs.push(refreshCase());
      Promise.all(jobs).then(render).catch(() => render());
    }, 5000);
    setInterval(() => {
      if (S.busy || !S.me || S.me.role !== "desk") return;
      Promise.all([api("GET", "/api/v1/banks"), api("GET", "/api/v1/reports/shift")])
        .then(([b, sh]) => { S.banks = b.banks; S.notary = b.notary; S.hour = b.hour; S.shift = sh; renderTop(); renderSide(); })
        .catch(() => renderTop());
    }, 30000);
  }
  boot();
})();
