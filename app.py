"""Избранные дела kad.arbitr.ru — локальная программа.

Запуск:  python app.py            (откроется браузер на http://127.0.0.1:8765)
         python app.py --port 9000 --data другая_папка --no-browser
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import core
import providers

HERE = os.path.dirname(os.path.abspath(__file__))
# в собранном приложении (PyInstaller) файлы лежат в sys._MEIPASS
STATIC = os.path.join(getattr(sys, "_MEIPASS", HERE), "static")


class Jobs:
    """Фоновое обновление дел (по одному запросу за раз — сервисы ограничивают число потоков)."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.state = {"running": False, "total": 0, "done": 0, "new": 0, "errors": [], "finished_at": ""}

    def start(self, ids):
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "total": len(ids), "done": 0, "new": 0, "errors": [], "finished_at": ""}
        threading.Thread(target=self._run, args=(ids,), daemon=True).start()
        return True

    def _run(self, ids):
        try:
            provider = providers.get_provider(self.store.settings())
        except providers.ProviderError as e:
            with self.lock:
                self.state.update(running=False, errors=[str(e)], finished_at=core.now())
            return
        for cid in ids:
            try:
                n = self.store.refresh(cid, provider)
                with self.lock:
                    self.state["new"] += n
            except Exception as e:  # noqa: BLE001 — ошибка одного дела не должна останавливать остальные
                c = self.store.get(cid, full=False)
                with self.lock:
                    self.state["errors"].append(f"{c['number'] if c else cid}: {e}")
            with self.lock:
                self.state["done"] += 1
        self.store.auto_backup()
        with self.lock:
            self.state.update(running=False, finished_at=core.now())

    def status(self):
        with self.lock:
            return dict(self.state)


def make_handler(store, jobs):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kad-favorites"

        def log_message(self, fmt, *args):
            pass

        # -------- ответы

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, text, filename, ctype):
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw.decode("utf-8")) if raw else {}

        def guard(self):
            # защита от запросов с чужих сайтов, открытых в том же браузере
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                self.send_json({"error": "forbidden"}, 403)
                return False
            return True

        def route(self, method):
            if not self.guard():
                return
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if not parts or parts[0] != "api":
                    return self.static(url.path)
                self.api(method, parts[1:], q)
            except (ValueError, KeyError) as e:
                self.send_json({"error": str(e)}, 400)
            except providers.ProviderError as e:
                self.send_json({"error": str(e)}, 502)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.send_json({"error": f"Внутренняя ошибка: {e}"}, 500)

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def do_PATCH(self):
            self.route("PATCH")

        def do_DELETE(self):
            self.route("DELETE")

        def static(self, path):
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC, name))
            if not full.startswith(STATIC) or not os.path.isfile(full):
                self.send_error(404)
                return
            ctype = {".html": "text/html", ".js": "application/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml"}.get(os.path.splitext(full)[1], "application/octet-stream")
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        # -------- API

        def api(self, method, p, q):
            stamp = datetime.now().strftime("%Y-%m-%d")

            if p == ["cases"] and method == "GET":
                return self.send_json(store.list_cases())

            if p == ["cases"] and method == "POST":
                b = self.body()
                entries, errors = core.parse_entries(b.get("text", ""))
                if not entries and errors:
                    raise ValueError("Не распознан ни один номер дела. Формат: А40-12345/2024 или ссылка kad.arbitr.ru/Card/…")
                ids, created = [], 0
                for e in entries:
                    cid, new = store.add(e, title=b.get("title", "") if len(entries) == 1 else "", tags=b.get("tags"))
                    ids.append(cid)
                    created += new
                if b.get("fetch", True) and ids:
                    jobs.start([i for i in ids if not store.get(i, full=False)["loaded"]] or ids)
                store.auto_backup()
                return self.send_json({"ids": ids, "created": created, "duplicates": len(entries) - created,
                                       "invalid": errors})

            if len(p) >= 2 and p[0] == "cases":
                cid = int(p[1])
                if len(p) == 2 and method == "GET":
                    c = store.get(cid)
                    return self.send_json(c) if c else self.send_json({"error": "Не найдено"}, 404)
                if len(p) == 2 and method == "PATCH":
                    store.update(cid, self.body())
                    store.auto_backup()
                    return self.send_json(store.get(cid))
                if len(p) == 2 and method == "DELETE":
                    store.delete(cid)
                    return self.send_json({"ok": True})
                if p[2:] == ["refresh"] and method == "POST":
                    n = store.refresh(cid, providers.get_provider(store.settings()))
                    return self.send_json({"new": n, "case": store.get(cid)})

            if p == ["refresh-all"] and method == "POST":
                b = self.body()
                ids = [c["id"] for c in store.list_cases() if b.get("include_archived") or not c["archived"]]
                if not jobs.start(ids):
                    raise ValueError("Обновление уже идёт")
                return self.send_json(jobs.status())

            if p == ["job"]:
                return self.send_json(jobs.status())

            if p == ["mark-all-seen"] and method == "POST":
                store.mark_all_seen()
                return self.send_json({"ok": True})

            if p == ["feed"]:
                return self.send_json(store.feed())

            if p == ["search"] and method == "POST":
                b = self.body()
                prov = providers.get_provider(store.settings())
                return self.send_json(prov.search(b["participant"], b.get("case_type", "B"), int(b.get("page", 1))))

            if p == ["settings"]:
                if method == "POST":
                    store.save_settings(self.body())
                return self.send_json(store.public_settings())

            if p == ["export"]:
                fmt = q.get("format", "json")
                if fmt == "json":
                    return self.send_file(json.dumps(store.backup_dict(), ensure_ascii=False, indent=1),
                                          f"избранное-кад-{stamp}.json", "application/json")
                if fmt == "csv":
                    return self.send_file(store.export_csv(), f"избранное-кад-{stamp}.csv", "text/csv")
                if fmt == "txt":
                    return self.send_file(store.export_numbers(), f"номера-дел-{stamp}.txt", "text/plain")
                if fmt == "ics":
                    return self.send_file(store.export_ics(), f"заседания-{stamp}.ics", "text/calendar")
                raise ValueError("Неизвестный формат")

            if p == ["import"] and method == "POST":
                b = self.body()
                res = store.import_backup(b.get("backup"), b.get("mode", "merge"))
                store.auto_backup()
                return self.send_json(res)

            if p == ["backups"] and method == "GET":
                return self.send_json(store.list_backups())
            if p == ["open-folder"] and method == "POST":
                opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
                subprocess.Popen(opener + [store.backup_dir])
                return self.send_json({"path": store.backup_dir})
            if p == ["backups"] and method == "POST":
                return self.send_json({"name": store.auto_backup(force=True, label="manual")})
            if len(p) == 3 and p[0] == "backups" and p[2] == "restore" and method == "POST":
                return self.send_json(store.import_backup(store.read_backup(p[1]), self.body().get("mode", "replace")))
            if len(p) == 2 and p[0] == "backups" and method == "GET":
                return self.send_file(json.dumps(store.read_backup(p[1]), ensure_ascii=False, indent=1),
                                      p[1], "application/json")

            self.send_json({"error": "Не найдено"}, 404)

    return Handler


def create_server(data_dir, port):
    """Создаёт сервер; port=0 — выбрать любой свободный."""
    os.makedirs(data_dir, exist_ok=True)
    store = core.Store(data_dir)
    store.auto_backup()
    jobs = Jobs(store)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, jobs))


def main():
    ap = argparse.ArgumentParser(description="Избранные дела kad.arbitr.ru")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=os.path.join(HERE, "data"), help="папка профиля (база и резервные копии)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    server = create_server(args.data, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Избранные дела КАД: {url}\nПапка профиля: {os.path.abspath(args.data)}\nОстановить: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
