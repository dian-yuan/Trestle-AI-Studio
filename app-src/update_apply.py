#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trestle 更新助手：在启动器退出后拉起安装器，并把结果写进日志。

为什么要单独一个进程：
- 启动器必须先退出，安装器才能替换文件；
- 安装器在 Program Files 里需要管理员授权，必须显式 runas；
- 失败时要把旧版拉回来，不能把用户晾在“窗口关了、没反应”。

这个文件由 build_portable.py 复制到 app/update_apply.py。
它只用标准库，不 import 工程里的任何模块。
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def log(path, text):
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def msgbox(text):
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), "Trestle", 0x30)
    except Exception:
        pass


def write_state(log_path, stage, error=""):
    """和启动器共用的最近更新状态；下次启动时界面能提示「上次没完成」。"""
    try:
        p = Path(log_path).with_name("last-update.json")
        obj = {}
        if p.is_file():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                obj = {}
        obj.update({"stage": stage,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if error:
            obj["error"] = str(error)[:300]
        elif stage in ("done", "installing", "starting"):
            obj.pop("error", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    except Exception:
        pass


def wait_pid(pid, timeout=60.0):
    if not pid:
        time.sleep(2.5)
        return
    try:
        import ctypes as _c
        k32 = _c.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            time.sleep(2.5)
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                break
            if code.value != 259:  # STILL_ACTIVE
                break
            time.sleep(0.15)
        k32.CloseHandle(h)
    except Exception:
        time.sleep(2.5)


def _param_text(params):
    """把参数列表拼成 Windows 命令行；带空格的路径必须加引号。

    之前直接 str(list)，安装器收到的是 "['/SILENT', ...]" 这种字符串，
    /ALLUSERS 和 /DIR 都没生效，结果弹出了安装向导。
    """
    out = []
    for p in params:
        p = str(p)
        if any(c in p for c in (" ", "\t", '"')):
            p = '"' + p.replace('"', '\\"') + '"'
        out.append(p)
    return " ".join(out)


def run_elevated(setup, params, workdir):
    """用 UAC 的 runas 起安装器，返回 (exit_code, error)。"""
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = str(setup)
    sei.lpParameters = _param_text(params)
    sei.lpDirectory = str(workdir)
    sei.nShow = SW_SHOWNORMAL
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        return None, err
    h = sei.hProcess
    if not h:
        return 0, 0
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        k32.WaitForSingleObject(h, INFINITE)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return int(code.value), 0
    finally:
        k32.CloseHandle(h)


def run_normal(setup, params, workdir):
    try:
        p = subprocess.Popen([str(setup)] + list(params), cwd=str(workdir),
                             close_fds=True)
        return int(p.wait()), 0
    except Exception as e:
        return None, str(e)


def relaunch(app_dir):
    app = Path(app_dir)
    exe = app / "Changji.exe"
    if exe.is_file():
        try:
            subprocess.Popen([str(exe)], cwd=str(app), close_fds=True)
            return
        except Exception as e:
            log(app_dir, "拉回旧版失败：%s" % e)
    py = app / "runtime" / "pythonw.exe"
    if not py.is_file():
        py = app / "runtime" / "python.exe"
    script = app / "launcher.py"
    if py.is_file() and script.is_file():
        try:
            subprocess.Popen([str(py), str(script)], cwd=str(app), close_fds=True)
            return
        except Exception as e:
            log(app_dir, "拉回旧版（python）失败：%s" % e)
    msgbox("Trestle 没有启动。\n请手动双击：\n%s" % (app / "Changji.exe"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--mode", default="CURRENTUSER")
    ap.add_argument("--log", required=True)
    ap.add_argument("--wait-pid", type=int, default=0)
    a = ap.parse_args()

    setup = Path(a.setup)
    app_dir = Path(a.app_dir)
    write_state(a.log, "starting")
    log(a.log, "更新助手启动：setup=%s app=%s mode=%s pid=%s"
        % (setup, app_dir, a.mode, a.wait_pid))
    if not setup.is_file():
        log(a.log, "安装包不存在")
        write_state(a.log, "failed", "安装包不存在：%s" % setup)
        msgbox("更新包不见了：\n%s\n\n请重新下载更新。" % setup)
        relaunch(app_dir)
        return 2

    wait_pid(a.wait_pid)
    params = ["/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS",
              "/DIR=%s" % app_dir, "/%s" % a.mode, "/LOG=%s" % a.log]
    write_state(a.log, "installing")
    log(a.log, "开始安装：%s" % " ".join([str(setup)] + params))
    if str(a.mode).upper() == "ALLUSERS":
        code, err = run_elevated(setup, params, app_dir)
    else:
        code, err = run_normal(setup, params, app_dir)

    if err:
        log(a.log, "安装器没有启动：%s" % err)
        if int(err) == ERROR_CANCELLED:
            write_state(a.log, "failed", "管理员授权被取消")
            msgbox("更新已取消（没有获得管理员授权）。\n\nTrestle 已重新打开，"
                   "需要更新时再点一次。")
        else:
            write_state(a.log, "failed", "安装器没有启动：%s" % err)
            msgbox("更新没有启动（错误 %s）。\n\n请手动运行：\n%s\n\n日志：\n%s"
                   % (err, setup, a.log))
        relaunch(app_dir)
        return 1

    log(a.log, "安装器退出码：%s" % code)
    if code != 0:
        write_state(a.log, "failed", "安装器退出码 %s" % code)
        msgbox("更新未完成（退出码 %s）。\n\nTrestle 已重新打开；"
               "也可以手动运行：\n%s\n\n日志：\n%s"
               % (code, setup, a.log))
        relaunch(app_dir)
        return int(code or 1)

    log(a.log, "安装成功，启动新版")
    write_state(a.log, "done")
    time.sleep(1.0)
    relaunch(app_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
