"""统一媒体封装。

- videoio / binres（← vrpad）：yuv444p 无损帧泵，fullredraw 域用
- ffmpeg（← vrpatch media.py）：bgr24 FfmpegSink + PATH 回退，patch 域用

两套发现/写入器暂时并存（迁移期零行为变化）；编码语义的统一 profile
（lossless444 / ai14 / delivery）在此模块文档化，由随迁基线测试守护。
"""
