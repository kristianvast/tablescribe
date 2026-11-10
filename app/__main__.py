from __future__ import annotations

import argparse
import logging
import socket
import subprocess
import threading

import uvicorn

from app.server import create_app
from app.session import SessionManager


def lan_ip() -> str | None:
    try:
        out = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="tablescribe server")
    parser.add_argument("--source", default="mic", help="mic | mic:<device> | file:<path>")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(SessionManager(source_spec=args.source))

    local_url = f"http://127.0.0.1:{args.port}"
    print(f"tablescribe: local (control) {local_url}", flush=True)
    ip = lan_ip() if args.host in ("0.0.0.0", "::") else None
    if ip:
        print(f"tablescribe: LAN (view-only) http://{ip}:{args.port}", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, lambda: subprocess.run(["open", local_url], check=False)).start()

    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
