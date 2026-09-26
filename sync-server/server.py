"""IdeaMemo single-user sync service. Python standard library only."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 8 * 1024 * 1024
MAX_CHANGES = 5000
NOTE_KEYS = {"title", "content", "createTime", "updateTime", "isCollected", "isDeleted"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_change(change):
    if not isinstance(change, dict) or set(change) != {"id", "baseRevision", "mutationId", "note"}:
        raise ValueError("Invalid change fields")
    if not isinstance(change["id"], str) or str(uuid.UUID(change["id"])) != change["id"]:
        raise ValueError("id must be a canonical UUID")
    if type(change["baseRevision"]) is not int or not 0 <= change["baseRevision"] <= 2**53 - 1:
        raise ValueError("Invalid revision")
    if not isinstance(change["mutationId"], str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,128}", change["mutationId"]):
        raise ValueError("Invalid mutationId")
    note = change["note"]
    if note is None:
        return
    if not isinstance(note, dict) or set(note) != NOTE_KEYS:
        raise ValueError("Invalid note fields")
    for key, limit in (("title", 4096), ("content", 1024 * 1024)):
        if not isinstance(note[key], str) or len(note[key].encode("utf-8")) > limit:
            raise ValueError(f"Invalid {key} or length limit exceeded")
    for key in ("createTime", "updateTime"):
        if type(note[key]) is not int or not 0 <= note[key] <= 2**53 - 1:
            raise ValueError("Invalid timestamp")
    for key in ("isCollected", "isDeleted"):
        if type(note[key]) is not bool:
            raise ValueError("Invalid boolean")


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, note TEXT);
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, digest TEXT NOT NULL, conflict TEXT);
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES('serverId', ?)", (str(uuid.uuid4()),))
            self.server_id = db.execute("SELECT value FROM meta WHERE key='serverId'").fetchone()[0]

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def sync(self, request):
        if not isinstance(request, dict) or request.get("protocol") != 1:
            raise ValueError("Unsupported protocol")
        if request.get("serverId") != self.server_id:
            raise ValueError("Server identity changed; restore the original data volume")
        changes = request.get("changes")
        if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
            raise ValueError("Too many changes; maximum 5000 per request")
        for change in changes:
            validate_change(change)
        if len({c["id"] for c in changes}) != len(changes):
            raise ValueError("Duplicate note IDs in request")
        conflicts = []
        # BEGIN IMMEDIATE serializes concurrent writers. The response is a consistent snapshot.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for change in changes:
                digest = hashlib.sha256(canonical(change).encode()).hexdigest()
                receipt = db.execute("SELECT * FROM receipts WHERE id=?", (change["mutationId"],)).fetchone()
                if receipt:
                    if receipt["digest"] != digest:
                        raise ValueError("mutationId was reused with different data")
                    if receipt["conflict"]:
                        conflicts.append(json.loads(receipt["conflict"]))
                    continue
                current = db.execute("SELECT * FROM notes WHERE id=?", (change["id"],)).fetchone()
                revision = current["revision"] if current else 0
                payload = None if change["note"] is None else canonical(change["note"])
                conflict = None
                if change["baseRevision"] == revision:
                    db.execute("INSERT INTO notes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,note=excluded.note",
                               (change["id"], revision + 1, payload))
                elif current and current["note"] == payload:
                    pass  # The same edit has already arrived from another client.
                elif change["note"] is not None:
                    # Keep the server version and a separate, deterministic copy of the offline edit.
                    copy_id = str(uuid.uuid5(uuid.UUID(self.server_id), change["mutationId"]))
                    copy = dict(change["note"])
                    copy["title"] = copy["title"][:900] + "（同步冲突副本）"
                    copy["isDeleted"] = False
                    db.execute("INSERT INTO notes VALUES(?,1,?)", (copy_id, canonical(copy)))
                    conflict = {"id": change["id"], "copyId": copy_id, "reason": "concurrent_edit"}
                else:
                    conflict = {"id": change["id"], "copyId": None, "reason": "delete_conflict"}
                db.execute("INSERT INTO receipts VALUES(?,?,?)",
                           (change["mutationId"], digest, canonical(conflict) if conflict else None))
                if conflict:
                    conflicts.append(conflict)
            notes = [{"id": r["id"], "revision": r["revision"], "note": json.loads(r["note"]) if r["note"] else None}
                     for r in db.execute("SELECT * FROM notes ORDER BY id")]
        return {"protocol": 1, "serverId": self.server_id, "notes": notes, "conflicts": conflicts}


def make_server(address, store, token):
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("SYNC_TOKEN must contain at least 32 ASCII characters without spaces")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *_):
            pass  # Never log authentication headers or note content.

        def reply(self, code, body):
            data = canonical(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            received = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(received, ("Bearer " + token).encode("ascii")):
                self.reply(401, {"error": "Invalid access token"})
                return False
            return True

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            elif self.path == "/v1/info":
                if self.authorized():
                    self.reply(200, {"protocol": 1, "serverId": store.server_id})
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/v1/sync":
                self.reply(404, {"error": "Not found"})
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Chunked requests are unsupported")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    self.reply(413, {"error": "Request must be between 1 byte and 8 MiB"})
                    return
                request = json.loads(self.rfile.read(size))
                self.reply(200, store.sync(request))
            except (ValueError, TypeError, UnicodeError) as exc:
                self.reply(400, {"error": str(exc)})
            except sqlite3.Error:
                self.reply(503, {"error": "Database unavailable; retry later"})

    return ThreadingHTTPServer(address, Handler)


if __name__ == "__main__":
    store = Store(os.environ.get("SYNC_DB", "/data/ideamemo.sqlite3"))
    server = make_server(("0.0.0.0", int(os.environ.get("PORT", "8787"))), store, os.environ.get("SYNC_TOKEN", ""))
    print("IdeaMemo sync listening on port", server.server_port, flush=True)
    server.serve_forever()
