import threading
import webbrowser
import time
import sys

from server import run_server

DESKTOP_MODE = "browser"

try:
    import webview
    DESKTOP_MODE = "pywebview"
except ImportError:
    pass


def start_server():
    run_server()


def wait_for_server(url, timeout=15):
    import httpx
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(url + "/api/stages", timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    print("Server failed to start within timeout")


def main():
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    url = "http://127.0.0.1:8000"
    wait_for_server(url)

    if DESKTOP_MODE == "pywebview":
        webview.create_window("CRM System", url, width=1280, height=800, resizable=True)
        webview.start()
    else:
        webbrowser.open(url)
        print(f"Server started at {url}")
        print("Press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")


if __name__ == "__main__":
    main()
