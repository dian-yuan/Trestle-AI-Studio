"""保留的命令入口。

只暴露确定性、刚需的检查：

    check
    asset check
    scene check
    segment check <段号>
    unique

score / look / gate / status / pack 等旧命令不再从这里出去。
"""
import argparse

from . import core


def check(args):
    return core.cmd_check(args) or 0


def asset_check(args):
    return core.cmd_asset_check(args) or 0


def scene_check(args):
    return core.cmd_scene(argparse.Namespace(
        cwd=getattr(args, "cwd", "."),
        unit=getattr(args, "unit", None),
        s_cmd="check",
    )) or 0


def segment_check(args):
    return core.cmd_segment(argparse.Namespace(
        cwd=getattr(args, "cwd", "."),
        unit=getattr(args, "unit", None),
        s_cmd="check",
        seg=getattr(args, "seg", ""),
        scene="",
        title="",
        sec=0,
    )) or 0


def unique(args):
    return core.cmd_unique(args) or 0
