"""Разведка kad.arbitr.ru из настоящего окна браузера (WebKit) на Mac в GitHub Actions.

Сохраняет в diag/ ответы сайта: поиск, карточку дела и список документов —
по ним пишется разбор для работы без платного API.
"""

import json
import os
import re
import time

import webview

OUT = "diag"
os.makedirs(OUT, exist_ok=True)
report = {}

FETCH_JS = """(function () {
  window.__r = null;
  fetch(%s, %s).then(function (r) {
    return r.text().then(function (t) { window.__r = JSON.stringify({status: r.status, text: t.slice(0, 400000)}); });
  }).catch(function (e) { window.__r = JSON.stringify({error: String(e)}); });
  return 1;
})()"""

HEADERS = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "x-date-format": "iso",
           "Accept": "application/json, text/javascript, */*"}


def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1))


def fetch(win, url, opts=None):
    win.evaluate_js(FETCH_JS % (json.dumps(url), json.dumps(opts or {})))
    for _ in range(90):
        time.sleep(1)
        r = win.evaluate_js("window.__r")
        if r:
            return json.loads(r)
    return {"error": "timeout"}


def snapshot(win, prefix):
    report[prefix] = {
        "url": win.evaluate_js("location.href"),
        "title": win.evaluate_js("document.title"),
        # только имена cookies, без значений
        "cookie_names": win.evaluate_js("document.cookie.split(';').map(function (c) { return c.split('=')[0].trim(); })"),
        "text": win.evaluate_js("document.body ? document.body.innerText.slice(0, 1500) : ''"),
    }
    save(prefix + ".html", win.evaluate_js("document.documentElement.outerHTML") or "")


def search(win, name, body):
    full = {"Page": 1, "Count": 25, "Courts": [], "DateFrom": None, "DateTo": None, "Sides": [], "Judges": [],
            "CaseNumbers": [], "WithVKSInstances": False, **body}
    r = fetch(win, "/Kad/SearchInstances", {"method": "POST", "headers": HEADERS, "body": json.dumps(full)})
    report[name] = {"status": r.get("status"), "error": r.get("error"), "len": len(r.get("text") or "")}
    save(name + ".html", r.get("text") or json.dumps(r))
    return r.get("text") or ""


def run(win):
    try:
        time.sleep(25)  # проверка «я не робот» и установка cookies
        snapshot(win, "home")

        res = search(win, "search_side", {"Sides": [{"Name": "7707083893", "Type": -1, "ExactMatch": False}]})
        res_b = search(win, "search_bankrupt", {"Sides": [{"Name": "7707083893", "Type": -1, "ExactMatch": False}],
                                                "CaseType": "B"})
        cards = re.findall(r"/Card/([0-9a-f-]{36})", res_b + res)
        nums = re.findall(r"[АA]\d{1,3}-\d+/\d{4}", res_b + res)
        report["found_cards"] = cards[:5]
        report["found_numbers"] = nums[:5]
        if nums:
            search(win, "search_number", {"CaseNumbers": [nums[0]]})
        if cards:
            card = cards[0]
            win.load_url("https://kad.arbitr.ru/Card/" + card)
            time.sleep(20)
            snapshot(win, "card")
            html = win.evaluate_js("document.documentElement.outerHTML") or ""
            ids = [i for i in dict.fromkeys(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", html))
                   if i != card]
            report["card_guids"] = ids[:20]
            for n, iid in enumerate(ids[:8]):
                url = (f"/Kad/InstanceDocumentsPage?_={int(time.time() * 1000)}&id={iid}&caseId={card}"
                       f"&withProtocols=true&perPage=30&page=1")
                r = fetch(win, url, {"headers": HEADERS})
                ok = '"Success":true' in (r.get("text") or "")
                report.setdefault("documents", []).append({"id": iid, "status": r.get("status"), "ok": ok})
                if ok:
                    save("documents.json", r["text"])
                    break
    except Exception as e:  # noqa: BLE001
        report["exception"] = repr(e)
    finally:
        save("kad_report.json", report)
        home = report.get("home") or {}
        summary = {
            "home_title": home.get("title"), "home_url": home.get("url"),
            "home_text": re.sub(r"\s+", " ", home.get("text") or "")[:200],
            "cookie_names": home.get("cookie_names"),
            "search_side": report.get("search_side"), "search_bankrupt": report.get("search_bankrupt"),
            "search_number": report.get("search_number"),
            "cards": len(report.get("found_cards") or []), "card_title": (report.get("card") or {}).get("title"),
            "documents": report.get("documents"), "exception": report.get("exception"),
        }
        for k, v in summary.items():
            print(f"::notice title=kad-{k}::{json.dumps(v, ensure_ascii=False)[:400]}")
        win.destroy()


if __name__ == "__main__":
    w = webview.create_window("kad probe", "https://kad.arbitr.ru/", width=1280, height=900)
    webview.start(run, w)
