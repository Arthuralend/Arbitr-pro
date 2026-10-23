"""Работа с kad.arbitr.ru напрямую — через скрытое окно сайта в приложении (без платного API).

Окно создаёт desktop.py (pywebview). Запросы выполняет static/kad.js внутри страницы сайта,
поэтому их сопровождают те же cookies и проверки, что и в обычном браузере.
"""

import json
import os
import threading
import time
import uuid

import providers

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = "https://kad.arbitr.ru/"

# кнопка «★ В избранное» на страницах дел в окне сайта
STAR_JS = """(function () {
  if (!/\\/Card\\//i.test(location.pathname) || document.getElementById('__kad_star')) return;
  var b = document.createElement('button');
  b.id = '__kad_star';
  b.textContent = '★ В избранное';
  b.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:99999;padding:12px 18px;font:600 15px system-ui;'
    + 'background:#2457d6;color:#fff;border:0;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.3);cursor:pointer';
  b.onclick = function () {
    b.disabled = true;
    window.pywebview.api.add_case(location.href).then(function (msg) { b.textContent = msg; });
  };
  document.body.appendChild(b);
})();"""


def _static(name):
    base = getattr(__import__("sys"), "_MEIPASS", HERE)
    with open(os.path.join(base, "static", name), encoding="utf-8") as f:
        return f.read()


class Bridge:
    """Последовательные вызовы JS в окне kad.arbitr.ru."""

    def __init__(self, window, debug_dir):
        self.win = window
        self.lock = threading.Lock()
        self.debug_dir = debug_dir
        os.makedirs(debug_dir, exist_ok=True)
        self.lib = _static("kad.js")

    def _eval(self, js):
        try:
            return self.win.evaluate_js(js)
        except Exception:  # noqa: BLE001 — страница может перезагружаться
            return None

    def _ensure_ready(self):
        url = self._eval("location.href") or ""
        if "kad.arbitr.ru" not in url:
            self.win.load_url(HOME)
            time.sleep(3)
        for _ in range(60):
            if self._eval("document.readyState") == "complete":
                break
            time.sleep(1)
        if not self._eval("!!window.__kad"):
            self._eval(self.lib)
            time.sleep(0.3)
        if not self._eval("!!window.__kad"):
            raise providers.ProviderError("Окно kad.arbitr.ru не загрузилось. Проверьте интернет и нажмите «Окно kad.arbitr.ru».")

    def call(self, fn, args, timeout=150):
        with self.lock:
            self._ensure_ready()
            rid = uuid.uuid4().hex
            self._eval(f"window.__kad.start({json.dumps(rid)}, {json.dumps(fn)}, {json.dumps(args, ensure_ascii=False)})")
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(0.7)
                r = self._eval(f"window.__kad && window.__kad.take({json.dumps(rid)})")
                if r:
                    break
            else:
                raise providers.ProviderError("kad.arbitr.ru не ответил вовремя, попробуйте ещё раз")
        res = json.loads(r)
        raw = res.pop("raw", "")
        if raw:  # последние ответы сайта — для отчёта разработчику
            with open(os.path.join(self.debug_dir, f"{fn}.{'html' if fn != 'docs' else 'json'}"), "w", encoding="utf-8") as f:
                f.write(raw)
        if not res.get("ok"):
            if res.get("code") == "captcha":
                self.show()
                raise providers.ProviderError("kad.arbitr.ru просит пройти проверку «я не робот». Открылось окно сайта — "
                                              "пройдите проверку и повторите действие.")
            raise providers.ProviderError(res.get("message") or "Ошибка kad.arbitr.ru")
        return res

    def show(self):
        self.win.show()


BRIDGE = None  # устанавливает desktop.py


class KadSite:
    name = "kad"

    def __init__(self, token=None):
        if BRIDGE is None:
            raise providers.ProviderError("Режим «kad.arbitr.ru напрямую» работает только в приложении (Arbitr-pro.app). "
                                          "В браузерной версии выберите API или демо в Настройках.")
        self.bridge = BRIDGE

    def search(self, participant, case_type="B", page=1):
        rows = self.bridge.call("search", {"participant": participant, "page": page})["rows"]
        if case_type == "B":
            rows = [r for r in rows if r["type_code"] in ("bankruptcy", "")]
        for r in rows:
            r["type_title"] = providers.TYPE_TITLES.get(r["type_code"], "")
        return rows

    def case_info(self, number=None, case_id=None):
        row = {}
        if number:
            rows = self.bridge.call("search", {"number": number})["rows"]
            exact = [r for r in rows if r["number"].replace("A", "А") == number]
            if not (exact or rows):
                raise providers.NotFound("Дело не найдено на kad.arbitr.ru")
            row = (exact or rows)[0]
            case_id = row["case_id"]
        data = self.bridge.call("card", {**row, "case_id": case_id, "number": number or ""})["data"]
        # если карточка разобралась не полностью — дополняем из строки поиска
        if not any(data["participants"].values()) and row:
            data["participants"]["plaintiffs"] = row.get("plaintiffs", [])
            data["participants"]["respondents"] = row.get("respondents", [])
        data["number"] = data["number"] or row.get("number") or number or ""
        data["type_code"] = data["type_code"] or row.get("type_code", "")
        data["type_title"] = providers.TYPE_TITLES.get(data["type_code"], "")
        data["court"] = data["court"] or row.get("court", "")
        if not data["judges"] and row.get("judge"):
            data["judges"] = [row["judge"]]
        for inst in data["instances"]:
            for e in inst["events"]:
                e["key"] = e.get("key") or providers.event_key(e)
        return data


providers.PROVIDERS["kad"] = KadSite
