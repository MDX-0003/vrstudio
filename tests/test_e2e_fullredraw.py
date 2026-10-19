"""M3 验收：fullredraw 全流程端到端——真 CLI 子进程、真 vrgen、假模型返回。

链路：case new → pad → （ffmpeg 缩放当"模型返回"）→ vrgen register →
generate adopt → recompose → 断言输出恒为源尺寸 + case.toml 记账完整。

跳过条件：本地 bin/ 没有 ffmpeg，或找不到 vrgen 可执行文件（CI 上跳过）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vrstudio import config as config_mod
from vrstudio.media.binres import ffmpeg, find_tool
from vrstudio.media.videoio import probe
from tests.test_videoio_smoke import W, H, _closed_erp, _write

VRGEN_EXE = (Path(__file__).resolve().parents[2]      # E:\VRAIGC
             / "vrgen" / ".venv" / "Scripts" / "vrgen.exe")

pytestmark = pytest.mark.skipif(
    find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None
    or not VRGEN_EXE.is_file(),
    reason="需要本地 ffmpeg/ffprobe 与 vrgen 可执行文件（E:\\VRAIGC\\vrgen\\.venv）",
)


def test_fullredraw_end_to_end(tmp_path):
    conf = config_mod.load()
    root = config_mod.REPO_ROOT
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["VRSTUDIO_BIN_DIR"] = str(root / "bin")

    def vr(*args, expect=0):
        r = subprocess.run([sys.executable, "-m", "vrstudio", *args],
                           cwd=root, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == expect, (r.stdout + r.stderr)
        return r

    cases = tmp_path / "cases"
    workdir = tmp_path / "vrgen_runs"

    # 1) 源 + case
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)
    vr("case", "new", "e2e", "--flow", "fullredraw", "--source", str(src),
       "--cases-dir", str(cases))
    cdir = cases / "e2e"

    # 2) pad（画布 + mask + manifest）
    vr("pad", "--in", str(src), "--out", str(cdir / "seedance_input.mp4"),
       "--quiet", "--set", "video.preset=ultrafast")
    manifest = cdir / "seedance_input_pad.json"
    assert manifest.is_file()

    # 3) 假装模型返回了 21:9 的低分辨率件（缩放即可，几何契约要求比例错也能重组）
    gen = tmp_path / "gen.mp4"
    r = subprocess.run([ffmpeg(conf), "-y", "-v", "error", "-i",
                        str(cdir / "seedance_input.mp4"),
                        "-vf", "scale=252:108", "-c:v", "libx264", "-crf", "12",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(gen)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[:300]

    # 4) vrgen 手动后端登记这一轮
    r = subprocess.run([str(VRGEN_EXE), "register", "--job", "e2e", "--file", str(gen),
                        "--workdir", str(workdir)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr

    # 5) generate adopt：结果拷进 case + 写 [adopt] round
    vr("generate", "adopt", "--case", "e2e", "--cases-dir", str(cases),
       "--set", f"generate.vrgen={VRGEN_EXE}",
       "--set", f"generate.workdir={workdir}")
    assert (cdir / "result.mp4").is_file()
    case_text = (cdir / "case.toml").read_text(encoding="utf-8")
    assert "[adopt]" in case_text and "manual-" in case_text

    # 6) recompose：闸门 + 输出恒为源尺寸
    final = cdir / "out" / "manual.mp4"
    vr("recompose", "--in", str(cdir / "result.mp4"), "--manifest", str(manifest),
       "--out", str(final), "--quiet", "--set", "video.preset=ultrafast")
    assert probe(final, conf).size == (W, H)
    assert probe(final, conf).pix_fmt == "yuv420p"
    assert final.with_name("manual_recompose.json").is_file()

    # 7) vrgen status 也该认账（同构台账）
    r = subprocess.run([str(VRGEN_EXE), "status", "--job", "e2e",
                        "--workdir", str(workdir)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "manual-" in r.stdout
