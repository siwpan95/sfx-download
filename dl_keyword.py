"""Keyword batch downloader for the sfx toolkit.

Downloads sound-effect samples for a given keyword (e.g. "cowbell") from the
sources that support keyword search:

  * Pixabay    -- /sound-effects/search/<slug>/?pagi=N  (Playwright + JSON-LD)
  * FindSounds -- search.dll?keywords=<kw>&start=N      (Playwright, direct links)

Reuses the anti-bot stack from sfx_utils (identity rotation + stealth) and the
Pixabay extractors from dl_sounds.  Files are saved to sounds/<keyword>/.

Usage:
    python dl_keyword.py cowbell -n 40
    python dl_keyword.py cowbell -n 60 --out <输出目录>
    python dl_keyword.py cowbell --only Pixabay
"""

import argparse
import atexit
import os
import re
import sys
import urllib.parse

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import sfx_utils
import dl_sounds

_fetch = sfx_utils.enhanced_fetch
_fetch_page = sfx_utils.enhanced_fetch_page
_fetch_page_scroll = sfx_utils.enhanced_fetch_page_scroll

atexit.register(sfx_utils.close_pw)

MIN_SIZE = 8 * 1024       # drop HTML/redirect stubs
MAX_SIZE = 20 * 1024 * 1024  # samples should be small; drop full songs


# ── Pixabay ─────────────────────────────────────────────────
def _pix_detail_links(html):
    """Extract detail page links from a Pixabay search page.

    Note: dl_sounds._pixabay_extract_detail_links uses a strict slug regex that
    fails on new-style multi-segment slugs like
    /sound-effects/musical-043177-cowbell-115bpmflac-76044/ — we use a loose
    pattern here (any lowercase/digit/underscore/hyphen slug ending in -<id>/).
    """
    links = re.findall(
        r'href="(/sound-effects/(?!search/)[a-z0-9_-]+-\d+/)"', html)
    seen = set()
    unique = []
    for link in links:
        id_m = re.search(r"-(\d+)/$", link)
        if not id_m:
            continue
        sid = id_m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        unique.append(link)
    return unique


def pixabay_collect(keyword, pages=6):
    """Yield (mp3_url, label) from Pixabay search pages."""
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    known_ids = set()
    for page in range(1, pages + 1):
        if page == 1:
            url = f"https://pixabay.com/sound-effects/search/{slug}/"
        else:
            url = f"https://pixabay.com/sound-effects/search/{slug}/?pagi={page}"
        status, text = _fetch_page_scroll(url, timeout=35, scroll_count=4, min_length=3000)
        if status != 200 or "Just a moment" in text:
            continue
        links = _pix_detail_links(text)
        if not links:
            continue
        for path in links:
            id_m = re.search(r"-(\d+)/?$", path)
            if id_m:
                sid = id_m.group(1)
                if sid in known_ids:
                    continue
                known_ids.add(sid)
            status2, text2 = _fetch_page(f"https://pixabay.com{path}", timeout=25)
            if status2 != 200 or "Just a moment" in text2 or len(text2) < 3000:
                continue
            res = dl_sounds._pixabay_extract_content_url(text2)
            if res:
                mp3_url, name = res
                yield mp3_url, name


# ── FindSounds ──────────────────────────────────────────────
def findsounds_collect(keyword, pages=5):
    """Yield (mp3_url, label) from FindSounds search pages (10 results/page)."""
    seen = set()
    kw = urllib.parse.quote(keyword)
    for page in range(pages):
        start = page * 10 + 1
        url = f"https://www.findsounds.com/ISAPI/search.dll?start={start}&keywords={kw}"
        status, text = _fetch_page(url, timeout=25)
        if status != 200 or len(text) < 500:
            continue
        links = re.findall(r'(https?://[^\s"\'<>]+\.(?:mp3|wav))', text, re.IGNORECASE)
        for l in links:
            if l in seen:
                continue
            seen.add(l)
            fname = urllib.parse.unquote(l.rsplit("/", 1)[-1].rsplit(".", 1)[0])[:80]
            yield l, fname


# ── Mixkit ──────────────────────────────────────────────────
def mixkit_collect(keyword, pages=4):
    """Yield (wav_url, label) from Mixkit keyword tag pages.

    Mixkit now renders cards with data-audio-player-item-id-value and the WAV
    direct link template https://assets.mixkit.co/active_storage/sfx/{id}/{id}.wav
    still works without a detail page.  Cloudflare throttles infinite scroll,
    so each page yields only a few items — used as a supplemental source.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    known_ids = set()
    for page in range(1, pages + 1):
        if page == 1:
            url = f"https://mixkit.co/free-sound-effects/{slug}/"
        else:
            url = f"https://mixkit.co/free-sound-effects/{slug}/?page={page}"
        status, text = _fetch_page_scroll(url, timeout=30, scroll_count=5, min_length=3000)
        if status != 200 and len(text) < 3000:
            continue
        # new card structure
        ids = re.findall(r'data-audio-player-item-id-value="(\d+)"', text)
        # legacy structure fallback
        if not ids:
            ids = re.findall(r'data-algolia-analytics-item-id=["\'](\d+)["\']', text)
        titles = re.findall(r'class="[^"]*item-grid-card__title[^"]*"[^>]*>\s*([^<]+?)\s*<', text)
        if not titles:  # fallback: meta-links title
            titles = re.findall(r'class="[^"]*meta-links__link[^"]*"[^>]*>([^<]+)<', text)
        for idx, sid in enumerate(ids):
            if sid in known_ids:
                continue
            known_ids.add(sid)
            name = f"mixkit_{sid}"
            try:
                if idx < len(titles):
                    name = titles[idx].strip()
            except (ValueError, IndexError):
                pass
            wav_url = f"https://assets.mixkit.co/active_storage/sfx/{sid}/{sid}.wav"
            yield wav_url, name


# ── Download helpers ────────────────────────────────────────
def _safe_name(label, src, i):
    name = re.sub(r"[^a-zA-Z0-9\- ]", "", label or "")
    name = re.sub(r"\s+", "_", name).strip("-_").lower()
    if not name:
        name = f"{src.lower()}_{i:03d}"
    return name


def download_one(url, label, src, out_dir, referer, i):
    """Download a single file with size filtering. Returns (status, path)."""
    try:
        status, data = _fetch(url, retries=3, referer=referer)
    except Exception:
        return "fetch-error", None
    if status != 200 or not data:
        return f"http-{status}", None
    if len(data) < MIN_SIZE:
        return "too-small", None
    if len(data) > MAX_SIZE:
        return "too-big", None
    ext = ".mp3" if url.lower().rsplit("?", 1)[0].endswith(".mp3") else ".wav"
    path = os.path.join(out_dir, f"{_safe_name(label, src, i)}{ext}")
    # avoid clobbering: uniquify
    base, e = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        path = f"{base}_{n}{e}"
        n += 1
    dl_sounds._save_file(path, data)
    return "ok", path


# ── Main ────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Keyword-based sample batch downloader")
    p.add_argument("keyword", help="search keyword, e.g. cowbell")
    p.add_argument("-n", "--count", type=int, default=40, help="number of files to download")
    p.add_argument("--out", default=None, help="output dir (default: sounds/<keyword>/)")
    p.add_argument("--only", default=None,
                   help="comma list: Pixabay/FindSounds/Mixkit")
    p.add_argument("--pixabay-pages", type=int, default=12)
    p.add_argument("--mixkit-pages", type=int, default=4)
    p.add_argument("--findsounds-pages", type=int, default=5)
    args = p.parse_args()

    kw = args.keyword
    out_dir = args.out or os.path.join(_BASE, "sounds", kw)
    os.makedirs(out_dir, exist_ok=True)
    only = set(s.strip() for s in (args.only or "").split(",") if s.strip())

    sources = []
    if not only or "Pixabay" in only:
        sources.append(("Pixabay", pixabay_collect(kw, pages=args.pixabay_pages),
                        "https://pixabay.com"))
    if not only or "Mixkit" in only:
        sources.append(("Mixkit", mixkit_collect(kw, pages=args.mixkit_pages),
                        "https://mixkit.co"))
    if not only or "FindSounds" in only:
        sources.append(("FindSounds", findsounds_collect(kw, pages=args.findsounds_pages),
                        "https://www.findsounds.com"))

    stats = {"ok": 0, "fail": 0}
    downloaded = 0
    seen_urls = set()
    # persist downloaded URLs so re-runs don't re-fetch the same items
    state_path = os.path.join(out_dir, ".downloaded_urls.txt")
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            seen_urls.update(l.strip() for l in f if l.strip())
    i = 0
    print(f"Keyword: {kw} | target: {args.count} | out: {out_dir} | skip {len(seen_urls)} known urls")
    try:
        while downloaded < args.count:
            progress = False
            for src, gen, referer in sources:
                if downloaded >= args.count:
                    break
                try:
                    url, label = next(gen)
                except StopIteration:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                i += 1
                st, path = download_one(url, label, src, out_dir, referer, i)
                with open(state_path, "a", encoding="utf-8") as f:
                    f.write(url + "\n")
                if st == "ok":
                    downloaded += 1
                    stats["ok"] += 1
                    size = os.path.getsize(path) // 1024
                    print(f"[{downloaded:3d}/{args.count}] OK  {src:<10s} {os.path.basename(path)} ({size} KB)")
                else:
                    stats["fail"] += 1
                    print(f"          SKIP {src:<10s} {st}")
                progress = True
            if not progress:
                break
    except KeyboardInterrupt:
        print("Interrupted.")

    print(f"\nDone. downloaded={stats['ok']} failed={stats['fail']} -> {out_dir}")


if __name__ == "__main__":
    main()
