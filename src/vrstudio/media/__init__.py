"""统一媒体封装。

- videoio / binres（← vrpad）：bgr48le 16-bit 帧泵，fullredraw 域用
- ffmpeg（← vrpatch media.py）：bgr48le FfmpegSink + PATH 回退，patch 域用

两套发现/写入器暂时并存。编码语义全仓库只有一个（决策 99）：libx265
HEVC Main10 10-bit yuv420p10le；浏览器预览代理仍保留 H.264 8-bit yuv420p。
像素基线测试需要精确往返时显式钉 crf 0，不依赖生产默认值。
"""
