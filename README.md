# vrstudio — 360° 视频流程仓库

> 状态：**M0 脚手架**。CLI 命令面已按架构声明（stub），实现未开始。
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

## 目录（目标态，见 docs/architecture.md §3）

```
src/vrstudio/cli/     # case/generate ★新增 + extract/merge/restore/pad/recompose/辅助 ←旧仓库迁入
src/vrstudio/patch/   # ← vrpatch 核心库（投影/合成/时间对齐/缩放拟合）
src/vrstudio/redraw/  # ← vrpad 核心库（几何/循环pad/混合/配准/重组）
src/vrstudio/media/   # 统一 ffmpeg 封装（三档编码 profile）
src/vrstudio/web/     # ← vrpatch web 骨架扩展（M4）
cases/ sources/ logs/ bin/   # 运行期数据，gitignore
```

## 冻结契约（不许改字段，AGENTS.md 硬规则 2）

- `extracts/<ts>/clip.json`（单段，version=1）——patch 流几何契约
- `<stem>_pad.json` ——fullredraw 流画布↔源映射，recompose 唯一几何权威
