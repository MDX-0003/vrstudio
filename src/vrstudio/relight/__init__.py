"""重打光：GT 提供结构与细节，AI 氛围图只提供低频光照（决策 48）。

```text
align.py    相似变换的唯一权威点：SIFT+RANSAC 估计、相位相关回退、环绕 warp、可信度判据
fields.py   2K 上的照明表示：引导滤波低频、亮度增益、可见度、全局白点
compose.py  合成与高光软裁切
report.py   报告 JSON 与诊断图数组
```

模块内部是纯函数，不做文件 I/O、不含命令行；输入输出经由 8-bit BGR 数组。命令在
``vrstudio.cli.relight``。
"""
