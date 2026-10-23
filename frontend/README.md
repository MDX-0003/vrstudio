# vrstudio 新前端

Vue 3 + TypeScript + Vite + Element Plus。当前是 Phase 0 原型，使用页面内数据，不调用后端接口。

## 环境

- Node.js >= 22.12（Vite 8 的要求）。
- 依赖使用 npm 安装，锁文件是 `package-lock.json`。

## 运行方式

开发模式使用两个进程，两个终端。

1. 终端 A 启动后端：

```powershell
cd E:\VRAIGC\vrstudio
uv run vrstudio serve --host 127.0.0.1 --port 8760
```

2. 终端 B 启动前端：

```powershell
cd E:\VRAIGC\vrstudio\frontend
npm install
npm run dev
```

3. 浏览器打开 `http://127.0.0.1:5173/next/`。

Vite 只负责页面与热更新，并把 `/api`、`/img`、`/video` 代理到 `http://127.0.0.1:8760`。后端没有启动时，列表会显示 502；这是开发模式下的预期错误，先启动后端再刷新。

运行模式只使用一个进程。

1. 构建前端：

```powershell
cd E:\VRAIGC\vrstudio\frontend
npm run typecheck
npm run build
```

2. 启动服务：

```powershell
cd E:\VRAIGC\vrstudio
uv run vrstudio serve --host 0.0.0.0 --port 8760
```

3. 浏览器打开 `http://<服务器地址>:8760/next/`。

构建产物写入 `vrstudio/src/vrstudio/web/static/next/`，该目录不进 git。Starlette 在 `/next` 挂载这份产物；旧前端仍在 `/`。运行模式不需要 Node 与 npm，局域网同事只访问 8760。

## Phase 0 原型范围

- 已实现：应用壳、左侧导航、case 列表、新建 case 抽屉、生成页、提示词抽屉、候选与最近成片区、底部提交条、任务中心与设置的占位页。
- 未实现：真实接口、上传、身份、任务轮询、选区拖框、首尾帧候选、对比画布。按 `docs/plan/frontend-refactor-plan.md` 的 Phase 1–4 接入。

## 设计

视觉方向、颜色、字体、间距与布局见 `DESIGN.md`。
