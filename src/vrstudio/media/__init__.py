"""统一媒体封装。

- videoio / binres（← vrpad）：bgr24 帧泵，fullredraw 域用
- ffmpeg（← vrpatch media.py）：bgr24 FfmpegSink + PATH 回退，patch 域用

两套发现/写入器暂时并存（迁移期零行为变化）。编码语义全仓库只有一个：
H.264 8-bit yuv420p（浏览器可直接播放）；4:4:4 档已废弃（决策 19）。
像素基线测试需要精确往返时显式钉 crf 0，不依赖生产默认值。
"""
