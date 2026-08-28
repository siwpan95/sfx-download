"""通用免费公版音频爬取程序 — 命令行入口。

用法示例:
    # 爬取 10 个任意公版音频（默认目录 ./audio）
    python crawl.py -n 10

    # 只爬音乐类，且许可含 Public Domain
    python crawl.py -n 20 -c music -l "Public Domain"

    # 只要 LibriVox 有声书
    python crawl.py -n 5 --only LibriVox

    # 排除需要 Playwright 的源（纯 HTTP 源更快更稳）
    python crawl.py -n 10 --exclude Pixabay,Zapsplat,Mixkit

    # 列出所有数据源 / 分类 / 许可
    python crawl.py --list
    python crawl.py --list-categories
    python crawl.py --list-licenses
"""

import argparse
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from crawler_core import (
    run_crawl, list_crawlers, all_categories, all_licenses,
    select_crawlers,
)


def _show_list():
    crawlers = list_crawlers()
    print(f"共 {len(crawlers)} 个数据源:\n")
    print(f"  {'名称':<20s} {'分类':<12s} 许可")
    print(f"  {'-'*20} {'-'*12} {'-'*30}")
    for c in crawlers:
        print(f"  {c['name']:<20s} {(c.get('category') or 'all'):<12s} {c.get('license') or '-'}")


def main():
    p = argparse.ArgumentParser(
        description="通用免费公版音频爬取程序 (基于 sfx 改造)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--count", type=int, default=10, help="下载数量 (默认 10)")
    p.add_argument("-o", "--out", default=None, help="输出目录 (默认 ./audio)")
    p.add_argument("-c", "--category", action="append", default=[],
                   help="分类过滤，可重复: music/sfx/audiobook/field/all")
    p.add_argument("-l", "--license", action="append", default=[],
                   help="许可过滤（关键字，可重复）: Public Domain/CC0/CC BY ...")
    p.add_argument("--only", default=None, help="仅使用指定源 (逗号分隔)")
    p.add_argument("--exclude", default=None, help="排除指定源 (逗号分隔)")
    p.add_argument("--delay", type=float, default=0.3, help="每个下载槽之间的间隔秒数")
    p.add_argument("--list", action="store_true", help="列出所有数据源")
    p.add_argument("--list-categories", action="store_true", help="列出所有分类")
    p.add_argument("--list-licenses", action="store_true", help="列出所有许可")
    args = p.parse_args()

    if args.list:
        _show_list()
        return
    if args.list_categories:
        print("分类: ", ", ".join(all_categories()))
        return
    if args.list_licenses:
        print("许可: ", ", ".join(all_licenses()))
        return

    only = set(s.strip() for s in (args.only or "").split(",") if s.strip()) or None
    exclude = set(s.strip() for s in (args.exclude or "").split(",") if s.strip()) or None

    # 预览将生效的数据源
    active = select_crawlers(args.category, args.license, only, exclude)
    if not active:
        print("没有符合条件的数据源。用 --list 查看全部。")
        sys.exit(1)
    print(f"将使用 {len(active)} 个数据源: {', '.join(c['name'] for c in active)}\n")

    run_crawl(
        target=args.count,
        out_dir=args.out,
        categories=args.category or None,
        licenses=args.license or None,
        only=only,
        exclude=exclude,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
