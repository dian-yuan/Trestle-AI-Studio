"""Trestle 出片引擎 CLI。

只暴露确定性、刚需的命令：

    changji check [<unit>]
    changji asset check
    changji scene check [-u <unit>]
    changji segment check <段号> [-u <unit>]
    changji unique
    changji ready [-u <unit>]

不暴露：score / look / gate / done / status / next / note / save / log /
audit / pack / adapter / upgrade / tree / space / idea / script / split /
unit / asset new|list|level / segment new|list / board / build。

质量判断归用户审片，不归程序打分。
"""

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from . import checks
from . import ready as ready_mod

USAGE = """Trestle 出片引擎

  changji check [<unit>] [--cwd <工程根>]
  changji asset check [--cwd <工程根>]
  changji scene check [-u <unit>] [--cwd <工程根>]
  changji segment check <段号> [-u <unit>] [--cwd <工程根>]
  changji unique [--cwd <工程根>]
  changji ready [-u <unit>] [--cwd <工程根>]

只做确定性检查与准入；不打分、不审片、不打包。
"""


def _arg(argv, name, default=""):
    for i, x in enumerate(argv):
        if x == name and i + 1 < len(argv):
            return argv[i + 1]
        if x.startswith(name + "="):
            return x.split("=", 1)[1]
    return default


def _cwd(argv):
    return _arg(argv, "--cwd", ".") or "."


def _positionals(argv):
    out, i = [], 0
    while i < len(argv):
        x = argv[i]
        if x in ("--cwd", "-u", "--unit"):
            i += 2
            continue
        if x.startswith(("--cwd=", "-u=", "--unit=")):
            i += 1
            continue
        if not x.startswith("-"):
            out.append(x)
        i += 1
    return out


def _unit(argv, positional=False):
    u = _arg(argv, "-u", "") or _arg(argv, "--unit", "")
    if not u and positional:
        pos = _positionals(argv)
        if pos:
            return pos[0]
    return u


def _pos(argv):
    pos = _positionals(argv)
    return pos[0] if pos else ""


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(args)
    sys.stdout.write(buf.getvalue())
    return code


def _ready(argv):
    cwd = _cwd(argv)
    unit = _arg(argv, "-u", "") or _arg(argv, "--unit", "")
    try:
        res = ready_mod.check(Path(cwd), unit or None)
    except SystemExit:
        res = {"ok": False, "unit": unit, "checks": [
            {"name": "工程", "ok": False,
             "detail": "这里不是工程（缺 project.json）"}]}
    except Exception as e:
        res = {"ok": False, "unit": unit, "checks": [
            {"name": "引擎", "ok": False, "detail": str(e)[:500]}]}
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    return 0 if res.get("ok") else 1


def _deny(cmd):
    sys.stderr.write(
        "不提供 `%s`。\n%s\n"
        "质量判断由用户审片；程序只做检查与准入。\n" % (cmd, USAGE))
    return 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0

    cmd = argv[0]
    cwd = _cwd(argv)

    if cmd == "ready":
        return _ready(argv[1:])

    if cmd == "check":
        unit = _unit(argv[1:], positional=True)
        return _run(checks.check, argparse.Namespace(cwd=cwd, unit=unit or None))

    if cmd == "unique":
        return _run(checks.unique, argparse.Namespace(cwd=cwd))

    if cmd == "asset":
        if len(argv) < 2 or argv[1] != "check":
            return _deny("asset " + (argv[1] if len(argv) > 1 else ""))
        return _run(checks.asset_check, argparse.Namespace(cwd=cwd))

    if cmd == "scene":
        if len(argv) < 2 or argv[1] != "check":
            return _deny("scene " + (argv[1] if len(argv) > 1 else ""))
        unit = _unit(argv[2:])
        return _run(checks.scene_check,
                    argparse.Namespace(cwd=cwd, unit=unit or None))

    if cmd == "segment":
        if len(argv) < 2 or argv[1] != "check":
            return _deny("segment " + (argv[1] if len(argv) > 1 else ""))
        rest = argv[2:]
        seg = _pos(rest)
        if not seg:
            sys.stderr.write("用法：changji segment check <段号> [-u <unit>]\n")
            return 2
        unit = _unit(rest)
        return _run(checks.segment_check,
                    argparse.Namespace(cwd=cwd, unit=unit or None, seg=seg))

    return _deny(cmd)


if __name__ == "__main__":
    sys.exit(main())
