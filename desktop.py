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
    server = app.create_server(data_dir(), int(os.environ.get("ARBITR_PORT", "0")))
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
    webview.create_window("Избранные дела КАД", url, width=1280, height=850, min_size=(380, 500),
                          text_select=True)
    webview.start()


if __name__ == "__main__":
    main()
