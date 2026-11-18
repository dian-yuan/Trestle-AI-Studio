"""文件监听：只读 mtime + size，不读内容。

每 1 秒扫一遍被监听的路径，算出哪些分区变了，给分区版本号 +1，
然后回调。写文件的人不需要通知它。

    w = Watcher(root, unit, on_change=fn)
    w.start(); w.versions(); w.stop()
"""

import threading
import time
from pathlib import Path

from .snapshot import SECTIONS, SKIP_DIRS


def _walk(base, exts=None):
    base = Path(base)
    if not base.exists():
        return
    for p in base.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(base).parts[:-1]):
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        yield p


def watch_paths(root, unit=None):
    """返回 (path, 是否目录)。unit 为空时监听所有单元。"""
    root = Path(root)
    out = [(root / "project.json", False)]
    udirs = []
    if unit:
        d = root / "units" / unit
        if d.exists():
            udirs.append(d)
    else:
        base = root / "units"
        if base.exists():
            udirs = [d for d in base.iterdir() if d.is_dir()]
    for u in udirs:
        for sub in ("storyboard", "prompts", "review", "script",
                    "renders", "keyframes", "segments"):
            out.extend((p, False) for p in _walk(u / sub))
    out.extend((p, False) for p in _walk(root / "assets"))
    out.extend((p, False) for p in _walk(root / "design"))
    return out


def scan(root, unit):
    out = {}
    for p, _isdir in watch_paths(root, unit):
        try:
            st = p.stat()
        except OSError:
            continue
        out[str(p)] = (st.st_mtime_ns, st.st_size)
    return out


def sections_of(path, root):
    """一个文件变化会影响哪些分区。"""
    rel = Path(path).relative_to(root).as_posix()
    low = rel.lower()

    if low.startswith("assets/"):
        return {"assets", "shots"}
    if rel == "project.json":
        return {"status", "assets"}
    if rel.endswith("/manifest.json"):
        return {"status", "assets"}
    if "/storyboard/" in low:
        if low.endswith((".md", ".html", ".htm")):
            return {"script", "scenes"}
        return {"scenes", "shots", "storyboards"}
    if "/prompts/" in low:
        return {"prompts", "shots"}
    if "/review/" in low:
        return {"review", "shots"} if "shot-review" in low else {"review"}
    if "/segments/" in low:
        return {"storyboards", "media", "shots"}
    if "/renders/" in low or "/keyframes/" in low:
        return {"media", "shots"}
    if "/script/" in low:
        return {"script", "scenes"}
    if low.startswith("design/"):
        return {"script"}
    if low.endswith((".md", ".html", ".htm")):
        return {"script"}
    return {"status"}


class Watcher(threading.Thread):
    def __init__(self, root, unit, interval=1.0, on_change=None):
        super().__init__(name="wb-watch", daemon=True)
        self.root = Path(root).resolve()
        self.unit = unit
        self.interval = interval
        self.on_change = on_change
        self.versions = {s: 0 for s in SECTIONS}
        self.last_change = 0.0
        self.file_count = 0
        self._stop = threading.Event()

    def run(self):
        prev = scan(self.root, self.unit)
        self.file_count = len(prev)
        while not self._stop.wait(self.interval):
            try:
                cur = scan(self.root, self.unit)
            except Exception:
                continue
            self.file_count = len(cur)
            if cur == prev:
                continue
            changed = set()
            for p in set(prev) | set(cur):
                if prev.get(p) != cur.get(p):
                    try:
                        changed |= sections_of(p, self.root)
                    except ValueError:
                        continue
            prev = cur
            if not changed:
                continue
            for s in changed:
                self.versions[s] = self.versions.get(s, 0) + 1
            self.last_change = time.time()
            if self.on_change:
                try:
                    self.on_change(sorted(changed))
                except Exception:
                    pass

    def snapshot_versions(self):
        return dict(self.versions)

    def stop(self):
        self._stop.set()
