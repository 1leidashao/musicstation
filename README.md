# MusicStation 音乐下载站

一个本地运行的音乐下载站：网页上搜索歌曲 / 歌手 / 专辑，首页聚合**四平台热歌榜**，后端调用 [musicdl](https://github.com/CharlesPikachu/musicdl) 按音源优先级与品质规则下载，下载完成后**自动整理保存到本地音乐目录**（按歌手分文件夹）。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![musicdl](https://img.shields.io/badge/musicdl-%3E%3D2.13.11-orange.svg)](https://pypi.org/project/musicdl/)

---

## 目录

- [功能特性](#功能特性)
- [界面预览](#界面预览)
- [整体架构](#整体架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [HTTP 接口清单](#http-接口清单)
- [音源优先级与品质规则](#音源优先级与品质规则)
- [下载与本地保存流程](#下载与本地保存流程)
- [常见问题](#常见问题)
- [免责声明](#免责声明)
- [许可证](#许可证)

---

## 功能特性

| 功能 | 说明 |
|---|---|
| 热门推荐 | 首页聚合 **QQ音乐 / 酷我音乐 / 网易云音乐 / 咪咕音乐** 各前 30 首热歌，提供「聚合去重视图」和多源切换标签，跨平台同名歌曲自动合并并标注音源 |
| 统一搜索 | 一次搜索同时返回 **单曲 / 歌手 / 专辑** 三组结果；歌手与专辑数据按 **QQ音乐 → 酷我音乐 → 网易云音乐 → 咪咕音乐** 优先级取数（前一个源失败自动降级） |
| 歌手页 | 上方列出该歌手**全部专辑**（点击进入专辑页），每张专辑带「⬇ 专辑」按钮，支持「下载全部专辑」 |
| 歌手全部歌曲 | 歌手页下方列出该歌手全部歌曲（分页显示，每页 20 首），每首可单独下载，支持「下载全部歌曲」 |
| 专辑页 | 点击专辑卡片列出专辑内**全部**歌曲，支持「下载本专辑全部」 |
| 单曲下载 | 每首歌右侧「下载」按钮 |
| 下载列表页 | 服务端记录所有任务，刷新 / 换页不丢失，实时显示每首歌的 状态 / 音源 / 格式 / 体积 / 结果 |
| 运行日志页 | 实时滚动显示 搜索 → 下载 → 本地保存 全过程日志 |
| 多音源降级 | 下载按 20 个免凭据音源依次尝试，某个音源失败自动降级到下一个 |
| 品质规则 | **FLAC 无损优先**（同格式取体积最大 = 最高品质）；没有无损时按体积递减取有损；自动跳过 Live / 翻唱 / 伴奏 / DJ 版本和加密格式（mgg 等） |
| 本地保存 | 下载完成后自动移动到 `<本地音乐目录>/<歌手名>/`，同名文件自动加序号，不会覆盖 |

> **音源说明**：musicdl 共注册 57 个音源，本项目经过逐个冒烟测试，只保留 **搜索可用、返回有效音频格式、且无需任何账号凭据** 的 20 个音源（QQ音乐 / 酷我音乐 / 网易云音乐 / 咪咕音乐 / 波点音乐 / 千千音乐 / JOOX / TwoT58 / TuneHub / B站音乐 / Soda音乐 / 歌曲宝 / 歌曲海 / 方皮音乐 / 米兔音乐 / 音乐库 / MyFreeMP3 / 街声 / Audius / Jamendo）。音源顺序在 `config.json` 的 `download.source_priority` 中可自由增删调整。

> **咪咕热歌榜说明**：咪咕官方榜单接口已停止对外开放（项目内保留了多个候选端点的自动尝试，一旦恢复即自动生效）。首页会隐藏咪咕榜单标签，但咪咕仍是**下载链路的第 4 优先音源**，功能不受影响。

---

## 界面预览

> 待补充。启动后访问 `http://localhost:8765`，首页即「音乐」标签页（热歌榜 + 搜索），另有「下载列表」「运行日志」两个标签页。

---

## 整体架构

```
浏览器 (static/index.html)
   │  GET /api/hot                        → 四平台热歌榜 + 聚合去重
   │  GET /api/search?q=xx                → 搜索单曲 + 歌手 + 专辑（优先级链）
   │  GET /api/artist/<source>/<id>       → 歌手页（全部专辑 + 全部歌曲）
   │  GET /api/album/<source>/<id>        → 专辑页（专辑全部歌曲）
   │  POST /api/download                  → 提交下载任务（返回 job_id）
   │  POST /api/download_albums           → 批量下载专辑（后端展开曲目后入队）
   │  GET /api/jobs                       → 下载列表页（所有任务快照，2 秒轮询）
   │  GET /api/logs?since=n               → 日志页（增量日志，1 秒轮询）
   ▼
Flask (app.py) ── 数据适配层 (platform_api.py) ──> QQ / 酷我 / 网易云 / 咪咕 公开 Web 接口
   │
   ▼
下载引擎 (downloader.py)
   1. 逐音源搜索（QQ → 酷我 → 网易云 → 咪咕 → ...）
   2. 挑选最佳候选（歌名精确匹配 > 无损 > 体积）
   3. musicdl 下载到本地缓存目录
   4. 移动到 <本地音乐目录>/<歌手名>/，整理残留元数据
```

- **展示链路与下载链路相互独立**：musicdl 只提供「按关键词搜歌 + 下载」，不提供榜单 / 歌手专辑列表，所以浏览、搜索的元数据由 `platform_api.py` 直接调用各平台公开 Web 接口；真正下载时仍由 musicdl 按音源优先级重新搜索获取下载地址。
- **任务与日志均为服务端内存态**：任务列表、日志环形缓冲都保存在服务进程内，前端只是轮询展示；重启服务后清空（歌曲文件已在本地音乐目录，无影响）。

---

## 目录结构

```
musicstation/
├── app.py                 # Flask 后端（HTTP 接口 + 静态页面）
├── platform_api.py        # 四音源统一数据适配层（热歌榜/歌手/专辑，含优先级降级链）
├── downloader.py          # 下载引擎（音源优先级、品质挑选、本地保存整理、日志）
├── config.py              # 读取 config.json（带默认值兜底，缺失不崩溃）
├── config.example.json    # 配置模板（复制为 config.json 后按需修改）
├── requirements.txt       # Python 依赖
├── start.bat              # Windows 一键启动脚本（双击即用）
├── static/
│   └── index.html         # 前端单页应用（音乐 / 下载列表 / 运行日志）
├── LICENSE
└── README.md
```

> `config.json` 与 `server.log`、`__pycache__/`、本地下载目录已被 `.gitignore` 排除，不会提交到仓库。

---

## 快速开始

### 1. 环境要求

- Python 3.9+
- 可访问公网（调用各音乐平台公开接口）

### 2. 安装依赖

```bash
# Windows（PowerShell / CMD）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> `musicdl` 安装体积较大，建议使用国内镜像源。

### 3. 配置

```bash
copy config.example.json config.json
```

编辑 `config.json`，至少确认 / 修改：

| 配置项 | 说明 |
|---|---|
| `download.local_dir` | 本地音乐保存目录（下载完成后按 `<目录>/<歌手名>/` 整理保存） |
| `server.port` | Web 服务端口（默认 8765） |
| `download.source_priority` | 下载音源优先级（从高到低依序降级） |

各配置项缺失时自动使用内置默认值，不会崩溃；完整字段说明见[配置说明](#配置说明)。

### 4. 启动

**Windows：** 双击 `start.bat`，或在命令行执行：

```bash
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python app.py
```

**Linux / macOS：**

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python app.py
```

> Windows 下必须带 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`，否则中文日志 / 文件名会乱码（`start.bat` 已内置）。

启动成功后控制台会打印：

```
MusicStation 启动: http://localhost:8765
本地音乐目录: downloads
```

浏览器打开 **http://localhost:8765** 即可使用；局域网内其他设备可用 `http://<本机IP>:8765`。

### 5. 停止

命令行窗口 `Ctrl+C`，或直接关窗口。

---

## 配置说明

所有可配置项都集中在 `config.json`，改完重启服务即生效（前端分页 / 轮询参数在下次刷新页面时生效）。缺失项自动使用内置默认值。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `server.host` | `0.0.0.0` | 监听地址（只本机访问可改 `127.0.0.1`） |
| `server.port` | `8765` | 服务端口 |
| `download.local_dir` | `downloads` | 本地音乐保存目录 |
| `download.source_priority` | 20 个音源 | 下载音源优先级（从高到低依序降级） |
| `download.source_labels` | 中文名映射 | 各音源显示名 |
| `download.search_size_per_source` | `20` | 每音源搜索候选数 |
| `download.lossless_exts` | `flac/wav/alac/ape/wv/tta` | 视为无损的格式（优先挑选） |
| `download.lossy_exts` | `mp3/m4a/aac/ogg` | 有损格式（无损缺失时按体积递减选择） |
| `download.exclude_keywords` | `live/cover/翻唱/伴奏/铃声/dj` | 命中降权的修饰词 |
| `download.cleanup.retry_wait_seconds` | `2.0` | 目录整理重试间隔（秒） |
| `download.cleanup.rmdir_retries` | `3` | 空目录删除重试次数 |
| `platforms.source_order` | `qq/kuwo/netease/migu` | 歌手 / 专辑数据源优先级 |
| `platforms.hot_limit` | `30` | 每平台热歌榜条数 |
| `platforms.artist_songs_cap` | `500` | 歌手歌曲抓取上限 |
| `platforms.artist_albums_cap` | `100` | 歌手专辑抓取上限 |
| `platforms.search_*_limit` | 见示例 | 搜索返回条数 |
| `platforms.request_timeout` | `12` | 各平台接口超时（秒） |
| `platforms.page_delay` | `0.2` | 翻页请求间隔（秒），过快易被风控 |
| `platforms.chart_ids` | 见示例 | 各平台榜单 ID（换榜单可改） |
| `frontend.page_size` | `20` | 歌手页歌曲分页大小 |
| `frontend.jobs_poll_ms` | `2000` | 下载列表轮询间隔（毫秒） |
| `frontend.logs_poll_ms` | `1000` | 日志页轮询间隔（毫秒） |
| `log.level` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `log.buffer_capacity` | `2000` | 内存日志环形缓冲条数（日志页可回看的条数） |

> ⚠️ `config.json` 可能包含个人路径等隐私信息，已被 `.gitignore` 排除，请勿提交到公开仓库。

---

## HTTP 接口清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 前端页面 |
| GET | `/api/hot` | 四平台热歌榜 + 聚合去重结果 |
| GET | `/api/search?q=关键词` | 搜索单曲 + 歌手 + 专辑（歌手 / 专辑带 source 标注） |
| GET | `/api/artist/<source>/<id>?name=歌手名` | 歌手全部专辑 + 全部歌曲 |
| GET | `/api/album/<source>/<id>` | 专辑全部歌曲 |
| POST | `/api/download` | 提交下载任务，body：`{"title":"...","artist":"本地文件夹名","songs":[{"name":"歌名","singer":"歌手"}]}` |
| POST | `/api/download_albums` | 批量下载专辑，body：`{"artist":"本地文件夹名","albums":[{"source":"qq","id":"专辑mid","name":"专辑名"}]}`，后端逐张展开曲目后入队 |
| GET | `/api/jobs` | 所有任务快照（下载列表页数据源） |
| GET | `/api/job/<id>` | 单个任务快照 |
| GET | `/api/logs?since=N` | 增量日志（返回第 N 条之后） |
| GET | `/api/config` | 前端配置下发（分页大小 / 轮询间隔） |

---

## 音源优先级与品质规则

### 下载音源优先级

默认从高到低依次尝试，某音源失败自动降级到下一个：

```
QQ音乐 → 酷我音乐 → 网易云音乐 → 咪咕音乐 → 波点音乐 → 千千音乐 → JOOX
→ TwoT58 → TuneHub → B站音乐 → Soda音乐 → 歌曲宝 → 歌曲海 → 方皮音乐
→ 米兔音乐 → 音乐库 → MyFreeMP3 → 街声 → Audius → Jamendo
```

### 单首歌的完整处理流程

1. **搜索**：以 `歌名 歌手` 为关键词，按音源优先级逐个搜索。
2. **匹配挑选**（`_pick_best`）：
   - 只保留格式可播放的（跳过 mgg / mflac 加密容器）；
   - 歌名归一化后必须能对上（完全相等 > 包含 > 被包含，对不上直接淘汰）；
   - 歌手名命中加分；歌名带 Live / 翻唱 / 伴奏 / 铃声 / DJ 字样降权；
   - 排序：**匹配分 > 是否无损 > 文件体积**，取第一名为最佳候选。
3. **下载**：调用该音源客户端下载到本地缓存目录。
4. **本地保存**：把音频（及同名 `.lrc` 歌词）移动到 `<本地音乐目录>/<歌手名>/`，同名文件自动加序号。
5. **整理**：清理本次下载产生的 `.pkl` 元数据与空目录（不删除音频文件）。
6. **失败降级**：上面任何一步失败（搜索无匹配 / 下载失败 / 文件为空），自动换**下一个音源**重来；全部音源失败才标记该歌曲失败。

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 点下载没反应 | 看「下载列表」页有没有任务；没有则看「运行日志」页的报错。多为音源接口偶发失败，引擎会自动降级重试 |
| 某首歌显示"所有音源均未成功" | 各音源都没有精确匹配或都下载失败，可在日志页看每一步的降级原因 |
| 找不到下载的文件 | 默认保存在 `downloads/<歌手名>/` 下；可在 `config.json` 的 `download.local_dir` 查看 / 修改 |
| 端口被占用 | 改 `config.json` 的 `server.port` 后重启 |
| 咪咕榜单标签显示"不可用" | 咪咕官方榜单接口已停对外，属已知限制；下载不受影响 |
| 歌手歌曲数和平台官方不一致 | QQ 走搜索聚合（搜索上限约 600 条）、酷我封顶 500 首、网易云公开接口只有热门 50 首、咪咕走搜索聚合 |
| 下载的体积特别大（100MB+） | HiRes 母带级 FLAC，属正常（品质最高的优先规则） |

---

## 免责声明

本项目仅供**个人学习与测试**，请尊重各音乐平台版权及使用条款，勿用于任何商业用途或传播未授权内容。

- musicdl 依赖的音源接口随时可能变化，如大面积失败可升级：`pip install -U musicdl -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 各平台 Web 接口也可能随时调整（加签名 / 风控），`platform_api.py` 中每个适配器都有独立的异常兜底，单源失败自动降级，不会导致整站不可用。

---

## 许可证

本项目代码采用 [MIT License](LICENSE) 开源。

依赖的 [musicdl](https://github.com/CharlesPikachu/musicdl) 使用 [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)，**仅允许非商业用途**，使用前请阅读其条款。
