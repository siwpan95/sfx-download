"""通用免费公版音频爬取引擎 (generic free/public-domain audio crawler).

在 sfx 音效下载器基础上改造而来，核心变化：
    * 抽象出统一的 "抓取器(crawler)" 接口 -> 通过 JSON 配置 + 抓取函数注册表解耦
    * 增加 分类(category) 与 许可(license) 维度，下载时可按分类/许可过滤
    * 复用 sfx_utils 的反爬增强（浏览器指纹轮换 + Playwright stealth + 资源拦截）
    * 输出按  下载目录/<category>/<source>/<name>.<ext>  落盘

所有抓取器函数签名统一为:
    () -> (url, label, ext[, referer])  或  (None, None[, ...])
    - 返回 None/空 表示本次未取得链接（引擎会换源重试）
    - ext 缺省为 "mp3"
    - referer 用于需要防盗链 Referer 头的下载
"""

import os
import sys
import re
import json
import time
import random
import atexit
import urllib.parse

import sfx_utils

# 程序退出时自动关闭 Playwright 浏览器
atexit.register(sfx_utils.close_pw)

# ── 路径配置 ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")         # 统一下载根目录（音效 + 公版音频均落于此）
SOURCES_DIR = os.path.join(BASE_DIR, "sources")       # 数据源 JSON 配置目录（统一输入）

# 引擎内部重试（每个下载槽最多尝试几个不同数据源）
MAX_SCRAPE_RETRIES = 4

# ── 工具函数（委托到 sfx_utils 的增强版本）───────────────
_fetch = sfx_utils.enhanced_fetch
_fetch_page = sfx_utils.enhanced_fetch_page
_fetch_page_scroll = sfx_utils.enhanced_fetch_page_scroll


def _save_file(filepath, data):
    """保存二进制数据到文件（自动建目录）"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(data)


# 文件名安全化 + 计数去重
_used_names = {}


def _safe_filename(label, src_name, fallback_id):
    """把 label 转成安全的文件名（不含扩展名）"""
    name = ""
    if label:
        name = re.sub(r'[^a-zA-Z0-9\-\s]', '', label)
        name = re.sub(r'\s+', '_', name).strip('-_').lower()
    if not name:
        name = f"{src_name.lower()}_{fallback_id}"

    count = _used_names.get(name, 0)
    _used_names[name] = count + 1
    if count > 0:
        name = f"{name}_{count}"

    return name


# 源统计
_source_stats = {}


# ═══════════════════════════════════════════════════════════
#  抓取器注册表（crawler registry）
#  每个条目: {name, fn, category, license, enabled}
#  category: all / music / sfx / audiobook / podcast / field
#  license : 文本描述（如 "CC0", "Public Domain", "CC BY"）
# ═══════════════════════════════════════════════════════════

_CRAWLERS = []          # 注册表（有序）
_CRAWLER_NAMES = set()  # 防重名


def register_crawler(name, fn, category="all", license="Unknown", enabled=True):
    """注册一个抓取器到全局表（供 sources_*.py 调用）"""
    if name in _CRAWLER_NAMES:
        return
    _CRAWLER_NAMES.add(name)
    _CRAWLERS.append({
        "name": name,
        "fn": fn,
        "category": category,
        "license": license,
        "enabled": enabled,
    })


def list_crawlers():
    _auto_load_source_modules()
    return list(_CRAWLERS)


# ═══════════════════════════════════════════════════════════
#  JSON 配置加载（供各抓取器按需调用）
# ═══════════════════════════════════════════════════════════

def load_source_config(name, directory=None):
    """从 sources/ 或 sources_pubdom/ 加载 {name}.json 配置"""
    candidates = []
    if directory:
        candidates.append(os.path.join(directory, f"{name}.json"))
    candidates.append(os.path.join(SOURCES_DIR, f"{name}.json"))
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return None


# ═══════════════════════════════════════════════════════════
#  公共抓取辅助：从文本里抽 mp3/wav 直链
# ═══════════════════════════════════════════════════════════

_AUDIO_RE = re.compile(r'https?://[^\s"\'<>()]+\.(?:mp3|wav|ogg|flac|m4a|aac|oga)', re.IGNORECASE)


def extract_audio_links(text, base_url=None):
    """从一段 HTML/文本里提取音频直链列表（去重，保持顺序）"""
    links = _AUDIO_RE.findall(text or "")
    seen = set()
    out = []
    for lnk in links:
        if lnk in seen:
            continue
        seen.add(lnk)
        out.append(lnk)
    return out


def abs_url(rel, base_url):
    """把相对链接转绝对链接"""
    if rel.startswith("http"):
        return rel
    if rel.startswith("//"):
        return "https:" + rel
    return urllib.parse.urljoin(base_url, rel)


# ═══════════════════════════════════════════════════════════
#  数据源加载（自动 import sources_*.py 完成注册）
# ═══════════════════════════════════════════════════════════

def _auto_load_source_modules():
    """导入所有 sources_*.py 模块（注册抓取器），只执行一次"""
    if getattr(_auto_load_source_modules, "_done", False):
        return
    _auto_load_source_modules._done = True

    py_files = []
    for d in (BASE_DIR, SOURCES_DIR):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.startswith("sources_") and fn.endswith(".py"):
                py_files.append(os.path.join(d, fn))

    for path in sorted(py_files):
        mod_name = os.path.splitext(os.path.basename(path))[0]
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:  # 单个源加载失败不影响其它源
            print(f"  [warn] 数据源模块加载失败: {os.path.basename(path)} -> {e}")


# ═══════════════════════════════════════════════════════════
#  下载执行
# ═══════════════════════════════════════════════════════════

def crawl_one(idx, active_crawlers, out_dir):
    """每次随机选一个数据源下载，失败时重试其它源（最多 MAX_SCRAPE_RETRIES 次）

    返回 (ok: bool, src_name: str)
    """
    tried = set()
    for attempt in range(MAX_SCRAPE_RETRIES):
        available = [c for c in active_crawlers if c["name"] not in tried]
        if not available:
            break

        crawler = random.choice(available)
        src_name = crawler["name"]
        tried.add(src_name)

        prefix = f"  [{idx + 1}] [{src_name}]" + (f" (retry {attempt})" if attempt > 0 else "")
        print(prefix, end="", flush=True)

        try:
            result = crawler["fn"]()
            if not result or not result[0]:
                print(" (no url)")
                _bump(src_name, ok=False)
                continue

            # 支持 (url,label) / (url,label,ext) / (url,label,ext,referer)
            referer = None
            if len(result) == 4:
                url, label, ext, referer = result
            elif len(result) == 3:
                url, label, ext = result
            else:
                url, label = result
                ext = "mp3"

            if not url:
                print(" (no url)")
                _bump(src_name, ok=False)
                continue

            status, audio = _fetch(url, referer=referer)
            if status == 200 and audio:
                fname = _safe_filename(label, src_name, idx + 1)
                # 按 分类/源 落盘
                cat = crawler.get("category", "all") or "all"
                dst_dir = os.path.join(out_dir, cat, src_name)
                os.makedirs(dst_dir, exist_ok=True)
                filepath = os.path.join(dst_dir, f"{fname}.{ext}")
                _save_file(filepath, audio)
                print(f" -> {cat}/{src_name}/{fname}.{ext}")
                _bump(src_name, ok=True)
                return True, src_name
            else:
                print(f" (HTTP {status})")
                _bump(src_name, ok=False)
        except Exception as e:
            print(f" err:{e}")
            _bump(src_name, ok=False)

    print(f"  [{idx + 1}] [---]  all retries exhausted")
    return False, "---"


def _bump(src_name, ok):
    st = _source_stats.setdefault(src_name, {"ok": 0, "fail": 0})
    st["ok" if ok else "fail"] += 1


# ═══════════════════════════════════════════════════════════
#  过滤 / 选择
# ═══════════════════════════════════════════════════════════

def select_crawlers(categories=None, licenses=None, only=None, exclude=None, enabled_only=True):
    """根据分类/许可/白名单/黑名单筛选抓取器"""
    _auto_load_source_modules()
    cats = set(c.lower() for c in (categories or []) if c and c.lower() != "all")
    lics = set(l.lower() for l in (licenses or []) if l)

    out = []
    for c in _CRAWLERS:
        if enabled_only and not c.get("enabled", True):
            continue
        if only:
            if c["name"] not in only:
                continue
        if exclude and c["name"] in exclude:
            continue
        if cats:
            if (c.get("category", "all") or "all").lower() not in cats and "all" not in cats:
                continue
        if lics:
            lic = (c.get("license", "") or "").lower()
            if not any(k in lic for k in lics):
                continue
        out.append(c)
    return out


def all_categories():
    _auto_load_source_modules()
    s = set()
    for c in _CRAWLERS:
        s.add((c.get("category", "all") or "all").lower())
    return sorted(s)


def all_licenses():
    _auto_load_source_modules()
    s = set()
    for c in _CRAWLERS:
        lic = (c.get("license", "") or "").strip()
        if lic:
            s.add(lic)
    return sorted(s)


def active_source_names(categories=None, licenses=None, only=None, exclude=None):
    return [c["name"] for c in select_crawlers(categories, licenses, only, exclude)]


# ═══════════════════════════════════════════════════════════
#  主流程（供 CLI / GUI 调用）
# ═══════════════════════════════════════════════════════════

def run_crawl(target, out_dir=None, categories=None, licenses=None,
              only=None, exclude=None, delay=0.3):
    """执行一次爬取。返回 (ok_count, total)"""
    out_dir = out_dir or SOUNDS_DIR
    os.makedirs(out_dir, exist_ok=True)

    active = select_crawlers(categories, licenses, only, exclude)
    if not active:
        print("  没有符合条件的数据源！")
        return 0, 0

    print("=" * 60)
    print("  通用免费公版音频爬取程序")
    print(f"  数据源: {len(active)} 个  |  每槽重试: {MAX_SCRAPE_RETRIES} 次")
    print(f"  保存路径: {out_dir}")
    if categories:
        print(f"  分类过滤: {', '.join(categories)}")
    if licenses:
        print(f"  许可过滤: {', '.join(licenses)}")
    print("=" * 60)

    target = max(1, int(target))
    print(f"\n  目标: {target} 个\n")

    ok = 0
    for i in range(target):
        if crawl_one(i, active, out_dir)[0]:
            ok += 1
        if delay:
            time.sleep(delay)

    print(f"\n  {'─' * 50}")
    print(f"  完成！成功 {ok}/{target} 个")
    if _source_stats:
        print(f"\n  ── 数据源统计 ──")
        for name in sorted(_source_stats.keys()):
            st = _source_stats[name]
            total = st["ok"] + st["fail"]
            rate = st["ok"] / total * 100 if total else 0
            bar = "#" * int(rate / 10) + "-" * (10 - int(rate / 10))
            print(f"  {name:<20s} [{bar}] {st['ok']}/{total} ({rate:.0f}%)")
    sfx_utils.close_pw()
    return ok, target
