# bin/ — 机器本地外部二进制

放 ffmpeg.exe（以及 patch 流需要的 rife-ncnn-vulkan-*）。体积大，gitignore
（本 README 除外）。发现顺序：`bin/` 优先于 `PATH`。

ffmpeg 获取方式与旧仓库 vrpad 相同（`tools/fetch_bin.py --from <本机ffmpeg路径>`，
M2 迁入）。
