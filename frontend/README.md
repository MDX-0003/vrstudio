# vrstudio 新前端

Vue 3 + TypeScript + Vite + Element Plus。当前是 Phase 0 原型，使用页面内数据，不调用后端接口。

## 环境

- Node.js >= 22.12（Vite 8 的要求）。
- 依赖使用 npm 安装，锁文件是 `package-lock.json`。

## 开发

```powershell
cd E:\VRAIGC\vrstudio\frontend
npm install
npm run dev
```

开发服务器默认在 `http://127.0.0.1:5173/next/`，并把 `/api`、`/img`、`/video` 代理到 `http://127.0.0.1:8760`。启动代理目标前需要先运行 `uv run vrstudio serve`。

## 构建

```powershell
npm run typecheck
npm run build
```

构建产物写入 `vrstudio/src/vrstudio/web/static/next/`，该目录不进 git。Starlette 在 `/next` 挂载这份产物；旧前端仍在 `/`。

## Phase 0 原型范围

- 已实现：应用壳、左侧导航、case 列表、新建 case 抽屉、生成页、提示词抽屉、候选与最近成片区、底部提交条、任务中心与设置的占位页。
- 未实现：真实接口、上传、身份、任务轮询、选区拖框、首尾帧候选、对比画布。按 `docs/plan/frontend-refactor-plan.md` 的 Phase 1–4 接入。

## 设计

视觉方向、颜色、字体、间距与布局见 `DESIGN.md`。
