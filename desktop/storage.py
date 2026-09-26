"""Offline storage and the protocol shared with the Android client."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_url(url):
    url = url.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("请输入完整的 http:// 或 https:// 服务地址，不要包含密码、查询参数或片段。")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("服务发生重定向，请直接填写最终服务地址。")


def request_json(url, token, body=None):
    req = Request(url, data=None if body is None else canonical(body).encode("utf-8"),
                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect).open(req, timeout=30) as response:
            data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("同步数据超过 64 MiB 限制。")
            return json.loads(data)
    except HTTPError as exc:
        exc.close()
        messages = {401: "访问密钥不正确。", 413: "本次同步超过 8 MiB，请减少单次修改量。"}
        raise ValueError(messages.get(exc.code, f"服务返回错误 {exc.code}，本地内容已保留。")) from exc


class Notebook:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, note TEXT, revision INTEGER NOT NULL DEFAULT 0, synced TEXT NOT NULL DEFAULT 'null');
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=""):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def configure(self, url, token, auto):
        url = normalize_url(url)
        if len(token.strip()) < 32 or not token.strip().isascii() or any(c.isspace() for c in token.strip()):
            raise ValueError("访问密钥至少需要 32 个 ASCII 字符，不能包含空格。")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)",
                           [("url", url), ("token", token.strip()), ("auto", str(bool(auto)))])

    def rows(self, query="", trash=False):
        with self.connect() as db:
            rows = db.execute("SELECT id,note FROM notes WHERE note IS NOT NULL").fetchall()
        result = [(row["id"], json.loads(row["note"])) for row in rows]
        return sorted([(id_, n) for id_, n in result if n["isDeleted"] == trash and
                       query.casefold() in (n["title"] + "\n" + n["content"]).casefold()],
                      key=lambda pair: pair[1]["updateTime"], reverse=True)

    def get(self, id_):
        with self.connect() as db:
            row = db.execute("SELECT note FROM notes WHERE id=?", (id_,)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def save(self, id_, title, content, collected=False):
        if len(title.encode("utf-8")) > 4096 or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("标题最多 4 KiB，正文最多 1 MiB。")
        now = int(time.time() * 1000)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT note FROM notes WHERE id=?", (id_,)).fetchone() if id_ else None
            old = json.loads(row[0]) if row and row[0] else None
            if not id_:
                id_ = str(uuid.uuid4())
            note = {"title": title, "content": content, "createTime": old["createTime"] if old else now,
                    "updateTime": now, "isCollected": collected, "isDeleted": old["isDeleted"] if old else False}
            if old and all(note[k] == old[k] for k in ("title", "content", "isCollected")):
                return id_
            db.execute("INSERT INTO notes(id,note) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET note=excluded.note", (id_, canonical(note)))
        return id_

    def trash(self, id_, deleted):
        with self.connect() as db:
            row = db.execute("SELECT note FROM notes WHERE id=?", (id_,)).fetchone()
            if row and row[0]:
                note = json.loads(row[0])
                note.update(isDeleted=deleted, updateTime=int(time.time() * 1000))
                db.execute("UPDATE notes SET note=? WHERE id=?", (canonical(note), id_))

    def sync(self):
        url, token = self.setting("url"), self.setting("token")
        if not url or not token:
            raise ValueError("请先在同步设置中填写服务地址和访问密钥。")
        identity = request_json(url + "/v1/info", token)
        if identity.get("protocol") != 1:
            raise ValueError("服务协议不兼容。")
        server_id = str(uuid.UUID(identity["serverId"]))
        with self.connect() as db:
            bound = db.execute("SELECT value FROM settings WHERE key='serverId'").fetchone()
            if bound and bound[0] != server_id:
                raise ValueError("服务器数据卷已改变。请恢复原数据卷，以免重复上传或误删记录。")
            db.execute("INSERT OR REPLACE INTO settings VALUES('serverId',?)", (server_id,))
            rows = db.execute("SELECT * FROM notes").fetchall()
        captured = {row["id"]: row["note"] for row in rows}
        changes = []
        for row in rows:
            note = json.loads(row["note"]) if row["note"] else None
            if canonical(note) != row["synced"]:
                change = {"id": row["id"], "baseRevision": row["revision"], "note": note}
                change["mutationId"] = hashlib.sha256(canonical(change).encode("utf-8")).hexdigest()
                changes.append(change)
        body = {"protocol": 1, "serverId": server_id, "changes": changes}
        if len(changes) > 5000 or len(canonical(body).encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("单次同步限制为 5000 条修改、8 MiB。请先减少修改量。")
        result = request_json(url + "/v1/sync", token, body)
        if result.get("protocol") != 1 or result.get("serverId") != server_id:
            raise ValueError("服务器响应身份不匹配，本地数据未覆盖。")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in result["notes"]:
                row = db.execute("SELECT note FROM notes WHERE id=?", (item["id"],)).fetchone()
                if row and (item["id"] not in captured or row[0] != captured[item["id"]]):
                    continue  # A newer local edit made during the request must remain dirty.
                payload = canonical(item["note"])
                db.execute("INSERT INTO notes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET note=excluded.note,revision=excluded.revision,synced=excluded.synced",
                           (item["id"], None if item["note"] is None else payload, item["revision"], payload))
        return result["conflicts"]
