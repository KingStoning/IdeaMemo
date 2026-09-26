"""Native Windows notebook; run directly or package using build.ps1."""
import os
import queue
import threading
import sys
import tempfile
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from storage import Notebook


class App:
    def __init__(self, root, notebook):
        self.root, self.book = root, notebook
        self.selected = None
        self.loading = False
        self.busy = False
        self.dirty = False
        self.timer = None
        self.results = queue.Queue()
        self.ids = []
        root.title("IdeaMemo · 灵感随手记")
        root.geometry("1060x720")
        root.minsize(780, 520)
        root.configure(bg="#f5f6f8")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f5f6f8")
        style.configure("TLabel", background="#f5f6f8", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 8))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Treeview", font=("Microsoft YaHei UI", 11), rowheight=58, borderwidth=0)
        style.map("Treeview", background=[("selected", "#dbe9e6")], foreground=[("selected", "#183c35")])

        header = ttk.Frame(root, padding=(24, 18))
        header.pack(fill="x")
        ttk.Label(header, text="IdeaMemo", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  留下此刻的想法", foreground="#65756f").pack(side="left", padx=12)
        self.settings_button = ttk.Button(header, text="同步设置", command=self.settings)
        self.settings_button.pack(side="right")
        self.sync_button = ttk.Button(header, text="立即同步", command=self.sync)
        self.sync_button.pack(side="right", padx=8)

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=24)
        sidebar = ttk.Frame(body, padding=(0, 0, 16, 0))
        body.add(sidebar, weight=1)
        self.query = tk.StringVar()
        search = ttk.Entry(sidebar, textvariable=self.query, font=("Microsoft YaHei UI", 11))
        search.pack(fill="x", ipady=8)
        ttk.Label(sidebar, text="搜索标题、正文或 #标签", foreground="#72817b").pack(anchor="w", pady=(5, 12))
        self.trash_view = tk.BooleanVar()
        self.trash_switch = ttk.Checkbutton(sidebar, text="显示回收站", variable=self.trash_view, command=self.switch_view)
        self.trash_switch.pack(anchor="w", pady=(0, 10))
        self.new_button = ttk.Button(sidebar, text="＋ 新建记录", command=self.new)
        self.new_button.pack(fill="x", pady=(0, 12))
        self.tree = ttk.Treeview(sidebar, show="tree", selectmode="browse")
        self.tree.column("#0", width=260, minwidth=160)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.count = ttk.Label(sidebar, text="")
        self.count.pack(anchor="w", pady=10)
        self.query.trace_add("write", lambda *_: self.refresh())

        editor = ttk.Frame(body, padding=(18, 4, 0, 0))
        body.add(editor, weight=3)
        self.title = tk.StringVar()
        self.title_entry = ttk.Entry(editor, textvariable=self.title, font=("Microsoft YaHei UI", 18))
        self.title_entry.pack(fill="x", ipady=10)
        self.date = ttk.Label(editor, text="新记录 · 自动保存在本机", foreground="#72817b")
        self.date.pack(anchor="w", pady=14)
        text_frame = ttk.Frame(editor)
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(text_frame, wrap="word", undo=True, font=("Microsoft YaHei UI", 12),
                            bg="#ffffff", fg="#243c35", insertbackground="#245e4d", relief="flat",
                            padx=20, pady=18, spacing1=4, spacing3=8)
        scrollbar = ttk.Scrollbar(text_frame, command=self.text.yview)
        scrollbar.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(fill="both", expand=True)
        self.text.bind("<<Modified>>", self.modified)
        self.title.trace_add("write", self.changed)
        actions = ttk.Frame(editor, padding=(0, 14))
        actions.pack(fill="x")
        self.collected = tk.BooleanVar()
        self.favorite = ttk.Checkbutton(actions, text="收藏", variable=self.collected, command=self.changed)
        self.favorite.pack(side="left")
        self.delete_button = ttk.Button(actions, text="移到回收站", command=self.delete)
        self.delete_button.pack(side="right")
        self.save_button = ttk.Button(actions, text="保存  Ctrl+S", command=self.save)
        self.save_button.pack(side="right", padx=8)
        self.status = tk.StringVar(value="离线可用 · 输入后自动保存；配置服务后即可双向同步")
        ttk.Label(root, textvariable=self.status, padding=(24, 12), foreground="#536c62").pack(fill="x")
        root.bind("<Control-s>", lambda _: self.save())
        root.bind("<Control-n>", lambda _: self.new())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.load(None)
        root.after(100, self.poll)
        root.after(3000, self.auto_sync)

    def refresh(self):
        self.loading = True
        self.tree.delete(*self.tree.get_children())
        rows = self.book.rows(self.query.get(), self.trash_view.get())
        for id_, note in rows:
            label = note["title"].strip() or note["content"].strip().split("\n")[0] or "无标题"
            date = datetime.fromtimestamp(note["updateTime"] / 1000).strftime("%m-%d %H:%M")
            self.tree.insert("", "end", iid=id_, text=("★ " if note["isCollected"] else "") + label[:35] + "\n" + date)
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
        self.count.configure(text=f"{len(rows)} 条记录")
        self.loading = False

    def load(self, id_):
        self.loading = True
        note = self.book.get(id_) if id_ else None
        self.selected = id_ if note else None
        self.title.set(note["title"] if note else "")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", note["content"] if note else "")
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.collected.set(note["isCollected"] if note else False)
        self.date.configure(text=datetime.fromtimestamp(note["updateTime"] / 1000).strftime("更新于 %Y-%m-%d %H:%M") if note else "新记录 · 支持 #标签 · 自动保存在本机")
        self.delete_button.configure(text="恢复记录" if note and note["isDeleted"] else "移到回收站")
        self.dirty = False
        self.loading = False

    def modified(self, *_):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.changed()

    def changed(self, *_):
        if self.loading or self.busy:
            return
        self.dirty = True
        if self.timer:
            self.root.after_cancel(self.timer)
        self.timer = self.root.after(600, self.save)

    def save(self):
        self.timer = None
        if not self.dirty or self.busy:
            return True
        title, content = self.title.get(), self.text.get("1.0", "end-1c")
        if not self.selected and not title.strip() and not content.strip():
            self.dirty = False
            return True
        try:
            self.selected = self.book.save(self.selected, title, content, self.collected.get())
            self.dirty = False
            self.refresh()
            self.status.set("已保存在本机 · " + datetime.now().strftime("%H:%M:%S"))
            return True
        except Exception as exc:
            self.status.set("保存失败：" + str(exc))
            return False

    def select(self, *_):
        if self.loading or self.busy:
            return
        selection = self.tree.selection()
        if selection and selection[0] != self.selected and self.save():
            self.load(selection[0])
            if self.tree.exists(selection[0]):
                self.tree.selection_set(selection[0])

    def new(self):
        if not self.busy and self.save():
            self.trash_view.set(False)
            self.load(None)
            self.refresh()
            self.title_entry.focus_set()

    def switch_view(self):
        if not self.busy and self.save():
            self.load(None)
            self.refresh()

    def delete(self):
        if self.busy or not self.selected or not self.save():
            return
        note = self.book.get(self.selected)
        if note and (note["isDeleted"] or messagebox.askyesno("移到回收站", "将这条记录移到回收站？可随时恢复，删除状态会同步到手机。", parent=self.root)):
            self.book.trash(self.selected, not note["isDeleted"])
            self.load(None)
            self.refresh()

    def settings(self):
        if self.busy:
            return
        win = tk.Toplevel(self.root)
        win.title("自部署同步")
        win.geometry("570x360")
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="两端填写相同地址和密钥，即可同步文字记录。").pack(anchor="w", pady=(0, 18))
        ttk.Label(frame, text="服务地址（公网请使用 HTTPS）").pack(anchor="w")
        url = tk.StringVar(value=self.book.setting("url"))
        ttk.Entry(frame, textvariable=url, width=58).pack(fill="x", pady=(5, 15), ipady=5)
        ttk.Label(frame, text="访问密钥").pack(anchor="w")
        token = tk.StringVar(value=self.book.setting("token"))
        ttk.Entry(frame, textvariable=token, show="•").pack(fill="x", pady=(5, 15), ipady=5)
        auto = tk.BooleanVar(value=self.book.setting("auto") == "True")
        ttk.Checkbutton(frame, text="应用打开时，每 60 秒自动同步", variable=auto).pack(anchor="w")

        def apply():
            try:
                self.book.configure(url.get(), token.get(), auto.get())
                win.destroy()
                self.sync()
            except Exception as exc:
                messagebox.showerror("设置未保存", str(exc), parent=win)

        ttk.Button(frame, text="保存并同步", command=apply).pack(anchor="e", pady=18)

    def sync(self):
        if self.busy or not self.save():
            return
        self.busy = True
        self.status.set("正在同步… 本地内容已保存")
        for widget in (self.title_entry, self.text, self.favorite, self.save_button, self.delete_button, self.new_button, self.sync_button, self.settings_button, self.trash_switch):
            widget.configure(state="disabled")

        def work():
            try:
                self.results.put((True, self.book.sync()))
            except Exception as exc:
                self.results.put((False, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            success, result = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for widget in (self.title_entry, self.text, self.favorite, self.save_button, self.delete_button, self.new_button, self.sync_button, self.settings_button, self.trash_switch):
                widget.configure(state="normal")
            if success:
                self.load(self.selected)
                self.refresh()
                self.status.set("同步完成 · " + datetime.now().strftime("%H:%M:%S") + (f" · {len(result)} 处冲突已保留，请检查冲突副本或恢复的记录" if result else ""))
            else:
                self.status.set("同步失败，本地记录已保留：" + result)
        self.root.after(100, self.poll)

    def auto_sync(self):
        if self.book.setting("auto") == "True" and not self.busy:
            self.sync()
        self.root.after(60000, self.auto_sync)

    def close(self):
        if self.busy:
            if messagebox.askyesno("正在同步", "本地内容已保存。现在退出，下次同步会继续处理，是否退出？", parent=self.root):
                self.root.destroy()
        elif self.save():
            self.root.destroy()


def main():
    if "--self-test" in sys.argv:
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            book = Notebook(Path(folder) / "test.sqlite3")
            app = App(root, book)
            app.title.set("打包验证")
            app.text.insert("1.0", "Unicode 笔记 #测试")
            root.update()
            assert app.save()
            assert book.get(app.selected)["content"] == "Unicode 笔记 #测试"
            root.destroy()
        return
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IdeaMemoDesktop"
    root = tk.Tk()
    App(root, Notebook(folder / "notes.sqlite3"))
    root.mainloop()


if __name__ == "__main__":
    main()
