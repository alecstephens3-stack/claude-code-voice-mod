#!/usr/bin/env python3
"""
checklist_server.py: a small local server for the checklist pages.

The mod writes a checklist page into the project and opens it through this server,
so the page can save its ticks and the model can read them back (read_checklist).
Without it, pages open as files and ticks live only in the browser's localStorage.

Routes (docs/CONTRACT.md has the shapes):

  GET  /api/health              {"ok": true}
  GET  /pages/<rel>             an .html file of the project, under artifacts/ or
                                clients/<name>/(artifacts|deliverables)/
  GET  /api/checklist/<id>      the saved ticks, or an empty record
  POST /api/checklist/<id>      merge ticks; store title and items when given

It listens on 127.0.0.1 only, answers only requests whose Host is localhost or
127.0.0.1 on its own port, and refuses a POST whose Origin is another site. Ticks
are written atomically to <data-dir>/checklists/<id>.json. Standard library only.

    python3 server/checklist_server.py --root /path/to/project
    python3 server/checklist_server.py --root . --port 7777 --data-dir .voice-mod
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
PAGE_RX = re.compile(r"^(artifacts/|clients/[^/]+/(artifacts|deliverables)/).+\.html$")
MAX_BODY = 64 * 1024


def empty(cid: str) -> dict:
    return {"id": cid, "ticks": {}, "updated": None}


def load(store: Path, cid: str) -> dict:
    try:
        return json.loads((store / f"{cid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty(cid)


def merge(saved: dict, body: dict, now: datetime) -> dict:
    """Merge a POST body into a saved record. Raises ValueError on a bad body."""
    ticks = body.get("ticks")
    if not isinstance(ticks, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in ticks.items()):
        raise ValueError("ticks must be an object of true/false values")
    out = dict(saved)
    out["ticks"] = {**saved.get("ticks", {}), **ticks}
    if isinstance(body.get("title"), str):
        out["title"] = body["title"][:300]
    items = body.get("items")
    if isinstance(items, list) and all(isinstance(i, dict) and isinstance(i.get("id"), str)
                                       and isinstance(i.get("text"), str) for i in items):
        out["items"] = [{"id": i["id"], "text": i["text"]} for i in items]
    out["updated"] = now.isoformat(timespec="seconds")
    return out


def save(store: Path, cid: str, record: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=store, prefix=f".{cid}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, store / f"{cid}.json")


def make_handler(root: Path, store: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "checklist-server"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        def send_json(self, status: int, data: dict) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"localhost:{port}", f"127.0.0.1:{port}"}

        def host_ok(self) -> bool:
            if self.headers.get("Host", "") not in self.hosts():
                self.send_json(403, {"error": "unknown host"})
                return False
            return True

        def do_GET(self):
            if not self.host_ok():
                return
            path = unquote(urlsplit(self.path).path)
            if path == "/api/health":
                return self.send_json(200, {"ok": True})
            m = re.fullmatch(r"/api/checklist/([^/]+)", path)
            if m:
                if not ID_RX.match(m.group(1)):
                    return self.send_json(400, {"error": "bad id"})
                return self.send_json(200, load(store, m.group(1)))
            if path.startswith("/pages/"):
                return self.page(path[len("/pages/"):])
            self.send_json(404, {"error": "not found"})

        def page(self, rel: str) -> None:
            target = (root / rel).resolve()
            if not PAGE_RX.match(rel) or ".." in rel or not target.is_relative_to(root) or not target.is_file():
                return self.send_json(404, {"error": "not found"})
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if not self.host_ok():
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in self.hosts()}:
                return self.send_json(403, {"error": "cross-site request"})
            m = re.fullmatch(r"/api/checklist/([^/]+)", unquote(urlsplit(self.path).path))
            if not m:
                return self.send_json(404, {"error": "not found"})
            cid = m.group(1)
            if not ID_RX.match(cid):
                return self.send_json(400, {"error": "bad id"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.send_json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                record = merge(load(store, cid), body if isinstance(body, dict) else {},
                               datetime.now(timezone.utc).astimezone())
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            record["id"] = cid
            save(store, cid, record)
            self.send_json(200, record)

    return Handler


def serve(root: Path, port: int, data_dir: str) -> ThreadingHTTPServer:
    root = root.resolve()
    store = (root / data_dir / "checklists").resolve()
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(root, store))


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve checklist pages and save their ticks.")
    ap.add_argument("--root", default=".", help="the project root (default: current folder)")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--data-dir", default=".voice-mod", help="where ticks are saved, relative to the root")
    a = ap.parse_args()
    httpd = serve(Path(a.root), a.port, a.data_dir)
    print(f"checklist server on http://localhost:{a.port} for {Path(a.root).resolve()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
