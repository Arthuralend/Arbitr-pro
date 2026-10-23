"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STAGES = ["Наблюдение", "Финансовое оздоровление", "Внешнее управление", "Конкурсное производство",
  "Реструктуризация долгов", "Реализация имущества", "Мировое соглашение", "Завершено", "Прекращено"];

const state = { cases: [], tab: "list", tags: new Set(), openId: null };

// ------------------------------------------------------------ утилиты

async function api(path, opts = {}) {
  const res = await fetch("/api/" + path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Ошибка " + res.status);
  return data;
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), ms);
}

function fmtDate(iso) {
  if (!iso) return "";
  const [d, t] = iso.split("T");
  const [y, m, dd] = d.split("-");
  return `${dd}.${m}.${y}` + (t ? " " + t.slice(0, 5) : "");
}

function relDays(iso) {
  if (!iso) return "";
  const diff = Math.round((new Date(iso.slice(0, 10)) - new Date(new Date().toISOString().slice(0, 10))) / 864e5);
  if (diff === 0) return "сегодня";
  if (diff === 1) return "завтра";
  if (diff > 1) return `через ${diff} дн.`;
  return `${-diff} дн. назад`;
}

function fedresursUrl(inn, name) {
  // ЕФРСБ — сообщения о банкротстве по должнику
  return inn ? `https://fedresurs.ru/search/entity?code=${encodeURIComponent(inn)}`
    : `https://fedresurs.ru/search/entity?name=${encodeURIComponent(name || "")}`;
}

// ------------------------------------------------------------ загрузка и фильтры

async function load() {
  state.cases = await api("cases");
  render();
}

function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  const view = $("input[name=view]:checked").value;
  const type = $("#f-type").value;
  const stage = $("#f-stage").value;
  let list = state.cases.filter((c) => {
    if (view === "active" && c.archived) return false;
    if (view === "archived" && !c.archived) return false;
    if (view === "new" && !c.new_count) return false;
    if (view === "pinned" && !c.pinned) return false;
    if (type && c.type_code !== type) return false;
    if (stage && c.stage !== stage) return false;
    for (const t of state.tags) if (!c.tags.includes(t)) return false;
    if (q) {
      const hay = [c.number, c.title, c.auto_title, c.court, c.notes, c.tags.join(" "), c.debtor?.name, c.debtor?.inn, c.judges.join(" ")]
        .join(" ").toLowerCase();
      if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  });
  const sort = $("#sort").value;
  const key = {
    added: (c) => c.created_at, event: (c) => c.last_event?.date || "",
    hearing: (c) => c.next_hearing?.start || "9999", title: (c) => c.title.toLowerCase(), number: (c) => c.number,
  }[sort];
  const desc = sort === "added" || sort === "event";
  list.sort((a, b) => (b.pinned - a.pinned) || (desc ? key(b).localeCompare(key(a)) : key(a).localeCompare(key(b), "ru")));
  return list;
}

function render() {
  const active = state.cases.filter((c) => !c.archived);
  $("#cnt-all").textContent = active.length || "";
  const newTotal = state.cases.reduce((s, c) => s + c.new_count, 0);
  $("#cnt-new").textContent = newTotal || "";
  document.title = (newTotal ? `(${newTotal}) ` : "") + "Избранные дела КАД";

  // метки
  const tags = [...new Set(state.cases.flatMap((c) => c.tags))].sort((a, b) => a.localeCompare(b, "ru"));
  for (const t of [...state.tags]) if (!tags.includes(t)) state.tags.delete(t);
  $("#f-tags").innerHTML = tags.length ? tags.map((t) => `<span class="tag ${state.tags.has(t) ? "on" : ""}" data-tag="${esc(t)}">${esc(t)}</span>`).join("")
    : '<span class="hint">нет</span>';

  const list = filtered();
  $("#empty").classList.toggle("hidden", state.cases.length > 0);
  $("#list").innerHTML = list.map(itemHtml).join("") ||
    (state.cases.length ? '<p class="hint">Ничего не найдено по фильтрам.</p>' : "");

  if (state.tab === "hearings") renderHearings();
}

function itemHtml(c) {
  const le = c.last_event;
  const nh = c.next_hearing;
  return `<div class="item ${c.new_count ? "has-new" : ""} ${c.archived ? "archived" : ""}" data-id="${c.id}">
    <button class="pin ${c.pinned ? "on" : ""}" data-pin="${c.id}" title="${c.pinned ? "Открепить" : "Закрепить сверху"}">★</button>
    <div>
      <div class="title">${esc(c.title)} ${c.new_count ? `<span class="new-pill">+${c.new_count} новых</span>` : ""}</div>
      <div class="meta">
        <span class="num">${esc(c.number || "—")}</span>
        ${c.court ? `<span>${esc(c.court)}</span>` : ""}
        ${c.stage ? `<span class="stage">${esc(c.stage)}</span>` : ""}
        ${c.finished ? "<span>завершено</span>" : ""}
        ${c.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
      </div>
      ${le ? `<div class="last">${fmtDate(le.date)} · <b>${esc(le.type)}</b> ${esc(le.content || le.info || "")}</div>` : ""}
      ${!c.loaded && !c.last_error ? '<div class="hint">данные ещё не загружены</div>' : ""}
      ${c.last_error ? `<div class="err">Ошибка обновления: ${esc(c.last_error)}</div>` : ""}
    </div>
    <div class="right">
      ${nh ? `<div>Заседание: <b>${fmtDate(nh.start)}</b><br>${relDays(nh.start)}</div>` : ""}
      ${c.last_checked ? `<div>проверено ${fmtDate(c.last_checked)}</div>` : ""}
    </div>
  </div>`;
}

// ------------------------------------------------------------ карточка дела

async function openCase(id) {
  state.openId = id;
  const c = await api("cases/" + id);
  $("#drawer").classList.remove("hidden");
  renderDrawer(c);
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  state.openId = null;
}

function partyList(list) {
  if (!list?.length) return '<span class="hint">—</span>';
  return list.map((p) => `<div class="party">${esc(p.name)}${p.inn ? ` <small>ИНН ${esc(p.inn)}</small>` : ""}${p.address ? `<br><small>${esc(p.address)}</small>` : ""}</div>`).join("");
}

function renderDrawer(c) {
  const d = c.data || {};
  const parts = d.participants || {};
  const stageOpts = ['<option value="">' + (c.stage_auto ? `Авто: ${esc(c.stage_auto)}` : "Авто: не определена") + "</option>"]
    .concat(STAGES.map((s) => `<option ${s === c.stage_manual ? "selected" : ""}>${s}</option>`)).join("");
  const upcoming = c.hearings.filter((h) => h.start >= new Date().toISOString().slice(0, 16));
  $("#drawer-panel").innerHTML = `
    <button class="close-x" data-close title="Закрыть (Esc)">×</button>
    <div class="hint num">${esc(c.number)} ${c.type_title ? "· " + esc(c.type_title) : ""}</div>
    <div class="title-edit">
      <input id="d-title" value="${esc(c.custom_title || c.auto_title)}" placeholder="${esc(c.auto_title || "Название дела")}">
      <button id="d-save-title">Сохранить</button>
    </div>
    <div class="hint">${c.custom_title ? `Своё название. Автоматическое: «${esc(c.auto_title)}» <button class="link" id="d-reset-title">вернуть автоматическое</button>` : "Название взято автоматически из дела — можно изменить"}</div>

    <div class="actions">
      ${c.url ? `<a class="button" href="${esc(c.url)}" target="_blank" rel="noopener">Открыть на kad.arbitr.ru ↗</a>` : ""}
      ${c.debtor ? `<a class="button" href="${esc(fedresursUrl(c.debtor.inn, c.debtor.name))}" target="_blank" rel="noopener">Должник на Федресурсе ↗</a>` : ""}
      <button id="d-copy">Копировать номер</button>
      <button id="d-refresh">⟳ Обновить</button>
      <button id="d-pin">${c.pinned ? "Открепить" : "★ Закрепить"}</button>
      <button id="d-archive">${c.archived ? "Вернуть из архива" : "В архив"}</button>
      <button id="d-delete" class="danger">Удалить</button>
    </div>
    ${c.last_error ? `<p class="err">Последнее обновление: ${esc(c.last_error)}</p>` : ""}

    <section>
      <h3>Мои пометки</h3>
      <label>Стадия <select id="d-stage">${stageOpts}</select></label>
      <label>Метки через запятую <input id="d-tags" value="${esc(c.tags.join(", "))}"></label>
      <label>Заметки <textarea id="d-notes" rows="4">${esc(c.notes)}</textarea></label>
      <button id="d-save" class="primary">Сохранить пометки</button>
    </section>

    ${c.loaded ? `
    <section>
      <h3>О деле</h3>
      <dl class="kv">
        <dt>Суд</dt><dd>${esc(c.court)}</dd>
        <dt>Судья</dt><dd>${esc(c.judges.join(", ")) || "—"}</dd>
        <dt>Состояние</dt><dd>${esc(c.state) || "—"}</dd>
        <dt>Дата регистрации</dt><dd>${fmtDate(c.start_date)}</dd>
        <dt>Проверено</dt><dd>${fmtDate(c.last_checked)}</dd>
        ${(d.instances || []).filter((i) => i.next_event).map((i) => `<dt>${esc(i.court)}</dt><dd>${esc(i.next_event)}</dd>`).join("")}
      </dl>
    </section>
    <section>
      <h3>Участники</h3>
      <dl class="kv">
        <dt>${c.type_code === "bankruptcy" ? "Заявители" : "Истцы"}</dt><dd>${partyList(parts.plaintiffs)}</dd>
        <dt>${c.type_code === "bankruptcy" ? "Должник" : "Ответчики"}</dt><dd>${partyList(parts.respondents)}</dd>
        ${parts.thirds?.length ? `<dt>Третьи лица</dt><dd>${partyList(parts.thirds)}</dd>` : ""}
        ${parts.others?.length ? `<dt>Иные лица</dt><dd>${partyList(parts.others)}</dd>` : ""}
      </dl>
    </section>
    <section>
      <h3>Заседания</h3>
      ${upcoming.length ? upcoming.map((h) => `<div><b>${fmtDate(h.start)}</b> (${relDays(h.start)}) — ${esc(h.location)}</div>`).join("") : '<span class="hint">Предстоящих заседаний нет</span>'}
    </section>
    <section>
      <div class="row-between"><h3>События (${c.events.length})</h3>${c.new_count ? '<button id="d-seen" class="link">Отметить просмотренными</button>' : ""}</div>
      ${c.events.map((e) => `<div class="ev ${e.is_new ? "is-new" : ""}">
        <div class="d">${fmtDate(e.date)}${e.court ? " · " + esc(e.court) : ""}${e.is_new ? ' · <span class="new-pill">новое</span>' : ""}</div>
        <div class="t">${esc(e.type)}${e.declarer ? ` <span class="hint">— ${esc(e.declarer)}</span>` : ""}</div>
        ${e.content ? `<div>${esc(e.content)}</div>` : ""}
        ${e.info ? `<div class="hint">${esc(e.info)}</div>` : ""}
        ${e.file ? `<a href="${esc(e.file)}" target="_blank" rel="noopener">Документ PDF ↗</a>` : ""}
      </div>`).join("") || '<span class="hint">Событий нет</span>'}
    </section>` : '<section><p class="hint">Данные ещё не загружены. Нажмите «Обновить».</p></section>'}
  `;

  const patch = async (body, msg) => {
    const upd = await api("cases/" + c.id, { method: "PATCH", body });
    await load();
    renderDrawer(upd);
    if (msg) toast(msg);
  };
  $("#d-save-title").onclick = () => {
    const v = $("#d-title").value.trim();
    patch({ custom_title: v === c.auto_title ? "" : v }, "Название сохранено");
  };
  $("#d-title").onkeydown = (e) => { if (e.key === "Enter") $("#d-save-title").click(); };
  if ($("#d-reset-title")) $("#d-reset-title").onclick = () => patch({ custom_title: "" }, "Возвращено автоматическое название");
  $("#d-save").onclick = () => patch({
    stage_manual: $("#d-stage").value, notes: $("#d-notes").value,
    tags: $("#d-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
  }, "Сохранено");
  $("#d-pin").onclick = () => patch({ pinned: c.pinned ? 0 : 1 });
  $("#d-archive").onclick = () => patch({ archived: c.archived ? 0 : 1 }, c.archived ? "Возвращено из архива" : "Перенесено в архив");
  if ($("#d-seen")) $("#d-seen").onclick = () => patch({ mark_seen: true });
  $("#d-copy").onclick = () => navigator.clipboard.writeText(c.number).then(() => toast("Номер скопирован"));
  $("#d-delete").onclick = async () => {
    if (!confirm(`Удалить дело ${c.number} из избранного? Ваши название и заметки будут удалены (останутся в старых резервных копиях).`)) return;
    await api("cases/" + c.id, { method: "DELETE" });
    closeDrawer();
    load();
    toast("Удалено");
  };
  $("#d-refresh").onclick = async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = "Обновляется… (до 2 мин)";
    try {
      const r = await api(`cases/${c.id}/refresh`, { method: "POST" });
      toast(r.new ? `Новых событий: ${r.new}` : "Обновлено, нового нет");
      await load();
      if (state.openId === c.id) renderDrawer(r.case);
    } catch (e) {
      toast(e.message, 6000);
      ev.target.disabled = false;
      ev.target.textContent = "⟳ Обновить";
    }
  };
}

// ------------------------------------------------------------ лента, заседания, поиск, копии

async function renderFeed() {
  const feed = await api("feed");
  if (!feed.length) {
    $("#feed").innerHTML = '<p class="hint">Пока пусто. Изменения появятся здесь после «Обновить все».</p>';
    return;
  }
  let day = "";
  $("#feed").innerHTML = feed.map((f) => {
    const d = f.ts.slice(0, 10);
    const head = d !== day ? `<div class="day">Найдено ${fmtDate(d)}</div>` : "";
    day = d;
    const e = f.event;
    return head + `<div class="feed-item" data-id="${f.case}">
      <div class="when">${esc(f.number)} · ${esc(f.title)}</div>
      <div><b>${fmtDate(e.date)} · ${esc(e.type)}</b> ${esc(e.content || "")}</div>
      ${e.info ? `<div class="hint">${esc(e.info)}</div>` : ""}
      ${e.file ? `<a href="${esc(e.file)}" target="_blank" rel="noopener">PDF ↗</a>` : ""}
    </div>`;
  }).join("");
}

async function renderHearings() {
  const showPast = $("#h-past").checked;
  const nowIso = new Date().toISOString().slice(0, 16);
  const active = state.cases.filter((c) => !c.archived && c.loaded);
  const full = await Promise.all(active.map((c) => api("cases/" + c.id)));
  const rows = full.flatMap((c) => c.hearings.map((h) => ({ ...h, c }))).filter((h) => showPast || h.start >= nowIso)
    .sort((a, b) => showPast ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start));
  $("#hearings").innerHTML = rows.map((h) => `<div class="h-item" data-id="${h.c.id}">
      <div><b>${fmtDate(h.start)}</b> <span class="hint">${relDays(h.start)}</span></div>
      <div>${esc(h.c.number)} — ${esc(h.c.title)}</div>
      <div class="when">${esc(h.location)}</div>
    </div>`).join("") || '<p class="hint">Заседаний нет.</p>';
}

async function renderBackups() {
  const list = await api("backups");
  $("#backups").innerHTML = "<tr><th>Файл</th><th>Дата</th><th>Размер</th><th></th></tr>" + (list.map((b) => `<tr>
    <td>${esc(b.name)}</td><td>${fmtDate(b.mtime)}</td><td>${Math.ceil(b.size / 1024)} КБ</td>
    <td><a href="/api/backups/${encodeURIComponent(b.name)}">скачать</a> · <button class="link" data-restore="${esc(b.name)}">восстановить</button></td>
  </tr>`).join("") || '<tr><td colspan="4" class="hint">Копий пока нет</td></tr>');
}

// ------------------------------------------------------------ фоновое обновление

async function pollJob() {
  const j = await api("job");
  const el = $("#job");
  if (j.running) {
    el.classList.remove("hidden");
    el.textContent = `Обновление дел: ${j.done} из ${j.total}… Новых событий: ${j.new}`;
    $("#btn-refresh-all").disabled = true;
    setTimeout(pollJob, 1500);
    if (j.done !== pollJob.lastDone) { pollJob.lastDone = j.done; load(); }
  } else {
    $("#btn-refresh-all").disabled = false;
    if (pollJob.wasRunning) {
      el.textContent = `Обновление завершено. Новых событий: ${j.new}.` + (j.errors.length ? ` Ошибок: ${j.errors.length} — ${j.errors.slice(0, 3).join("; ")}` : "");
      setTimeout(() => el.classList.add("hidden"), 10000);
      load();
      if (state.tab === "feed") renderFeed();
    }
  }
  pollJob.wasRunning = j.running;
}

// ------------------------------------------------------------ события интерфейса

function setTab(tab) {
  state.tab = tab;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + tab));
  if (tab === "feed") renderFeed();
  if (tab === "hearings") renderHearings();
  if (tab === "backup") renderBackups();
}

function init() {
  $("#f-stage").innerHTML += STAGES.map((s) => `<option>${s}</option>`).join("");
  $$(".tabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
  ["#q", "#f-type", "#f-stage", "#sort"].forEach((s) => $(s).addEventListener("input", render));
  $$("input[name=view]").forEach((r) => (r.onchange = render));
  $("#h-past").onchange = renderHearings;

  $("#f-tags").onclick = (e) => {
    const t = e.target.dataset.tag;
    if (!t) return;
    state.tags.has(t) ? state.tags.delete(t) : state.tags.add(t);
    render();
  };

  document.body.addEventListener("click", async (e) => {
    const pin = e.target.closest("[data-pin]");
    if (pin) {
      e.stopPropagation();
      const c = state.cases.find((x) => x.id === +pin.dataset.pin);
      await api("cases/" + c.id, { method: "PATCH", body: { pinned: c.pinned ? 0 : 1 } });
      return load();
    }
    if (e.target.closest("[data-close]")) return closeDrawer();
    const restore = e.target.closest("[data-restore]");
    if (restore) {
      if (!confirm(`Заменить текущее избранное содержимым копии ${restore.dataset.restore}? Текущее состояние будет предварительно сохранено.`)) return;
      const r = await api(`backups/${encodeURIComponent(restore.dataset.restore)}/restore`, { method: "POST", body: { mode: "replace" } });
      toast(`Восстановлено дел: ${r.added + r.updated}`);
      load();
      return renderBackups();
    }
    const item = e.target.closest("[data-id]");
    if (item && !e.target.closest("a")) openCase(+item.dataset.id);
  });

  // добавление
  $("#btn-add").onclick = () => { $("#add-form").reset(); $("#dlg-add").showModal(); };
  $("#dlg-add").onclose = async () => {
    if ($("#dlg-add").returnValue !== "ok") return;
    const f = $("#add-form");
    try {
      const r = await api("cases", { body: {
        text: f.text.value, title: f.title.value, fetch: f.fetch.checked,
        tags: f.tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      } });
      let msg = `Добавлено: ${r.created}`;
      if (r.duplicates) msg += `, уже были в избранном: ${r.duplicates}`;
      if (r.invalid.length) msg += `. Не распознано: ${r.invalid.join(", ")}`;
      toast(msg, 7000);
      await load();
      pollJob();
    } catch (err) { toast(err.message, 7000); }
  };

  // настройки
  $("#btn-settings").onclick = async () => {
    const s = await api("settings");
    const f = $("#settings-form");
    f.provider.value = s.provider;
    for (const p of ["api-cloud", "api-assist"]) {
      f["token_" + p].value = "";
      f["token_" + p].placeholder = s.has_token[p] ? "сохранён — оставьте пустым, чтобы не менять" : "не задан";
    }
    $("#dlg-settings").showModal();
  };
  $("#dlg-settings").onclose = async () => {
    if ($("#dlg-settings").returnValue !== "ok") return;
    const f = $("#settings-form");
    const body = { provider: f.provider.value };
    for (const p of ["api-cloud", "api-assist"]) if (f["token_" + p].value.trim()) body["token_" + p] = f["token_" + p].value.trim();
    await api("settings", { body });
    toast("Настройки сохранены");
  };

  $("#btn-refresh-all").onclick = async () => {
    const n = state.cases.filter((c) => !c.archived).length;
    if (!n) return toast("Нет дел для обновления");
    if (n > 20 && !confirm(`Будет отправлено ${n} запросов (каждый платный у поставщика API). Продолжить?`)) return;
    try { await api("refresh-all", { body: {} }); pollJob(); } catch (e) { toast(e.message); }
  };
  $("#btn-seen-all").onclick = async () => { await api("mark-all-seen", { body: {} }); load(); };

  // поиск по участнику
  $("#search-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    $("#search-res").innerHTML = '<p class="hint">Ищу… (может занять до 2 минут)</p>';
    try {
      const res = await api("search", { body: { participant: f.participant.value, case_type: f.case_type.value } });
      const have = new Set(state.cases.map((c) => c.number));
      $("#search-res").innerHTML = res.map((r) => `<div class="s-item row-between">
        <div><b class="num">${esc(r.number)}</b> · ${fmtDate(r.date)} · ${esc(r.court)} · ${esc(r.type_title)}<br>
        <span class="hint">${esc(r.plaintiffs.map((p) => p.name).join(", "))} → ${esc(r.respondents.map((p) => p.name).join(", "))}</span></div>
        ${have.has(r.number) ? '<span class="hint">★ в избранном</span>' : `<button data-add-num="${esc(r.number)}">★ В избранное</button>`}
      </div>`).join("") || '<p class="hint">Ничего не найдено.</p>';
    } catch (err) { $("#search-res").innerHTML = `<p class="err">${esc(err.message)}</p>`; }
  };
  $("#search-res").onclick = async (e) => {
    const b = e.target.closest("[data-add-num]");
    if (!b) return;
    b.disabled = true;
    await api("cases", { body: { text: b.dataset.addNum, fetch: true } });
    b.outerHTML = '<span class="hint">★ добавлено</span>';
    await load();
    pollJob();
  };

  // резервные копии
  $("#btn-import").onclick = async () => {
    const file = $("#import-file").files[0];
    if (!file) return toast("Выберите файл");
    const mode = $("input[name=imode]:checked").value;
    if (mode === "replace" && !confirm("Заменить всё текущее избранное содержимым файла?")) return;
    try {
      const backup = JSON.parse(await file.text());
      const r = await api("import", { body: { backup, mode } });
      toast(`Готово: добавлено ${r.added}, обновлено ${r.updated}`);
      load();
      renderBackups();
    } catch (e) { toast("Не удалось восстановить: " + e.message, 6000); }
  };
  $("#btn-backup-now").onclick = async () => { const r = await api("backups", { body: {} }); toast("Сохранено: " + r.name); renderBackups(); };
  $("#btn-copy-numbers").onclick = () => navigator.clipboard.writeText(state.cases.map((c) => c.number).filter(Boolean).join("\n"))
    .then(() => toast("Номера скопированы"));

  // горячие клавиши
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.openId) return closeDrawer();
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) || $("dialog[open]")) return;
    if (e.key === "/") { e.preventDefault(); setTab("list"); $("#q").focus(); }
    if (e.key === "n" || e.key === "т") { e.preventDefault(); $("#btn-add").click(); }
  });

  load();
  pollJob();
}

init();
