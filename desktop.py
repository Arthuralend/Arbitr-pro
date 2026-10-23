"""Запуск как обычной программы: своё окно вместо браузера.

Используется при сборке приложения для Mac (см. .github/workflows/build-mac.yml).
Данные хранятся в ~/Library/Application Support/Arbitr-pro (на Mac),
в %APPDATA%\\Arbitr-pro (Windows) или ~/.local/share/Arbitr-pro (Linux).
"""

import os
import sys
import threading
import webbrowser

import app
import core
import kadsite

APP_NAME = "Arbitr-pro"


def data_dir():
    if os.environ.get("ARBITR_DATA"):
        return os.environ["ARBITR_DATA"]
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


def main():
    os.makedirs(data_dir(), exist_ok=True)
    if getattr(sys, "frozen", False):
        # у приложения без терминала ошибки пишем в файл
        sys.stdout = sys.stderr = open(os.path.join(data_dir(), "log.txt"), "a", buffering=1, encoding="utf-8")
    server = app.create_server(data_dir(), int(os.environ.get("ARBITR_PORT", "0")), default_provider="demo" if os.environ.get("ARBITR_SELFTEST") else "kad")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import webview
    except ImportError:
        # без pywebview — откроем в браузере и будем работать, пока не закроют окно терминала
        webbrowser.open(url)
        threading.Event().wait()
        return
    webview.settings["ALLOW_DOWNLOADS"] = True  # экспорт копий, CSV, .ics
    if os.environ.get("ARBITR_SELFTEST"):
        url += "#selftest"
    main_win = webview.create_window("Избранные дела КАД", url, width=1280, height=850, min_size=(380, 500),
                                     text_select=True)

    # скрытое окно сайта: через него программа получает данные без API;
    # его можно показать (пройти проверку, искать дела и добавлять их кнопкой ★)
    store, jobs = server.store, server.jobs

    class KadApi:
        def add_case(self, url):
            entries, _ = core.parse_entries(url)
            if not entries:
                return "Не удалось распознать дело"
            cid, created = store.add(entries[0])
            jobs.start([cid])
            return "★ Добавлено в избранное" if created else "★ Уже в избранном"

    kad_win = webview.create_window("kad.arbitr.ru", kadsite.HOME, width=1200, height=850, hidden=True,
                                    js_api=KadApi())
    kadsite.BRIDGE = kadsite.Bridge(kad_win, os.path.join(data_dir(), "kad-debug"))
    quitting = []

    def on_kad_loaded():
        kad_win.evaluate_js(kadsite.STAR_JS)

    def on_kad_closing():
        if quitting:
            return True
        kad_win.hide()  # закрытие окна сайта только прячет его — оно нужно для работы
        return False

    def on_main_closed():
        quitting.append(1)
        kad_win.destroy()

    kad_win.events.loaded += on_kad_loaded
    kad_win.events.closing += on_kad_closing
    main_win.events.closed += on_main_closed
    webview.start()


if __name__ == "__main__":
    main()
