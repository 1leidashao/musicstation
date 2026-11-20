# -*- coding: utf-8 -*-
"""统一配置加载 —— 所有可配置项集中在 config.json，此处只做读取与默认值兜底。

config.json 缺失某项时使用下方 default 值，不会崩溃。
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_DIR, "config.json")

try:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        _conf = json.load(f)
except FileNotFoundError:
    # 首次克隆运行：请复制 config.example.json 为 config.json 后再启动
    _conf = {}


def get(path, default=None):
    """按 'a.b.c' 点路径读取配置项；不存在时返回 default"""
    node = _conf
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# ---------------------------------------------------------------- 服务
SERVER_HOST = get("server.host", "0.0.0.0")
SERVER_PORT = int(get("server.port", 8765))

# ---------------------------------------------------------------- 下载引擎
LOCAL_MUSIC_DIR = get("download.local_dir", os.path.join(os.path.expanduser("~"), "Music", "music"))
SOURCE_PRIORITY = get("download.source_priority",
                      ["QQMusicClient", "KuwoMusicClient", "NeteaseMusicClient", "MiguMusicClient"])
SOURCE_LABELS = get("download.source_labels", {
    "QQMusicClient": "QQ音乐", "KuwoMusicClient": "酷我音乐",
    "NeteaseMusicClient": "网易云音乐", "MiguMusicClient": "咪咕音乐",
})
SEARCH_SIZE_PER_SOURCE = int(get("download.search_size_per_source", 20))
LOSSLESS_EXTS = {e.lower() for e in get("download.lossless_exts",
                                        ["flac", "wav", "alac", "ape", "wv", "tta"])}
LOSSY_EXTS = {e.lower() for e in get("download.lossy_exts",
                                     ["mp3", "m4a", "aac", "ogg"])}
EXCLUDE_KEYWORDS = get("download.exclude_keywords",
                       ["live", "cover", "翻唱", "伴奏", "铃声", "dj"])
CLEANUP = get("download.cleanup", {
    "retry_wait_seconds": 2.0, "rmdir_retries": 3,
})

# ---------------------------------------------------------------- 平台数据
SOURCE_ORDER = get("platforms.source_order", ["qq", "kuwo", "netease", "migu"])
PLATFORM_LABELS = get("platforms.source_labels", {
    "qq": "QQ音乐", "kuwo": "酷我音乐", "netease": "网易云音乐", "migu": "咪咕音乐",
})
HOT_LIMIT = int(get("platforms.hot_limit", 30))
ARTIST_SONGS_CAP = int(get("platforms.artist_songs_cap", 500))
ARTIST_ALBUMS_CAP = int(get("platforms.artist_albums_cap", 100))
SEARCH_SONG_LIMIT = int(get("platforms.search_song_limit", 15))
SEARCH_ARTIST_LIMIT = int(get("platforms.search_artist_limit", 10))
SEARCH_ALBUM_LIMIT = int(get("platforms.search_album_limit", 20))
REQUEST_TIMEOUT = int(get("platforms.request_timeout", 12))
PAGE_DELAY = float(get("platforms.page_delay", 0.2))
CHART_IDS = get("platforms.chart_ids", {"qq_toplist": 26, "kuwo_bang": 93, "netease_playlist": 3778678})

# ---------------------------------------------------------------- 前端
FRONTEND = get("frontend", {"page_size": 20, "jobs_poll_ms": 2000, "logs_poll_ms": 1000})

# ---------------------------------------------------------------- 日志
LOG_LEVEL = str(get("log.level", "INFO")).upper()
LOG_BUFFER_CAPACITY = int(get("log.buffer_capacity", 2000))
