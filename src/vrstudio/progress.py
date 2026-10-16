"""Console output helpers.

Kept deliberately small: a level-prefixed log, and a rate meter that adapts
between a TTY (rewrite one line) and a pipe (one line per update). Long 8K passes
are otherwise indistinguishable from a hang.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


class Log:
    """Level-prefixed console log, optionally teed into a file.

    ``attach`` exists because some passes run for minutes and the terminal is not a
    record: a long wait whose only trace is scrollback cannot be inspected from
    another shell, and cannot be read at all after a Ctrl-C. The file gets every
    line regardless of ``quiet`` -- quiet is about the terminal, not about the record.
    """

    def __init__(self, quiet: bool = False, stream=None, file: str | Path | None = None):
        self.quiet = quiet
        self.stream = stream or sys.stderr
        self._file = None
        if file is not None:
            self.attach(file)

    def attach(self, path: str | Path) -> Path:
        """Append every subsequent line to ``path`` (created, with its parents)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        return path

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _emit(self, prefix: str, message: str) -> None:
        line = f"{prefix} {message}"
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()
        if self.quiet and prefix not in ("ERROR", "WARN"):
            return
        print(line, file=self.stream, flush=True)

    def info(self, message: str) -> None:
        self._emit("  ", message)

    def step(self, message: str) -> None:
        self._emit("->", message)

    def warn(self, message: str) -> None:
        self._emit("WARN", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", message)


class RateMeter:
    """Frames-per-second and ETA, refreshed at most a few times a second."""

    def __init__(self, total: int | None, log: Log, every_s: float = 0.5):
        self.total = total or 0
        self.log = log
        self.every_s = every_s
        self.start = time.monotonic()
        self._last = 0.0
        self.count = 0

    def tick(self, n: int = 1) -> None:
        self.count += n
        now = time.monotonic()
        if now - self._last < self.every_s:
            return
        self._last = now
        elapsed = now - self.start
        fps = self.count / elapsed if elapsed > 0 else 0.0
        if self.total:
            pct = 100.0 * self.count / self.total
            eta = (self.total - self.count) / fps if fps > 0 else 0.0
            line = f"{self.count}/{self.total} ({pct:5.1f}%)  {fps:5.1f} fps  eta {eta:5.0f}s"
        else:
            line = f"{self.count} frames  {fps:5.1f} fps"
        if sys.stderr.isatty():
            print(f"\r{line}   ", end="", file=sys.stderr, flush=True)
        else:
            print(f"   {line}", file=sys.stderr, flush=True)

    def done(self) -> float:
        elapsed = time.monotonic() - self.start
        if sys.stderr.isatty():
            print("\r" + " " * 72 + "\r", end="", file=sys.stderr, flush=True)
        return elapsed

    def summary(self) -> str:
        elapsed = time.monotonic() - self.start
        fps = self.count / elapsed if elapsed > 0 else 0.0
        return f"{self.count} frames in {elapsed:.1f}s ({fps:.1f} fps)"


# ── patch 流（← vrpatch progress.py，类改名避开与上面 vrpad 系的 Log/RateMeter 撞名）──
# vrpatch 的 cli 通过 `from ..progress import PhaseLog as Log` 使用它们，行为零变化。

def logs_dir() -> Path:
    """<project root>/logs — the repo root in a source checkout, cwd/logs as a
    fallback for exotic installs."""
    root = Path(__file__).resolve().parents[2]
    d = root / "logs" if (root / "pyproject.toml").exists() else Path.cwd() / "logs"
    d.mkdir(exist_ok=True)
    return d


def open_log_file(stem: str):
    """Create logs/<stem>_<timestamp>.log and return an open text handle."""
    path = logs_dir() / f"{stem}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    fh = open(path, "w", encoding="utf-8")
    return fh, path


def format_duration(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def clock():
    return time.strftime("%H:%M:%S")


class PhaseRateMeter:
    """Frame counter + throughput + ETA."""

    def __init__(self, total, unit="frames"):
        self.total = total
        self.unit = unit
        self.start = time.time()
        self.count = 0

    def tick(self, k=1):
        self.count += k

    @property
    def elapsed(self):
        return time.time() - self.start

    def eta(self):
        """Seconds remaining, or None until there is enough signal to guess."""
        if self.count <= 0 or self.count >= self.total:
            return 0.0
        rate = self.count / self.elapsed if self.elapsed > 0 else 0.0
        if rate <= 0:
            return None
        return (self.total - self.count) / rate

    def line(self, prefix):
        pct = 100.0 * self.count / self.total if self.total else 100.0
        rate = self.count / self.elapsed if self.elapsed > 0 else 0.0
        eta = self.eta()
        eta_s = "?" if eta is None else format_duration(eta)
        return (f"{prefix} {self.count}/{self.total} ({pct:5.1f}%) "
                f"{rate:5.1f} {self.unit}/s  elapsed "
                f"{format_duration(self.elapsed)}  ETA {eta_s}")


class PhaseLog:
    """Phase lines plus a live progress line that behaves when redirected.

    `every=0` (the default) picks the interval automatically: on a terminal the
    status line is rewritten in place, and when redirected it emits roughly 20
    lines per phase, so `> run.log` stays readable without being a firehose.

    `fh`: an optional log file opened by :func:`open_log_file`. Everything that
    reaches the console is mirrored there — one run, one timestamped file — so
    a run stays auditable after the terminal scrollback is gone.
    """

    def __init__(self, every=0, quiet=False, lines_per_phase=20, fh=None):
        self.tty = sys.stdout.isatty()
        self.every = every
        self.lines_per_phase = lines_per_phase
        self.quiet = quiet
        self.fh = fh

    def _interval(self, meter):
        if self.every > 0:
            return self.every
        return max(1, meter.total // self.lines_per_phase)

    def _to_file(self, line):
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()

    def phase(self, msg):
        self._end_line()
        line = f"[{clock()}] {msg}"
        print(line, flush=True)
        self._to_file(line)

    def info(self, msg):
        self._end_line()
        line = f"          {msg}"
        print(line, flush=True)
        self._to_file(line)

    def progress(self, meter, prefix):
        self._to_file(meter.line(prefix))
        if self.quiet:
            return
        if self.tty:
            sys.stdout.write("\r" + meter.line(prefix))
            sys.stdout.flush()
        elif meter.count % self._interval(meter) == 0 or meter.count >= meter.total:
            print(meter.line(prefix), flush=True)

    def _end_line(self):
        if self.tty:
            sys.stdout.write("\r" + " " * 100 + "\r")
            sys.stdout.flush()

    def done(self):
        self._end_line()
