"""通用免费公版音频爬取程序 — Tkinter 图形界面 (基于 sfx GUI 改造)"""

import os
import sys
import time
import signal
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from crawler_core import (
    list_crawlers, crawl_one, all_categories, all_licenses, SOUNDS_DIR,
)
import sfx_utils


class AudioCrawlerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("通用免费公版音频爬取程序")
        self.root.geometry("760x640")
        self.root.resizable(True, True)
        self._running = False
        self._stop_flag = False

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        signal.signal(signal.SIGINT, self._on_sigint)

        self._src_vars = {}
        self._cat_vars = {}
        self._lic_vars = {}
        self._build_ui()

    def _on_close(self):
        self._stop_flag = True
        self._running = False
        try:
            sfx_utils.close_pw()
        except Exception:
            pass
        self.root.destroy()

    def _on_sigint(self, signum, frame):
        self._on_close()

    def _build_ui(self):
        root = self.root

        top_frame = ttk.Frame(root, padding=8)
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="通用免费公版音频爬取程序",
                  font=("", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(top_frame, text=f" 保存至: {SOUNDS_DIR}", foreground="gray").pack(side=tk.RIGHT)

        ctrl_frame = ttk.Frame(root, padding=(8, 4))
        ctrl_frame.pack(fill=tk.X)

        ttk.Label(ctrl_frame, text="下载数量:").pack(side=tk.LEFT)
        self._num_var = tk.IntVar(value=10)
        ttk.Spinbox(ctrl_frame, from_=1, to=500, width=6,
                    textvariable=self._num_var).pack(side=tk.LEFT, padx=4)

        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Label(ctrl_frame, text="保存路径:").pack(side=tk.LEFT)
        self._path_var = tk.StringVar(value=SOUNDS_DIR)
        ttk.Entry(ctrl_frame, textvariable=self._path_var, width=40).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl_frame, text="浏览...", command=self._browse_dir, width=7).pack(side=tk.LEFT)

        # ── 过滤区：分类 + 许可 ──
        filter_frame = ttk.Frame(root, padding=(8, 2))
        filter_frame.pack(fill=tk.X, padx=4)

        cat_frame = ttk.LabelFrame(filter_frame, text="分类 (全选/取消)", padding=4)
        cat_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        cat_frame.bind("<Button-1>", lambda e: self._toggle_frame(cat_frame, self._cat_vars))
        cat_inner = ttk.Frame(cat_frame)
        cat_inner.pack(fill=tk.X)
        for i, cat in enumerate(all_categories()):
            v = tk.BooleanVar(value=True)
            self._cat_vars[cat] = v
            ttk.Checkbutton(cat_inner, text=cat, variable=v).grid(
                row=0, column=i, sticky=tk.W, padx=6, pady=2)

        lic_frame = ttk.LabelFrame(filter_frame, text="许可 (全选/取消)", padding=4)
        lic_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        lic_frame.bind("<Button-1>", lambda e: self._toggle_frame(lic_frame, self._lic_vars))
        lic_inner = ttk.Frame(lic_frame)
        lic_inner.pack(fill=tk.X)
        for i, lic in enumerate(all_licenses()):
            v = tk.BooleanVar(value=True)
            self._lic_vars[lic] = v
            ttk.Checkbutton(lic_inner, text=lic, variable=v).grid(
                row=0, column=i, sticky=tk.W, padx=6, pady=2)

        # ── 数据源勾选 ──
        src_frame = ttk.LabelFrame(root, text="数据源 (全选/取消)", padding=6)
        src_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        src_frame.bind("<Button-1>", lambda e: self._toggle_frame(src_frame, self._src_vars))
        inner = ttk.Frame(src_frame)
        inner.pack(fill=tk.BOTH, expand=True)

        cols = 2
        for i, c in enumerate(list_crawlers()):
            r, col = divmod(i, cols)
            v = tk.BooleanVar(value=True)
            self._src_vars[c["name"]] = v
            label = f"{c['name']}  [{c.get('category','all')}]  {c.get('license','') or ''}"
            ttk.Checkbutton(inner, text=label, variable=v).grid(
                row=r, column=col, sticky=tk.W, padx=8, pady=1, ipadx=4)

        # ── 按钮 ──
        btn_frame = ttk.Frame(root, padding=8)
        btn_frame.pack(fill=tk.X)
        self._start_btn = ttk.Button(btn_frame, text="▶ 开始爬取",
                                     command=self._start, width=14)
        self._start_btn.pack(side=tk.LEFT, padx=4)
        self._stop_btn = ttk.Button(btn_frame, text="⏹ 停止",
                                    command=self._stop, width=10, state=tk.DISABLED)
        self._stop_btn.pack(side=tk.LEFT, padx=4)
        self._open_btn = ttk.Button(btn_frame, text="📂 打开文件夹",
                                    command=self._open_folder, width=12)
        self._open_btn.pack(side=tk.RIGHT, padx=4)

        # ── 日志 ──
        log_frame = ttk.LabelFrame(root, text="日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._log = scrolledtext.ScrolledText(
            log_frame, height=12, state=tk.DISABLED,
            font=("Consolas", 9), wrap=tk.WORD)
        self._log.pack(fill=tk.BOTH, expand=True)
        self._log.tag_config("ok", foreground="green")
        self._log.tag_config("err", foreground="red")
        self._log.tag_config("warn", foreground="orange")

    def _toggle_frame(self, frame, var_map):
        # 只有点在标题栏空白处才触发；避免误触子控件
        all_on = all(v.get() for v in var_map.values())
        for v in var_map.values():
            v.set(not all_on)

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self._path_var.get(), mustexist=False)
        if d:
            self._path_var.set(d)

    def _open_folder(self):
        p = self._path_var.get()
        if os.path.isdir(p):
            os.startfile(p) if sys.platform == "win32" else None

    def _log_msg(self, msg, tag=None):
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, f"{msg}\n", tag or "")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _build_active(self):
        """根据勾选的分类/许可/源计算生效的抓取器列表"""
        active = []
        for c in list_crawlers():
            cat = (c.get("category") or "all")
            lic = (c.get("license") or "")
            if not self._src_vars.get(c["name"], tk.BooleanVar()).get():
                continue
            if not self._cat_vars.get(cat, tk.BooleanVar(value=True)).get():
                continue
            if lic and not self._lic_vars.get(lic, tk.BooleanVar(value=True)).get():
                continue
            active.append(c)
        return active

    def _start(self):
        active = self._build_active()
        if not active:
            messagebox.showwarning("提示", "请至少选择一个数据源")
            return

        target = max(1, self._num_var.get())
        out_dir = self._path_var.get()
        os.makedirs(out_dir, exist_ok=True)

        self._running = True
        self._stop_flag = False
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)

        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

        self._log_msg(f"目标: {target} 个 | 目录: {out_dir}")
        self._log_msg(f"数据源: {len(active)} 个\n")

        t = threading.Thread(target=self._run, args=(active, target, out_dir), daemon=True)
        t.start()

    def _stop(self):
        self._stop_flag = True
        self._log_msg("\n⏹ 正在停止...", "warn")

    def _run(self, active, target, out_dir):
        ok = 0
        fail = 0
        for i in range(target):
            if self._stop_flag:
                break
            success, _ = crawl_one(i, active, out_dir)
            if success:
                ok += 1
                self.root.after(0, lambda n=i + 1: self._log_msg(f"  [{n}] ✓ 成功", "ok"))
            else:
                fail += 1
                self.root.after(0, lambda n=i + 1: self._log_msg(f"  [{n}] ✗ 失败", "err"))
            time.sleep(0.3)

        try:
            sfx_utils.close_pw()
        except Exception:
            pass

        total = ok + fail
        self.root.after(0, lambda: self._log_msg(
            f"\n{'='*40}\n完成! 成功 {ok}/{total} 个, 失败 {fail} 个",
            "ok" if fail == 0 else "warn"))
        self.root.after(0, self._on_done)

    def _on_done(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)

    def run(self):
        self.root.mainloop()


def main():
    app = AudioCrawlerGUI()
    app.run()


if __name__ == "__main__":
    main()
