/* Табло офиса «Единого окна»: номер талона → стойка или окно банка, с голосовым объявлением на русском.
 * Открывается в браузере экрана офиса: https://<адрес сервера>/ui/board.html
 * Ключ устройства-табло (роль board) даёт только номера талонов и стоек — персональных данных на табло нет,
 * поэтому ключ хранится в браузере экрана, чтобы табло поднималось само после перезагрузки. */
(function () {
  "use strict";
  const KEY = "okno_board_key";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* без хранения — ввод после каждой перезагрузки */ } },
    clear() { try { localStorage.removeItem(KEY); } catch (e) { /* нет хранилища */ } }
  };
  const S = { key: store.get(), seen: new Map(), first: true, data: null, fresh: null, audio: null, voice: null, err: "", timer: null,
    rep: new Map() };  // автоповтор вызова к стойке: id → { cc, at, n }
  const REPEAT_MS = 40000, REPEAT_MAX = 2;

  function setup(err) {
    $("root").innerHTML = `<div class="setup"><h2>Настройка табло</h2>
      <p>Ключ устройства‑табло выдаёт администратор (при установке сервера — вместе с ключами планшетов).</p>
      <input id="bk" type="password" autocomplete="off" placeholder="Ключ табло">
      ${err ? `<p class="warn">${esc(err)}</p>` : ""}
      <button id="bkSave">Сохранить</button></div>`;
    $("bkSave").onclick = () => { const v = $("bk").value.trim(); if (!v) return; S.key = v; store.set(v); startScreen(); };
  }

  /* Браузер не даёт странице говорить без первого нажатия — поэтому экран запуска. */
  function startScreen() {
    $("root").innerHTML = `<div class="start"><h1>Табло очереди</h1>
      <p>Нажмите один раз в начале смены: браузер разрешит звук и голосовые объявления.</p>
      <button id="go">Включить табло со звуком</button></div>`;
    $("go").onclick = () => {
      try { S.audio = new (window.AudioContext || window.webkitAudioContext)(); S.audio.resume(); } catch (e) { S.audio = null; }
      pickVoice();
      if (window.speechSynthesis) window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
      poll(); S.timer = setInterval(poll, 2000);
    };
  }
  function pickVoice() {
    if (!window.speechSynthesis) return;
    const vs = window.speechSynthesis.getVoices();
    S.voice = vs.find((v) => /^ru/i.test(v.lang)) || null;
  }
  if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = pickVoice;

  function chime() {
    const a = S.audio; if (!a) return;
    [660, 880].forEach((f, i) => {
      const o = a.createOscillator(), g = a.createGain(), t = a.currentTime + i * 0.28;
      o.frequency.value = f; o.connect(g); g.connect(a.destination);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.35, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.26);
      o.start(t); o.stop(t + 0.28);
    });
  }
  const spoken = (num) => num.replace(/^(\D)0*(\d+)$/, "$1 $2");  // А017 → «А 17»
  function phrase(c) {
    return `Талон ${spoken(c.number)}. Пройдите ${c.kind === "bank" ? "к окну банка" : "к стойке"} номер ${c.station}.`;
  }
  /* Фраза на языке клиента — заранее записанный файл без номера: «подойдите к стойке / окну банка, указанному на табло».
     Файлы кладутся в web/voice/: tj-desk.mp3, tj-bank.mp3, uz-…, ky-…. Нет файла — звучит только русская фраза. */
  /* Все объявления идут по одному: сигнал → русская фраза → фраза на языке клиента → пауза → следующее. */
  const Q = []; let busy = false;
  function enqueue(c, text) { Q.push({ c, text }); if (!busy) next(); }
  function next() {
    const job = Q.shift(); if (!job) { busy = false; return; }
    busy = true;
    let finished = false;
    const done = () => { if (!finished) { finished = true; setTimeout(next, 1500); } };
    chime();
    const tail = () => {
      if (!job.c.lang || job.c.lang === "ru") { done(); return; }
      const a = new Audio(`voice/${encodeURIComponent(job.c.lang)}-${job.c.kind === "bank" ? "bank" : "desk"}.mp3`);
      a.onended = done; a.onerror = done;
      a.play().catch(done);  // файла нет или браузер не дал — остаётся русская фраза
      setTimeout(() => { a.pause(); done(); }, 8000);  // страховка: очередь объявлений не зависнет
    };
    if (!window.speechSynthesis || !S.voice) { setTimeout(tail, 900); return; }
    const u = new SpeechSynthesisUtterance(job.text);
    u.voice = S.voice; u.lang = S.voice.lang; u.rate = 0.9;
    let ended = false;
    const fin = () => { if (!ended) { ended = true; setTimeout(tail, 300); } };
    u.onend = fin; u.onerror = fin;
    setTimeout(fin, 12000);  // страховка: если синтезатор не сообщил об окончании
    setTimeout(() => window.speechSynthesis.speak(u), 600);
  }
  function announce(c) {
    const prev = S.rep.get(c.id);
    S.rep.set(c.id, { cc: c.call_count, at: Date.now(), n: prev && prev.cc === c.call_count ? prev.n : 0 });
    enqueue(c, phrase(c));
  }
  /* Не подошёл — повтор через 40 секунд, не больше двух раз; к окну банка — без автоповтора. */
  function autoRepeat(calls) {
    const now = Date.now(), live = new Set(calls.map((c) => c.id));
    [...S.rep.keys()].forEach((id) => { if (!live.has(id)) S.rep.delete(id); });  // ушёл с табло — забываем
    calls.forEach((c) => {
      if (c.kind !== "desk") return;
      const r = S.rep.get(c.id);
      if (!r || r.cc !== c.call_count || r.n >= REPEAT_MAX || now - r.at < REPEAT_MS) return;
      r.n += 1; r.at = now;
      enqueue(c, "Повторный вызов. " + phrase(c));
    });
  }

  async function poll() {
    let r;
    try {
      r = await fetch("/api/v1/board", { headers: { "X-Device-Key": S.key }, cache: "no-store" });
    } catch (e) { S.err = "Нет связи с сервером"; draw(); return; }
    if (r.status === 401 || r.status === 403) { clearInterval(S.timer); store.clear(); setup("Ключ не подходит или устройство отключено"); return; }
    if (!r.ok) { S.err = "Сервер ответил " + r.status; draw(); return; }
    S.err = "";
    S.data = await r.json();
    const fresh = [];
    S.data.calls.slice().reverse().forEach((c) => {  // от старых к новым — объявляем по порядку
      if (S.seen.get(c.id) !== c.call_count) { if (!S.first) fresh.push(c); S.seen.set(c.id, c.call_count); }
    });
    S.first = false;
    fresh.forEach((c) => announce(c));
    if (!fresh.length) autoRepeat(S.data.calls);
    if (fresh.length) S.fresh = { id: fresh[fresh.length - 1].id, n: fresh[fresh.length - 1].call_count };
    draw();
  }

  function where(c) { return `${c.kind === "bank" ? "Окно банка" : "Стойка"} ${esc(c.station)}`; }
  function draw() {
    const d = S.data || { calls: [], waiting: 0 };
    const [top, ...rest] = d.calls;
    const now = new Date();
    const isFresh = top && S.fresh && S.fresh.id === top.id && S.fresh.n === top.call_count;
    $("root").innerHTML = `<div class="top"><h1>Единое окно · вызов</h1>
        <div class="meta">Ожидают: <b>${d.waiting}</b> · <b>${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}</b></div></div>
      ${top ? `<div class="now ${isFresh ? "fresh" : ""}"><div class="num">${esc(top.number)}</div><div class="arrow">→</div>
        <div class="st">${where(top)}<small>${top.kind === "bank" ? "подойдите к окну банка" : "подойдите к стойке"}</small></div></div>
        <div class="list">${rest.map((c) => `<div class="row"><b>${esc(c.number)}</b><span>${where(c)}</span></div>`).join("")}</div>`
        : `<div class="empty">Ожидайте вызова по номеру талона</div>`}
      <div class="foot"><span>${S.err ? esc(S.err) : ""}</span><span>${S.voice ? "" : "Нет русского голоса в системе — объявления только на экране и звуковым сигналом"}</span></div>`;
    if (isFresh) setTimeout(() => { if (S.fresh && S.fresh.id === top.id) { S.fresh = null; } }, 9000);
  }

  if (S.key) startScreen(); else setup();
})();
