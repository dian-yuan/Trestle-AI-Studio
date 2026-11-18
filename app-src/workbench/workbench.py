#!/usr/bin/env python3
"""审片台（场 → 分镜 → 镜头）。

    python bin/workbench.py -u S1-E01 [--port 8462] [--no-open]
    python bin/workbench.py --bg
    python bin/workbench.py --stop | --status | --open
    python bin/workbench.py --selftest

真正的实现都在 wb/ 包里：snapshot / watch / views / server。
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from wb import server  # noqa: E402


def _state(root):
    return server._read_state(root)


def _open(root):
    st = _state(root)
    if not st or not server._ping(st.get("port")):
        print("工作台没有在跑。先启动：npm run dev", file=sys.stderr)
        return 1
    webbrowser.open(st["url"])
    print("已打开 " + st["url"])
    return 0


def _bg(root, unit, port, open_browser):
    argv = [sys.executable, str(Path(__file__).resolve()),
            "-u", unit, "--cwd", str(root), "--port", str(port), "--no-open"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.DETACHED_PROCESS |
                               subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)
    for _ in range(80):
        time.sleep(0.1)
        st = _state(root)
        if st and server._ping(st.get("port"), 0.3):
            print("")
            print("  %s" % st.get("url"))
            print("  单元 %s ｜ 后台运行 ｜ 停止 npm run stop" % st.get("unit"))
            print("")
            if open_browser:
                webbrowser.open(st["url"])
            return 0
    print("后台启动超时。用前台跑一次看错误：python bin/workbench.py -u %s" % unit,
          file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description="审片台（场 → 分镜 → 镜头）")
    ap.add_argument("-u", "--unit", default=None)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--port", type=int, default=8462)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--bg", action="store_true", help="后台启动")
    ap.add_argument("--stop", action="store_true", help="停止")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--open", action="store_true", help="只打开浏览器")
    ap.add_argument("--selftest", action="store_true", help="自检")
    args = ap.parse_args()

    root = Path(args.cwd).expanduser().resolve()

    if args.selftest:
        from wb.selftest import run
        return run(root)
    if args.stop:
        return server.stop(root)
    if args.status:
        return server.status(root)
    if args.open:
        return _open(root)

    # 已经在跑就不起第二个
    st = _state(root)
    if st and server._ping(st.get("port"), 0.4):
        print("工作台已经在跑：%s" % st.get("url"))
        if not args.no_open:
            webbrowser.open(st["url"])
        return 0

    if args.bg:
        return _bg(root, args.unit or "", args.port, not args.no_open)

    return server.serve(root, args.unit, port=args.port,
                        open_browser=not args.no_open)


if __name__ == "__main__":
    sys.exit(main())
