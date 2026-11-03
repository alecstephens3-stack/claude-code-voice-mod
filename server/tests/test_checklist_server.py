#!/usr/bin/env python3
"""Tests for server/checklist_server.py: the routes the checklist page and the mod use.

    python3 -m unittest discover -s server/tests
"""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checklist_server as cs  # noqa: E402


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / "artifacts/checklists").mkdir(parents=True)
        (cls.root / "artifacts/checklists/demo.html").write_text("<h1>Demo</h1>", encoding="utf-8")
        (cls.root / "notes.html").write_text("<h1>Private</h1>", encoding="utf-8")
        cls.httpd = cs.serve(cls.root, 0, ".voice-mod")
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, host=None, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"localhost:{self.port}"}
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    def test_health(self):
        status, raw = self.req("GET", "/api/health")
        self.assertEqual((status, json.loads(raw)), (200, {"ok": True}))

    def test_unknown_checklist_is_empty(self):
        status, raw = self.req("GET", "/api/checklist/never-saved")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"id": "never-saved", "ticks": {}, "updated": None})

    def test_ticks_merge_and_persist(self):
        items = [{"id": "s1", "text": "Open the app."}, {"id": "s2", "text": "Click Close."}]
        status, _ = self.req("POST", "/api/checklist/install-2026-10-03", {"ticks": {"s1": True}, "title": "Install", "items": items})
        self.assertEqual(status, 200)
        status, raw = self.req("POST", "/api/checklist/install-2026-10-03", {"ticks": {"s2": True}})
        saved = json.loads(raw)
        self.assertEqual(saved["ticks"], {"s1": True, "s2": True})
        self.assertEqual(saved["title"], "Install")
        self.assertEqual(saved["items"], items)
        self.assertIsNotNone(saved["updated"])
        on_disk = json.loads((self.root / ".voice-mod/checklists/install-2026-10-03.json").read_text())
        self.assertEqual(on_disk["ticks"], {"s1": True, "s2": True})

    def test_bad_input_is_refused(self):
        self.assertEqual(self.req("GET", "/api/checklist/Bad_ID")[0], 400)
        self.assertEqual(self.req("POST", "/api/checklist/x", {"ticks": {"s1": "yes"}})[0], 400)
        self.assertEqual(self.req("POST", "/api/checklist/x", b"x" * (cs.MAX_BODY + 1))[0], 413)

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.req("GET", "/api/health", host="evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/checklist/x", {"ticks": {}}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/checklist/x", {"ticks": {}}, origin=f"http://localhost:{self.port}")[0], 200)

    def test_pages_only_under_page_folders(self):
        status, raw = self.req("GET", "/pages/artifacts/checklists/demo.html")
        self.assertEqual((status, raw), (200, b"<h1>Demo</h1>"))
        self.assertEqual(self.req("GET", "/pages/notes.html")[0], 404)
        self.assertEqual(self.req("GET", "/pages/artifacts/../notes.html")[0], 404)
        self.assertEqual(self.req("GET", "/pages/artifacts/checklists/missing.html")[0], 404)


if __name__ == "__main__":
    unittest.main()
