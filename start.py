# -*- coding: utf-8 -*-
"""Cross-platform launcher: start the local web app and open the browser."""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def free_port(start=8765):
    for port in range(start, start + 30):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    return start


def main():
    from pcr_team.server import run
    port = free_port()
    url = "http://127.0.0.1:%d/" % port
    print("=" * 56)
    print("  PCR 配队工坊")
    print("  地址：%s  （关闭窗口即停止）" % url)
    print("=" * 56)
    def open_browser():
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        if shutil.which("termux-open-url"):
            subprocess.Popen(["termux-open-url", url])
        else:
            webbrowser.open(url)
    threading.Thread(target=open_browser, daemon=True).start()
    run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
