// Встраивается в окно kad.arbitr.ru (приложение). Запросы идут от имени самого сайта,
// с его cookies и проверками, поэтому работают без платного API.
// Вызовы из программы: window.__kad.start(id, "search"|"card", args) и window.__kad.take(id).
(function () {
  if (window.__kad) return;

  const HEADERS = { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "x-date-format": "iso",
    Accept: "application/json, text/javascript, */*; q=0.01" };
  const results = {};
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
  const isoDate = (s) => { const m = /(\d{2})\.(\d{2})\.(\d{4})/.exec(s || ""); return m ? `${m[3]}-${m[2]}-${m[1]}` : ""; };
  const parseHtml = (html) => new DOMParser().parseFromString(html, "text/html");
  const looksBlocked = (status, body) => status === 403 || status === 429 || /captcha|капч|не робот/i.test(body.slice(0, 5000));

  async function request(url, opts) {
    const r = await fetch(url, { credentials: "include", ...opts });
    const body = await r.text();
    if (r.status === 451) throw { code: "geo", status: 451, message: "kad.arbitr.ru не отвечает на запросы из этой сети (код 451). Если включён VPN — выключите его." };
    if (looksBlocked(r.status, body)) throw { code: "captcha", status: r.status, message: "kad.arbitr.ru просит пройти проверку «я не робот»." };
    if (!r.ok) throw { code: "http", status: r.status, message: `kad.arbitr.ru ответил ошибкой ${r.status}`, raw: body.slice(0, 3000) };
    return body;
  }

  // участник из блока вида «Название<br>адрес<br>ИНН: 123»
  function party(el) {
    const roll = el.querySelector(".js-rolloverHtml");
    const full = roll || el;
    const clone = el.cloneNode(true);
    clone.querySelectorAll(".js-rolloverHtml").forEach((n) => n.remove());
    let name = txt(clone);
    const lines = full.innerHTML.split(/<br\s*\/?>/i).map((h) => txt(parseHtml(h).body)).filter(Boolean);
    if (!name) name = lines[0] || "";
    const inn = (/ИНН:?\s*(\d{10,12})/.exec(txt(full)) || [])[1] || "";
    const address = lines.find((l) => l !== name && !/ИНН|ОГРН/.test(l) && /\d{6}|г\.|ул\.|обл/.test(l)) || "";
    return { name, inn, address };
  }

  function typeOf(html) {
    if (/bankrupt|банкрот/i.test(html)) return "bankruptcy";
    if (/administrative|административ/i.test(html)) return "admin";
    if (/civil|гражданск/i.test(html)) return "civil";
    return "";
  }

  // ---------------------------------------------------------------- поиск

  async function search(args) {
    const body = { Page: args.page || 1, Count: 25, Courts: [], DateFrom: null, DateTo: null, Sides: [], Judges: [],
      CaseNumbers: [], WithVKSInstances: false };
    if (args.number) body.CaseNumbers = [args.number];
    if (args.participant) body.Sides = [{ Name: args.participant, Type: -1, ExactMatch: false }];
    const html = await request("/Kad/SearchInstances", { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
    const doc = parseHtml("<table>" + html + "</table>");
    const rows = [];
    doc.querySelectorAll("tr").forEach((tr) => {
      const a = tr.querySelector("a.num_case") || tr.querySelector('a[href*="/Card/"]');
      if (!a) return;
      const guid = (/Card\/([0-9a-f-]{36})/i.exec(a.getAttribute("href")) || [])[1] || "";
      const numTd = tr.querySelector("td.num") || tr.cells[0];
      const courtTd = tr.querySelector("td.court") || tr.cells[1];
      const plTd = tr.querySelector("td.plaintiff") || tr.cells[2];
      const rsTd = tr.querySelector("td.respondent") || tr.cells[3];
      const partsOf = (td) => {
        if (!td) return [];
        let items = [];
        for (const sel of [".js-rollover", ".one", "li"]) {
          items = [...td.querySelectorAll(sel)];
          if (items.length) break;
        }
        const seen = new Set();
        return (items.length ? items : [td]).map(party).filter((p) => {
          const k = p.name + "|" + p.inn;
          if (!p.name || seen.has(k)) return false;
          seen.add(k);
          return true;
        });
      };
      const judge = txt(courtTd && courtTd.querySelector(".judge"));
      const courtEl = courtTd && [...courtTd.querySelectorAll("div, span")].reverse().find((d) => /суд|АС |ФАС|апелляц/i.test(d.getAttribute("title") || d.textContent));
      rows.push({
        case_id: guid.toLowerCase(),
        number: txt(a),
        date: isoDate(txt(numTd)),
        type_code: typeOf(numTd ? numTd.innerHTML : ""),
        court: courtEl ? (courtEl.getAttribute("title") || txt(courtEl)) : "",
        judge,
        plaintiffs: partsOf(plTd),
        respondents: partsOf(rsTd),
      });
    });
    return { rows, raw: html.slice(0, 300000) };
  }

  // ---------------------------------------------------------------- карточка дела

  function parseMsDate(v) {
    const m = /Date\((\d+)/.exec(v || "");
    return m ? new Date(+m[1]).toISOString().slice(0, 10) : isoDate(v) || (/^\d{4}-\d{2}-\d{2}/.test(v || "") ? v.slice(0, 10) : "");
  }

  async function documents(caseId, instId) {
    const items = [];
    for (let page = 1; page <= 10; page++) {
      const url = `/Kad/InstanceDocumentsPage?_=${Date.now()}&id=${instId}&caseId=${caseId}&withProtocols=true&perPage=100&page=${page}`;
      const data = JSON.parse(await request(url, { headers: HEADERS }));
      const res = data.Result || {};
      (res.Items || []).forEach((it) => items.push(it));
      if (!res.Items || items.length >= (res.TotalCount || 0) || !res.Items.length) break;
    }
    return items.map((it) => ({
      key: it.Id || "",
      date: isoDate(it.DisplayDate) || parseMsDate(it.Date),
      publish_date: parseMsDate(it.PublishDate) || isoDate(it.PublishDisplayDate),
      type: it.DocumentTypeName || it.EventTypeName || "",
      content: (it.ContentTypes || []).join("; ") || it.ContentTypeName || "",
      info: (it.AdditionalInfo || "").replace(/^[.\s]+/, ""),
      file: it.FileName ? `https://kad.arbitr.ru/Document/Pdf/${it.CaseId || caseId}/${it.Id}/${encodeURIComponent(it.FileName)}?isAddingAdditionalInfo=False` : "",
      declarer: (it.Declarers || []).map((d) => d.Organization || d.Name || "").filter(Boolean).join(", "),
      declarer_inn: (it.Declarers || []).map((d) => d.Inn || "").filter(Boolean).join(", "),
      claim_sum: it.ClaimSum || 0,
    }));
  }

  async function card(args) {
    const html = await request("/Card/" + args.case_id, {});
    const doc = parseHtml(html);
    const caseId = ((doc.querySelector("#caseId") || {}).value || args.case_id).toLowerCase();
    const number = txt(doc.querySelector(".js-case-header-case_num, .b-case-header-case_num, #caseName")) || args.number || "";
    const header = txt(doc.querySelector(".b-case-header, .js-case-header")) ;
    const groups = { plaintiffs: "td.plaintiffs, td.plaintiff", respondents: "td.defendants, td.respondents, td.respondent",
      thirds: "td.third, td.thirds", others: "td.others, td.other" };
    const participants = {};
    for (const [k, sel] of Object.entries(groups)) {
      participants[k] = [];
      doc.querySelectorAll(sel).forEach((td) => td.querySelectorAll("li").forEach((li) => {
        const p = party(li);
        if (p.name) participants[k].push(p);
      }));
    }
    const instances = [];
    const heads = [...doc.querySelectorAll(".b-chrono-item-header[data-id], [data-id].js-chrono-item-header")];
    for (const h of heads) {
      const id = h.getAttribute("data-id");
      const t = txt(h);
      const courtEl = h.querySelector(".instantion-name, .b-instantion-name");
      const court = courtEl ? txt(courtEl) : ((/((?:\d+\s+)?(?:АС|ФАС|ВС|Арбитражный суд|Верховный Суд|арбитражный апелляционный суд)[^,;]*?)(?=\s+[АA]\d|\s+Следующ|$)/.exec(t) || [])[1] || "");
      let events = [];
      try { events = await documents(caseId, id); } catch (e) { if (e.code === "captcha" || e.code === "geo") throw e; }
      instances.push({
        number: (/[АA]\d{1,3}-\d+\/\d{2,4}(?:-\d+)*/.exec(t) || [])[0] || "",
        level: null,
        name: (/(Первая|Апелляционная|Кассационная|Надзорная)[^,]*?инстанци[яи]/i.exec(t) || [])[0] || "",
        court,
        next_event: (/Следующее заседание:?\s*[^\n]*?\d{2}\.\d{2}\.\d{4},?\s*\d{2}:\d{2}[^А-Я]*/.exec(t) || [])[0] || "",
        events,
      });
    }
    const judge = txt(doc.querySelector(".judge, .b-case-info .judge"));
    return {
      data: {
        case_id: caseId, number, url: "https://kad.arbitr.ru/Card/" + caseId,
        type_code: typeOf(header) || args.type_code || "",
        type_title: "", state: txt(doc.querySelector(".b-case-state, .js-case-state")), finished: /завершен/i.test(txt(doc.querySelector(".b-case-state, .js-case-state"))),
        start_date: args.date || "", court: (instances[instances.length - 1] || {}).court || args.court || "",
        judges: judge ? [judge] : (args.judge ? [args.judge] : []),
        participants, instances, hearings: [],
      },
      raw: html.slice(0, 400000),
    };
  }

  const fns = { search, card };
  window.__kad = {
    start(id, fn, args) {
      results[id] = null;
      fns[fn](args || {}).then((r) => { results[id] = JSON.stringify({ ok: true, ...r }); })
        .catch((e) => { results[id] = JSON.stringify({ ok: false, code: e.code || "js", status: e.status || 0,
          message: e.message || String(e), raw: e.raw || "" }); });
      return 1;
    },
    take(id) {
      const r = results[id];
      if (r) delete results[id];
      return r || null;
    },
  };
})();
