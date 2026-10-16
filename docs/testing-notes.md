# 测试注意事项（事故记录）

## 2026-10-16：stub 冒烟测试挂死整个套件，输出文件只见到 ruff 的结果

**现象**：后台跑门禁 `uv run ruff check . && uv run pytest -q`，ruff 几秒出结果，
pytest 半个多小时不退出；输出文件里始终只有一行 `All checks passed!`，看不到
pytest 的任何进度。

**定位过程与证据**：

- 任务管理器与 `Get-CimInstance` 查看 pytest 进程（PID 33652/30592）：CPU 累计不足
  一秒、没有子进程、没有监听端口——不是在算，是卡在 Python 内部空等。
- `uvx py-spy dump --pid <pid>` 抓栈，逐帧显示挂点：
  `tests/test_cli.py:45 test_stubs_exit_2_until_implemented` →
  `vrstudio/cli/serve.py:26 serve` → `uvicorn run` → `asyncio run_forever` → 空等。

**原因（两件事叠加）**：

1. `test_cli.py` 的 `test_stubs_exit_2_until_implemented` 对 `STUB_COMMANDS` 里的
   每个命令做**裸调用**并断言退出码 2（stub 时代的契约）。M4 把 `serve` 实现成
   了真命令（启动 uvicorn 无限服务），但没把它从 `STUB_COMMANDS` 挪走——测试里
   那次 `runner.invoke(app, ["serve"])` 就真的启动了 web 服务器，`run_forever`
   永不返回，整个套件从此挂起。套件其余部分并不慢：真 ffmpeg 的 e2e
   （fullredraw/pad/recompose/extract/merge/restore/scalefit 的文件往返）合计
   一两分钟，其余一百多个是纯内存测试。
2. 输出文件只见 ruff 一行的原因：命令输出重定向到文件时 pytest 是块缓冲（攒满
   约 8KB 或进程退出才写出）；ruff 跑完退出时把自己的行刷了出去，pytest 因挂死
   永不退出，已跑完部分的输出全部停在缓冲区里。

**修复**：`serve` 挪进 `MIGRATED_COMMANDS`（走 `--help` 断言，不裸调用），
`STUB_COMMANDS` 清空保留结构供未来 stub 使用；杀掉挂死的 pytest 进程后门禁全绿。

**以后的规矩**：

- 实现任何 stub 命令的**同一次提交**必须把它从 `STUB_COMMANDS` 挪进
  `MIGRATED_COMMANDS`，并同步 `--help` 断言。
- stub 测试只允许容纳"裸调用立即返回退出码 2"的命令；预期会常驻或长时间运行的
  命令（serve、未来的 watch 类）永远不得进入 stub 集合。
- 门禁输出文件长时间只有第一段命令的结果、且进程 CPU 接近零时，优先怀疑挂死
  而不是慢：`uvx py-spy dump --pid <pid>` 一抓栈就能定位到帧。
