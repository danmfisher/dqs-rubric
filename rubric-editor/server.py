#!/usr/bin/env python3
"""
FlexGen Rubric Editor — local development server.
Uses Python stdlib only — no third-party dependencies required.

Usage:
    python3 server.py

Then open: http://localhost:3737
"""

import json
import os
import socket
import signal
import threading
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 3737
BASE_DIR   = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"
RUBRIC_PATH = BASE_DIR.parent / "rubric.json"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico":  "image/x-icon",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
}

# ── helpers ─────────────────────────────────────────────────────────────────

def read_rubric():
    with open(RUBRIC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def resolve_track(rubric, track_id):
    """Return a deep copy of the rubric with track-specific descriptions
    resolved to the flat {L1:..., L2:...} shape the frontend expects.

    Nodes with "track_specific": true have descriptions keyed by track id
    instead of level.  All other nodes are passed through unchanged.
    """
    import copy
    r = copy.deepcopy(rubric)
    r["meta"]["current_track"] = track_id
    for dim in r["dimensions"]:
        for comp in dim["competencies"]:
            if comp.get("track_specific"):
                comp["descriptions"] = comp["descriptions"].get(track_id, {})
            for cap in comp["capabilities"]:
                if cap.get("track_specific"):
                    cap["descriptions"] = cap["descriptions"].get(track_id, {})
    return r

def default_track(rubric):
    return rubric["meta"].get("default_track") or rubric["meta"]["tracks"][0]["id"]

def read_philosophy():
    """Parse philosophy.md into a list of {number, title, content, group} dicts.

    Group membership is declared via HTML comments immediately before a section:
        <!-- group: Group Name -->
    The declared group is inherited by all subsequent sections until a new
    group comment is encountered.
    """
    import re
    path = BASE_DIR.parent / "philosophy.md"
    text = path.read_text(encoding="utf-8")

    # Find every group-comment and every ## section header, in document order,
    # then walk them together to assign group membership.
    tokens = []
    for m in re.finditer(
            r'(?:<!-- group:\s*(.+?)\s*-->|^## (\d+)\.\s+(.+?)$(.*?)(?=^## |<!-- group:|\Z))',
            text, re.MULTILINE | re.DOTALL):
        if m.group(1) is not None:
            tokens.append(("group", m.group(1)))
        else:
            tokens.append(("section", m.group(2), m.group(3).strip(), m.group(4).strip()))

    sections = []
    current_group = ""
    for tok in tokens:
        if tok[0] == "group":
            current_group = tok[1]
        else:
            sections.append({
                "number":  tok[1],
                "title":   tok[2],
                "content": tok[3],
                "group":   current_group,
            })
    return sections

def write_rubric(data):
    data["meta"]["last_updated"] = datetime.date.today().isoformat()
    with open(RUBRIC_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def build_csv(rubric, level):
    """Build a CSV for the given level using only stdlib — no third-party deps."""
    import csv, io
    level = level.upper()
    level_name = rubric["meta"]["level_names"].get(level, level)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(["Dimension", "Competency", "Capability", f"{level} — {level_name}"])
    for dim in rubric["dimensions"]:
        for comp in dim["competencies"]:
            for cap in comp["capabilities"]:
                desc = cap["descriptions"].get(level, "")
                writer.writerow([dim["name"], comp["name"], cap["name"], desc])
    return buf.getvalue().encode("utf-8"), None

# ── request handler ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} — {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _query_params(self):
        """Parse query string into a dict of {key: [values]}."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        return parse_qs(parsed.query)

    def do_GET(self):
        path = self.path.split("?")[0]
        qp   = self._query_params()

        # API: download raw philosophy markdown (must come before /api/philosophy)
        if path == "/api/philosophy/download":
            try:
                md_path = BASE_DIR.parent / "philosophy.md"
                data = md_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="FlexGen_Rubric_Philosophy.md"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # API: evaluation wizard example cards
        if path == "/api/examples":
            try:
                examples_path = BASE_DIR.parent / "examples.json"
                if examples_path.exists():
                    self.send_json(json.loads(examples_path.read_text(encoding="utf-8")))
                else:
                    self.send_json([])
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # API: philosophy sections (parsed JSON)
        if path == "/api/philosophy":
            try:
                self.send_json(read_philosophy())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # API: get rubric (resolved for the requested track)
        if path == "/api/rubric":
            try:
                rubric   = read_rubric()
                track_id = qp.get("track", [default_track(rubric)])[0]
                self.send_json(resolve_track(rubric, track_id))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # API: export level as CSV (stdlib only, no third-party deps)
        if path.startswith("/api/export/"):
            level = path[len("/api/export/"):]
            try:
                rubric   = read_rubric()
                track_id = qp.get("track", [default_track(rubric)])[0]
                resolved = resolve_track(rubric, track_id)
                data, err = build_csv(resolved, level)
                if err:
                    self.send_json({"error": err}, 500)
                    return
                safe_track = track_id.replace("/", "-")
                self.send_bytes(
                    data,
                    "text/csv; charset=utf-8",
                    f"Rubric_{safe_track}_{level.upper()}.csv"
                )
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # Static files
        if path == "/" or path == "":
            path = "/index.html"

        file_path = PUBLIC_DIR / path.lstrip("/")
        if file_path.is_file():
            suffix = file_path.suffix.lower()
            mime = MIME.get(suffix, "application/octet-stream")
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        if self.path == "/api/rubric":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                write_rubric(data)
                self.send_json({"ok": True, "last_updated": data["meta"]["last_updated"]})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_json({"error": "Not found"}, 404)


# ── graceful server with address reuse ──────────────────────────────────────

class ReusingHTTPServer(HTTPServer):
    """HTTPServer that explicitly sets SO_REUSEADDR and SO_REUSEPORT so a
    restarted process can immediately rebind to the same port."""
    allow_reuse_address = True

    def server_bind(self):
        # SO_REUSEADDR lets a restarted process bind to a port still in
        # TIME_WAIT.  We deliberately skip SO_REUSEPORT: on Linux it causes
        # the kernel to load-balance new connections across *all* sockets
        # sharing the port, including lingering ones from the dying process,
        # which produces hung connections on immediate restart.
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not RUBRIC_PATH.exists():
        print(f"\n  ERROR: rubric.json not found at {RUBRIC_PATH}\n")
        exit(1)

    print(f"\n  ✓ rubric.json found: {RUBRIC_PATH}")
    print(f"  ✓ CSV export enabled (stdlib only)")

    server = ReusingHTTPServer(("localhost", PORT), Handler)

    # Handle SIGTERM (sent by start.sh) and Ctrl+C identically.
    #
    # server.shutdown() must NOT be called directly from the signal handler:
    # it waits on an event that serve_forever() (running in the same main
    # thread) can never set while the handler is executing — deadlock.
    #
    # Fix: dispatch shutdown to a daemon thread so the signal handler returns
    # immediately, serve_forever() can finish its current poll iteration, see
    # the shutdown request, and exit cleanly — at which point the daemon
    # thread's wait() returns and the process exits normally.
    def _shutdown(sig, frame):
        print("\n  Shutting down…")
        t = threading.Thread(
            target=lambda: (server.shutdown(), server.server_close()),
            daemon=True,
        )
        t.start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    print(f"\n  Rubric Editor running at http://localhost:{PORT}\n")
    print(f"  Press Ctrl+C to stop.\n")
    server.serve_forever()
    print("  Server stopped.")
