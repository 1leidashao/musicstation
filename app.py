# -*- coding: utf-8 -*-
"""MusicStation —— 音乐下载站后端"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, send_from_directory

import config as cfg
import downloader
import platform_api as pa

app = Flask(__name__, static_folder="static")
ENGINE = downloader.ENGINE


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/hot")
def api_hot():
    """四音源热歌榜（各 30 首）+ 跨源去重聚合"""
    try:
        charts, merged, errors = pa.hot_all(pa.HOT_LIMIT)
        return jsonify({
            "ok": True,
            "order": pa.SOURCE_ORDER,
            "labels": pa.SOURCE_LABELS,
            "charts": charts,
            "merged": merged,
            "errors": errors,
        })
    except Exception as err:
        return jsonify({"ok": False, "error": f"热歌榜获取失败: {err}"}), 500


@app.route("/api/search")
def api_search():
    """搜索: 单曲（网易云展示用）; 歌手/专辑按 QQ→酷我→网易云→咪咕 优先级取数"""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "关键词为空", "songs": [], "artists": [], "albums": []})
    out = {"ok": True, "keyword": q, "songs": [], "artists": [], "albums": [], "errors": {}}
    # 单曲: 网易云搜索（仅用于展示，下载时引擎会自行按四源优先级搜索）
    try:
        out["songs"] = pa.SOURCES["netease"].search_songs(q, 15)
    except Exception as err:
        out["errors"]["songs"] = str(err)
    # 歌手: 优先级链
    name, artists, errs = pa.chain_call("search_artists", q)
    out["artists"] = [dict(a, source=name) for a in (artists or [])]
    out["artist_source"] = name
    out["errors"].update({f"artists:{k}": v for k, v in errs.items()})
    # 专辑: 优先级链
    name2, albums, errs2 = pa.chain_call("search_albums", q)
    out["albums"] = [dict(a, source=name2) for a in (albums or [])]
    out["album_source"] = name2
    out["errors"].update({f"albums:{k}": v for k, v in errs2.items()})
    return jsonify(out)


@app.route("/api/artist/<source>/<artist_id>")
def api_artist(source, artist_id):
    """歌手详情: 上方专辑列表 + 下方全部歌曲（数据来自指定音源，音源按优先级在前端/搜索时确定）"""
    src = pa.get_source(source)
    if not src:
        return jsonify({"ok": False, "error": f"未知音源: {source}"}), 400
    artist_name = (request.args.get("name") or "").strip()
    try:
        albums = src.artist_albums(artist_id, artist_name)
    except Exception as err:
        albums = []
        downloader.LOG.warning(f"[artist] {src.label} 专辑获取失败: {err}")
    try:
        songs = src.artist_songs(artist_id, artist_name)
    except Exception as err:
        songs = []
        downloader.LOG.warning(f"[artist] {src.label} 歌曲获取失败: {err}")
    return jsonify({
        "ok": True,
        "source": source,
        "source_label": src.label,
        "artist": artist_name,
        "albums": albums,
        "songs": songs,
        "errors": [],
    })


@app.route("/api/album/<source>/<album_id>")
def api_album(source, album_id):
    """专辑详情: 专辑内全部歌曲"""
    src = pa.get_source(source)
    if not src:
        return jsonify({"ok": False, "error": f"未知音源: {source}"}), 400
    try:
        result = src.album_songs(album_id)
        return jsonify({"ok": True, "source": source, "source_label": src.label, **result})
    except Exception as err:
        return jsonify({"ok": False, "error": str(err), "album": "", "artist": "", "songs": []}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    songs = data.get("songs") or []
    artist = re.sub(r'[\\/:*?"<>|]', "_", (data.get("artist") or "").strip())
    if not songs:
        return jsonify({"ok": False, "error": "没有要下载的歌曲"}), 400
    songs = [{"name": s.get("name", "").strip(), "singer": s.get("singer", "").strip()} for s in songs if s.get("name", "").strip()]
    if not songs:
        return jsonify({"ok": False, "error": "没有要下载的歌曲"}), 400
    if not artist:
        singers = {s["singer"] for s in songs if s["singer"]}
        artist = singers.pop() if len(singers) == 1 else "单曲合集"
    os.makedirs(cfg.LOCAL_MUSIC_DIR, exist_ok=True)
    job_id = ENGINE.start_job(data.get("title") or artist, songs, artist)
    return jsonify({"ok": True, "job_id": job_id, "artist_dir": artist})


@app.route("/api/download_albums", methods=["POST"])
def api_download_albums():
    """批量下载专辑: 后端展开各专辑曲目后统一入队"""
    data = request.get_json(force=True, silent=True) or {}
    albums = data.get("albums") or []
    artist = re.sub(r'[\\/:*?"<>|]', "_", (data.get("artist") or "").strip() or "单曲合集")
    if not albums:
        return jsonify({"ok": False, "error": "没有要下载的专辑"}), 400
    songs, skipped = [], []
    for al in albums[:100]:
        src = pa.get_source(al.get("source") or "")
        if not src:
            continue
        try:
            result = src.album_songs(al.get("id"))
            batch = [s for s in result.get("songs") or [] if s.get("name")]
        except Exception as err:
            downloader.LOG.warning(f"[albums] 专辑《{al.get('name')}》展开失败: {err}")
            batch = []
        if batch:
            songs.extend(batch)
            downloader.LOG.info(f"[albums] 《{al.get('name')}》展开 {len(batch)} 首")
        else:
            skipped.append(al.get("name") or al.get("id"))
    if not songs:
        return jsonify({"ok": False, "error": "所有专辑均未获取到歌曲"}), 502
    os.makedirs(cfg.LOCAL_MUSIC_DIR, exist_ok=True)
    title = f"专辑下载 {len(albums)} 张/{len(songs)} 首"
    job_id = ENGINE.start_job(title, songs, artist)
    return jsonify({"ok": True, "job_id": job_id, "songs": len(songs), "skipped": skipped})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    snap = ENGINE.job_snapshot(job_id)
    if not snap:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, **snap})


@app.route("/api/jobs")
def api_jobs():
    """全部任务（服务端为准，下载列表页数据源）"""
    return jsonify({"ok": True, "jobs": [j for j in ENGINE.all_snapshots() if j]})


@app.route("/api/config")
def api_config():
    """前端配置下发: 分页大小 / 轮询间隔等（来自 config.json 的 frontend 段）"""
    return jsonify({"ok": True, **cfg.FRONTEND})


@app.route("/api/logs")
def api_logs():
    """增量读取运行日志: /api/logs?since=N 返回第 N 条之后的日志"""
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    items, total = downloader.log_snapshot(since)
    return jsonify({"ok": True, "logs": items, "total": total})


if __name__ == "__main__":
    os.makedirs(cfg.LOCAL_MUSIC_DIR, exist_ok=True)
    print(f"MusicStation 启动: http://localhost:{cfg.SERVER_PORT}")
    print(f"本地音乐目录: {cfg.LOCAL_MUSIC_DIR}")
    app.run(host=cfg.SERVER_HOST, port=cfg.SERVER_PORT, threaded=True)
