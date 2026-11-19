# vrstudio 开发守则

工作区级规范在 `E:\VRAIGC\AGENTS.md`（硬规则 1–7 全部适用），本文只记本仓库要点：

- **定位**：两条流程（patch / fullredraw）+ 几何/像素库 + web 控制台。生成调用一律
  委托 vrgen（CLI 子进程），本仓库不做任何平台交互、不持有登录态。
- **case.toml 只放静态事实**（格式：`docs/case-model.md` §2）：生成侧的一切
  （提示词/task_id/台账）都在 vrgen，case 里只有一行 `[adopt]` 指针。
- **几何/像素改动**：必须 pytest 全绿（含随迁的恒等式与 maxdiff==0 基线）再提交
  （硬规则 4）；编码语义只经 `media/` 的显式 profile（16-bit 帧泵 + HEVC Main10 交付档，浏览器预览代理单独一档，决策 99）。
- **配置作用域**：安装版读写 `%APPDATA%\vrstudio\default.toml`，源码检出只读仓库根 `vrstudio.toml`，`config set/show` 按形态选写入口（决策 103）。
- **旧仓库只读**：`E:\360AIGC\vrpatch`、`E:\360AIGC\vrpad` 是搬运来源与对照基线，
  不回头改。
