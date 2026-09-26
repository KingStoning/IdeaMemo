import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
from storage import Notebook, canonical, request_json

spec = importlib.util.spec_from_file_location("server", ROOT / "sync-server/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
TOKEN = "test-only-" + "a" * 32


def note(text="灵感 #项目/测试"):
    return dict(title="记录", content=text, createTime=1000, updateTime=2000, isCollected=False, isDeleted=False)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = server.Store(self.folder / "server.sqlite3")
        self.http = server.make_server(("127.0.0.1", 0), self.store, TOKEN)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.a = self.client("a")
        self.b = self.client("b")

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.temp.cleanup()

    def client(self, name):
        client = Notebook(self.folder / f"{name}.sqlite3")
        client.configure(self.url, TOKEN, False)
        return client

    def change(self, id_=None, revision=0, payload=None):
        return dict(id=id_ or str(uuid.uuid4()), baseRevision=revision, mutationId=str(uuid.uuid4()), note=payload)

    def send(self, changes):
        return self.store.sync(dict(protocol=1, serverId=self.store.server_id, changes=changes))

    def seeded(self):
        id_ = self.a.save(None, "最初", "内容 😀 #项目", True)
        self.a.sync()
        self.b.sync()
        return id_

    def test_bidirectional_edit_unicode_tags_favorite_and_trash_restore(self):
        id_ = self.seeded()
        self.assertEqual(self.b.get(id_)["content"], "内容 😀 #项目")
        self.assertTrue(self.b.get(id_)["isCollected"])
        self.b.save(id_, "电脑修改", "新的正文", False)
        self.b.sync()
        self.a.sync()
        self.assertEqual(self.a.get(id_)["title"], "电脑修改")
        self.a.trash(id_, True)
        self.a.sync()
        self.b.sync()
        self.assertEqual(len(self.b.rows(trash=True)), 1)
        self.b.trash(id_, False)
        self.b.sync()
        self.a.sync()
        self.assertEqual(len(self.a.rows()), 1)

    def test_concurrent_edits_keep_both_versions(self):
        id_ = self.seeded()
        self.a.save(id_, "手机", "手机离线编辑")
        self.b.save(id_, "电脑", "电脑离线编辑")
        self.a.sync()
        conflicts = self.b.sync()
        self.a.sync()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual({n["content"] for _, n in self.a.rows()}, {"手机离线编辑", "电脑离线编辑"})
        self.assertEqual(self.a.rows(), self.b.rows())

    def test_lost_response_retry_is_idempotent_even_with_conflict(self):
        id_ = self.seeded()
        self.a.save(id_, "手机", "版本 A")
        self.a.sync()
        self.b.save(id_, "电脑", "版本 B")
        actual = request_json

        def lost(url, token, body=None):
            result = actual(url, token, body)
            if body is not None:
                raise OSError("lost response")
            return result

        with patch("storage.request_json", side_effect=lost):
            with self.assertRaises(OSError):
                self.b.sync()
        self.assertEqual(len(self.b.rows()), 1)
        self.b.sync()
        self.b.sync()
        self.assertEqual(len(self.b.rows()), 2)

    def test_permanent_delete_tombstone_prevents_resurrection(self):
        id_ = self.seeded()
        result = self.send([self.change(id_, 1, None)])
        self.assertIsNone(result["notes"][0]["note"])
        self.b.sync()
        self.a.sync()
        self.assertIsNone(self.a.get(id_))
        self.assertEqual(self.a.rows(), [])
        self.assertIsNone(self.send([])["notes"][0]["note"])

    def test_edit_after_remote_delete_is_preserved_as_copy(self):
        id_ = self.seeded()
        self.send([self.change(id_, 1, None)])
        self.b.save(id_, "本地保留", "未上传的编辑")
        conflicts = self.b.sync()
        self.assertEqual(len(conflicts), 1)
        self.assertIsNone(self.b.get(id_))
        self.assertEqual(self.b.rows()[0][1]["content"], "未上传的编辑")

    def test_stale_permanent_delete_does_not_erase_new_edit(self):
        id_ = self.seeded()
        self.a.save(id_, "新版本", "保留我")
        self.a.sync()
        result = self.send([self.change(id_, 1, None)])
        self.assertEqual(result["conflicts"][0]["reason"], "delete_conflict")
        self.assertEqual(result["notes"][0]["note"]["content"], "保留我")

    def test_local_edit_during_network_is_not_overwritten(self):
        id_ = self.seeded()
        self.a.save(id_, "远端", "远端改动")
        self.a.sync()
        actual = request_json

        def edit_while_request(url, token, body=None):
            result = actual(url, token, body)
            if body is not None:
                self.b.save(id_, "仍在输入", "新的本地编辑")
            return result

        with patch("storage.request_json", side_effect=edit_while_request):
            self.b.sync()
        self.assertEqual(self.b.get(id_)["content"], "新的本地编辑")
        self.assertEqual(len(self.b.sync()), 1)
        self.assertEqual(len(self.b.rows()), 2)

    def test_new_client_merges_existing_local_notes(self):
        self.seeded()
        c = self.client("c")
        c.save(None, "另一台", "已有笔记")
        c.sync()
        self.a.sync()
        self.assertEqual(len(c.rows()), 2)
        self.assertEqual(len(self.a.rows()), 2)

    def test_bad_token_and_health(self):
        with self.assertRaises(ValueError):
            request_json(self.url + "/v1/info", "wrong")
        with urlopen(self.url + "/health") as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_server_identity_survives_restart_and_replacement_is_blocked(self):
        self.seeded()
        self.assertEqual(server.Store(self.store.path).server_id, self.store.server_id)
        with patch("storage.request_json", return_value={"protocol": 1, "serverId": str(uuid.uuid4())}):
            with self.assertRaisesRegex(ValueError, "数据卷"):
                self.a.sync()

    def test_validation_and_atomic_rollback(self):
        first = self.change(payload=note())
        invalid = self.change(payload=note())
        invalid["note"]["content"] = 123
        with self.assertRaises(ValueError):
            self.send([first, invalid])
        self.assertEqual(self.send([])["notes"], [])
        self.send([first])
        reused = copy.deepcopy(first)
        reused["note"]["content"] = "different"
        with self.assertRaisesRegex(ValueError, "reused"):
            self.send([self.change(payload=note()), reused])
        self.assertEqual(len(self.send([])["notes"]), 1)

    def test_parallel_writers_serialize_and_preserve_conflicts(self):
        id_ = self.seeded()
        changes = [self.change(id_, 1, note(str(i))) for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda c: self.send([c]), changes))
        self.assertEqual(len(self.send([])["notes"]), 8)
        self.assertEqual(sum(len(r["conflicts"]) for r in results), 7)

    def test_android_serialization_order_and_null_tombstone(self):
        # Same JSON shape as Kotlin @Serializable classes; clients need not hash identically.
        change = self.change(payload=note())
        request = json.dumps(dict(protocol=1, serverId=self.store.server_id, changes=[change]), ensure_ascii=False)
        req = Request(self.url + "/v1/sync", data=request.encode("utf-8"),
                      headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        with urlopen(req) as response:
            result = json.load(response)
        self.assertEqual(result["notes"][0]["note"], change["note"])
        self.assertEqual(result["notes"][0]["revision"], 1)

    def test_oversize_http_request_is_rejected_before_read(self):
        req = Request(self.url + "/v1/sync", data=b"{}", headers={"Authorization": "Bearer " + TOKEN, "Content-Length": str(9 * 1024 * 1024)})
        with self.assertRaises(HTTPError) as error:
            urlopen(req)
        self.assertEqual(error.exception.code, 413)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
