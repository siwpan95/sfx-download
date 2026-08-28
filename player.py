"""增强版音频试听器 — 合并 player.py 与 musicplayer.py，并参考主流方案增强。

输入: 递归扫描统一音频库 crawler_core.SOUNDS_DIR（即 ./sounds）。

播放后端（参考 pygame.mixer.music 官方文档）:
  * 优先 pygame.mixer.music —— 支持 暂停/继续、进度拖动(set_pos)、音量(set_volume)、
    进度查询(get_pos)、播放结束自动下一首。
  * 缺失时回退 playsound3 —— 仅 播放/停止（无进度/音量/拖动）。

元数据（参考 mutagen 文档）:
  * 用 mutagen 读取时长与标签(title/artist)，无需播放即可完成；缺失时回退 pydub 探测时长。

新增能力（相对原两版）:
  * 暂停/继续、进度条拖动、音量滑块、当前曲目时长/标签显示
  * 播放列表 实时搜索/过滤、删除选中、清空
  * 循环(关/列表/单曲)、主题(深/浅)、快捷键、右键菜单、系统托盘(minimize)
所有可选依赖( pygame / mutagen / pystray / pydub / playsound3 )均做优雅降级。
"""

import os
import sys
import json
import shutil
import tempfile
import threading
import time

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
    HAS_PYGAME = True
except Exception:
    HAS_PYGAME = False

try:
    import mutagen
    HAS_MUTAGEN = True
except Exception:
    HAS_MUTAGEN = False

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except Exception:
    PYDUB_AVAILABLE = False

try:
    from playsound3 import playsound as _playsound
    HAS_PLAYSOUND = True
except Exception:
    _playsound = None
    HAS_PLAYSOUND = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

import tkinter as tk
from tkinter import ttk, messagebox

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
from crawler_core import SOUNDS_DIR  # 统一音频库根目录（./sounds）

CONFIG_FILE = os.path.expanduser("~/.audiocrawler_player_config.json")
TEMP_DIR = os.path.join(tempfile.gettempdir(), "audio_preview_converted")
AUDIO_EXTENSIONS = (
    '.mp3', '.mp2', '.wav', '.wave', '.ogg', '.oga', '.flac',
    '.mid', '.midi', '.mod', '.xm', '.it', '.s3m',
    '.669', '.med', '.mtm', '.okt', '.stm', '.ult', '.far', '.amf',
    '.aiff', '.aif', '.aifc', '.au', '.snd', '.voc',
    '.m4a', '.aac', '.mp4', '.m4b', '.wma', '.opus', '.spx'
)


def _fmt(sec):
    try:
        sec = int(sec)
    except Exception:
        return "--:--"
    if sec < 0:
        sec = 0
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {"theme": "dark", "volume": 0.8}


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False)
    except Exception:
        pass


class EnhancedPlayer:
    def __init__(self, scan_root):
        self.scan_root = scan_root
        self.config = load_config()
        self.current_theme = self.config.get("theme", "dark")
        self.volume = max(0.0, min(1.0, float(self.config.get("volume", 0.8))))

        # 播放状态
        self.playlist = []          # 全部音频文件绝对路径
        self.view = []              # 当前过滤后，playlist 的下标列表（与列表框一一对应）
        self.current_index = -1
        self.playing = False
        self.paused = False
        self.loop_mode = "none"     # none / all / one
        self._durations = {}        # path -> 秒
        self._updating = False      # 进度条程序化更新中（避免触发拖动回调）
        self._filter = ""
        self._conv_map = {}         # 原始 path -> 转换后的临时 wav
        self._play_thread = None

        self.root = tk.Tk()
        self.root.title("🔊 音频试听器 (增强版)")
        self.root.geometry("560x560")
        self.root.resizable(True, True)
        self._build_ui()
        self._apply_theme()
        self._rebuild_playlist(self._scan_dir())
        self.root.after(5000, self._auto_refresh)
        self.root.after(250, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ───────────────────────── UI 构建 ─────────────────────────
    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="🔊 音频试听器 (增强版)", font=("Arial", 15, "bold")).pack(side=tk.LEFT)
        ttk.Label(toolbar, text=f" {self.scan_root}", foreground="gray",
                  font=("Arial", 8)).pack(side=tk.LEFT, padx=6)
        self.theme_btn = ttk.Button(toolbar, text="🌙" if self.current_theme == "dark" else "☀️",
                                    command=self._toggle_theme, width=3)
        self.theme_btn.pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="⌨️", command=self._show_shortcuts, width=3).pack(side=tk.RIGHT, padx=2)
        if HAS_TRAY:
            ttk.Button(toolbar, text="➖", command=self._minimize_to_tray, width=3).pack(side=tk.RIGHT, padx=2)

        # 搜索过滤
        search_frame = ttk.Frame(self.root, padding=(10, 2))
        search_frame.pack(fill=tk.X)
        ttk.Label(search_frame, text="🔍").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.count_label = ttk.Label(search_frame, text="0 个", foreground="gray")
        self.count_label.pack(side=tk.RIGHT, padx=4)

        # 当前文件 + 时长
        file_frame = ttk.Frame(self.root, padding=(10, 4))
        file_frame.pack(fill=tk.X)
        ttk.Label(file_frame, text="当前:").pack(side=tk.LEFT)
        self.file_label = ttk.Label(file_frame, text="未选择文件", foreground="#888888")
        self.file_label.pack(side=tk.LEFT, padx=10)
        self.meta_label = ttk.Label(file_frame, text="", foreground="gray", font=("Arial", 8))
        self.meta_label.pack(side=tk.RIGHT)

        # 进度条 + 时间
        prog_frame = ttk.Frame(self.root, padding=(10, 0))
        prog_frame.pack(fill=tk.X)
        self.progress = ttk.Scale(prog_frame, from_=0, to=1, value=0,
                                  command=self._on_seek, state="disabled")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.time_label = ttk.Label(prog_frame, text="00:00 / 00:00", foreground="gray", width=14)
        self.time_label.pack(side=tk.RIGHT)

        # 播放列表
        list_frame = ttk.Frame(self.root, padding=(10, 6))
        list_frame.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(list_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, bg="#3a3a3a", fg="white",
                                  selectbackground="#5a5a5a", selectforeground="white",
                                  font=("Arial", 9), height=12, yscrollcommand=sb.set,
                                  activestyle="none")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind('<<ListboxSelect>>', self._on_select)
        self.listbox.bind('<Double-Button-1>', self._on_double)
        self.listbox.bind('<Button-3>', self._on_right_click)

        # 列表操作按钮
        btn_row = ttk.Frame(self.root, padding=(10, 2))
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="🔄 刷新", command=self._manual_refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑️ 清空", command=self._clear_playlist).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑️ 删除选中", command=self._remove_selected).pack(side=tk.LEFT, padx=2)

        # 播放控制
        ctrl = ttk.Frame(self.root, padding=(10, 6))
        ctrl.pack(fill=tk.X)
        self.loop_btn = ttk.Button(ctrl, text="🔁 列表", command=self._toggle_loop, width=9)
        self.loop_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="⏮", command=self._prev, width=4).pack(side=tk.LEFT, padx=2)
        self.play_btn = ttk.Button(ctrl, text="▶", command=self._on_play_click,
                                   state="disabled", width=5)
        self.play_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="⏭", command=self._next, width=4).pack(side=tk.LEFT, padx=2)
        # 音量
        ttk.Label(ctrl, text="🔊").pack(side=tk.LEFT, padx=(12, 2))
        self.volume_var = tk.DoubleVar(value=self.volume)
        self.volume_scale = ttk.Scale(ctrl, from_=0, to=1, value=self.volume,
                                      command=self._on_volume, length=90,
                                      state="normal" if HAS_PYGAME else "disabled")
        self.volume_scale.pack(side=tk.LEFT, padx=2)

        # 状态栏
        status_frame = ttk.Frame(self.root, padding=(10, 4))
        status_frame.pack(fill=tk.X)
        self.status_label = ttk.Label(status_frame, text="正在扫描...", font=("Arial", 9))
        self.status_label.pack(side=tk.LEFT)
        self.mode_label = ttk.Label(status_frame, text=" ➡️ ", font=("Arial", 9))
        self.mode_label.pack(side=tk.RIGHT)

        # 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="▶ 播放", command=lambda: self._goto(self._sel_index(), play=True))
        self.menu.add_command(label="🗑️ 删除", command=self._remove_selected)
        self.menu.add_separator()
        self.menu.add_command(label="🗑️ 清空列表", command=self._clear_playlist)

        # 快捷键
        self.root.bind("<space>", lambda e: self._on_play_click())
        self.root.bind("<Right>", lambda e: self._next())
        self.root.bind("<Left>", lambda e: self._prev())
        self.root.bind("l", lambda e: self._toggle_loop())
        self.root.bind("t", lambda e: self._toggle_theme())
        self.root.bind("s", lambda e: self._stop_playback())
        self.root.bind("<Delete>", lambda e: self._remove_selected())
        self.root.bind("/", lambda e: self._focus_search())

    # ───────────────────────── 扫描 / 列表 ─────────────────────────
    def _scan_dir(self):
        files = []
        if os.path.exists(self.scan_root):
            for r, _d, fs in os.walk(self.scan_root):
                for f in fs:
                    if f.lower().endswith(AUDIO_EXTENSIONS) and not f.startswith('._'):
                        files.append(os.path.join(r, f))
        return sorted(files, key=lambda p: os.path.basename(p).lower())

    def _rebuild_playlist(self, files):
        self.playlist = []
        skipped = 0
        for f in files:
            if f not in self.playlist and os.path.isfile(f):
                self.playlist.append(f)
            else:
                skipped += 1
        if not self.playlist:
            self.current_index = -1
            self.file_label.config(text="未找到可播放文件")
            self.play_btn.config(state="disabled")
            self.status_label.config(text=f"目录中无可播放音频（{skipped} 个跳过）")
        else:
            msg = f"共 {len(self.playlist)} 个"
            if skipped:
                msg += f"（跳过 {skipped} 个）"
            self.status_label.config(text=msg)
            if self.current_index < 0:
                self.current_index = 0
        self._apply_filter(keep_selection=True)

    def _apply_filter(self, keep_selection=False):
        self._filter = (self.search_var.get() or "").strip().lower()
        self.view = [i for i, p in enumerate(self.playlist)
                     if not self._filter or self._filter in os.path.basename(p).lower()]
        self.listbox.delete(0, tk.END)
        for i in self.view:
            self.listbox.insert(tk.END, os.path.basename(self.playlist[i]))
        self.count_label.config(text=f"{len(self.view)}/{len(self.playlist)}")
        # 高亮当前曲目（若仍在视图中）
        if 0 <= self.current_index < len(self.playlist) and self.current_index in self.view:
            pos = self.view.index(self.current_index)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(pos)
            self.listbox.see(pos)

    def _manual_refresh(self):
        self._rebuild_playlist(self._scan_dir())
        self.status_label.config(text=f"已刷新，共 {len(self.playlist)} 个")

    def _auto_refresh(self):
        if self.root.winfo_exists():
            new = self._scan_dir()
            if set(new) != set(self.playlist):
                self._rebuild_playlist(new)
            self.root.after(5000, self._auto_refresh)

    def _sel_index(self):
        sel = self.listbox.curselection()
        if sel:
            return self.view[sel[0]]
        return self.current_index

    # ───────────────────────── 时长 / 元数据 ─────────────────────────
    def _duration(self, path):
        if path in self._durations:
            return self._durations[path]
        dur = 0.0
        if HAS_MUTAGEN:
            try:
                audio = mutagen.File(path, easy=True)
                if audio and getattr(audio, "info", None) and hasattr(audio.info, "length"):
                    dur = float(audio.info.length)
            except Exception:
                dur = 0.0
        if not dur and PYDUB_AVAILABLE:
            try:
                seg = AudioSegment.from_file(path)
                dur = seg.duration_seconds
            except Exception:
                dur = 0.0
        self._durations[path] = dur
        return dur

    def _tags(self, path):
        if HAS_MUTAGEN:
            try:
                audio = mutagen.File(path, easy=True)
                if audio:
                    title = (audio.get("title") or [""])[0] if audio.get("title") else ""
                    artist = (audio.get("artist") or [""])[0] if audio.get("artist") else ""
                    if title:
                        return title, artist
            except Exception:
                pass
        return os.path.splitext(os.path.basename(path))[0], ""

    # ───────────────────────── 选择 / 跳转 ─────────────────────────
    def _on_select(self, _ev):
        self.current_index = self._sel_index()
        self._refresh_current_info()

    def _on_double(self, _ev):
        self._goto(self._sel_index(), play=True)

    def _on_right_click(self, ev):
        sel = self.listbox.curselection()
        if sel:
            self.current_index = self._sel_index()
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(sel[0])
        self.menu.tk_popup(ev.x_root, ev.y_root)

    def _goto(self, index, play=False):
        if not (0 <= index < len(self.playlist)):
            return
        self.current_index = index
        self._refresh_current_info()
        if play:
            self._start_playback()
        elif 0 <= self.current_index < len(self.playlist) and self.current_index in self.view:
            pos = self.view.index(self.current_index)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(pos)
            self.listbox.see(pos)

    def _refresh_current_info(self):
        if not (0 <= self.current_index < len(self.playlist)):
            self.file_label.config(text="未选择文件")
            self.meta_label.config(text="")
            return
        path = self.playlist[self.current_index]
        title, artist = self._tags(path)
        dur = self._duration(path)
        self.file_label.config(text=title or os.path.basename(path), foreground=self._fg())
        extra = []
        if artist:
            extra.append(f"artist: {artist}")
        extra.append(f"[{_fmt(dur)}]")
        rel = os.path.relpath(path, self.scan_root)
        self.meta_label.config(text=f"{' · '.join(extra)}  ·  {rel}")
        self._update_progress(0, dur)

    # ───────────────────────── 播放控制 ─────────────────────────
    def _on_play_click(self):
        if not self.playlist:
            self.status_label.config(text="没有可播放的文件")
            return
        if self.playing:
            self._toggle_pause()
        else:
            self._start_playback()

    def _start_playback(self):
        if not (0 <= self.current_index < len(self.playlist)):
            return
        path = self.playlist[self.current_index]
        if HAS_PYGAME:
            try:
                pygame.mixer.music.load(path)
            except Exception:
                conv = self._convert(path) if PYDUB_AVAILABLE else None
                if not conv:
                    self.status_label.config(text=f"无法加载: {os.path.basename(path)}")
                    return
                path = conv
            try:
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
            except Exception as e:
                self.status_label.config(text=f"播放失败: {e}")
                return
            self.playing = True
            self.paused = False
            self._set_play_btn("⏸")
            self.status_label.config(text=f"▶ {os.path.basename(path)}")
            if 0 <= self.current_index < len(self.playlist) and self.current_index in self.view:
                pos = self.view.index(self.current_index)
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(pos)
                self.listbox.see(pos)
        elif HAS_PLAYSOUND:
            self._play_thread = threading.Thread(target=self._playsound_thread, args=(path,), daemon=True)
            self._play_thread.start()
            self.playing = True
            self.paused = False
            self._set_play_btn("⏹")
            self.status_label.config(text=f"▶ {os.path.basename(path)}")
        else:
            self.status_label.config(text="缺少播放后端 (安装 pygame 或 playsound3)")

    def _playsound_thread(self, path):
        try:
            _playsound(path)
        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text=f"播放失败: {e}"))
        if self.playing and not self.paused:
            self.root.after(0, self._on_end)

    def _toggle_pause(self):
        if not HAS_PYGAME or not self.playing:
            return
        if self.paused:
            pygame.mixer.music.unpause()
            self.paused = False
            self._set_play_btn("⏸")
            self.status_label.config(text=f"▶ {os.path.basename(self.playlist[self.current_index])}")
        else:
            pygame.mixer.music.pause()
            self.paused = True
            self._set_play_btn("▶")
            self.status_label.config(text="⏸ 已暂停")

    def _stop_playback(self):
        if HAS_PYGAME and self.playing:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.playing = False
        self.paused = False
        self._set_play_btn("▶")
        self._update_progress(0, self._duration(self.playlist[self.current_index]) if 0 <= self.current_index < len(self.playlist) else 0)
        self.status_label.config(text="已停止")

    def _on_end(self):
        if self.loop_mode == "one":
            self._start_playback()
            return
        if self.loop_mode == "all":
            self._next(auto=True)
        elif self.current_index < len(self.playlist) - 1:
            self._next(auto=True)
        else:
            self._stop_playback()
            self.status_label.config(text="播放完成")

    def _next(self, auto=False):
        if not self.playlist:
            return
        if self.current_index < len(self.playlist) - 1:
            self._goto(self.current_index + 1, play=self.playing or auto)
        elif self.loop_mode == "all":
            self._goto(0, play=self.playing or auto)
        else:
            if auto:
                self._stop_playback()
                self.status_label.config(text="播放完成")
            else:
                self._goto(0, play=self.playing)

    def _prev(self):
        if not self.playlist:
            return
        if self.current_index > 0:
            self._goto(self.current_index - 1, play=self.playing)
        else:
            self._goto(len(self.playlist) - 1, play=self.playing)

    # ───────────────────────── 进度 / 音量 / 拖动 ─────────────────────────
    def _tick(self):
        if self.root.winfo_exists():
            if self.playing and not self.paused and HAS_PYGAME and 0 <= self.current_index < len(self.playlist):
                try:
                    pos_ms = pygame.mixer.music.get_pos()
                except Exception:
                    pos_ms = -1
                if pos_ms >= 0:
                    elapsed = pos_ms / 1000.0
                    dur = self._duration(self.playlist[self.current_index])
                    if dur and elapsed >= dur - 0.3:
                        self._on_end()
                    else:
                        self._update_progress(elapsed, dur)
            self.root.after(250, self._tick)

    def _update_progress(self, elapsed, dur):
        self._updating = True
        if dur and dur > 0:
            self.progress.config(to=dur, state="normal" if HAS_PYGAME else "disabled")
            self.progress.set(min(elapsed, dur))
            self.time_label.config(text=f"{_fmt(elapsed)} / {_fmt(dur)}")
        else:
            self.progress.config(to=1, state="disabled")
            self.progress.set(0)
            self.time_label.config(text=f"{_fmt(elapsed)} / --:--")
        self._updating = False

    def _on_seek(self, val):
        if self._updating:
            return
        if not (HAS_PYGAME and self.playing and 0 <= self.current_index < len(self.playlist)):
            return
        try:
            pygame.mixer.music.set_pos(float(val))
        except Exception:
            pass

    def _on_volume(self, val):
        self.volume = float(val)
        if HAS_PYGAME:
            try:
                pygame.mixer.music.set_volume(self.volume)
            except Exception:
                pass
        self.config["volume"] = self.volume
        save_config(self.config)

    # ───────────────────────── 列表编辑 ─────────────────────────
    def _remove_selected(self):
        idx = self._sel_index()
        if idx < 0:
            return
        removed = self.playlist.pop(idx)
        self._durations.pop(removed, None)
        self._conv_map.pop(removed, None)
        if self.current_index == idx:
            self.current_index = min(idx, len(self.playlist) - 1) if self.playlist else -1
            if self.current_index >= 0:
                self._refresh_current_info()
            else:
                self.file_label.config(text="未选择文件")
                self._set_play_btn("▶", disabled=True)
        elif self.current_index > idx:
            self.current_index -= 1
        self._apply_filter()

    def _clear_playlist(self):
        self._stop_playback()
        self.playlist = []
        self.view = []
        self.current_index = -1
        self.listbox.delete(0, tk.END)
        self.file_label.config(text="未选择文件")
        self._set_play_btn("▶", disabled=True)
        self._update_progress(0, 0)
        self.status_label.config(text="播放列表已清空")

    # ───────────────────────── 循环 / 主题 / 托盘 ─────────────────────────
    def _toggle_loop(self):
        modes = {"none": ("🔁 列表", "all"), "all": ("🔂 单曲", "one"), "one": ("➡️ 关闭", "none")}
        icons = {"none": "➡️", "all": "🔁", "one": "🔂"}
        btn_text, self.loop_mode = modes[self.loop_mode]
        self.loop_btn.config(text=btn_text)
        self.mode_label.config(text=f" {icons[self.loop_mode]} ")
        self.status_label.config(text=f"循环模式: {btn_text}")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.config["theme"] = self.current_theme
        save_config(self.config)
        self._apply_theme()
        self.theme_btn.config(text="🌙" if self.current_theme == "dark" else "☀️")

    def _apply_theme(self):
        themes = {
            "dark": {"bg": "#2b2b2b", "fg": "white", "select": "#5a5a5a", "button": "#4a4a4a", "listbg": "#3a3a3a"},
            "light": {"bg": "#f0f0f0", "fg": "black", "select": "#c0c0c0", "button": "#e0e0e0", "listbg": "#ffffff"},
        }
        c = themes[self.current_theme]
        self.root.configure(bg=c["bg"])
        self._fg = lambda: c["fg"]
        for w in self.root.winfo_children():
            try:
                w.configure(bg=c["bg"])
            except Exception:
                pass
        self.listbox.configure(bg=c["listbg"], fg=c["fg"], selectbackground=c["select"], selectforeground=c["fg"])

    def _fg(self):
        return "#ffffff" if self.current_theme == "dark" else "#000000"

    def _set_play_btn(self, text, disabled=False):
        self.play_btn.config(text=text, state="disabled" if disabled else "normal")

    def _focus_search(self):
        for w in self.root.winfo_children():
            if isinstance(w, ttk.Frame):
                for child in w.winfo_children():
                    if isinstance(child, ttk.Entry):
                        child.focus_set()
                        return

    def _show_shortcuts(self):
        win = tk.Toplevel(self.root)
        win.title("快捷键")
        win.geometry("300x260")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        f = ttk.Frame(win, padding="20")
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text="⌨️ 快捷键", font=("Arial", 14, "bold")).pack(pady=(0, 15))
        items = [
            ("空格", "播放 / 暂停"),
            ("→", "下一首"),
            ("←", "上一首"),
            ("L", "切换循环模式"),
            ("T", "切换主题"),
            ("S", "停止"),
            ("Delete", "删除选中"),
            ("/", "聚焦搜索框"),
            ("双击", "播放选中文件"),
            ("右键", "操作菜单"),
        ]
        for k, d in items:
            row = ttk.Frame(f)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=k, font=("Arial", 10, "bold"), width=12).pack(side=tk.LEFT)
            ttk.Label(row, text=d).pack(side=tk.LEFT)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=10)

    def _minimize_to_tray(self):
        if not HAS_TRAY:
            return
        try:
            self.root.withdraw()
            self._tray_icon.run_detached()
        except Exception:
            pass

    def _build_tray(self):
        if not HAS_TRAY:
            return

        def _img():
            im = Image.new('RGB', (64, 64), color='#2b2b2b')
            d = ImageDraw.Draw(im)
            d.ellipse((12, 12, 52, 52), fill='#4a9eff')
            d.polygon([(26, 22), (26, 42), (42, 32)], fill='white')
            return im

        def _on_click(icon, event):
            self.root.after(0, self.root.deiconify)

        def _on_exit(icon, event):
            self.root.after(0, self._on_closing)

        self._tray_icon = pystray.Icon(
            "audio_player", _img(), "音频试听器",
            pystray.Menu(pystray.MenuItem("显示", _on_click), pystray.MenuItem("退出", _on_exit)),
        )

    def _on_closing(self):
        self.playing = False
        self.paused = False
        if HAS_PYGAME:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self._cleanup_temp()
        if HAS_TRAY:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    # ───────────────────────── 格式转换（pygame 不支持的格式） ─────────────────────────
    def _convert(self, file_path):
        if file_path in self._conv_map and os.path.isfile(self._conv_map[file_path]):
            return self._conv_map[file_path]
        if not PYDUB_AVAILABLE:
            return None
        os.makedirs(TEMP_DIR, exist_ok=True)
        import hashlib
        h = hashlib.md5(file_path.encode()).hexdigest()[:8]
        safe = os.path.splitext(os.path.basename(file_path))[0]
        tmp = os.path.join(TEMP_DIR, f"{h}_{safe}.wav")
        if os.path.exists(tmp):
            self._conv_map[file_path] = tmp
            return tmp
        try:
            audio = AudioSegment.from_file(file_path)
            audio = audio.set_frame_rate(44100).set_channels(2).set_sample_width(2)
            audio.export(tmp, format="wav")
            self._conv_map[file_path] = tmp
            return tmp
        except Exception:
            return None

    def _cleanup_temp(self):
        if os.path.exists(TEMP_DIR):
            try:
                shutil.rmtree(TEMP_DIR)
            except Exception:
                pass

    def run(self):
        if HAS_TRAY:
            self._build_tray()
        self.root.mainloop()


def main():
    scan_root = sys.argv[1] if len(sys.argv) > 1 else SOUNDS_DIR
    EnhancedPlayer(scan_root).run()


if __name__ == "__main__":
    main()
