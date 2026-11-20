# -*- coding: utf-8 -*-
"""四音源（QQ音乐/酷我/网易云/咪咕）统一数据适配层

职责：热歌榜、歌手搜索、专辑搜索、歌手全部歌曲、歌手专辑列表、专辑歌曲。
下载仍由 musicdl 引擎按 QQ→酷我→网易云→咪咕 优先级执行（见 downloader.py）。

数据源优先级（歌手/专辑数据）: qq -> kuwo -> netease -> migu
"""
import json
import logging
import re
import time

import requests

import config as cfg

LOG = logging.getLogger("musicstation")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 音源优先级 / 标签 / 各类上限 / 请求超时 / 翻页间隔 / 榜单 ID —— 全部来自 config.json
SOURCE_ORDER = cfg.SOURCE_ORDER          # qq -> kuwo -> netease -> migu
SOURCE_LABELS = cfg.PLATFORM_LABELS
HOT_LIMIT = cfg.HOT_LIMIT                # 每源热歌数
ARTIST_SONGS_CAP = cfg.ARTIST_SONGS_CAP  # 歌手歌曲抓取上限
ARTIST_ALBUMS_CAP = cfg.ARTIST_ALBUMS_CAP  # 歌手专辑抓取上限
REQUEST_TIMEOUT = cfg.REQUEST_TIMEOUT    # 各平台接口超时（秒）
PAGE_DELAY = cfg.PAGE_DELAY              # 翻页请求间隔（秒），避免请求过快被风控
CHART_IDS = cfg.CHART_IDS                # 各平台榜单 ID


def _session():
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA})
    return s


def _norm(text):
    """歌名/歌手名归一化（用于去重与匹配）"""
    return re.sub(r"[\s·・,，、/|()（）\[\]【】'\"`~!！?？.。:：;；\-—_]+", "", str(text or "")).lower()


def _dedup_key(song):
    first_singer = (song.get("singer") or "").split("/")[0].split("&")[0].strip()
    return _norm(song.get("name")) + "|" + _norm(first_singer)


def _singer_names(item):
    for key in ("singer", "singers", "artistList"):
        val = item.get(key)
        if isinstance(val, list):
            return [x.get("name", "") for x in val if isinstance(x, dict) and x.get("name")]
        if isinstance(val, str) and val:
            return [val]
    return []


class BaseSource:
    name = ""
    label = ""

    def __init__(self):
        self.s = _session()

    def _get(self, url, params=None, headers=None, timeout=12):
        r = self.s.get(url, params=params or {}, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # 子类需实现的接口 -------------------------------------------------
    def hot(self, limit=HOT_LIMIT):
        raise NotImplementedError

    def search_artists(self, keyword):
        raise NotImplementedError

    def search_albums(self, keyword):
        raise NotImplementedError

    def artist_songs(self, artist_id, artist_name):
        raise NotImplementedError

    def artist_albums(self, artist_id, artist_name):
        raise NotImplementedError

    def album_songs(self, album_id):
        raise NotImplementedError


# ============================================================ QQ音乐
class QQSource(BaseSource):
    name = "qq"
    label = "QQ音乐"
    HEADERS = {"User-Agent": UA, "Referer": "https://y.qq.com/"}

    def hot(self, limit=HOT_LIMIT):
        d = self._get("https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg",
                      {"topid": 26, "format": "json", "song_begin": 0, "song_num": limit},
                      self.HEADERS)
        out = []
        for item in (d.get("songlist") or [])[:limit]:
            x = item.get("data") or {}
            out.append({
                "name": x.get("songname", ""),
                "singer": " / ".join(_singer_names(x)),
                "album": x.get("albumname", ""),
                "duration": int(x.get("interval") or 0),
            })
        return out

    def _search(self, keyword, t, n, p=1):
        d = self._get("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                      {"w": keyword, "t": t, "format": "json", "n": n, "p": p}, self.HEADERS)
        return d.get("data") or {}

    def search_artists(self, keyword):
        data = self._search(keyword, 9, 10)
        out = []
        for a in ((data.get("singer") or {}).get("list") or []):
            out.append({
                "id": a.get("singerMID", ""),
                "name": a.get("singerName", ""),
                "pic": a.get("singerPic", ""),
                "song_count": a.get("songNum", 0),
                "album_count": a.get("albumNum", 0),
            })
        return [a for a in out if a["name"]]

    def search_albums(self, keyword):
        data = self._search(keyword, 8, 20)
        out = []
        for a in ((data.get("album") or {}).get("list") or []):
            out.append({
                "id": a.get("albumMID", ""),
                "name": a.get("albumName", ""),
                "artist": a.get("singerName", ""),
                "pic": a.get("albumPic", ""),
                "publish": a.get("publicTime", ""),
            })
        return [a for a in out if a["name"]]

    def artist_songs(self, artist_id, artist_name):
        """QQ 未开放歌手全曲目接口，按歌手名搜索歌曲并过滤（按相关度排序）"""
        songs, seen = [], set()
        target = _norm(artist_name)
        for p in range(1, 8):  # 7 页 x 90 条
            try:
                data = self._search(artist_name, 0, 90, p)
            except Exception:
                break
            lst = (data.get("song") or {}).get("list") or []
            if not lst:
                break
            for x in lst:
                singers = _singer_names(x)
                if not any(target and target in _norm(n) for n in singers):
                    continue
                key = _norm(x.get("songname")) + "|" + _norm(singers[0] if singers else "")
                if key in seen:
                    continue
                seen.add(key)
                songs.append({
                    "name": x.get("songname", ""),
                    "singer": " / ".join(singers),
                    "album": x.get("albumname", ""),
                    "duration": int(x.get("interval") or 0),
                })
            if len(songs) >= ARTIST_SONGS_CAP:
                break
            time.sleep(PAGE_DELAY)
        return songs[:ARTIST_SONGS_CAP]

    def artist_albums(self, artist_id, artist_name):
        albums, seen = [], set()
        for p in range(1, 6):
            try:
                data = self._search(artist_name, 8, 30, p)
            except Exception:
                break
            lst = (data.get("album") or {}).get("list") or []
            if not lst:
                break
            for a in lst:
                if a.get("singerMID") != artist_id:
                    continue
                if a.get("albumMID") in seen:
                    continue
                seen.add(a.get("albumMID"))
                albums.append({
                    "id": a.get("albumMID", ""),
                    "name": a.get("albumName", ""),
                    "artist": a.get("singerName", ""),
                    "pic": a.get("albumPic", ""),
                    "publish": a.get("publicTime", ""),
                })
            if len(lst) < 30 or len(albums) >= ARTIST_ALBUMS_CAP:
                break
            time.sleep(PAGE_DELAY)
        # 按发行时间倒序（新的在前）
        albums.sort(key=lambda x: x.get("publish", ""), reverse=True)
        return albums[:ARTIST_ALBUMS_CAP]

    def album_songs(self, album_id):
        d = self._get("https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg",
                      {"albummid": album_id, "format": "json"}, self.HEADERS)
        data = d.get("data") or {}
        songs = []
        for x in (data.get("list") or []):
            x = x.get("musicData") or x
            songs.append({
                "name": x.get("songname", ""),
                "singer": " / ".join(_singer_names(x)),
                "album": x.get("albumname", "") or data.get("name", ""),
                "duration": int(x.get("interval") or 0),
            })
        return {
            "album": data.get("name", ""),
            "artist": data.get("singername", "") or " / ".join(_singer_names(data)),
            "songs": [s for s in songs if s["name"]],
        }


# ============================================================ 酷我音乐
class KuwoSource(BaseSource):
    name = "kuwo"
    label = "酷我音乐"
    WAPI = "https://m.kuwo.cn/newh5app/wapi/api/www"
    HEADERS = {"User-Agent": UA, "Referer": "https://m.kuwo.cn/"}
    HOT_BANG_ID = 93  # 酷我热歌榜

    def hot(self, limit=HOT_LIMIT):
        d = self._get(f"{self.WAPI}/bang/bang/musicList",
                      {"bangId": self.HOT_BANG_ID, "pn": 1, "rn": limit}, self.HEADERS)
        data = d.get("data") or {}
        out = []
        for x in (data.get("musicList") or [])[:limit]:
            out.append({
                "name": x.get("name", ""),
                "singer": x.get("artist", ""),
                "album": x.get("album", ""),
                "duration": int(x.get("duration") or 0),
            })
        return [o for o in out if o["name"]]

    def search_artists(self, keyword):
        d = self._get(f"{self.WAPI}/search/searchArtistBykeyWord",
                      {"key": keyword, "pn": 1, "rn": 10}, self.HEADERS)
        out = []
        for a in ((d.get("data") or {}).get("list") or []):
            out.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "pic": a.get("pic", ""),
                "song_count": a.get("musicNum", 0),
            })
        return [a for a in out if a["name"]]

    def search_albums(self, keyword):
        try:
            d = self._get(f"{self.WAPI}/search/searchAlbumBykeyWord",
                          {"key": keyword, "pn": 1, "rn": 20}, self.HEADERS)
        except Exception:
            return []
        out = []
        for a in ((d.get("data") or {}).get("albumList") or ((d.get("data") or {}).get("list") or [])):
            out.append({
                "id": str(a.get("albumid", "")),
                "name": a.get("album", "") or a.get("name", ""),
                "artist": a.get("artist", ""),
                "pic": a.get("pic", ""),
                "publish": a.get("releaseDate", ""),
            })
        return [a for a in out if a["name"]]

    def artist_songs(self, artist_id, artist_name):
        songs, total = [], 0
        for pn in range(1, 9):  # 8 页 x 100
            try:
                d = self._get(f"{self.WAPI}/artist/artistMusic",
                              {"artistid": artist_id, "pn": pn, "rn": 100}, self.HEADERS)
            except Exception:
                break
            data = d.get("data") or {}
            total = int(data.get("total") or 0)
            lst = data.get("list") or []
            if not lst:
                break
            for x in lst:
                songs.append({
                    "name": x.get("name", ""),
                    "singer": x.get("artist", ""),
                    "album": x.get("album", ""),
                    "duration": int(x.get("duration") or 0),
                })
            if len(songs) >= ARTIST_SONGS_CAP or len(songs) >= total:
                break
            time.sleep(PAGE_DELAY)
        return [s for s in songs if s["name"]][:ARTIST_SONGS_CAP]

    def artist_albums(self, artist_id, artist_name):
        albums, total = [], 0
        for pn in range(1, 8):
            try:
                d = self._get(f"{self.WAPI}/artist/artistAlbum",
                              {"artistid": artist_id, "pn": pn, "rn": 30}, self.HEADERS)
            except Exception:
                break
            data = d.get("data") or {}
            total = int(str(data.get("total") or 0))
            lst = data.get("albumList") or []
            if not lst:
                break
            for a in lst:
                albums.append({
                    "id": str(a.get("albumid", "")),
                    "name": a.get("album", ""),
                    "artist": a.get("artist", ""),
                    "pic": a.get("pic", ""),
                    "publish": a.get("releaseDate", ""),
                })
            if len(albums) >= ARTIST_ALBUMS_CAP or len(albums) >= total:
                break
            time.sleep(PAGE_DELAY)
        return [a for a in albums if a["name"]]

    def album_songs(self, album_id):
        d = self._get(f"{self.WAPI}/album/albumInfo",
                      {"albumId": album_id, "pn": 1, "rn": 200}, self.HEADERS)
        data = d.get("data") or {}
        songs = []
        for x in (data.get("musicList") or []):
            songs.append({
                "name": x.get("name", ""),
                "singer": x.get("artist", ""),
                "album": data.get("album", "") or x.get("album", ""),
                "duration": int(x.get("duration") or 0),
            })
        return {
            "album": data.get("album", ""),
            "artist": data.get("artist", ""),
            "songs": [s for s in songs if s["name"]],
        }


# ============================================================ 网易云音乐
class NeteaseSource(BaseSource):
    name = "netease"
    label = "网易云音乐"
    BASE = "https://music.163.com"
    HEADERS = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    HOT_PLAYLIST_ID = 3778678  # 云音乐热歌榜

    def _post(self, path, data):
        r = self.s.post(self.BASE + path, data=data, headers=dict(self.HEADERS,
                       **{"Content-Type": "application/x-www-form-urlencoded"}), timeout=15)
        r.raise_for_status()
        return r.json()

    def _fmt_song(self, item):
        try:
            artists = [a.get("name", "") for a in (item.get("artists") or item.get("ar") or [])]
            return {
                "name": item.get("name", ""),
                "singer": " / ".join([a for a in artists if a]),
                "album": (item.get("album") or item.get("al") or {}).get("name", ""),
                "duration": int((item.get("duration") or item.get("dt") or 0) / 1000),
            }
        except Exception:
            return None

    def hot(self, limit=HOT_LIMIT):
        d = self._api_get("/api/playlist/detail", {"id": self.HOT_PLAYLIST_ID})
        playlist = d.get("playlist") or d.get("result") or {}
        tracks = playlist.get("tracks") or []
        if not tracks:
            ids = [t.get("id") for t in (playlist.get("trackIds") or [])[:limit]]
            if not ids:
                return []
            c = "[" + ",".join('{"id":%d}' % i for i in ids) + "]"
            detail = self._post("/api/v3/song/detail", {"c": c})
            tracks = detail.get("songs", [])
        songs = [s for s in (self._fmt_song(x) for x in tracks) if s]
        return songs[:limit]

    def _api_get(self, path, params=None):
        return self._get(self.BASE + path, params or {}, self.HEADERS)

    def search_songs(self, keyword, limit=15):
        d = self._post("/api/search/get/web", {"s": keyword, "type": 1, "limit": limit, "offset": 0})
        songs = [s for s in (self._fmt_song(x) for x in (d.get("result") or {}).get("songs", [])) if s]
        return songs

    def search_artists(self, keyword):
        d = self._post("/api/search/get/web", {"s": keyword, "type": 100, "limit": 10, "offset": 0})
        out = []
        for a in (d.get("result") or {}).get("artists", []):
            out.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "pic": a.get("img1v1Url", ""),
                "song_count": a.get("musicSize", 0),
                "album_count": a.get("albumSize", 0),
            })
        return [a for a in out if a["name"]]

    def search_albums(self, keyword):
        d = self._post("/api/search/get/web", {"s": keyword, "type": 10, "limit": 20, "offset": 0})
        out = []
        for a in (d.get("result") or {}).get("albums", []):
            artist = " / ".join(x.get("name", "") for x in (a.get("artists") or []) if x.get("name"))
            out.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "artist": artist,
                "pic": a.get("picUrl", ""),
                "publish": time.strftime("%Y-%m-%d", time.localtime((a.get("publishTime") or 0) / 1000)) if a.get("publishTime") else "",
            })
        return [a for a in out if a["name"]]

    def artist_songs(self, artist_id, artist_name):
        """网易云公开接口仅提供热门歌曲（50 首）"""
        d = self._api_get(f"/api/artist/{artist_id}")
        songs = [s for s in (self._fmt_song(x) for x in d.get("hotSongs", [])) if s]
        return songs

    def artist_albums(self, artist_id, artist_name):
        albums = []
        offset = 0
        while offset < ARTIST_ALBUMS_CAP * 100:
            d = self._api_get(f"/api/artist/albums/{artist_id}", {"offset": offset, "limit": 50})
            batch = d.get("hotAlbums") or []
            if not batch:
                break
            for a in batch:
                albums.append({
                    "id": str(a.get("id", "")),
                    "name": a.get("name", ""),
                    "artist": (a.get("artist") or {}).get("name", ""),
                    "pic": a.get("picUrl", ""),
                    "publish": time.strftime("%Y-%m-%d", time.localtime(a["publishTime"] / 1000)) if a.get("publishTime") else "",
                    "song_count": a.get("size", 0),
                })
            offset += 50
            if len(albums) >= ARTIST_ALBUMS_CAP or not d.get("more"):
                break
        return [a for a in albums if a["name"]][:ARTIST_ALBUMS_CAP]

    def album_songs(self, album_id):
        d = self._api_get(f"/api/v1/album/{album_id}")
        album = d.get("album") or {}
        songs = [s for s in (self._fmt_song(x) for x in d.get("songs", [])) if s]
        return {
            "album": album.get("name", ""),
            "artist": (album.get("artist") or {}).get("name", ""),
            "songs": songs,
        }


# ============================================================ 咪咕音乐
class MiguSource(BaseSource):
    name = "migu"
    label = "咪咕音乐"
    SEARCH = "https://c.musicapp.migu.cn/v1.0/content/search_all.do"
    RESOURCE = "https://c.musicapp.migu.cn/MIGUM2.0/v1.0/content/resourceinfo.do"
    HEADERS = {"User-Agent": UA, "Origin": "https://h5.nf.migu.cn", "Referer": "https://h5.nf.migu.cn/",
               "ua": "Android_migu", "version": "6.8.8", "channel": "014021I", "subchannel": "014021I"}

    def _search(self, text, switch, page_size=20, page=1):
        d = self._get(self.SEARCH, {
            "text": text, "pageNo": page, "pageSize": page_size, "isCopyright": 1, "sort": 1,
            "searchSwitch": json.dumps(switch, separators=(",", ":")),
        }, self.HEADERS)
        return d

    def hot(self, limit=HOT_LIMIT):
        """咪咕官方榜单接口已不再对外提供（已探测多种端点）。
        兜底：尝试若干已知榜单端点，均失败则返回空（前端隐藏该榜单）。"""
        candidates = [
            ("https://app.c.nf.migu.cn/MIGUM3.0/v1.0/content/querycontentbyid.do",
             {"contendid": "292", "pageSize": limit, "pageNo": 1}),
            ("https://app.c.nf.migu.cn/MIGUM3.0/v1.0/template/rank/release",
             {"templateVersion": "2", "pageNo": 1, "pageSize": limit}),
        ]
        for url, params in candidates:
            try:
                d = self._get(url, params, self.HEADERS)
                # 成功时解析，失败时抛异常/返回错误码
                if str(d.get("code")) not in ("000000", "200", 200):
                    continue
                out = []
                for x in (d.get("data") or {}).get("songList") or []:
                    out.append({
                        "name": x.get("name") or x.get("songName", ""),
                        "singer": " / ".join(_singer_names(x)) or x.get("singer", ""),
                        "album": ((x.get("albums") or [{}])[0].get("name", "")) if x.get("albums") else "",
                        "duration": 0,
                    })
                    if len(out) >= limit:
                        break
                if out:
                    return out
            except Exception:
                continue
        return []  # 咪咕榜单不可用，前端隐藏

    def search_artists(self, keyword):
        d = self._search(keyword, {"song": 0, "album": 0, "singer": 1, "tagSong": 0, "mvSong": 0, "bestShow": 1}, 10)
        out = []
        for a in ((d.get("singerResultData") or {}).get("result") or []):
            out.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "pic": "",
                "song_count": 0,
            })
        return [a for a in out if a["name"]]

    def search_albums(self, keyword):
        d = self._search(keyword, {"song": 0, "album": 1, "singer": 0, "tagSong": 0, "mvSong": 0, "bestShow": 1}, cfg.SEARCH_ALBUM_LIMIT)
        out = []
        for a in ((d.get("albumResultData") or {}).get("result") or []):
            out.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "artist": a.get("singer", ""),
                "pic": "",
                "publish": a.get("publishDate", ""),
            })
        return [a for a in out if a["name"]]

    def artist_songs(self, artist_id, artist_name):
        """咪咕无公开歌手全曲目接口，按歌手名搜索歌曲并过滤"""
        songs, seen = [], set()
        target = _norm(artist_name)
        for page in range(1, 8):
            try:
                d = self._search(artist_name, {"song": 1, "album": 0, "singer": 0, "tagSong": 0, "mvSong": 0, "bestShow": 1}, 20, page)
            except Exception:
                break
            lst = (d.get("songResultData") or {}).get("result") or []
            if not lst:
                break
            for x in lst:
                singers = _singer_names(x)
                if not any(target and target in _norm(n) for n in singers):
                    continue
                key = _norm(x.get("name")) + "|" + _norm(singers[0] if singers else "")
                if key in seen:
                    continue
                seen.add(key)
                albums = x.get("albums") or []
                songs.append({
                    "name": x.get("name", ""),
                    "singer": " / ".join(singers),
                    "album": albums[0].get("name", "") if albums else "",
                    "duration": 0,
                })
            if len(songs) >= ARTIST_SONGS_CAP:
                break
            time.sleep(PAGE_DELAY)
        return songs[:ARTIST_SONGS_CAP]

    def artist_albums(self, artist_id, artist_name):
        """按歌手名搜索专辑并过滤歌手"""
        albums = []
        target = _norm(artist_name)
        try:
            d = self._search(artist_name, {"song": 0, "album": 1, "singer": 0, "tagSong": 0, "mvSong": 0, "bestShow": 1}, 50)
        except Exception:
            return []
        for a in ((d.get("albumResultData") or {}).get("result") or []):
            if target and target not in _norm(a.get("singer", "")):
                continue
            albums.append({
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "artist": a.get("singer", ""),
                "pic": "",
                "publish": a.get("publishDate", ""),
            })
        return [a for a in albums if a["name"]][:ARTIST_ALBUMS_CAP]

    def album_songs(self, album_id):
        d = self._get(self.RESOURCE,
                      {"needSimple": "01", "resourceType": 2003, "resourceId": album_id}, self.HEADERS)
        res = d.get("resource") or []
        songs = []
        for block in res:
            for x in (block.get("songItems") or []):
                songs.append({
                    "name": x.get("songName", ""),
                    "singer": x.get("singerName", ""),
                    "album": block.get("title", ""),
                    "duration": 0,
                })
        info = res[0] if res else {}
        return {
            "album": info.get("title", ""),
            "artist": info.get("singer", ""),
            "songs": [s for s in songs if s["name"]],
        }


# ============================================================ 管理器
SOURCES = {cls.name: cls() for cls in (QQSource, KuwoSource, NeteaseSource, MiguSource)}


def get_source(name):
    return SOURCES.get(name)


def hot_all(limit=HOT_LIMIT):
    """四音源热歌榜 + 聚合去重"""
    charts, errors = {}, {}
    for name in SOURCE_ORDER:
        try:
            charts[name] = SOURCES[name].hot(limit)
        except Exception as err:
            charts[name] = []
            errors[name] = str(err)
            LOG.warning(f"[hot] {SOURCE_LABELS[name]} 榜单获取失败: {err}")
    # 聚合（按音源优先级顺序去重，保留首个）
    merged, seen = [], set()
    for name in SOURCE_ORDER:
        for song in charts.get(name) or []:
            key = _dedup_key(song)
            if key in seen:
                # 标记多源命中
                for m in merged:
                    if _dedup_key(m) == key:
                        m.setdefault("sources", [name]).append(name)
                        break
                continue
            seen.add(key)
            song = dict(song)
            song["sources"] = [name]
            merged.append(song)
    return charts, merged, errors


def chain_call(method, *args, **kwargs):
    """按 qq -> kuwo -> netease -> migu 顺序尝试，返回第一个成功且有数据的结果"""
    errors = {}
    for name in SOURCE_ORDER:
        src = SOURCES[name]
        try:
            result = getattr(src, method)(*args, **kwargs)
            if result:
                return name, result, errors
            errors[name] = "无数据"
        except Exception as err:
            errors[name] = str(err)
            LOG.warning(f"[{method}] {SOURCE_LABELS[name]} 失败: {err}")
    return None, None, errors
