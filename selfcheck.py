#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sfx-download 无头自检：音频直链提取 + 文件名安全化 + 数据源注册表筛选。

不 import ``gui.py`` / ``player.py``（tkinter/pygame 窗口）。``crawler_core`` 顶层只
定义注册表与纯函数，``sfx_utils`` 的 Playwright 只在真正抓取页面时才用，因此本自检
不联网、不启动浏览器、不写 ``sounds/``。

验证点
------
1. ``extract_audio_links``：只抽 mp3/wav/ogg 等音频直链、忽略非音频与相对链接、
   按出现顺序去重；``abs_url`` 处理 http / 协议相对 / 相对路径；
2. ``_safe_filename``：非法字符清除、空白转下划线、空标签回退 ``源名_id``、重名加序号；
3. 注册表：``all_categories`` / ``all_licenses`` 非空，``select_crawlers`` 的分类
   过滤只返回匹配分类，``active_source_names`` 与注册结果一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True          # 不在项目里留 .pyc
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class CheckError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise CheckError(message)


def check_links(crawler_core):
    html = """
    <audio src="https://a.example/one.mp3"></audio>
    <a href='https://a.example/one.mp3'>duplicate</a>
    https://b.example/two.WAV
    https://c.example/three.ogg?token=1
    https://d.example/not-audio.txt
    //e.example/relative.mp3
    """
    links = crawler_core.extract_audio_links(html)
    check(links == [
        "https://a.example/one.mp3",
        "https://b.example/two.WAV",
        "https://c.example/three.ogg",
    ], f"音频直链提取/去重结果错误：{links!r}")

    check(crawler_core.abs_url("https://x.example/a.mp3", "https://base.example/") ==
          "https://x.example/a.mp3", "绝对 URL 被改写")
    check(crawler_core.abs_url("//cdn.example/a.mp3", "https://base.example/") ==
          "https://cdn.example/a.mp3", "协议相对 URL 解析错误")
    check(crawler_core.abs_url("a/b.mp3", "https://base.example/dir/") ==
          "https://base.example/dir/a/b.mp3", "相对 URL 解析错误")


def check_filenames(crawler_core):
    safe = crawler_core._safe_filename
    check(safe("Hello World! 你好", "Mixkit", 3) == "hello_world",
          "文件名安全化结果错误")
    check(safe("Hello World! 你好", "Mixkit", 3) == "hello_world_1",
          "同名文件未追加序号去重")
    check(safe("", "Mixkit", 7) == "mixkit_7",
          "空标签未回退为 <源名>_<id>")
    check(safe("///", "src", 2) == "src_2",
          "全非法字符标签未回退为 <源名>_<id>")


def check_registry(crawler_core):
    categories = crawler_core.all_categories()
    check(len(categories) >= 2, f"分类表过少：{categories!r}")
    licenses = crawler_core.all_licenses()
    check(len(licenses) >= 2, f"许可表过少：{licenses!r}")

    music = crawler_core.select_crawlers(categories=["music"])
    check(music, "music 分类没有命中任何数据源")
    check(all(crawler["category"].lower() == "music" for crawler in music),
          "分类过滤返回了非 music 数据源")

    crawler_core.register_crawler("SelfCheckOnly", lambda: (None, None),
                                  category="selfcheck", license="CC0")
    selected = crawler_core.select_crawlers(categories=["selfcheck"])
    check([crawler["name"] for crawler in selected] == ["SelfCheckOnly"],
          f"分类过滤结果错误：{[c['name'] for c in selected]!r}")
    check(crawler_core.active_source_names(categories=["selfcheck"]) == ["SelfCheckOnly"],
          "active_source_names 与 select_crawlers 结果不一致")
    return len(categories), len(licenses)


def main():
    import crawler_core

    check_links(crawler_core)
    check_filenames(crawler_core)
    category_count, license_count = check_registry(crawler_core)
    print(
        "selfcheck ok: audio link extraction (dedupe/rel->abs) + filename sanitize "
        "(dedupe suffix, fallback) + registry filtering (%d categories, %d licenses)"
        % (category_count, license_count)
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 自检入口需要把任何失败转成 FAIL + 退出码 1
        print(f"FAIL: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
