# vrstudio — 360° 视频流程仓库

> 状态：**M4 web 控制台可用**（patch 流程已通过浏览器验收；fullredraw 链路可跑通）。
> 架构与路线：`E:\VRAIGC\docs\`（architecture / case-model / roadmap）。

两条互斥的流程，一个 case 二选一：

| flow | 场景 | 链路 |
|---|---|---|
| `patch`（局部重绘） | UE 出全景，其中有白膜人物要重绘 | pick（视口/inner 草稿）→ extract（冻结 clip.json）→ **vrgen** → adopt → restore/scalefit → merge 贴回 |
| `fullredraw`（全景重绘） | AI 重绘整个 360° | pad（循环冗余画布 21:9）→ **vrgen** → adopt → recompose（配准+混合+还源宽） |

生成调用全部委托给 **vrgen**（`E:\VRAIGC\vrgen`，CLI 子进程，禁止互相 import）。
Web 控制台是总入口（M4），CLI 与 web 共享同一执行路径。

## 快速开始

```bash
uv sync --extra dev          # 装环境
uv run vrstudio --help       # 命令面
uv run pytest                # 本地门禁（等价于 CI 的 test job）
uv run ruff check .          # lint
```

## 启动 / 重启 web 控制台

本机使用（默认只监听 127.0.0.1，端口 8760）：

```bash
uv run vrstudio serve
```

启动后打开 <http://127.0.0.1:8760/>。需要让局域网其他机器访问时加 `--host`：

```bash
uv run vrstudio serve --host 0.0.0.0
```

前台运行时按 `Ctrl+C` 停止，再执行同一条命令就是重启。如果忘了服务开在哪个终端，可以在 PowerShell 里按端口结束进程再启动：

```powershell
Get-NetTCPConnection -LocalPort 8760 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
uv run vrstudio serve
```

注意：改了 `src/vrstudio/` 下的 Python 代码（包括 `web/app.py`）必须重启服务才会生效；只改模板或静态文件时刷新网页即可。`--cases-dir` 可指定别的 case 根目录，默认是本仓库的 `cases/`。

## 目录（目标态，见 docs/architecture.md §3）

```
src/vrstudio/cli/     # case/generate ★新增 + extract/merge/restore/pad/recompose/辅助 ←旧仓库迁入
src/vrstudio/patch/   # ← vrpatch 核心库（投影/合成/时间对齐/缩放拟合）
src/vrstudio/redraw/  # ← vrpad 核心库（几何/循环pad/混合/配准/重组）
src/vrstudio/media/   # 统一 ffmpeg 封装（唯一编码档：H.264 8-bit yuv420p）
src/vrstudio/web/     # ← vrpatch web 骨架扩展（M4）
cases/ sources/ logs/ bin/   # 运行期数据，gitignore
```

## 冻结契约（不许改字段，AGENTS.md 硬规则 2）

- `extracts/<ts>/clip.json`（单段，version=1）——patch 流几何契约
- `<stem>_pad.json` ——fullredraw 流画布↔源映射，recompose 唯一几何权威

## 生成session相关

```
mglc project list
mglc episode list --project-id 2060150527854528
mglc session list --project-id 2060150527854528 --episode 1
```

结果示例：

`project list`得到的是项目级别的信息，2060150527854528是项目id

```
PS E:\VRAIGC\vrstudio> mglc project list
{
  "code": 0,
  "data": {
    "items": [
      {
        "name": "2026大秦寻仙VR",
        "projectId": 2060150527854528,
        "projectType": null
      }
    ]
  },
  "msg": "OK"
}
```

每个项目下会有多个episode（剧集/章节），对应着ui界面可见的同名信息

```
PS E:\VRAIGC\vrstudio> mglc episode list --project-id 2060150527854528
{
  "code": 0,
  "data": [
    {
      "auditSummary": {},
      "createdAt": 1791801412,
      "curScriptId": 0,
      "description": "",
      "enterpriseId": 2060067127834560,
      "episodeName": "秦宫夜宴",
      "episodeNo": 1,
      "finishedShotCount": 0,
      "id": 2040714108516707,
      "isDelete": 0,
      "projectId": 2060150527854528,
      "shotCount": 0,
      "sortOrder": 0,
      "updatedAt": 1791978800
    },
    {
      "auditSummary": {},
      "createdAt": 1791978780,
      "curScriptId": 0,
      "description": "",
      "enterpriseId": 2060067127834560,
      "episodeName": "琅琊祭海",
      "episodeNo": 2,
      "finishedShotCount": 0,
      "id": 2040714108516818,
      "isDelete": 0,
      "projectId": 2060150527854528,
      "shotCount": 0,
      "sortOrder": 0,
      "updatedAt": 1791978789
    },
    {
      "auditSummary": {},
      "createdAt": 1792060450,
      "curScriptId": 0,
      "description": "",
      "enterpriseId": 2060067127834560,
      "episodeName": "巨人国",
      "episodeNo": 3,
      "finishedShotCount": 0,
      "id": 2040714108516878,
      "isDelete": 0,
      "projectId": 2060150527854528,
      "shotCount": 0,
      "sortOrder": 0,
      "updatedAt": 1792060458
    },
    {
      "auditSummary": {
        "refImages": {
          "failed": 0,
          "passed": 0,
          "unreviewed": 2
        }
      },
      "createdAt": 1792060460,
      "curScriptId": 0,
      "description": "",
      "enterpriseId": 2060067127834560,
      "episodeName": "巨鲸横海",
      "episodeNo": 4,
      "finishedShotCount": 0,
      "id": 2040714108516879,
      "isDelete": 0,
      "projectId": 2060150527854528,
      "shotCount": 2,
      "sortOrder": 0,
      "updatedAt": 1792060468
    },
    {
      "auditSummary": {},
      "createdAt": 1792060471,
      "curScriptId": 0,
      "description": "",
      "enterpriseId": 2060067127834560,
      "episodeName": "仙山幻境",
      "episodeNo": 5,
      "finishedShotCount": 0,
      "id": 2040714108516880,
      "isDelete": 0,
      "projectId": 2060150527854528,
      "shotCount": 0,
      "sortOrder": 0,
      "updatedAt": 1792060475
    }
  ],
  "msg": "OK"
}
```

注意区分episode和session，episode是"第X集"，session从属于某个episode，一个episode下面下面可以有多个session，所以`session list` 必须指定 `--episode`。

假如我想在第一集里创建一次生成会话，那么其所在层级为：

- 企业：`2060067127834560`
- 项目：`2060150527854528`（2026大秦寻仙VR）
- 剧集：`episode 1`，`episodeId 2040714108516707`
- 生成会话：`sessionId 2060150528006080`（一般我们需要去网页端新建一个“对话”，然后在自己的网页里刷新可见，如此一来，从个人网页端生成的视频，都会被归纳到同一个对话下）
