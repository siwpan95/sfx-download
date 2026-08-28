"""公版 / 免费音频数据源（注册到 crawler_core 引擎）。

这些源主要提供 公共领域(Public Domain) / CC0 / CC BY 许可的音频：
  * Internet Archive  (archive.org)         — 海量公共领域音频（音乐/有声书/田野录音）
  * LibriVox          (librivox.org)         — 公共领域有声书（托管于 archive.org）
  * SoundHelix        (soundhelix.com)       — CC0 免费示范音乐
  * ccMixter          (ccmixter.org)         — CC BY 混音/音乐
  * Free Music Archive (freemusicarchive.org)— CC 许可音乐（尽力而为）

注意：archive.org 的搜索/元数据接口为公开 HTTP JSON API，无需注册，无 Cloudflare 挑战。
"""

import json
import random
import re
import urllib.parse
import time

from crawler_core import register_crawler, _fetch
import sfx_utils

_fetch_page = sfx_utils.enhanced_fetch_page

# ═══════════════════════════════════════════════════════════
#  Internet Archive 通用抓取器（工厂函数）
# ═══════════════════════════════════════════════════════════

_ia_known_items = {}   # query -> set(identifier)


def _ia_search(query, rows=30):
    """archive.org 高级搜索，返回 [(identifier, title), ...]"""
    q = f"({query}) AND mediatype:audio"
    url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q) +
           "&fl[]=identifier&fl[]=title&rows=" + str(rows) +
           "&page=1&output=json")
    status, data = _fetch(url, retries=4)
    if status != 200 or not data:
        return []
    try:
        j = json.loads(data)
        docs = j.get("response", {}).get("docs", [])
        return [(d.get("identifier"), (d.get("title") or "")) for d in docs if d.get("identifier")]
    except Exception:
        return []


def _ia_files(identifier):
    """获取某 archive.org 条目下的 mp3 文件列表"""
    url = f"https://archive.org/metadata/{identifier}"
    status, data = _fetch(url, retries=4)
    if status != 200 or not data:
        return []
    try:
        j = json.loads(data)
        files = j.get("files", [])
        mp3s = []
        for f in files:
            name = (f.get("name") or "").lower()
            fmt = (f.get("format") or "").lower()
            if name.endswith(".mp3") and fmt not in ("metadata", "txt", "json", "xml", "thumbnail", "thumb"):
                mp3s.append(f)
        return mp3s
    except Exception:
        return []


def _make_ia_crawler(query, category, name, license_text):
    """为某个 archive.org 查询生成一个抓取器闭包"""
    known = set()

    def fn():
        for _ in range(4):  # 内部重试：换搜索批次
            items = _ia_search(query, rows=30)
            if not items:
                return None, None
            random.shuffle(items)
            for ident, title in items:
                if ident in known:
                    continue
                known.add(ident)
                files = _ia_files(ident)
                if not files:
                    continue
                f = random.choice(files)
                fname = f.get("name")
                dl = (f.get("url")
                      or f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}")
                label = (title or ident)[:60]
                return dl, label, "mp3", None
        return None, None

    register_crawler(name, fn, category=category, license=license_text)
    return fn


# 注册三个 archive.org 子源
_make_ia_crawler(
    query='subject:music AND -collection:librivoxaudio',
    category="music", name="InternetArchive-Music",
    license_text="Public Domain (Archive.org)",
)
_make_ia_crawler(
    query='collection:librivoxaudio',
    category="audiobook", name="LibriVox",
    license_text="Public Domain (LibriVox)",
)
_make_ia_crawler(
    query='subject:"field recording"',
    category="field", name="InternetArchive-Field",
    license_text="Public Domain (Archive.org)",
)


# ═══════════════════════════════════════════════════════════
#  SoundHelix — CC0 免费示范音乐
# ═══════════════════════════════════════════════════════════

_SOUNDHELIX_MAX = 20  # SoundHelix 公开曲目编号上限（约 1-20）


def _soundhelix_scrape_one():
    n = random.randint(1, _SOUNDHELIX_MAX)
    url = f"https://www.soundhelix.com/examples/mp3/SoundHelix-Song-{n}.mp3"
    return url, f"SoundHelix-Song-{n}", "mp3"


register_crawler("SoundHelix", _soundhelix_scrape_one,
                 category="music", license="CC0 (SoundHelix)")


# ═══════════════════════════════════════════════════════════
#  ccMixter — CC BY 混音/音乐（官方 JSON API，无需注册）
# ═══════════════════════════════════════════════════════════

_ccmixter_known = set()


def _ccmixter_scrape_one():
    api = ("https://ccmixter.org/api/query?f=json&dataview=links"
           "&limit=100&sort=rank&type=audio&find=mp3")
    status, data = _fetch(api, retries=4)
    if status != 200 or not data:
        return None, None
    try:
        items = json.loads(data)
    except Exception:
        return None, None
    if not isinstance(items, list):
        return None, None

    random.shuffle(items)
    for it in items:
        # 提取 mp3 直链
        mp3 = None
        files = it.get("files") or []
        for fobj in files:
            if isinstance(fobj, str) and fobj.lower().endswith(".mp3"):
                mp3 = fobj
                break
            if isinstance(fobj, dict):
                u = fobj.get("url") or fobj.get("file") or fobj.get("file_page") or ""
                if isinstance(u, str) and u.lower().endswith(".mp3"):
                    mp3 = u
                    break
        if not mp3:
            # 回退：file_page 页面里找 mp3
            fp = it.get("file_page") or ""
            if fp and ".mp3" in fp:
                mp3 = fp
        if not mp3:
            continue

        key = it.get("id") or mp3
        if key in _ccmixter_known:
            continue
        _ccmixter_known.add(key)

        label = (it.get("title") or "ccmixter")[:60]
        if not mp3.startswith("http"):
            mp3 = "https://ccmixter.org" + mp3
        return mp3, label, "mp3", None

    return None, None


register_crawler("ccMixter", _ccmixter_scrape_one,
                 category="music", license="CC BY (ccMixter)")


# ═══════════════════════════════════════════════════════════
#  Free Music Archive — CC 许可音乐（尽力而为，可能不稳定）
# ═══════════════════════════════════════════════════════════

_fma_known = set()


def _fma_scrape_one():
    # FMA 旧站 genre 页；若被封锁则引擎会自动换源重试
    genres = ["classical", "electronic", "jazz", "folk", "punk", "rock", "soul-rb", "pop", "ambient"]
    for genre in random.sample(genres, min(4, len(genres))):
        url = f"https://freemusicarchive.org/genre/{genre}/"
        status, text = _fetch_page(url, timeout=25)
        if status != 200 or len(text) < 2000:
            continue
        mp3s = re.findall(r'href="([^"]+\.mp3)"', text)
        cands = [m for m in mp3s if m not in _fma_known]
        if not cands:
            continue
        link = random.choice(cands)
        _fma_known.add(link)
        if not link.startswith("http"):
            link = "https://freemusicarchive.org" + link
        label = link.rsplit("/", 1)[-1].rsplit(".", 1)[0][:60]
        return link, label, "mp3", None
    return None, None


register_crawler("FreeMusicArchive", _fma_scrape_one,
                 category="music", license="CC (FreeMusicArchive)")
