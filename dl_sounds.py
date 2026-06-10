"""Download random free sound effects from sites that require NO registration.

  - SoundGator / SFXLibrary / Komori / BigSoundBank / PacDV / Pixabay / SoundEffectsPlus / SoundJay / QuickSounds / Mixkit / Myinstants / AudioMicro / FindSounds / PartnersInRhyme / FreeSoundEffects / FreeSFX
  - 数据源配置按 JSON 拆分: sources/ 目录下每个数据源一个 JSON
  - 按量下载（NUM_SOUNDS），内容随机，不按关键词
  - CLI: python dl_sounds.py
  - GUI: python gui.py (Tkinter 图形界面)
  - 已移除: Freesound API, Pixabay API（需注册）
  - 已移除: Chosic（音乐播放列表生成器，无下载选项）
  - 已移除: OrangeFreeSounds, SoundsCrate（Cloudflare WAF 硬封锁，Playwright 也超时）
"""

import os
import sys
import json
import time
import random
import re
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import ssl
from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────────
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
NUM_SOUNDS = 10  # 下载几个音效

os.makedirs(SOUNDS_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════

_ctx = ssl.create_default_context()


def _fetch(url, retries=2, referer=None):
    """HTTP GET，返回 (status, data_bytes)"""
    req = urllib.request.Request(url)
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
    )
    if referer:
        req.add_header("Referer", referer)
    last_err = None
    for _attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15, context=_ctx) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                time.sleep(3)
                continue
            return e.code, b""
        except Exception as e:
            last_err = e
            time.sleep(1)
    if last_err:
        raise last_err
    raise RuntimeError(f"Failed to fetch: {url}")


# ── 共享 Playwright 浏览器（懒初始化，所有数据源共用）─────────
_pw_playwright = None
_pw_browser = None
_pw_context = None


def _pw_init():
    """启动 Chromium + 共享 context（首次调用时自动 init）"""
    global _pw_playwright, _pw_browser, _pw_context
    if _pw_browser is not None:
        return
    _pw_playwright = sync_playwright().start()
    _pw_browser = _pw_playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    _pw_context = _pw_browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )


def _fetch_page(url, timeout=30):
    """Playwright 获取 HTML 页面的文本内容 → (status, text)

    优势:
    - 真实浏览器可执行 JS，自动通过 Cloudflare Challenge
    - 共享 context，cookies 跨请求保持
    - 各数据源 HTML 抓取统一用此函数

    注意: 仅用于 HTML 页面获取，不用于二进制下载。
    """
    page = None
    try:
        _pw_init()
        page = _pw_context.new_page()
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        time.sleep(2)  # 等待 CF JS 执行
        html = page.content()
        # Cloudflare 拦截检测：被拦则刷新一次（cookie 已有则放行）
        if "Just a moment" in html:
            page.reload(timeout=timeout * 1000, wait_until="domcontentloaded")
            time.sleep(2)
            html = page.content()
        return (200 if "Just a moment" not in html else 403), html
    except Exception:
        return 0, ""
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def _save_file(filepath, data):
    with open(filepath, "wb") as f:
        f.write(data)


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


_used_names = {}


# ═══════════════════════════════════════════════════════════
#  加载 JSON 配置
# ═══════════════════════════════════════════════════════════

def _load_json_config(name):
    """从 sources/{name}.json 加载配置"""
    path = os.path.join(SOURCES_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════
#  数据源 1: SoundGator
# ═══════════════════════════════════════════════════════════

_soundgator_cfg = None
_soundgator_known_ids = set()


def _soundgator_scrape_one():
    """从 SoundGator 随机抓取一个音效的 mp3 下载链接"""
    global _soundgator_cfg
    if _soundgator_cfg is None:
        _soundgator_cfg = _load_json_config("soundgator")

    cat_url = random.choice(_soundgator_cfg["categories"])
    status, text = _fetch_page(cat_url)
    if status != 200:
        return None, None

    products = re.findall(r'/product/(\d+)-([^/"]+)-sound-effect', text)
    if not products:
        return None, None

    candidates = [(pid, name) for pid, name in products if pid not in _soundgator_known_ids]
    if not candidates:
        return None, None

    pid, name = random.choice(candidates)
    _soundgator_known_ids.add(pid)

    prod_url = f"https://www.soundgator.com/product/{pid}-{name}-sound-effect"
    status2, text2 = _fetch_page(prod_url)
    if status2 != 200:
        return None, None

    mp3 = re.search(r'https?://[^"\s]+?\.mp3', text2)
    if mp3:
        return mp3.group(0), name
    audio = re.search(r'data-audio=["\']([^"\']+)["\']', text2)
    if audio:
        return audio.group(1), name
    return None, None


# ═══════════════════════════════════════════════════════════
#  数据源 2: SFXLibrary
# ═══════════════════════════════════════════════════════════

_sfxlibrary_cfg = None
_sfxlibrary_known_paths = set()


def _sfxlibrary_scrape_one():
    """从 SFXLibrary 随机抓取一个音效的 mp3 下载链接"""
    global _sfxlibrary_cfg
    if _sfxlibrary_cfg is None:
        _sfxlibrary_cfg = _load_json_config("sfxlibrary")

    page_url = random.choice(_sfxlibrary_cfg["listing_pages"])
    status, text = _fetch_page(page_url)
    if status != 200:
        return None, None

    sound_links = re.findall(r'href="(\.?/sound/[^"]+)"', text)
    if not sound_links:
        return None, None

    candidates = [l for l in sound_links if l not in _sfxlibrary_known_paths]
    if not candidates:
        return None, None

    detail_path = random.choice(candidates)
    _sfxlibrary_known_paths.add(detail_path)

    detail_url = f"https://www.sfxlibrary.com{detail_path.lstrip('.')}"
    status2, text2 = _fetch_page(detail_url)
    if status2 != 200:
        return None, None

    mp3 = re.search(r'src="([^"]+\.mp3)"', text2)
    if mp3:
        rel = mp3.group(1)
        if rel.startswith("http"):
            return rel, detail_path.split("/")[-1]
        return f"https://www.sfxlibrary.com/{rel.lstrip('/')}", detail_path.split("/")[-1]

    mp3 = re.search(r'https?://[^"\s]+\.mp3', text2)
    if mp3:
        return mp3.group(0), detail_path.split("/")[-1]

    return None, None


# ═══════════════════════════════════════════════════════════
#  数据源 3: 小森平免费音效 (taira-komori.net)
# ═══════════════════════════════════════════════════════════

_komori_cfg = None
_komori_sound_names = {}
_komori_used = set()


def _komori_scrape_sound_names(page_name):
    """从分类页提取所有音效文件名"""
    if page_name in _komori_sound_names:
        return _komori_sound_names[page_name]

    url = f"https://taira-komori.net/{page_name}"
    status, text = _fetch_page(url)
    if status != 200:
        return []
    matches = re.findall(r'href="(sound_os2/[^/]+/([^"/]+?)\.mp3)"', text)
    if not matches:
        raw_names = re.findall(r'([a-zA-Z0-9_]+)\.mp3', text)
        matches = [(None, n) for n in raw_names]

    _komori_sound_names[page_name] = matches
    return matches


def _komori_scrape_one():
    """从小森平随机抓取一个音效的 mp3 下载链接"""
    global _komori_cfg
    if _komori_cfg is None:
        _komori_cfg = _load_json_config("komori")

    cats = _komori_cfg["category_pages"]
    entry = random.choice(cats)
    page_name = entry["file"]

    matches = _komori_scrape_sound_names(page_name)
    if not matches:
        return None, None

    available = [(rel_path, name) for rel_path, name in matches
                 if (page_name, name) not in _komori_used]
    if not available:
        return None, None

    rel_path, name = random.choice(available)
    _komori_used.add((page_name, name))

    if rel_path:
        url = f"https://taira-komori.net/{rel_path}"
    else:
        category = page_name.replace("cn.html", "")
        url = f"https://taira-komori.net/sound/{category}/{name}.mp3"
    return url, name


# ═══════════════════════════════════════════════════════════
#  数据源 4: BigSoundBank
# ═══════════════════════════════════════════════════════════

_bigsoundbank_cfg = None
_bigsoundbank_known_ids = set()


def _bigsoundbank_scrape_one():
    """从 BigSoundBank 随机抓取一个音效的 mp3 下载链接

    策略：
    1. 随机选一个 CatID → 抓取分类页 search?CatID=XXX
    2. 从分类页提取声音 ID（链接格式 /{slug}-s{ID}.html）
    3. 直接用 ID 构造 mp3 URL: /UPLOAD/mp3/{ID}.mp3
    """
    global _bigsoundbank_cfg
    if _bigsoundbank_cfg is None:
        _bigsoundbank_cfg = _load_json_config("bigsoundbank")

    cat_id = random.choice(_bigsoundbank_cfg["category_ids"])
    cat_url = f"{_bigsoundbank_cfg['base_url']}/search?CatID={cat_id}"

    status, text = _fetch_page(cat_url)
    if status != 200:
        return None, None

    # 提取声音 ID: /{slug}-s{ID}.html
    ids = re.findall(r'/([^/"]+)-s(\d+)\.html', text)
    if not ids:
        return None, None

    candidates = [(slug, sid) for slug, sid in ids if sid not in _bigsoundbank_known_ids]
    if not candidates:
        return None, None

    slug, sid = random.choice(candidates)
    _bigsoundbank_known_ids.add(sid)

    mp3_url = _bigsoundbank_cfg["mp3_url_template"].format(id=sid)
    return mp3_url, slug


# ═══════════════════════════════════════════════════════════
#  数据源 5: PacDV
# ═══════════════════════════════════════════════════════════

_pacdv_cfg = None
_pacdv_known_urls = set()


def _pacdv_scrape_one():
    """从 PacDV 随机抓取一个音效的 mp3 下载链接

    策略：
    1. 随机选一个分类页面
    2. 直接从页面提取所有 .mp3 直链
    3. 随机选一个下载
    """
    global _pacdv_cfg
    if _pacdv_cfg is None:
        _pacdv_cfg = _load_json_config("pacdv")

    cat = random.choice(_pacdv_cfg["categories"])
    cat_url = f"{_pacdv_cfg['base_url']}/{cat['url']}"

    status, text = _fetch_page(cat_url)
    if status != 200:
        return None, None

    # 提取所有 .mp3 链接
    mp3_matches = re.findall(r'href="([^"]+\.mp3)"', text)
    if not mp3_matches:
        return None, None

    candidates = [u for u in mp3_matches if u not in _pacdv_known_urls]
    if not candidates:
        return None, None

    rel_url = random.choice(candidates)
    _pacdv_known_urls.add(rel_url)

    # 解析为绝对 URL
    if rel_url.startswith("http"):
        abs_url = rel_url
    else:
        abs_url = f"{_pacdv_cfg['base_url']}/{rel_url.lstrip('/')}"

    # 提取文件名作为 label
    label = os.path.splitext(os.path.basename(rel_url))[0]
    return abs_url, label


# ═══════════════════════════════════════════════════════════
#  数据源 6: Pixabay Sound Effects (Playwright 浏览器 + JSON-LD)
# ═══════════════════════════════════════════════════════════

_pixabay_cfg = None
_pixabay_known_ids = set()


def _pixabay_extract_detail_links(html):
    """从搜索页 HTML 中提取音效详情页链接列表"""
    # 匹配 /sound-effects/slug-name-123456/ 格式
    links = re.findall(
        r'href=["\'](/sound-effects/[a-z][a-z0-9_]*-[a-z0-9_]*-\d+/)["\']',
        html,
    )
    # 去重并过滤已知的
    seen = set()
    unique = []
    for link in links:
        # 从 URL 提取 ID
        id_m = re.search(r"-(\d+)/?$", link)
        if not id_m:
            continue
        sid = id_m.group(1)
        if sid in _pixabay_known_ids or sid in seen:
            continue
        seen.add(sid)
        unique.append(link)
    return unique


def _pixabay_extract_content_url(html):
    """从详情页 HTML 的 JSON-LD 中提取 AudioObject 的 contentUrl"""
    ld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    for block in ld_blocks:
        try:
            data = json.loads(block)
            # 可能是单个对象或数组
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "AudioObject":
                    url = item.get("contentUrl", "")
                    name = item.get("name", "")
                    if url:
                        return url, name
        except (json.JSONDecodeError, TypeError):
            continue
    return None, None


def _pixabay_scrape_one():
    """从 Pixabay Sound Effects 随机抓取一个音效的 mp3 下载链接

    策略 (Playwright 浏览器 + JSON-LD):
    ───────────────────────────────────────────────────
    1. Playwright 启动 Chromium → 可执行 JS 通过 CF Challenge
    2. 搜索页 → 提取详情页链接
    3. 详情页 → 从 JSON-LD 提取 AudioObject.contentUrl
       → cdn.pixabay.com 免费下载直链

    浏览器复用：首次启动 Chromium，后续请求共用同一实例。
    """
    global _pixabay_cfg
    if _pixabay_cfg is None:
        _pixabay_cfg = _load_json_config("pixabay")

    slug = random.choice(_pixabay_cfg["search_slugs"])

    page = random.randint(1, 3)
    if page == 1:
        search_url = f"{_pixabay_cfg['base_url']}/sound-effects/search/{slug}/"
    else:
        search_url = (
            f"{_pixabay_cfg['base_url']}/sound-effects/search/"
            f"{slug}/?pagi={page}"
        )

    status, text = _fetch_page(search_url)
    if status != 200:
        return None, None

    if "Just a moment" in text or len(text) < 2000:
        return None, None

    detail_links = _pixabay_extract_detail_links(text)
    if not detail_links:
        return None, None

    detail_path = random.choice(detail_links)

    id_m = re.search(r"-(\d+)/?$", detail_path)
    if id_m:
        _pixabay_known_ids.add(id_m.group(1))

    detail_url = f"{_pixabay_cfg['base_url']}{detail_path}"

    status2, text2 = _fetch_page(detail_url)
    if status2 != 200:
        return None, None

    if "Just a moment" in text2 or len(text2) < 2000:
        return None, None

    mp3_url, name = _pixabay_extract_content_url(text2)
    if not mp3_url:
        return None, None

    return mp3_url, name


# ═══════════════════════════════════════════════════════════
#  数据源 7: SoundEffectsPlus
# ═══════════════════════════════════════════════════════════

_seplus_cfg = None
_seplus_known_fids = set()


def _seplus_scrape_one():
    """从 SoundEffectsPlus 随机抓取一个音效的下载链接

    策略：
    1. 随机选分类 → 随机页码
    2. 从分类页提取产品 URL（/product/{slug}/）
    3. 访问产品页 → 提取 download.php?fid={numeric_id}
    4. 通过 download.php 下载（返回 zip 或 mp3）
    """
    global _seplus_cfg
    if _seplus_cfg is None:
        _seplus_cfg = _load_json_config("soundeffectsplus")

    cat = random.choice(_seplus_cfg["categories"])
    # 第1页无页码后缀，第2页起 /2/, /3/...
    page = random.randint(1, _seplus_cfg["max_pages_per_category"])
    if page == 1:
        cat_url = f"{_seplus_cfg['base_url']}/{cat['url']}"
    else:
        cat_url = f"{_seplus_cfg['base_url']}/{cat['url']}{page}/"

    status, text = _fetch_page(cat_url)
    if status != 200:
        return None, None

    # 提取产品 URL — href 可能用单引号或双引号
    products = re.findall(r'''href\s*=\s*["'](/product/[^"']+/)["']''', text)
    if not products:
        # 回退: 直接匹配 /product/xxx/ 路径
        products = re.findall(r'/product/([^/"\s]+)/', text)
        products = [f"/product/{p}/" for p in set(products)]

    candidates = [p for p in products if p not in _seplus_known_fids]
    if not candidates:
        return None, None

    product_url = f"{_seplus_cfg['base_url']}{random.choice(candidates)}"
    _seplus_known_fids.add(product_url)

    # 访问产品页提取 download.php?fid=xxx
    status2, text2 = _fetch_page(product_url)
    if status2 != 200:
        return None, None

    # 提取 fid
    m = re.search(r'download\.php\?fid=(\d+)', text2)
    if not m:
        return None, None

    fid = m.group(1)
    dl_url = f"{_seplus_cfg['base_url']}/download.php?fid={fid}"

    # 提取名称
    label = product_url.rstrip("/").rsplit("/", 1)[-1]
    # SoundEffectsPlus 下载的是压缩包
    return dl_url, label, "zip"


# ═══════════════════════════════════════════════════════════
#  数据源 8: SoundJay
# ═══════════════════════════════════════════════════════════

_soundjay_cfg = None
_soundjay_known_urls = set()


def _soundjay_scrape_one():
    """从 SoundJay 随机抓取一个音效的 mp3 下载链接

    策略：
    1. 随机选一个分类页面（如 button-sounds-1.html）
    2. 从页面提取所有 .mp3 直链
    3. 随机选一个下载
    """
    global _soundjay_cfg
    if _soundjay_cfg is None:
        _soundjay_cfg = _load_json_config("soundjay")

    page_path = random.choice(_soundjay_cfg["category_pages"])
    page_url = f"{_soundjay_cfg['base_url']}/{page_path}"

    status, text = _fetch_page(page_url, timeout=20)
    if status != 200 or len(text) < 2000:
        return None, None

    # 提取所有 .mp3 链接
    mp3s = re.findall(r'["\']([^"\']+\.mp3)["\']', text)
    if not mp3s:
        return None, None

    candidates = [u for u in mp3s if u not in _soundjay_known_urls]
    if not candidates:
        return None, None

    rel_url = random.choice(candidates)
    _soundjay_known_urls.add(rel_url)

    # 转绝对 URL
    if rel_url.startswith("http"):
        abs_url = rel_url
    else:
        abs_url = f"{_soundjay_cfg['base_url']}/{rel_url.lstrip('/')}"

    label = os.path.splitext(os.path.basename(rel_url))[0]
    return abs_url, label


# ═══════════════════════════════════════════════════════════
#  数据源 9: QuickSounds
# ═══════════════════════════════════════════════════════════

_quicksounds_cfg = None
_quicksounds_known_ids = set()


def _quicksounds_scrape_one():
    """从 QuickSounds 随机抓取一个音效的 mp3 下载链接

    策略：
    1. 随机生成 sound_id（已知范围 29000~29300）
    2. 访问 /sound/{id} 详情页
    3. 提取 /uploads/tracks/{name}.mp3 下载直链
    4. 下载时需要 Referer 头（指向详情页）
    """
    global _quicksounds_cfg
    if _quicksounds_cfg is None:
        _quicksounds_cfg = _load_json_config("quicksounds")

    lo, hi = _quicksounds_cfg["sound_id_range"]
    attempts = 0
    while attempts < 15:
        attempts += 1
        sound_id = random.randint(lo, hi)
        if sound_id in _quicksounds_known_ids:
            continue
        _quicksounds_known_ids.add(sound_id)

        detail_url = f"{_quicksounds_cfg['base_url']}/sound/{sound_id}"
        status, text = _fetch_page(detail_url, timeout=20)
        if status != 200 or len(text) < 5000:
            continue

        # 提取 mp3 链接，优先 /uploads/tracks/ 模式
        mp3s = re.findall(r'["\']([^"\']*\.mp3[^"\']*)["\']', text)
        mp3_url = None
        for m in mp3s:
            if "/uploads/tracks/" in m:
                mp3_url = m if m.startswith("http") else f"{_quicksounds_cfg['base_url']}{m}"
                break
        if not mp3_url and mp3s:
            # 回退：取第一个合法 mp3
            for m in mp3s:
                try:
                    candidate = m if m.startswith("http") else f"{_quicksounds_cfg['base_url']}{m.lstrip('/')}"
                    mp3_url = candidate
                    break
                except Exception:
                    continue

        if not mp3_url:
            continue

        # 从标题提取名称
        title_m = re.search(r'<title>([^<]+)</title>', text)
        label = title_m.group(1).replace(" - QuickSounds.com", "").strip() if title_m else f"qs_{sound_id}"
        return mp3_url, label, "mp3", detail_url

    return None, None


# ═══════════════════════════════════════════════════════════
#  数据源 10: Mixkit (WAV 全量音效)
# ═══════════════════════════════════════════════════════════

_mixkit_cfg = None
_mixkit_known_ids = set()


def _mixkit_scrape_one():
    """从 Mixkit 随机抓取一个音效的 WAV 下载链接

    策略：
    1. 随机选一个分类 slug（如 /free-sound-effects/bleep/）
    2. 从分类页 HTML 提取 data-algolia-analytics-item-id（音效 ID）
       以及相邻的标题文本
    3. 用 CDN 模板构造 WAV 直链: assets.mixkit.co/active_storage/sfx/{id}/{id}.wav
    4. urllib + Referer 下载（无需登录）

    注：-preview.mp3 是裁剪版，只有完整文件的 ~15-20%
        完整文件只有 .wav 格式可用
    """
    global _mixkit_cfg
    if _mixkit_cfg is None:
        _mixkit_cfg = _load_json_config("mixkit")

    slug = random.choice(_mixkit_cfg["category_slugs"])
    cat_url = f"{_mixkit_cfg['base_url']}/free-sound-effects/{slug}/"

    status, text = _fetch_page(cat_url, timeout=25)
    if status != 200 or len(text) < 5000:
        return None, None

    # 从页面提取所有音效 ID: data-algolia-analytics-item-id="数字"
    id_matches = re.findall(
        r'data-algolia-analytics-item-id=["\'](\d+)["\']', text,
    )
    if not id_matches:
        return None, None

    candidates = [sid for sid in id_matches if sid not in _mixkit_known_ids]
    if not candidates:
        return None, None

    sound_id = random.choice(candidates)
    _mixkit_known_ids.add(sound_id)

    # 尝试从附近提取名称：找 meta-links__link 或卡片标题
    name = f"mixkit_{sound_id}"
    # 方法1: 找包含该ID的按钮附近的最接近的标题文本
    # 页面结构中每个卡片的标题在 download-button 之前
    title_match = re.search(
        rf'["\']([^"\']{{3,60}})["\'][^>]*>[^<]*<[^<]*data-algolia-analytics-item-id=["\']{sound_id}["\']',
        text,
    )
    if not title_match:
        # 回退: 搜整个页面的所有可能标题模式
        titles = re.findall(r'class="[^"]*meta-links__link[^"]*"[^>]*>([^<]+)<', text)
        ids_in_order = re.findall(r'data-algolia-analytics-item-id=["\'](\d+)["\']', text)
        try:
            idx = ids_in_order.index(sound_id)
            if idx < len(titles):
                name = titles[idx]
        except (ValueError, IndexError):
            pass

    wav_url = _mixkit_cfg["cdn_url_template"].format(id=sound_id)
    return wav_url, name, "wav", cat_url


# ═══════════════════════════════════════════════════════════
#  数据源 11: Myinstants (meme sound buttons)
# ═══════════════════════════════════════════════════════════

_myinstants_cfg = None
_myinstants_known_uuids = set()


def _myinstants_scrape_one():
    """从 Myinstants 随机抓取一个音效按钮的 mp3 下载链接

    策略（无需 Playwright，urllib 即可）：
    1. 随机选一个列表页（首页/热门/搜索）
    2. 从 HTML 提取 /memesounds/{uuid}/{slug}/ 链接
    3. 构造直链: /AllSounds/{uuid}.mp3
    4. urllib + Referer 下载
    """
    global _myinstants_cfg
    if _myinstants_cfg is None:
        _myinstants_cfg = _load_json_config("myinstants")

    listing_url = random.choice(_myinstants_cfg["listing_pages"])
    status, data = _fetch(listing_url)
    if status != 200 or not data:
        return None, None
    text = data.decode("utf-8", errors="ignore")

    # 提取 UUID + slug: /memesounds/{uuid}/{slug}/
    matches = re.findall(
        r'/memesounds/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/([^/"\s]+)/',
        text,
    )
    if not matches:
        return None, None

    candidates = [(uid, slug) for uid, slug in matches if uid not in _myinstants_known_uuids]
    if not candidates:
        return None, None

    uid, slug = random.choice(candidates)
    _myinstants_known_uuids.add(uid)

    mp3_url = f"{_myinstants_cfg['base_url']}/AllSounds/{uid}.mp3"
    return mp3_url, slug, "mp3", listing_url


# ── AudioMicro ──────────────────────────────────────────

_audiomicro_cfg = None
_audiomicro_known_hashes: set = set()


def _audiomicro_scrape_one():
    """随机分类 → 提取下载hash和标题 → /tracks/download/{hash}/wav"""
    global _audiomicro_cfg, _audiomicro_known_hashes
    if _audiomicro_cfg is None:
        _audiomicro_cfg = _load_json_config("audiomicro")

    cats = _audiomicro_cfg["categories"]
    cat = random.choice(cats)
    page = random.randint(1, _audiomicro_cfg.get("pages_per_category", 3))
    page_url = f"{_audiomicro_cfg['base_url']}{cat}"
    if page > 1:
        page_url += f"/page-{page}"

    status, data = _fetch(page_url)
    if status != 200 or not data:
        return None, None
    text = data.decode("utf-8", errors="ignore")

    # 提取 title + hash 对: 标题在 <span class="track-title">, hash 在 /tracks/download/{hash}/wav
    titles = re.findall(r'class="track-title"[^>]*>\s*(.*?)\s*</span>', text)
    hashes = re.findall(r'/tracks/download/([a-f0-9]+)/wav', text)

    if not hashes:
        return None, None

    # 尝试一一对应
    pairs = []
    for i, h in enumerate(hashes):
        label = titles[i] if i < len(titles) else f"audio{h[:8]}"
        pairs.append((h, label))

    candidates = [(h, lb) for h, lb in pairs if h not in _audiomicro_known_hashes]
    if not candidates:
        return None, None

    track_hash, label = random.choice(candidates)
    _audiomicro_known_hashes.add(track_hash)

    dl_url = f"{_audiomicro_cfg['base_url']}/tracks/download/{track_hash}/wav"
    return dl_url, label, "wav", page_url


# ── FindSounds ──────────────────────────────────────────

_findsounds_cfg = None
_findsounds_known_urls: set = set()


def _findsounds_scrape_one():
    """随机关键词搜索 → 提取结果中的 mp3/wav 链接 → 直接下载"""
    global _findsounds_cfg, _findsounds_known_urls
    if _findsounds_cfg is None:
        _findsounds_cfg = _load_json_config("findsounds")

    keyword = random.choice(_findsounds_cfg["keywords"])

    # 随机分页 (start=1,11,21,...)
    page_start = random.randint(0, 5) * 10  # 0, 10, 20, 30, 40, 50
    if page_start == 0:
        url = f"{_findsounds_cfg['base_url']}/ISAPI/search.dll?keywords={urllib.parse.quote(keyword)}"
    else:
        url = f"{_findsounds_cfg['base_url']}/ISAPI/search.dll?start={page_start + 1}&keywords={urllib.parse.quote(keyword)}"

    status, data = _fetch(url)
    if status != 200 or not data:
        return None, None
    text = data.decode("ISO-8859-1", errors="ignore")

    # 提取所有 mp3/wav 链接
    audio_links = re.findall(r'(https?://[^\s"\'<>]+\.(?:mp3|wav))', text, re.IGNORECASE)
    if not audio_links:
        return None, None

    candidates = [l for l in audio_links if l not in _findsounds_known_urls]
    if not candidates:
        return None, None

    mp3_url = random.choice(candidates)
    _findsounds_known_urls.add(mp3_url)

    # 从 URL 文件名提取标签
    fname = mp3_url.rsplit("/", 1)[-1]
    label = urllib.parse.unquote(fname.rsplit(".", 1)[0])[:80]
    ext = fname.rsplit(".", 1)[-1].lower()

    return mp3_url, label, ext, url


# ── PartnersInRhyme ──────────────────────────────────────
# 需要 Playwright (urllib 被 Cloudflare 403)

_pir_cfg = None
_pir_known_urls: set = set()


def _pir_scrape_one():
    """随机分类页 → 提取音效子页面 → 访问详情 → 提取下载链接"""
    global _pir_cfg, _pir_known_urls
    if _pir_cfg is None:
        _pir_cfg = _load_json_config("partnersinrhyme")

    cat = random.choice(_pir_cfg["categories"])
    list_url = f"{_pir_cfg['base_url']}{cat}/"

    status, text = _fetch_page(list_url)
    if status != 200:
        return None, None

    # 音效子页面: /soundfx/{cat}/{name}.shtml
    sound_pages = re.findall(r'href="(soundfx/[^"]+\.shtml)"', text)
    candidates = [sp for sp in sound_pages if sp not in _pir_known_urls]
    if not candidates:
        return None, None

    sp = random.choice(candidates)
    _pir_known_urls.add(sp)

    detail_url = f"{_pir_cfg['base_url']}/{sp}"
    status2, text2 = _fetch_page(detail_url)
    if status2 != 200:
        return None, None

    # 提取下载链接 (mp3 或 wav)
    # 按钮文本 "Download Mp3" / "Download Wav"
    dl_mp3 = re.search(r'(https?://[^\s"\']+\.mp3)', text2, re.IGNORECASE)
    dl_wav = re.search(r'(https?://[^\s"\']+\.wav)', text2, re.IGNORECASE)

    # 回退: 从按钮 href 提取
    if not dl_mp3 and not dl_wav:
        btn = re.search(r'(?:href|src)="([^"]*(?:download|mp3|wav)[^"]*)"', text2, re.IGNORECASE)
        if btn:
            dl_url = btn.group(1)
            if ".mp3" in dl_url.lower():
                label = sp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                return dl_url, label, "mp3", detail_url
            elif ".wav" in dl_url.lower():
                label = sp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                return dl_url, label, "wav", detail_url

    if dl_wav:
        label = sp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return dl_wav.group(1), label, "wav", detail_url
    elif dl_mp3:
        label = sp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return dl_mp3.group(1), label, "mp3", detail_url

    return None, None


# ── FreeSoundEffects ─────────────────────────────────────
# 需要 Playwright (urllib 被 Cloudflare 403)

_fse_cfg = None
_fse_known_ids: set = set()


def _fse_scrape_one():
    """随机分类或搜索 → 列表页 → 提取音效详情 → 获取下载链接"""
    global _fse_cfg, _fse_known_ids
    if _fse_cfg is None:
        _fse_cfg = _load_json_config("freesoundeffects")

    # 随机选分类或搜索词
    use_search = random.random() < 0.4
    if use_search:
        term = random.choice(_fse_cfg["search_terms"])
        list_url = f"{_fse_cfg['base_url']}/search/?q={urllib.parse.quote(term)}"
    else:
        cat = random.choice(_fse_cfg["categories"])
        list_url = f"{_fse_cfg['base_url']}{cat}"

    status, text = _fetch_page(list_url)
    if status != 200:
        return None, None

    # 提取音效条目 — 每个有 Download MP3/WAV 按钮
    # 模式: href 包含 download 或 sound-effect 或 detail
    items = re.findall(r'(?:href|data-url)="([^"]*(?:download|sound-effect|detail|sfx-?\d*)[^"]*)"', text, re.IGNORECASE)
    if not items:
        return None, None

    candidates = [i for i in items if i not in _fse_known_ids]
    if not candidates:
        return None, None

    item = random.choice(candidates)
    _fse_known_ids.add(item)

    # 如果是直接下载链接
    if any(ext in item.lower() for ext in [".mp3", ".wav"]):
        ext = "mp3" if ".mp3" in item.lower() else "wav"
        label = item.rsplit("/")[-1].rsplit(".")[0][:80]
        return item, label, ext, list_url

    # 否则访问详情页获取下载链接
    detail_url = item if item.startswith("http") else f"{_fse_cfg['base_url']}{item}"
    status2, text2 = _fetch_page(detail_url)
    if status2 != 200:
        return None, None

    dl = re.search(r'(https?://[^\s"\']+\.(?:mp3|wav))', text2, re.IGNORECASE)
    if dl:
        url = dl.group(1)
        ext = "mp3" if ".mp3" in url.lower() else "wav"
        label = url.rsplit("/")[-1].rsplit(".")[0][:80]
        return url, label, ext, detail_url

    return None, None


# ── FreeSFX ───────────────────────────────────────────────
# 需要 Playwright (urllib 返回空音频链接)

_fsfx_cfg = None
_fsfx_known_names: set = set()


def _fsfx_scrape_one():
    """随机分类页 → 提取音效条目 → 获取下载 MP3 按钮"""
    global _fsfx_cfg, _fsfx_known_names
    if _fsfx_cfg is None:
        _fsfx_cfg = _load_json_config("freesfx")

    cat = random.choice(_fsfx_cfg["categories"])
    list_url = f"{_fsfx_cfg['base_url']}{cat}"

    status, text = _fetch_page(list_url)
    if status != 200:
        return None, None

    # 提取音效名称 + 下载链接
    # FreeSFX 页面结构: 标题 + "Download MP3" 按钮
    titles = re.findall(r'class="[^"]*title[^"]*"[^>]*>(.*?)</(?:h\d|div|span)>', text, re.DOTALL | re.I)
    if not titles:
        # 备用模式
        titles = re.findall(r'<h[23][^>]*>([^<]+(?:Drill|Start|Stop|Sound|Effect)[^<]*)</h[23]>', text)

    # 下载链接: href 或 data-file
    dls = re.findall(r'''(?:"|')([^"']*(?:download|soundeffect)[^"']*\.mp3[^"']*)''', text, re.I)
    if not dls:
        dls = re.findall(r'data-(?:file|url|src)="([^"]+\.mp3[^"]*)"', text, re.I)

    if not dls:
        return None, None

    idx = random.randint(0, len(dls) - 1)
    dl_url = dls[idx]

    # 去重
    name_key = dl_url.split("/")[-1]
    if name_key in _fsfx_known_names:
        return None, None
    _fsfx_known_names.add(name_key)

    label = titles[idx] if idx < len(titles) else name_key.replace(".mp3", "")[:80]
    label = re.sub(r'\s+', ' ', label).strip()

    return dl_url, label, "mp3", list_url


# ═══════════════════════════════════════════════════════════
#  下载执行
# ═══════════════════════════════════════════════════════════

SOURCES = [
    ("SoundGator",         _soundgator_scrape_one),
    ("SFXLibrary",         _sfxlibrary_scrape_one),
    ("Komori",             _komori_scrape_one),
    ("BigSoundBank",       _bigsoundbank_scrape_one),
    ("PacDV",              _pacdv_scrape_one),
    ("Pixabay",            _pixabay_scrape_one),
    ("SoundEffectsPlus",   _seplus_scrape_one),
    ("SoundJay",           _soundjay_scrape_one),
    ("QuickSounds",        _quicksounds_scrape_one),
    ("Mixkit",             _mixkit_scrape_one),
    ("Myinstants",         _myinstants_scrape_one),
    ("AudioMicro",         _audiomicro_scrape_one),
    ("FindSounds",         _findsounds_scrape_one),
    ("PartnersInRhyme",   _pir_scrape_one),
    ("FreeSoundEffects",   _fse_scrape_one),
    ("FreeSFX",            _fsfx_scrape_one),
]


def _download_one(idx):
    """每次随机选一个数据源下载"""
    src_name, scrape_fn = random.choice(SOURCES)
    print(f"  [{idx + 1}] [{src_name}]", end="")

    try:
        result = scrape_fn()
        if not result:
            print(" (no url)")
            return False

        # 支持两元组 / 三元组 / 四元组 (url, label, ext, referer)
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
            return False

        status, audio = _fetch(url, referer=referer)
        if status == 200 and audio:
            fname = _safe_filename(label, src_name, idx + 1)
            filepath = os.path.join(SOUNDS_DIR, f"{fname}.{ext}")
            _save_file(filepath, audio)
            print(f" -> {fname}.{ext}")
            return True
        else:
            print(f" (HTTP {status})")
    except Exception as e:
        print(f" err:{e}")

    return False


def main():
    # 自动发现 sources/ 下的 JSON 并显示数据源列表
    source_names = []
    if os.path.isdir(SOURCES_DIR):
        for f in sorted(os.listdir(SOURCES_DIR)):
            if f.endswith(".json"):
                source_names.append(f.replace(".json", ""))

    source_display = " | ".join(source_names) if source_names else "SoundGator | SFXLibrary | Komori"

    print("=" * 56)
    print("  免费音效下载器（无需注册、无需 API Key）")
    print(f"  数据源: {source_display}")
    print("=" * 56)

    target = max(1, NUM_SOUNDS)
    print(f"\n  目标: {target} 个  保存路径: {SOUNDS_DIR}\n")

    ok = 0
    for i in range(target):
        if _download_one(i):
            ok += 1
        time.sleep(0.3)

    print(f"\n  完成！成功下载 {ok}/{target} 个音效")


if __name__ == "__main__":
    main()
