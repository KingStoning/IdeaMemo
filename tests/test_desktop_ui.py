"""Exercise real Tk widgets against an isolated database."""
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))
from main import App
from storage import Notebook


class DesktopUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.book = Notebook(Path(self.temp.name) / "notes.sqlite3")
        self.app = App(self.root, self.book)
        self.root.update()

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()

    def test_create_edit_search_select_and_restart(self):
        self.app.title.set("Windows 测试")
        self.app.text.insert("1.0", "第一条记录 #工作")
        self.root.update()
        self.assertTrue(self.app.save())
        first = self.app.selected
        self.app.new()
        self.app.title.set("第二条")
        self.app.text.insert("1.0", "新的想法")
        self.root.update()
        self.app.save()
        second = self.app.selected
        self.assertNotEqual(first, second)
        self.app.tree.selection_set(first)
        self.root.update()
        self.assertEqual(self.app.title.get(), "Windows 测试")
        self.app.text.insert("end", " 已修改")
        self.app.tree.selection_set(second)
        self.root.update()
        self.assertEqual(self.app.selected, second)
        self.assertTrue(self.book.get(first)["content"].endswith("已修改"))
        self.app.query.set("#工作")
        self.root.update()
        self.assertEqual(self.app.tree.get_children(), (first,))
        self.assertEqual(len(Notebook(self.book.path).rows()), 2)

    def test_remote_tombstone_clears_selection(self):
        id_ = self.book.save(None, "记录", "将被删除")
        self.app.load(id_)
        with self.book.connect() as db:
            db.execute("UPDATE notes SET note=NULL WHERE id=?", (id_,))
        self.app.load(id_)
        self.assertIsNone(self.app.selected)


if __name__ == "__main__":
    unittest.main()
