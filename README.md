# 通用免费公版音频爬取程序 (AudioCrawler)

在 `sfx` 免费音效下载器基础上改造而来的**通用**音频爬取引擎：既能抓取
原 18 个免注册音效站，也能抓取 **公共领域 / CC0 / CC BY** 的免费公版音频
（Internet Archive、LibriVox 有声书、SoundHelix、ccMixter 等）。

核心特性：

- **统一抓取器接口** — 任何音频源都实现 `() -> (url, label[, ext, referer])`
  签名，通过 `sources_*.py` 注册到引擎，新增源无需改动引擎。
- **分类 / 许可过滤** — 下载时可按 `分类(music/sfx/audiobook/field)` 与
  `许可(Public Domain / CC0 / CC BY / Free)` 过滤，只取真正「公版 / 免费」的内容。
- **按目录落盘** — 文件保存到 `下载目录/<分类>/<源名>/<名称>.<ext>`，便于整理。
- **反爬增强复用** — 复用 `sfx_utils` 的 25 种浏览器指纹轮换 + Playwright
  stealth 注入 + 资源拦截（图片/媒体/字体屏蔽，提速 3–5 倍）。
- **多入口** — CLI (`crawl.py`)、GUI (`gui.py`)、增强版试听器 (`player.py`)。
- **增强版试听器** — 合并原 `player.py` / `musicplayer.py`：基于 `pygame` 支持
  **暂停/继续、进度拖动、音量调节、结束自动下一首**，用 `mutagen` 显示时长与标签，
  播放列表支持**实时搜索过滤**，并保留主题切换、循环模式、快捷键、右键菜单、系统托盘；
  任一可选依赖缺失时自动降级（如回退 `playsound3` 仅播放/停止）。

## 数据源一览

| 源 | 分类 | 许可 | 方式 |
|---|---|---|---|
| InternetArchive-Music | music | Public Domain | HTTP JSON API |
| LibriVox | audiobook | Public Domain | HTTP JSON API |
| InternetArchive-Field | field | Public Domain | HTTP JSON API |
| SoundHelix | music | CC0 | HTTP 直链 |
| ccMixter | music | CC BY | HTTP JSON API |
| FreeMusicArchive | music | CC | Playwright(尽力) |
| SoundGator / Pixabay / Mixkit / Zapsplat … (18 个) | sfx | Free/Royalty-Free | Playwright |

> 许可标注基于各站点的通用授权说明，下载前请自行确认具体条目许可。

## 快速开始

```bash
pip install playwright urllib3 requests playsound3
playwright install chromium        # 仅使用 InternetArchive/SoundHelix/ccMixter 时可省略

# 命令行
python crawl.py -n 10                          # 爬 10 个（任意源）
python crawl.py -n 20 -c music -l "Public Domain"   # 仅公共领域音乐
python crawl.py -n 5 --only LibriVox           # 仅 LibriVox 有声书
python crawl.py --list                         # 列出全部数据源
python crawl.py --list-categories              # 列出分类
python crawl.py --list-licenses                # 列出许可

# 图形界面
python gui.py

# 试听已下载音频
python player.py                  # 扫描 ./sounds
python player.py D:\path\to\dir  # 扫描指定目录
```

## 目录结构

```
sfx/
├── crawler_core.py     # 通用爬虫引擎（注册表 / 过滤 / 下载 / CLI 逻辑）
├── crawl.py            # 命令行入口
├── gui.py              # Tkinter 图形界面（分类/许可/源 多维筛选）
├── player.py           # 增强版音频试听器（递归扫描 sounds/；暂停/拖动/音量/搜索/托盘）
├── sources_sfx.py      # 适配并注册原 18 个音效源
├── sources_pubdom.py   # 注册公版音频源（IA/LibriVox/SoundHelix/ccMixter/FMA）
├── sfx_utils.py        # 反爬增强工具（指纹轮换 + Playwright stealth）
├── dl_sounds.py        # 原 sfx 下载器（保留为独立脚本，其抓取函数已被通用引擎整合）
├── sounds/             # 统一下载/试听目录（按 分类/源 分目录；旧扁平音效归档于 sounds/legacy/）
├── sources/            # 所有数据源 JSON 配置（统一输入）
└── stealth.min.js      # Playwright 反检测脚本
```

## 自定义 / 扩展新源

1. 新建 `sources_xxx.py`，在模块顶层调用：
   ```python
   from crawler_core import register_crawler
   def my_scrape_one():
       ...                      # 返回 (url, label) 或 (url, label, ext, referer)
       return url, label
   register_crawler("MySource", my_scrape_one, category="music", license="CC0")
   ```
2. 引擎启动时会自动发现并加载所有 `sources_*.py`。

## 许可

内部项目，全哥创建。所下载音频的版权 / 许可归各源网站及原作者所有，
请遵守对应许可（尤其是 CC BY 需署名、CC0/Public Domain 可自由使用）。
