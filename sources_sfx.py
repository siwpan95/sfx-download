"""把原 sfx 的 18 个音效数据源注册到通用爬虫引擎 crawler_core。

原 dl_sounds.py 里的抓取函数（_soundgator_scrape_one 等）签名统一为
(url, label[, ext, referer])，与 crawler_core 兼容，因此这里直接复用，
仅补充 category / license 元数据。这样既保留原音效源，又纳入统一的
分类/许可过滤与按目录落盘体系。
"""

import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import dl_sounds  # noqa: E402  (复用原抓取函数)
from crawler_core import register_crawler  # noqa: E402

_LIC = "Free / Royalty-Free"

# (名字, 抓取函数, 分类)
_LEGACY = [
    ("SoundGator",       dl_sounds._soundgator_scrape_one,       "sfx"),
    ("SFXLibrary",       dl_sounds._sfxlibrary_scrape_one,       "sfx"),
    ("Komori",           dl_sounds._komori_scrape_one,           "sfx"),
    ("BigSoundBank",     dl_sounds._bigsoundbank_scrape_one,     "sfx"),
    ("PacDV",            dl_sounds._pacdv_scrape_one,            "sfx"),
    ("Pixabay",          dl_sounds._pixabay_scrape_one,          "sfx"),
    ("SoundEffectsPlus", dl_sounds._seplus_scrape_one,           "sfx"),
    ("SoundJay",         dl_sounds._soundjay_scrape_one,         "sfx"),
    ("QuickSounds",      dl_sounds._quicksounds_scrape_one,      "sfx"),
    ("Mixkit",           dl_sounds._mixkit_scrape_one,           "sfx"),
    ("Myinstants",       dl_sounds._myinstants_scrape_one,       "sfx"),
    ("AudioMicro",       dl_sounds._audiomicro_scrape_one,       "sfx"),
    ("FindSounds",       dl_sounds._findsounds_scrape_one,       "sfx"),
    ("PartnersInRhyme",  dl_sounds._pir_scrape_one,              "sfx"),
    ("Zapsplat",         dl_sounds._zapsplat_scrape_one,         "sfx"),
    ("WavSource",        dl_sounds._wavsource_scrape_one,        "sfx"),
    ("Sampleswap",       dl_sounds._sampleswap_scrape_one,       "sfx"),
    ("99Sounds",         dl_sounds._99sounds_scrape_one,         "sfx"),
]

for _name, _fn, _cat in _LEGACY:
    register_crawler(_name, _fn, category=_cat, license=_LIC)
