# -*- coding: utf-8 -*-
"""下载引擎：音源优先级 + 品质递减 + 本地下载 + 本地保存整理"""
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import logging
from dataclasses import dataclass, field

import config as cfg

logging.getLogger("musicdl").setLevel(logging.WARNING)

# ---------------------------------------------------------------- 运行日志（内存环形缓冲，供日志页实时读取）
from collections import deque


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class RingBufferLogHandler(logging.Handler):
    def __init__(self, capacity=2000):
        super().__init__()
        self.buf = deque(maxlen=capacity)

    def emit(self, record):
        try:
            msg = _ANSI_RE.sub("", record.getMessage())
            self.buf.append({
                "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "msg": msg,
            })
        except Exception:
            pass

    def snapshot(self, since=0):
        items = list(self.buf)
        return items[since:], len(items)


LOG = logging.getLogger("musicstation")
LOG.setLevel(cfg.LOG_LEVEL)
_handler = RingBufferLogHandler(capacity=cfg.LOG_BUFFER_CAPACITY)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
LOG.addHandler(_handler)
LOG.propagate = False
# 同时把 musicdl / werkzeug 的日志引入缓冲，日志页可以看到更完整的过程
for _name in ("musicdl", "werkzeug"):
    _l = logging.getLogger(_name)
    _l.addHandler(_handler)


def log_snapshot(since=0):
    return _handler.snapshot(since)

SOURCE_PRIORITY = cfg.SOURCE_PRIORITY  # 下载音源优先级（1 QQ 2 酷我 3 网易云 4 咪咕），config.json 可调
SOURCE_LABELS = cfg.SOURCE_LABELS
LOSSLESS_EXTS = cfg.LOSSLESS_EXTS
LOSSY_EXTS = cfg.LOSSY_EXTS
# 允许挑选的格式（跳过 mgg/mflac 等加密容器，避免下到解不开的文件）
PICKABLE_EXTS = LOSSLESS_EXTS | LOSSY_EXTS
# 命中这些关键词的候选降权（Live/翻唱/伴奏等），config.json 的 download.exclude_keywords 可调
_EXCLUDE_RE = re.compile("|".join(re.escape(k) for k in cfg.EXCLUDE_KEYWORDS), re.I) if cfg.EXCLUDE_KEYWORDS else None


def norm(s):
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())[:120]


# ---------------------------------------------------------------- 引擎
@dataclass
class SongTask:
    name: str
    singer: str
    artist_dir: str  # 本地保存的歌手目录名
    status: str = "pending"  # pending/searching/downloading/uploading/done/failed
    source: str = ""
    ext: str = ""
    size: str = ""
    message: str = ""


@dataclass
class Job:
    id: str
    title: str
    tasks: list = field(default_factory=list)
    state: str = "running"  # running/done
    created: float = field(default_factory=time.time)


class DownloadEngine:
    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self.jobs = {}

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                from musicdl import musicdl
                init_cfg = {s: {"work_dir": cfg.LOCAL_MUSIC_DIR,
                                "search_size_per_source": cfg.SEARCH_SIZE_PER_SOURCE}
                            for s in SOURCE_PRIORITY}
                self._client = musicdl.MusicClient(music_sources=SOURCE_PRIORITY,
                                                   init_music_clients_cfg=init_cfg)
            return self._client

    # ---------------- 匹配与品质选择 ----------------
    def _pick_best(self, song_name, singer, infos):
        """按 品质递减 规则挑最佳：先无损(体积最大优先)，再有损(体积最大优先)"""
        target, artist = norm(song_name), norm(singer)
        cands = []
        for info in infos:
            ext = (info.ext or "").lstrip(".").lower()
            if ext not in PICKABLE_EXTS:
                continue
            name_n = norm(info.song_name)
            if not name_n:
                continue
            # 歌名匹配度：完全相等 > 前缀包含 > 包含（不匹配则跳过）
            if name_n == target:
                name_score = 3
            elif name_n.startswith(target) or target in name_n:
                name_score = 2
            elif target.startswith(name_n):
                name_score = 1
            else:
                continue
            singer_n = norm(info.singers)
            singer_score = 2 if artist and artist in singer_n else (1 if not artist else 0)
            # 命中 Live/翻唱等修饰词的降权
            penalty = 1 if (_EXCLUDE_RE and _EXCLUDE_RE.search(info.song_name or "")) else 0
            lossless = 1 if ext in LOSSLESS_EXTS else 0
            try:
                size = int(info.file_size_bytes or 0)
            except Exception:
                size = 0
            cands.append((-(name_score * 10 + singer_score * 5 - penalty), -lossless, -size, info))
        if not cands:
            return None
        cands.sort(key=lambda x: x[:3])
        return cands[0][3]

    def _fmt_size(self, info):
        try:
            b = int(info.file_size_bytes or 0)
            return f"{b/1048576:.1f}MB" if b else (info.file_size or "")
        except Exception:
            return info.file_size or ""

    # ---------------- 单曲完整流程 ----------------
    def process_song(self, task, on_update):
        keyword = f"{task.name} {task.singer}".strip()
        LOG.info(f"开始处理: {task.name} - {task.singer}（关键词「{keyword}」）")
        for source in SOURCE_PRIORITY:
            task.source = SOURCE_LABELS.get(source, source)
            task.status, task.message = "searching", f"{task.source} 搜索中..."
            on_update(task)
            LOG.info(f"[{task.name}] {task.source}（优先级 {SOURCE_PRIORITY.index(source)+1}）搜索中...")
            try:
                results = self.client.music_clients[source].search(keyword=keyword)
            except Exception as err:
                task.message = f"{task.source} 搜索失败: {err}"
                on_update(task)
                LOG.warning(f"[{task.name}] {task.source} 搜索失败: {err}")
                continue
            best = self._pick_best(task.name, task.singer, results)
            if best is None:
                task.message = f"{task.source} 无匹配结果"
                on_update(task)
                LOG.info(f"[{task.name}] {task.source} 搜索到 {len(results)} 条，无精确匹配，降级下一音源")
                continue

            task.ext = (best.ext or "").lstrip(".")
            task.size = self._fmt_size(best)
            task.status, task.message = "downloading", f"{task.source} 下载中 ({task.ext} {task.size})"
            on_update(task)
            LOG.info(f"[{task.name}] {task.source} 命中: {best.song_name} - {best.singers}（{task.ext} {task.size}），开始下载")
            try:
                downloaded = self.client.music_clients[source].download(song_infos=[best])
            except Exception as err:
                task.message = f"{task.source} 下载失败: {err}"
                on_update(task)
                LOG.warning(f"[{task.name}] {task.source} 下载失败: {err}，降级下一音源")
                continue
            if not downloaded:
                task.message = f"{task.source} 下载失败（无可写结果）"
                on_update(task)
                LOG.warning(f"[{task.name}] {task.source} 下载失败（无可写结果），降级下一音源")
                continue

            save_path = downloaded[0].save_path
            work_dir = os.path.dirname(save_path)
            if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                task.message = f"{task.source} 下载的文件为空"
                on_update(task)
                LOG.warning(f"[{task.name}] {task.source} 下载的文件为空，降级下一音源")
                continue

            task.status, task.message = "saving", f"保存到本地 -> {task.artist_dir}/"
            on_update(task)
            LOG.info(f"[{task.name}] 下载完成 {save_path}，保存到本地 -> {task.artist_dir}/")
            try:
                final_path, msg = self._save_locally(save_path, work_dir, task.artist_dir)
            except Exception as err:
                task.status = "failed"
                task.message = f"本地保存异常: {err}（原文件保留在 {save_path}）"
                on_update(task)
                LOG.error(f"[{task.name}] 本地保存异常: {err}（原文件保留在 {save_path}）")
                return

            task.status = "done"
            task.message = f"{msg} | 来源 {task.source} {task.ext.upper()} {task.size}"
            on_update(task)
            LOG.info(f"[{task.name}] ✔ {msg}（来源 {task.source} {task.ext.upper()} {task.size}）")
            return

        task.status = "failed"
        task.message = "所有音源均未成功获取该歌曲"
        on_update(task)
        LOG.error(f"[{task.name}] ✘ 所有音源（{' → '.join(SOURCE_LABELS.get(s, s) for s in SOURCE_PRIORITY)}）均未成功")

    def _save_locally(self, save_path, work_dir, artist_dir):
        """把下载好的音频（及同名 .lrc 歌词）移动到 本地音乐目录/<歌手> 下，命名冲突自动加序号"""
        base = os.path.abspath(cfg.LOCAL_MUSIC_DIR)
        target_dir = os.path.join(base, sanitize(artist_dir) or "单曲合集")
        os.makedirs(target_dir, exist_ok=True)

        def _target_for(path):
            filename = os.path.basename(path)
            target = os.path.join(target_dir, filename)
            if not os.path.exists(target) or os.path.abspath(path) == os.path.abspath(target):
                return target
            stem, ext = os.path.splitext(filename)
            i = 1
            while os.path.exists(os.path.join(target_dir, f"{stem}({i}){ext}")):
                i += 1
            return os.path.join(target_dir, f"{stem}({i}){ext}")

        moved = []
        for p in (save_path, os.path.splitext(save_path)[0] + ".lrc"):
            if os.path.exists(p):
                target = _target_for(p)
                shutil.move(p, target)
                moved.append(target)
        final_path = moved[0] if moved else save_path
        self._tidy(work_dir)
        return final_path, f"已保存 {final_path}"

    def _tidy(self, work_dir):
        """清理本次下载目录里的 pkl 元数据与空目录（不删除音频文件）"""
        base = os.path.abspath(cfg.LOCAL_MUSIC_DIR)
        try:
            if os.path.isdir(work_dir) and os.path.abspath(work_dir).startswith(base):
                for f in os.listdir(work_dir):
                    if f.endswith(".pkl"):
                        try:
                            os.remove(os.path.join(work_dir, f))
                        except Exception:
                            pass
                self._rmdir_if_empty(work_dir)
                parent = os.path.dirname(work_dir)
                if os.path.abspath(parent) != base:
                    self._rmdir_if_empty(parent)
        except Exception as err:
            LOG.warning(f"本地整理异常: {err}")

    def _rmdir_if_empty(self, path, attempts=None, wait=None):
        attempts = attempts or int(cfg.CLEANUP.get("rmdir_retries", 3))
        wait = wait if wait is not None else float(cfg.CLEANUP.get("retry_wait_seconds", 2.0))
        for i in range(attempts):
            try:
                if os.listdir(path):
                    return False
                os.rmdir(path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if i < attempts - 1:
                    time.sleep(wait)
        return False

    # ---------------- 任务编排 ----------------
    def start_job(self, title, songs, artist_dir):
        job_id = uuid.uuid4().hex[:10]
        tasks = [SongTask(name=s["name"], singer=s.get("singer", ""),
                          artist_dir=artist_dir or s.get("singer", "未知歌手")) for s in songs]
        job = Job(id=job_id, title=title, tasks=tasks)
        self.jobs[job_id] = job
        LOG.info(f"[任务 {job_id}] 创建: {title}（{len(tasks)} 首）→ 本地目录「{artist_dir}」")

        def worker():
            for task in tasks:
                try:
                    self.process_song(task, lambda t: None)
                except Exception as err:
                    task.status = "failed"
                    task.message = f"处理异常: {err}"
                    LOG.error(f"[任务 {job_id}] {task.name} 处理异常: {err}\n{traceback.format_exc()}")
            job.state = "done"
            ok = sum(1 for t in tasks if t.status == "done")
            LOG.info(f"[任务 {job_id}] 结束: {ok}/{len(tasks)} 成功")
            self._prune()

        threading.Thread(target=worker, daemon=True).start()
        return job_id

    def all_snapshots(self):
        return [self.job_snapshot(jid) for jid in self.jobs]

    def _prune(self):
        """任务结束后清掉本地音乐目录下的 pkl 元数据与空目录（均为本次流程产生）"""
        base = cfg.LOCAL_MUSIC_DIR
        if not os.path.isdir(base):
            return
        # 扫两遍: Windows 下目录句柄释放有延迟，第一遍 rmdir 失败的空目录第二遍再试
        for _round in range(2):
            removed_any = False
            for root, dirs, files in os.walk(base, topdown=False):
                for f in files:
                    if f.endswith(".pkl"):
                        try:
                            os.remove(os.path.join(root, f))
                        except Exception:
                            pass
                if root != base and not dirs and not os.listdir(root):
                    try:
                        os.rmdir(root)
                        removed_any = True
                    except OSError:
                        pass
            if not removed_any:
                break

    def job_snapshot(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return None
        return {
            "id": job.id, "title": job.title, "state": job.state,
            "tasks": [{"name": t.name, "singer": t.singer, "status": t.status,
                       "source": t.source, "ext": t.ext, "size": t.size,
                       "message": t.message} for t in job.tasks],
        }


ENGINE = DownloadEngine()
