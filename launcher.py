#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trestle · 启动器。

双击 → 项目选择页（Edge 应用窗口）→ 选中工程 → 同一个窗口进工作台。
关掉窗口，启动的本地服务一起停。

只用标准库。打包版用内置 runtime，源码目录里直接跑用当前解释器。

    python launcher.py              正常启动
    python launcher.py --no-window  只起选择页服务并打印地址（调试用）
    python launcher.py --status     看当前几个工程在跑
    python launcher.py --stop       停掉全部
"""

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent
APP_NAME = "Trestle"
APP_ID = "Changji"          # 运行态目录用 ASCII 名
APP_SUB = "AI 视频工程台"
VERSION = "1.0.0-beta.5"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
EDGE = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
MAX_RECENT = 8
_FFMPEG_CACHE = {}
_FFMPEG_INFO_CACHE = {}


def ffmpeg_path():
    """ffmpeg 在哪：CJ_FFMPEG → 安装包 runtime → 开发机 vendor → PATH。"""
    if "path" in _FFMPEG_CACHE:
        return _FFMPEG_CACHE["path"]
    env = os.environ.get("CJ_FFMPEG")
    if env:
        p = Path(env)
        for x in ((p / "ffmpeg.exe") if p.is_dir() else p, p):
            try:
                if x.is_file():
                    _FFMPEG_CACHE["path"] = str(x)
                    return str(x)
            except OSError:
                pass
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for p in (APP / "runtime" / "ffmpeg" / exe,
              APP / "vendor" / "ffmpeg" / exe):
        try:
            if p.is_file():
                _FFMPEG_CACHE["path"] = str(p)
                return str(p)
        except OSError:
            pass
    _FFMPEG_CACHE["path"] = shutil.which("ffmpeg") or ""
    return _FFMPEG_CACHE["path"]


def ffmpeg_info():
    if "info" in _FFMPEG_INFO_CACHE:
        return _FFMPEG_INFO_CACHE["info"]
    path = ffmpeg_path()
    info = {"ready": False, "path": path, "version": "",
            "bundled": bool(path and path.lower().startswith(str(APP).lower()))}
    if path:
        try:
            r = subprocess.run([path, "-version"], capture_output=True,
                               text=True, timeout=8,
                               creationflags=CREATE_NO_WINDOW)
            first = (r.stdout or "").splitlines()[0] if r.stdout else ""
            info["version"] = (first.replace("ffmpeg version ", "").split()[0]
                               if first.startswith("ffmpeg version ") else first[:80])
            info["ready"] = r.returncode == 0
        except Exception as e:
            info["error"] = str(e)[:160]
    _FFMPEG_INFO_CACHE["info"] = info
    return info


def edge_path():
    for p in EDGE:
        try:
            if p.is_file():
                return str(p)
        except OSError:
            pass
    return ""


def disk_free_gb(path=None):
    try:
        return round(shutil.disk_usage(str(path or APP)).free / 1073741824.0, 1)
    except Exception:
        return None


def env_rows():
    """启动器环境检查：一次算好，面板、向导、诊断共用。"""
    ff = ffmpeg_info()
    ep = edge_path()
    free = disk_free_gb()
    wb = workbuddy_exe()
    cl = shutil.which("claude") or ""
    sk = skill_path()
    st = skill_status()
    ver = st.get("version") or ""
    if ff.get("ready"):
        ff_desc = ("内置 " if ff.get("bundled") else "系统 ") + (ff.get("version") or "可用")
    else:
        ff_desc = "未找到"
    return [
        {"key": "agent", "label": "WorkBuddy / Claude",
         "ready": bool(wb or cl),
         "detail": ("WorkBuddy" if wb else ("Claude Code" if cl else "未安装"))},
        {"key": "skill", "label": "导演 skill",
         "ready": bool(sk), "detail": ("内置" + (" v" + ver if ver else ""))
         if sk else "未找到"},
        {"key": "ffmpeg", "label": "ffmpeg", "ready": bool(ff.get("ready")),
         "detail": ff_desc},
        {"key": "disk", "label": "磁盘",
         "ready": free is None or free >= 2,
         "detail": ("剩 %.1f GB" % free) if free is not None else "读不到"},
        {"key": "edge", "label": "Edge / WebView2",
         "ready": bool(ep) if os.name == "nt" else True,
         "detail": ep or "未找到"},
        {"key": "libtv", "label": "LibTV", "ready": None,
         "detail": "出片前再连，不阻塞看结构", "optional": True},
    ]


def env_report_text():
    lines = ["Trestle %s 环境检查 · %s" % (active_version(), time.strftime("%Y-%m-%d %H:%M"))]
    for r in env_rows():
        mark = "○" if r.get("optional") else ("✓" if r.get("ready") else "✗")
        lines.append("%s %s：%s" % (mark, r["label"], r.get("detail") or ""))
    lines.append("安装目录：%s" % APP)
    lines.append("系统：%s" % platform.platform())
    lines += ["", "反馈请补充：", "1. 卡在哪一步", "2. 期望什么",
              "3. 实际发生了什么", "4. 截图或录屏", "5. 是否愿意再用"]
    return "\n".join(lines)


def env_fix_prompt(report=None):
    report = report or env_report_text()
    return (
        "你是 Trestle 的环境助手。请按「一次一步」教我把下面的缺项修好；"
        "需要执行命令时先说明会发生什么，等我确认。\n"
        "不要修改我的工程内容，不要生成、布线或调用 LibTV。\n\n"
        + report)


def env_report_payload():
    return {
        "text": env_report_text(),
        "rows": env_rows(),
        "version": active_version(),
        "install_dir": str(APP),
        "system": platform.platform(),
        "workbuddy": workbuddy_exe(),
        "claude": shutil.which("claude") or "",
        "ffmpeg": ffmpeg_info(),
        "edge": {"ready": bool(edge_path()), "path": edge_path()},
        "disk": {"free_gb": disk_free_gb()},
    }


# ---------------------------------------------------------------- 环境

def runtime_python(console=False):
    """打包版用内置 runtime；源码目录里跑用当前解释器。"""
    override = os.environ.get("CJ_PYTHON")
    if override:
        return Path(override)
    rt = APP / "runtime"
    if rt.is_dir():
        for name in (("python.exe" if console else "pythonw.exe"), "python.exe"):
            p = rt / name
            if p.exists():
                return p
    return Path(sys.executable)


def workbench_script():
    cands = []
    ha = hot_app_dir()
    if ha:
        cands.append(ha / "workbench" / "workbench.py")
    env = os.environ.get("CJ_WORKBENCH")
    if env:
        cands.append(Path(env))
    cands += [
        APP / "workbench" / "workbench.py",
        APP / "app" / "workbench" / "workbench.py",
        APP / "tools" / "workbench" / "workbench.py",
    ]
    for p in cands:
        if p.is_file():
            return p
    raise RuntimeError("找不到工作台：workbench.py")


def config_dir():
    env = os.environ.get("CJ_CONFIG_DIR")
    if env:
        d = Path(env)
    else:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        d = (Path(base) / APP_ID) if base else (Path.home() / ".changji")
        if base:                       # 从旧的中文运行态目录迁过来
            old = Path(base) / "场记"   # 旧版中文运行态目录，只用于迁移
            if not d.exists() and old.is_dir():
                try:
                    old.rename(d)
                except OSError:
                    pass
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_ptr_path():
    return config_dir() / "current.json"


def hot_app_dir():
    """热更新版本目录里的 app/。只有热版本高于安装版本时才用。"""
    try:
        ptr = _jload(current_ptr_path(), {}) or {}
        hv = str(ptr.get("version") or "")
        if not hv or _ver(hv) <= _ver(VERSION):
            return None
        d = Path(str(ptr.get("dir") or ""))
        app = d / "app"
        if app.is_dir() and (app / "ui" / "picker.html").is_file():
            return app
    except Exception:
        pass
    return None


def skill_ptr_path():
    return config_dir() / "skill-current.json"


def hot_skill_dir():
    try:
        ptr = _jload(skill_ptr_path(), {}) or {}
        d = Path(str(ptr.get("dir") or ""))
        if (d / "SKILL.md").is_file():
            return d
    except Exception:
        pass
    return None


def template_root():
    ha = hot_app_dir()
    if ha and (ha / "templates").is_dir():
        return ha / "templates"
    env = os.environ.get("CJ_TEMPLATES")
    if env:
        return Path(env)
    for d in (APP / "templates", APP / "app" / "templates"):
        if d.is_dir():
            return d
    return APP / "templates"


def _pick_skill(cands):
    best, bestv, bestrank = None, None, -1
    active = globals().get("WORKBUDDY_SKILL", "")
    for d in cands:
        if not d:
            continue
        d = Path(d)
        if not (d / "SKILL.md").is_file():
            continue
        v = _ver(_skill_version(d))
        rank = 1 if Path(d).name == active else 0
        if best is None or (rank, v, str(d)) > (bestrank, bestv, str(best)):
            best, bestv, bestrank = d, v, rank
    return best


def _skill_names():
    active = globals().get("WORKBUDDY_SKILL", "")
    return [active] if active else []


def skill_path():
    """导演 skill 在哪。只找内置 / 热更新 / 安装包；不读 Agent 用户目录。"""
    env = os.environ.get("CJ_SKILLS")
    if env:
        for name in _skill_names():
            d = Path(env) / name
            if (d / "SKILL.md").is_file():
                return d
    cands = [hot_skill_dir()]
    ha = hot_app_dir()
    for name in _skill_names():
        if ha:
            cands.append(ha / "skills" / name)
        cands += [
            APP / "skills" / name,
            APP / "app" / "skills" / name,
            APP / "app-src" / "skill" / name,
        ]
    return _pick_skill(cands)


def bundled_skill_path():
    """安装包 / 热更新 / 单独 skill 包里那份（不是用户目录里的）。"""
    cands = [hot_skill_dir()]
    ha = hot_app_dir()
    for name in _skill_names():
        if ha:
            cands.append(ha / "skills" / name)
        cands += [
            APP / "skills" / name,
            APP / "app" / "skills" / name,
            APP / "app-src" / "skill" / name,
        ]
    return _pick_skill(cands)


def guide_skill_path():
    """内置 Trestle 教师在哪。核心资料不落到用户目录，只写一个桥接 skill。"""
    cands = []
    ha = hot_app_dir()
    if ha:
        cands.append(ha / "skills" / GUIDE_SKILL)
    cands += [
        APP / "skills" / GUIDE_SKILL,
        APP / "app" / "skills" / GUIDE_SKILL,
        APP / "app-src" / "skill" / GUIDE_SKILL,
    ]
    return _pick_skill(cands)


def sync_guide_bridge():
    """给 WorkBuddy 写一个极小的桥接 skill：只指向内置教师资料。"""
    src = guide_skill_path()
    if not src:
        return None
    text = (
        "---\n"
        "name: changji-guide\n"
        "description: Trestle 教师桥接。先读内置 Trestle 教师资料，再按规则回答。\n"
        "version: 0.1.0\n"
        "user_invocable: true\n"
        "allowed-tools: Read, Glob, Grep\n"
        "---\n\n"
        "# Trestle 教师\n\n"
        "先读 `%s`，按它的规则回答。\n"
        "资料目录：`%s`。\n"
        "只教，不写工程文件、不生成、不布线。\n"
        % (src / "SKILL.md", src / "references")
    )
    changed = False
    for target in (
        Path.home() / ".workbuddy" / "skills" / GUIDE_SKILL,
        Path.home() / ".claude" / "skills" / GUIDE_SKILL,
    ):
        try:
            f = target / "SKILL.md"
            if f.is_file() and f.read_text(encoding="utf-8") == text:
                continue
            target.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            changed = True
        except Exception:
            continue
    if changed:
        _invalidate_workbuddy_skill_cache()
    return src


def _skill_version(d):
    try:
        for line in (Path(d) / "SKILL.md").read_text(
                encoding="utf-8", errors="replace").splitlines()[:24]:
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def _is_reparse(p):
    try:
        return bool(Path(p).lstat().st_file_attributes & 0x400)
    except Exception:
        try:
            return Path(p).is_symlink()
        except Exception:
            return False


def _remove_link(p):
    if Path(p).is_symlink():
        Path(p).unlink()
    else:
        os.rmdir(str(p))          # Windows 目录联接：删链接，不删目标


def _skill_marker(d):
    try:
        return json.loads((Path(d) / ".changji-skill-source").read_text(
            encoding="utf-8"))
    except Exception:
        return {}


def _skill_backup_root(target):
    # <home>/.workbuddy/skills/<name> -> <home>/.workbuddy/skill-backups
    return Path(target).parent.parent / "skill-backups"


def _workbuddy_skill_in_cache(name):
    p = Path.home() / ".workbuddy" / ".skill-list-cache.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        if name in (row.get("name"), row.get("slug"), row.get("overrideKey")):
            return True
    return False


def _invalidate_workbuddy_skill_cache():
    """Trestle 复制了新 skill 后，让 WorkBuddy 下次启动重新扫一遍技能列表。

    WorkBuddy 把技能列表缓存在 ~/.workbuddy/.skill-list-cache.json，
    而且只在启动时扫目录；不删缓存的话，新复制进去的 changji-director
    会在 deeplink 里报“未匹配到以下技能”。删缓存不影响技能文件。
    """
    p = Path.home() / ".workbuddy" / ".skill-list-cache.json"
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def skill_targets():
    """changji-director 是内置核心资产，不复制到 Agent 的用户 skill 目录。"""
    return []


def _skill_target_state(target):
    target = Path(target)
    has = (target / "SKILL.md").is_file()
    return {
        "path": str(target),
        "exists": has,
        "version": _skill_version(target) if has else "",
        "linked": bool(os.path.lexists(str(target)) and _is_reparse(target)),
    }


def skill_status(source=None, targets=None):
    """返回内置 skill 版本 + 每个 Agent 目录的安装状态。"""
    source = Path(source) if source else bundled_skill_path()
    ok = bool(source and (Path(source) / "SKILL.md").is_file())
    src_ver = _skill_version(source) if ok else ""
    rows = [_skill_target_state(t) for t in (targets or skill_targets())]
    for row in rows:
        row["needs_sync"] = bool(
            ok and (not row["exists"] or _ver(row["version"]) < _ver(src_ver)))
    return {
        "source": str(source) if ok else "",
        "version": src_ver,
        "targets": rows,
        "needs_sync": any(row["needs_sync"] for row in rows),
        "pending": _skill_pending_path().is_file(),
        "internal": True,
    }


def _skill_pending_path():
    return config_dir() / "skill-pending.json"


def _write_skill_pending(data):
    try:
        _jwrite(_skill_pending_path(), data)
    except Exception:
        pass


def _clear_skill_pending():
    try:
        _skill_pending_path().unlink()
    except Exception:
        pass


def retry_pending_skill_sync():
    """上次同步因为 Agent 占用文件失败时，下次启动再试一次。"""
    data = _jload(_skill_pending_path(), {}) or {}
    src = Path(str(data.get("source") or ""))
    if not (src / "SKILL.md").is_file():
        _clear_skill_pending()
        return None
    return sync_bundled_skills(source=src, force=True)


def sync_bundled_skills(source=None, targets=None, force=False):
    """把内置 skill 复制到 Agent 的用户 skill 目录，并返回同步结果。

    为什么是复制而不是目录联接：Claude Code 扫描 skills 时不跟随 junction，
    会把联接当成普通文件跳过；WorkBuddy 虽然跟随，但备份目录也会被当成第二个
    skill。复制一份真实目录，两边都能稳定识别。

    只在版本不同（或 force=True）时覆盖；旧目录先移到 skill-backups/，
    用户的本地改动不会丢。失败会写 skill-pending.json，下次启动重试。
    """
    result = {"ok": True, "source": "", "version": "",
              "targets": [], "errors": []}
    copied_workbuddy = False
    source = Path(source) if source else bundled_skill_path()
    if not source or not (source / "SKILL.md").is_file():
        result.update({"ok": False, "error": "找不到内置 skill"})
        return result
    source = source.resolve()
    src_ver = _skill_version(source)
    result.update({"source": str(source), "version": src_ver})
    targets = list(targets) if targets is not None else skill_targets()
    log = config_dir() / "skill-sync.log"
    stamp = time.strftime("%Y%m%d-%H%M%S")

    def note(text):
        try:
            with open(str(log), "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:
            pass

    for target in targets:
        target = Path(target)
        row = {"path": str(target), "version": src_ver, "action": "copy"}
        try:
            # 旧版本留下的 <name>.bak-* 也在 skills/ 下，会被 Agent 当成
            # 第二个同名 skill。统一挪到 skill-backups/，只留当前这一份。
            try:
                for old in target.parent.glob(target.name + ".bak-*"):
                    backup = _skill_backup_root(target) / old.name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if backup.exists():
                        shutil.rmtree(str(old), ignore_errors=True)
                    else:
                        shutil.move(str(old), str(backup))
                    note("挪走旧副本 %s -> %s" % (old, backup))
            except Exception as e:
                note("清理旧副本失败 %s：%s" % (target.parent, e))
            if os.path.lexists(str(target)):
                if _is_reparse(target):
                    _remove_link(target)
                else:
                    tgt_ver = _skill_version(target)
                    same = (tgt_ver == src_ver)
                    if same and not force:
                        row["action"] = "skip"
                        row["version"] = tgt_ver
                        result["targets"].append(row)
                        continue
                    # 用户目录里的版本比内置还新：不降级，force 也不降。
                    if _ver(tgt_ver) > _ver(src_ver):
                        row["action"] = "newer"
                        row["version"] = tgt_ver
                        result["targets"].append(row)
                        continue
                    backup = _skill_backup_root(target) / (
                        target.name + ".bak-" + stamp)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(target), str(backup))
                    except Exception:
                        shutil.copytree(str(target), str(backup))
                        shutil.rmtree(str(target), ignore_errors=True)
                    note("备份 %s -> %s" % (target, backup))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(source), str(target),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (target / ".changji-skill-source").write_text(
                json.dumps({"source": str(source), "version": src_ver,
                            "synced": time.strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            note("复制 %s（skill %s）" % (target, src_ver or "?"))
            try:
                if str(target).lower().startswith(
                        str(Path.home() / ".workbuddy").lower()):
                    copied_workbuddy = True
            except Exception:
                pass
        except Exception as e:
            row.update({"action": "error", "error": str(e)[:300]})
            result["ok"] = False
            result["errors"].append(row)
            note("同步失败 %s：%s" % (target, e))
        result["targets"].append(row)

    if result["errors"]:
        _write_skill_pending({
            "source": str(source), "version": src_ver,
            "errors": [{"path": e["path"], "error": e["error"]}
                       for e in result["errors"]],
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    else:
        _clear_skill_pending()
    if copied_workbuddy:
        _invalidate_workbuddy_skill_cache()
    else:
        try:
            wb_home = str(Path.home() / ".workbuddy").lower()
            for row in result.get("targets") or []:
                p = str(row.get("path") or "")
                if (p.lower().startswith(wb_home)
                        and not _workbuddy_skill_in_cache(Path(p).name)):
                    _invalidate_workbuddy_skill_cache()
                    break
        except Exception:
            pass
    return result


# ---------------------------------------------------------------- WorkBuddy

WORKBUDDY_SKILL = os.environ.get("CJ_WORKBUDDY_SKILL") or "changji-director"
GUIDE_SKILL = "changji-guide"


def _registry_workbuddy():
    """从 workbuddy:// 协议注册里读出客户端真实路径（支持装在非默认盘）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except Exception:
        return ""
    key = r"Software\Classes\workbuddy\shell\open\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            cmd = winreg.QueryValueEx(k, "")[0]
    except OSError:
        return ""
    m = re.search(r'"([^"]+\.exe)"', str(cmd or ""), re.I)
    if m and Path(m.group(1)).is_file():
        return m.group(1)
    return ""


def workbuddy_exe():
    """WorkBuddy 客户端在哪。装到别处可用 CJ_WORKBUDDY 指定。"""
    env = os.environ.get("CJ_WORKBUDDY")
    if env and Path(env).is_file():
        return str(Path(env))
    reg = _registry_workbuddy()
    if reg:
        return reg
    for name in ("WorkBuddy.exe", "workbuddy.exe", "workbuddy", "WorkBuddy"):
        hit = shutil.which(name)
        if hit:
            return hit
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    pf = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
    pf86 = Path(os.environ.get("ProgramFiles(x86)")
                or r"C:\Program Files (x86)")
    cands = [
        local / "Programs" / "WorkBuddy" / "WorkBuddy.exe",
        local / "WorkBuddy" / "WorkBuddy.exe",
        pf / "WorkBuddy" / "WorkBuddy.exe",
        pf86 / "WorkBuddy" / "WorkBuddy.exe",
        home / ".workbuddy" / "bin" / "workbuddy",
    ]
    for p in cands:
        if p.is_file():
            return str(p)
    return ""


def workbuddy_protocol_registered():
    """workbuddy:// 是否已注册（注册了就不用猜 exe 路径）。"""
    if os.name != "nt":
        return False
    try:
        import winreg
    except Exception:
        return False
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
        try:
            winreg.CloseKey(winreg.OpenKey(root, "workbuddy"))
            return True
        except OSError:
            continue
    return False


def workbuddy_prompt(project, info=None):
    info = info or inspect_project(project)
    name = str(info.get("name") or Path(project).name)
    active = str(info.get("active") or "")
    tail = ("当前单元是 %s。" % active) if active else ""
    if str(info.get("mode") or "general") == "short_drama":
        return ("用 changji-director 接手这个 Trestle 工程《%s》。%s"
                "按说好五段：先读 project.json 和当前单元，判断现在在 "
                "outline / characters / art / script / staging / storyboard 哪一段，"
                "然后告诉我下一步做什么。"
                % (name, tail))
    return ("用 %s 接手这个 Trestle 工程《%s》。%s"
            "按通用七步（创作思路 → 剧本/大纲 → 场 → 分镜 → 镜头 → 图谱 → 提示词）："
            "先读 project.json 和当前单元，只做下一步，然后告诉我接下来做什么。"
            % (WORKBUDDY_SKILL, name, tail))


def workbuddy_link(project, prompt):
    """WorkBuddy 的 task deeplink：开新任务、切到工程目录、读内置 skill。

    内置 skill 不挂成用户 skill chip，也不复制到 ~/.workbuddy/skills；
    prompt 第一段直接把内置 SKILL.md 的绝对路径交给客户端。
    """
    src = bundled_skill_path()
    prefix = ""
    if src:
        prefix = "先读 %s 里的 SKILL.md，按这个 skill 的规则执行。\n\n" % src
    q = [("action", "start"), ("cwd", str(project)),
         ("prompt", prefix + str(prompt or ""))]
    return "workbuddy://task?" + urllib.parse.urlencode(q)


def launch_workbuddy(project, prompt=None):
    """唤起 WorkBuddy 打开工程，并一键加载内置导演 skill。"""
    project = str(Path(project).resolve())
    info = inspect_project(project)
    if info.get("kind") not in ("ok", "no_unit"):
        return {"ok": False, "code": "not_project", "project": info}
    mark_changji_project(project)
    if not (bundled_skill_path() or skill_path()):
        return {"ok": False, "code": "no_skill", "project": info}

    exe = workbuddy_exe()
    url = workbuddy_link(project, prompt or workbuddy_prompt(project, info))
    # 显式把 URL 当参数交给客户端，比 ShellExecute 协议更可控：
    # 还能顺手摘掉 ELECTRON_RUN_AS_NODE——有它在 WorkBuddy 会以 Node 模式
    # 启动，把 URL 当脚本路径，既不开窗口也不转发。
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "ELECTRON_RUN_AS_NODE"}
    last_err = ""
    if exe:
        try:
            subprocess.Popen([exe, url], cwd=project, close_fds=True, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
            return {"ok": True, "how": "exe", "path": project,
                    "skill": WORKBUDDY_SKILL, "project": info}
        except Exception as e:
            last_err = str(e)[:300]
    if os.name == "nt" and workbuddy_protocol_registered():
        try:
            os.startfile(url)
            return {"ok": True, "how": "deeplink", "path": project,
                    "skill": WORKBUDDY_SKILL, "project": info}
        except OSError:
            pass
    if not exe:
        return {"ok": False, "code": "needs_install", "project": info}
    return {"ok": False, "code": "spawn_failed",
            "error": last_err or "WorkBuddy 启动失败", "project": info}


def ui_file(name):
    cands = []
    ha = hot_app_dir()
    if ha:
        cands.append(ha / "ui")
    cands += [APP / "ui", APP / "app" / "ui"]
    for d in cands:
        p = d / name
        if p.is_file():
            return p
    return None


def msg(text, title=APP_NAME):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x40)
    except Exception:
        print(text, file=sys.stderr)


# ---------------------------------------------------------------- JSON

def _jload(p, d=None):
    try:
        p = Path(p)
        if not p.is_file():
            return d
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return d


def _jwrite(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- 工程

def state_dir():
    d = config_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_paths(proj):
    """外部状态优先，兼容命令行起的工作台（状态还在工程里）。"""
    root = Path(proj).resolve()
    key = hashlib.sha1(str(root).lower().encode("utf-8")).hexdigest()[:16]
    return [state_dir() / (key + "-workbench.json"),
            root / ".workbuddy" / "tmp" / "workbench.json"]


def workbench_state(proj):
    """工作台自己写的运行态。"""
    for p in _state_paths(proj):
        st = _jload(p, None)
        if st:
            return st
    return None


def port_alive(port, timeout=0.4):
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout)
        s.close()
        return True
    except Exception:
        return False


def picker_healthy(port, timeout=0.8):
    """端口通不等于服务可用：确认选择页真的能应答，避免打开一个正在退出的地址。"""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/alive" % int(port),
            headers={"User-Agent": "changji"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode() == 200
    except Exception:
        return False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def mark_changji_project(path):
    """给工程盖个章：导演 skill 靠 meta.app 区分 Trestle 布局和上游 aiv 工程。

    只有打开工程时才写，浏览/检查不碰用户文件；字段很小，失败也不影响打开。
    """
    try:
        p = Path(path).resolve() / "project.json"
        st = _jload(p, None)
        if not isinstance(st, dict):
            return
        meta = st.get("meta")
        if not isinstance(meta, dict):
            meta = st["meta"] = {}
        if str(meta.get("app") or "") == "changji":
            return
        meta["app"] = "changji"
        _jwrite(p, st)
    except Exception:
        pass


def inspect_project(path):
    """判断一个文件夹能不能当工程打开。"""
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        p = Path(path)
    out = {"path": str(p)}
    if not p.is_dir():
        out["kind"] = "missing"
        return out

    pj = p / "project.json"
    if not pj.is_file():
        try:
            out["empty"] = not any(p.iterdir())
        except Exception:
            out["empty"] = False
        out["kind"] = "not_project"
        return out

    st = _jload(pj, None)
    if not isinstance(st, dict):
        out["kind"] = "broken"
        out["error"] = "project.json 读不出来"
        return out

    meta = st.get("meta") or {}
    out["name"] = str(meta.get("name") or p.name)
    out["season"] = str(meta.get("season") or "")
    out["mode"] = str(meta.get("mode") or "general")
    units = []
    for uid, u in (st.get("units") or {}).items():
        m = _jload(p / "units" / uid / "manifest.json", {}) or {}
        sj = _jload(p / "units" / uid / "storyboard" / "shots.json", {}) or {}
        units.append({
            "id": uid,
            "name": str(m.get("entry") or m.get("title") or ""),
            "type": str(m.get("type") or (u or {}).get("type") or ""),
            "shots": len(sj.get("shots") or []),
        })
    out["units"] = units
    out["active"] = str(meta.get("active_unit") or (units[0]["id"] if units else ""))
    out["kind"] = "ok" if units else "no_unit"
    return out


def list_templates():
    root = template_root()
    meta = {
        "empty": ("空工程", "一套目录结构 + 一集空壳，从零开始"),
        "esp32-20s": ("20 秒样片", "1 场 / 2 段 / 4 镜 / 20 秒，开箱能看"),
        "demo": ("45 秒小样", "3 场 / 14 镜 / 45 秒，先看结构"),
        "project": ("45 秒小样", "3 场 / 14 镜 / 45 秒，先看结构"),
    }
    order = {"empty": 0, "esp32-20s": 1, "demo": 2, "project": 2}
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if d.is_dir() and (d / "project.json").is_file():
            label, desc = meta.get(d.name, (d.name, ""))
            out.append({"id": d.name, "label": label, "desc": desc})
    out.sort(key=lambda x: (order.get(x["id"], 9), x["id"]))
    return out


def create_project(parent, name, template_id, use_git=True):
    parent = Path(parent).expanduser().resolve()
    name = (name or "").strip()
    if not name:
        raise ValueError("工程名不能为空")
    bad = set('\\/:*?"<>|')
    if any(c in bad for c in name) or name in (".", ".."):
        raise ValueError("工程名里有不能用的字符")
    dst = parent / name
    if dst.exists():
        raise ValueError("这个位置已经有同名文件夹了")

    tpl = template_root() / template_id
    if not (tpl / "project.json").is_file():
        raise RuntimeError("模板不存在：%s" % template_id)

    parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tpl, dst)
    for junk in (".git", ".workbuddy", "__pycache__"):
        p = dst / junk
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    pj = dst / "project.json"
    st = _jload(pj, {}) or {}
    st.setdefault("meta", {})["name"] = name
    _jwrite(pj, st)

    if use_git and shutil.which("git"):
        try:
            subprocess.run(["git", "init", "-b", "main"], cwd=str(dst),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    return dst


# ---------------------------------------------------------------- 目录选择框

def _browser_window():
    """前台窗口如果是浏览器，就拿它当对话框的父窗口。

    没有父窗口，系统对话框会被应用窗口盖住——这就是之前「弹在下面」的原因。
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    cls = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, cls, 64)
    return hwnd if cls.value == "Chrome_WidgetWin_1" else None


def _pick_modern(title, initial, owner):
    """Windows 的现代文件夹选择框（IFileOpenDialog）。

    失败抛异常；用户取消返回空串。
    """
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    c_long, c_void_p, c_uint = ctypes.c_long, ctypes.c_void_p, ctypes.c_uint

    ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID)]
    ole32.CLSIDFromString.restype = c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), c_void_p,
                                       ctypes.c_ulong, ctypes.POINTER(GUID),
                                       ctypes.POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = c_long
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    shell32.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p, c_void_p, ctypes.POINTER(GUID),
        ctypes.POINTER(c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = c_long

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(text, ctypes.byref(g))
        return g

    def call(ptr, index, argtypes, *args):
        vptr = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(c_void_p)))
        proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
        return proto(vptr[0][index])(ptr, *args)

    def release(ptr):
        if ptr:
            call(ptr, 2, [])

    CLSID_FileOpenDialog = guid("{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}")
    IID_IFileOpenDialog = guid("{d57c7288-d4ad-4768-be02-9d969532d960}")
    IID_IShellItem = guid("{43826d1e-e718-42ee-bc55-a1e261c37bfe}")

    FOS_PICKFOLDERS = 0x00000020
    FOS_FORCEFILESYSTEM = 0x00000040
    FOS_PATHMUSTEXIST = 0x00000800
    SIGDN_FILESYSPATH = 0x80058000
    CLSCTX_INPROC_SERVER = 0x1
    RPC_E_CHANGED_MODE = 0x80010106

    hr = ole32.CoInitializeEx(None, 0)          # 这个线程要用 STA
    if hr & 0xFFFFFFFF == RPC_E_CHANGED_MODE:
        raise OSError("COM 已经是 MTA")

    pfd = c_void_p()
    item = c_void_p()
    try:
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID_FileOpenDialog), None,
                                    CLSCTX_INPROC_SERVER,
                                    ctypes.byref(IID_IFileOpenDialog),
                                    ctypes.byref(pfd))
        if hr < 0 or not pfd:
            raise OSError("CoCreateInstance 失败：0x%08X" % (hr & 0xFFFFFFFF))

        opts = c_uint(0)
        call(pfd, 10, [ctypes.POINTER(c_uint)], ctypes.byref(opts))   # GetOptions
        call(pfd, 9, [c_uint],                                        # SetOptions
             opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST)
        call(pfd, 17, [ctypes.c_wchar_p], title)                      # SetTitle

        if initial and Path(initial).is_dir():
            if shell32.SHCreateItemFromParsingName(
                    str(initial), None, ctypes.byref(IID_IShellItem),
                    ctypes.byref(item)) == 0:
                call(pfd, 12, [c_void_p], item)                       # SetFolder
                release(item)
                item = c_void_p()

        hr = call(pfd, 3, [wintypes.HWND], owner or None)             # Show
        if hr < 0:
            if (hr & 0xFFFFFFFF) == 0x800704C7:                       # 用户取消
                return ""
            raise OSError("Show 失败：0x%08X" % (hr & 0xFFFFFFFF))

        result = c_void_p()
        hr = call(pfd, 20, [ctypes.POINTER(c_void_p)], ctypes.byref(result))
        if hr < 0 or not result:
            return ""
        try:
            out = ctypes.c_void_p()
            hr = call(result, 5, [c_uint, ctypes.POINTER(c_void_p)],
                      SIGDN_FILESYSPATH, ctypes.byref(out))
            if hr < 0 or not out:
                return ""
            try:
                return ctypes.wstring_at(out.value) or ""
            finally:
                ole32.CoTaskMemFree(out)
        finally:
            release(result)
    finally:
        release(pfd)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


def _pick_legacy(title, initial, owner):
    """老式选择框，只在现代接口不可用时兜底。"""
    import ctypes
    from ctypes import wintypes

    BFFM_INITIALIZED = 1
    BFFM_SETSELECTIONW = 0x0400 + 103
    BIF_RETURNONLYFSDIRS = 0x0001
    BIF_NEWDIALOGSTYLE = 0x0040
    BIF_EDITBOX = 0x0010

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    user32 = ctypes.windll.user32

    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL

    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                                  wintypes.LPARAM, wintypes.LPARAM)

    def _cb(hwnd, umsg, lparam, lpdata):
        if umsg == BFFM_INITIALIZED:
            if initial and Path(initial).is_dir():
                user32.SendMessageW(hwnd, BFFM_SETSELECTIONW, 1, str(initial))
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        return 0

    cb = CALLBACK(_cb)
    display = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFOW()
    bi.hwndOwner = owner
    bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
    bi.lpfn = ctypes.cast(cb, ctypes.c_void_p)
    bi.lParam = 0

    try:
        ole32.CoInitialize(None)
    except Exception:
        pass
    pidl = None
    try:
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ""
        buf = ctypes.create_unicode_buffer(260)
        if not shell32.SHGetPathFromIDListW(pidl, buf):
            return ""
        return buf.value
    finally:
        if pidl:
            ole32.CoTaskMemFree(pidl)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


def pick_folder(title="选择工程文件夹", initial=None):
    """Windows 文件夹选择框。非 Windows 或用户取消返回 None。"""
    if os.name != "nt":
        return None
    owner = _browser_window()
    try:
        return _pick_modern(title, initial, owner) or None
    except Exception:
        try:
            return _pick_legacy(title, initial, owner) or None
        except Exception:
            return None


# ---------------------------------------------------------------- 服务

class Running:
    def __init__(self):
        self.procs = {}                      # 工程路径 -> Popen
        self.opened = set()                  # 本次会话打开过的工程（含复用的）
        self.lock = threading.Lock()

    def stop(self, proj):
        proj = str(Path(proj).resolve())
        st = workbench_state(proj)
        if st and st.get("port"):
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:%s/api/shutdown" % int(st["port"]),
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json",
                             "X-WB-Token": str(st.get("token") or "")})
                urllib.request.urlopen(req, timeout=2).read()
            except Exception:
                pass
            deadline = time.time() + 4
            while time.time() < deadline and port_alive(st["port"], 0.2):
                time.sleep(0.1)
        with self.lock:
            proc = self.procs.pop(proj, None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        for p in _state_paths(proj):
            try:
                p.unlink()
            except OSError:
                pass

    def stop_all(self):
        with self.lock:
            paths = set(self.procs) | set(self.opened)
        for p in paths:
            self.stop(p)
        with self.lock:
            self.opened.clear()

    def open(self, proj):
        proj = Path(proj).resolve()
        key = str(proj)
        mark_changji_project(key)
        with self.lock:
            self.opened.add(key)
        st = workbench_state(proj)
        if st and port_alive(st.get("port")):
            return "http://127.0.0.1:%s/" % st["port"], False

        script = workbench_script()
        py = runtime_python()
        port = free_port()
        env = dict(os.environ)
        env["AIV_STATE_DIR"] = str(state_dir())
        env["AIV_APP_VERSION"] = active_version()
        # 让 Agent 终端里的 d / bin/d.cmd 直接用内置 runtime，客户不用装 Python
        env["WB_PYTHON"] = str(runtime_python(console=True))
        if LAUNCHER_URL:
            env["AIV_LAUNCHER_URL"] = LAUNCHER_URL
        sk = skill_path()
        if sk:
            env["AIV_SKILL_DIR"] = str(sk)
        ff = ffmpeg_path()
        if ff:
            env["CJ_FFMPEG"] = ff
        proc = subprocess.Popen(
            [str(py), str(script), "--cwd", key, "--port", str(port), "--no-open"],
            cwd=key, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            close_fds=True, env=env)
        with self.lock:
            self.procs[key] = proc

        deadline = time.time() + 25
        while time.time() < deadline:
            st = workbench_state(proj)
            if st and port_alive(st.get("port")):
                return "http://127.0.0.1:%s/" % st["port"], True
            if proc.poll() is not None:
                raise RuntimeError("工作台启动后立即退出")
            time.sleep(0.1)
        proc.terminate()
        raise RuntimeError("工作台启动超时")


RUNNING = Running()
LAUNCHER_URL = ""          # 选择页地址，注入给工作台做「回到项目列表」


# ---------------------------------------------------------------- 最近项目

def recents_path():
    return config_dir() / "recent.json"


def load_recents():
    rows = _jload(recents_path(), []) or []
    if not isinstance(rows, list):
        return []
    keep = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("path"):
            continue
        p = Path(r["path"])
        if (p / "project.json").is_file():
            keep.append(r)
    return keep[:MAX_RECENT]


def touch_recent(path, name):
    path = str(Path(path).resolve())
    rows = [r for r in load_recents() if r.get("path") != path]
    rows.insert(0, {"path": path, "name": name or Path(path).name,
                    "at": time.strftime("%Y-%m-%d %H:%M")})
    _jwrite(recents_path(), rows[:MAX_RECENT])


def forget_recent(path):
    path = str(Path(path).resolve())
    _jwrite(recents_path(),
            [r for r in load_recents() if r.get("path") != path])


# ---------------------------------------------------------------- 选择页

PAGE_FALLBACK = """<!doctype html><meta charset="utf-8">
<title>Trestle</title>
<body style="background:#171719;color:#f4f4f5;font:14px/1.6 system-ui;
padding:48px"><h1>Trestle</h1><p>缺 ui/picker.html，请重新安装。</p></body>"""


def render_page(token):
    p = ui_file("picker.html")
    html = p.read_text(encoding="utf-8") if p else PAGE_FALLBACK
    meta = '<meta name="cj-token" content="%s">' % token
    if "</head>" in html:
        return html.replace("</head>", meta + "\n</head>", 1)
    return meta + html


class Handler(BaseHTTPRequestHandler):
    server_version = "changgong/" + VERSION
    app = None

    def log_message(self, *a):
        pass

    # ---------- 基础 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8")) or {}
        except Exception:
            return {}

    def _authed(self):
        return self.headers.get("X-CJ-Token") == self.app.token

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, render_page(self.app.token),
                              "text/html; charset=utf-8")
        if path == "/favicon.ico":
            ico = ui_file("app.ico")
            if ico:
                return self._send(200, ico.read_bytes(), "image/x-icon")
            return self._json(404, {"ok": False})
        if path == "/api/alive":
            return self._json(200, {"ok": True})
        if path == "/api/update":
            return self._json(200, update_info())
        if path == "/api/skill":
            return self._json(200, skill_status())
        return self._json(404, {"ok": False})

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._authed():
            return self._json(403, {"ok": False, "error": "token"})
        body = self._body()
        try:
            if path == "/api/bootstrap":
                return self._json(200, self._bootstrap())
            if path == "/api/browse":
                p = pick_folder(body.get("title") or "选择工程文件夹",
                                body.get("initial"))
                if not p:
                    return self._json(200, {"ok": False, "cancel": True})
                if body.get("inspecting"):
                    return self._json(200, {"ok": True, "path": p,
                                            "project": inspect_project(p)})
                return self._json(200, {"ok": True, "path": p})
            if path == "/api/inspect":
                p = str(body.get("path") or "").strip()
                if not p:
                    return self._json(400, {"ok": False, "error": "缺少路径"})
                return self._json(200, {"ok": True, "project": inspect_project(p)})
            if path == "/api/open":
                p = str(body.get("path") or "").strip()
                info = inspect_project(p)
                if info["kind"] != "ok":
                    return self._json(200, {"ok": False, "project": info})
                url, started = RUNNING.open(info["path"])
                touch_recent(info["path"], info.get("name"))
                return self._json(200, {"ok": True, "url": url,
                                        "started": started})
            if path == "/api/workbuddy":
                p = str(body.get("path") or "").strip()
                if not p:
                    return self._json(400, {"ok": False,
                                            "error": "缺少路径"})
                out = launch_workbuddy(p, body.get("prompt"))
                if out.get("ok"):
                    touch_recent(out["path"],
                                 (out.get("project") or {}).get("name"))
                return self._json(200, out)
            if path == "/api/env":
                return self._json(200, env_report_payload())
            if path == "/api/diag":
                return self._json(200, {"ok": True,
                                        "text": env_report_text()})
            if path == "/api/env-fix":
                report = env_report_text()
                prompt = env_fix_prompt(report)
                url = "workbuddy://task?" + urllib.parse.urlencode(
                    [("action", "start"), ("prompt", prompt)])
                env = {k: v for k, v in os.environ.items()
                       if k.upper() != "ELECTRON_RUN_AS_NODE"}
                exe = workbuddy_exe()
                launched = ""
                if exe:
                    try:
                        subprocess.Popen([exe, url], close_fds=True, env=env,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         creationflags=CREATE_NO_WINDOW)
                        launched = "exe"
                    except Exception:
                        pass
                if not launched and os.name == "nt" \
                        and workbuddy_protocol_registered():
                    try:
                        os.startfile(url)
                        launched = "deeplink"
                    except OSError:
                        pass
                return self._json(200, {"ok": bool(launched),
                                        "how": launched, "url": url,
                                        "prompt": prompt, "report": report})
            if path == "/api/create":
                dst = create_project(
                    body.get("parent") or str(Path.home() / "Documents"),
                    body.get("name"),
                    body.get("template") or (list_templates() or [{}])[0].get("id", ""),
                    bool(body.get("git", True)))
                info = inspect_project(dst)
                if info["kind"] != "ok":
                    return self._json(200, {"ok": False, "project": info,
                                            "created": str(dst)})
                url, started = RUNNING.open(info["path"])
                touch_recent(info["path"], info.get("name"))
                return self._json(200, {"ok": True, "url": url,
                                        "started": started,
                                        "project": info})
            if path == "/api/forget":
                forget_recent(str(body.get("path") or ""))
                return self._json(200, {"ok": True,
                                        "recents": load_recents()})
            if path == "/api/reveal":
                p = str(body.get("path") or "")
                if p and Path(p).is_dir():
                    subprocess.Popen(["explorer", os.path.normpath(p)],
                                     close_fds=True)
                return self._json(200, {"ok": True})
            if path == "/api/quit":
                threading.Thread(target=self.app.request_quit,
                                 daemon=True).start()
                return self._json(200, {"ok": True})
            if path == "/api/skill/sync":
                out = sync_bundled_skills(force=bool(body.get("force", True)))
                out["status"] = skill_status()
                return self._json(200, out)
            if path == "/api/update/check":
                threading.Thread(target=check_update, daemon=True).start()
                return self._json(200, {"ok": True})
            if path == "/api/update/apply":
                info = update_info()
                if not info.get("available") or not (info.get("setup_url")
                                                     or info.get("hot_url")):
                    return self._json(400, {"ok": False,
                                            "error": "没有可安装的更新"})
                if info.get("status") in ("downloading", "installing"):
                    return self._json(200, {"ok": True})
                threading.Thread(target=apply_update, daemon=True).start()
                return self._json(200, {"ok": True})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)[:300]})
        return self._json(404, {"ok": False})

    def _bootstrap(self):
        docs = Path.home() / "Documents"
        sk = skill_path()
        st = skill_status()
        installed_ver = st.get("version") or ""
        ff = ffmpeg_info()
        ep = edge_path()
        free = disk_free_gb()
        return {
            "ok": True,
            "app": {"name": APP_NAME, "sub": APP_SUB, "version": active_version()},
            "recents": load_recents(),
            "templates": list_templates(),
            "defaultParent": str(docs / APP_ID),
            "running": self._running_ports(),
            "platform": {"workbench": True,
                         "version": active_version(),
                         "skill": str(sk) if sk else "",
                         "skill_version": st.get("version") or "",
                         "skill_installed": installed_ver,
                         "skill_needs_sync": False,
                         "skill_internal": True,
                         "skill_targets": [],
                         "workbuddy": workbuddy_exe(),
                         "claude": shutil.which("claude") or "",
                         "ffmpeg": ff,
                         "edge": {"ready": bool(ep), "path": ep},
                         "disk": {"free_gb": free,
                                  "ready": free is None or free >= 2},
                         "rows": env_rows()},
        }

    def _running_ports(self):
        out = {}
        with RUNNING.lock:
            items = list(RUNNING.procs)
        for p in items:
            st = workbench_state(p)
            if st and port_alive(st.get("port")):
                out[p] = st.get("port")
        return out


class Picker:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.httpd = None
        self.thread = None
        self.port = None
        self.quit_event = threading.Event()

    def request_quit(self):
        """用户点了「退出」：停服务，也把那个 Edge 应用窗口关掉。

        只 shutdown 服务的话，窗口会留在桌面上显示一个连不上的页面——
        看起来就是「点了没反应」。
        """
        self.quit_event.set()
        close_window()
        if self.httpd is not None:
            self.httpd.shutdown()

    def start(self):
        Handler.app = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        return "http://127.0.0.1:%d/" % self.port

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass


# ---------------------------------------------------------------- 窗口与单实例

def open_window(url, size="1280,860"):
    profile = config_dir() / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    for exe in EDGE:
        if not exe.exists():
            continue
        try:
            return subprocess.Popen(
                [str(exe), "--app=" + url,
                 "--user-data-dir=" + str(profile),
                 "--no-first-run", "--no-default-browser-check",
                 "--window-size=" + size],
                close_fds=True)
        except Exception:
            continue
    import webbrowser
    webbrowser.open(url)
    return None


def close_window():
    """关掉 open_window 起的那个 Edge 应用窗口。"""
    global WINDOW
    w = WINDOW
    if w is None:
        return
    try:
        if w.poll() is None:
            w.terminate()
            try:
                w.wait(timeout=3)
            except Exception:
                pass
    except Exception:
        pass
    if w.poll() is None and os.name == "nt":
        try:                         # 进程树里还有 Edge 子进程时兜底
            subprocess.run(["taskkill", "/PID", str(w.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass


def lock_path():
    return config_dir() / "launcher.json"


def pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW)
            return str(pid) in (out.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def instance_running():
    st = _jload(lock_path(), None)
    if not st:
        return None
    if st.get("pid") == os.getpid():
        return None
    if not pid_alive(st.get("pid")):
        return None
    if not st.get("port") or not port_alive(st["port"]):
        return None
    return st


def write_lock(port, token):
    _jwrite(lock_path(), {"pid": os.getpid(), "port": port, "token": token,
                          "at": time.strftime("%Y-%m-%d %H:%M:%S")})


def clear_lock():
    try:
        lock_path().unlink()
    except OSError:
        pass


def prune_state():
    """清掉上次异常退出留下的运行态文件。"""
    try:
        files = list(state_dir().glob("*-workbench.json"))
    except Exception:
        return
    for p in files:
        st = _jload(p, None)
        if st and st.get("pid") and pid_alive(st["pid"]):
            continue
        try:
            p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 更新

UPDATE_URL = os.environ.get("CJ_UPDATE_URL") or ""
UPDATE_URLS = []           # 清单地址列表，按顺序尝试
UPDATE_LOCK = threading.Lock()
UPDATE = {
    "enabled": False, "checked": False, "available": False,
    "current": VERSION, "version": "", "notes": "", "setup_url": "",
    "setup_mirrors": [], "manifest_url": "",
    "sha256": "", "status": "idle", "error": "", "progress": 0,
    "kind": "full", "hot_url": "", "hot_mirrors": [], "hot_sha256": "",
    "hot_size": 0, "min_hot_version": "", "can_hot": False, "skill": {},
    "hot_launcher_fingerprint": "", "needs_admin": False,
}
WINDOW = None              # 选择页窗口进程，更新时先关掉
PICKER = None              # 选择页服务，更新时先停掉


def update_urls():
    """清单地址：环境变量优先，其次是打包时写进的 update_url.txt。

    支持多行或逗号分隔：第一行/第一个是主源，后面的做兜底。
    """
    global UPDATE_URLS
    if UPDATE_URLS:
        return UPDATE_URLS
    raw = os.environ.get("CJ_UPDATE_URL") or ""
    if not raw:
        p = APP / "update_url.txt"
        if p.is_file():
            try:
                raw = p.read_text(encoding="utf-8")
            except Exception:
                raw = ""
    urls = []
    for part in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        part = part.strip()
        if part and part not in urls:
            urls.append(part)
    UPDATE_URLS = urls
    return urls


def update_state_path():
    return config_dir() / "update" / "last-update.json"


def _update_state(stage, **kw):
    """记录最近一次更新的阶段；下次启动时 UI 能告诉用户上次为什么没完成。"""
    try:
        p = update_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        obj = {"stage": stage, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        obj.update({k: v for k, v in kw.items() if v not in (None, "")})
        _jwrite(p, obj)
    except Exception:
        pass


def update_info():
    with UPDATE_LOCK:
        out = dict(UPDATE)
    try:
        out["installed_skill"] = skill_status()
    except Exception as e:
        out["installed_skill"] = {"ok": False, "error": str(e)[:200]}
    try:
        out["last_update"] = _jload(update_state_path(), {}) or {}
    except Exception:
        out["last_update"] = {}
    return out


def _ver(s):
    """版本比较键：核心数字比大小，预发布排在同号正式版之前。

    1.0.0-beta.2 < 1.0.0-rc.1 < 1.0.0；也兼容 1.0.0beta1 / v1.0.0。
    0.1.3 和 0.1.3.0 仍视为同一版。
    """
    text = str(s or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    core, sep, pre = text.partition("-")
    if not sep:
        m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", core)
        if m and m.group(2).strip():
            core, pre, sep = m.group(1), m.group(2), "-"

    out = []
    for part in core.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    key = tuple(out)
    if not sep:
        return (key, 1, ())                    # 正式版：同号预发布之后
    ids = []
    for token in re.findall(r"[a-z]+|\d+", pre):
        ids.append((0, int(token)) if token.isdigit() else (1, token))
    return (key, 0, tuple(ids))


def active_version():
    """实际生效的版本：热更新目录比安装版本新时用热版本，否则用 launcher 的 VERSION。"""
    try:
        ptr = _jload(current_ptr_path(), {}) or {}
        hv = str(ptr.get("version") or "")
        if hv and _ver(hv) > _ver(VERSION):
            return hv
    except Exception:
        pass
    return VERSION


def launcher_fingerprint(path=None):
    """launcher.py 去掉 VERSION 行后的指纹；用来判断热包能不能直接套用。"""
    try:
        p = Path(path) if path else (APP / "launcher.py")
        text = p.read_text(encoding="utf-8")
        text = re.sub(r'^VERSION = ".*"$', 'VERSION = "0.0.0"', text,
                      count=1, flags=re.M)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def fetch_manifest(timeout=6):
    """按顺序拉清单，返回 (manifest, 使用的 URL, 最后错误)。"""
    last_err = ""
    for url in update_urls():
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "changgong/" + active_version()})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            man = json.loads(raw.decode("utf-8"))
            if isinstance(man, dict):
                return man, url, ""
            last_err = "清单不是对象"
        except Exception as e:
            last_err = str(e)[:160]
    return None, "", last_err


def check_update():
    """后台拉清单；主源失败换下一个，全失败静默。"""
    urls = update_urls()
    with UPDATE_LOCK:
        UPDATE["enabled"] = bool(urls)
        if not urls or UPDATE["status"] in ("downloading", "installing"):
            UPDATE["checked"] = True
            return
        UPDATE["status"] = "checking"
        current = active_version()
        UPDATE["current"] = current

    man, used, last_err = fetch_manifest()

    if man is None:
        with UPDATE_LOCK:
            UPDATE["checked"] = True
            UPDATE["status"] = "idle"
            UPDATE["error"] = "检查更新失败：%s" % last_err
        return

    new = str(man.get("version") or "")
    mirrors = [str(x) for x in (man.get("setup_mirrors") or []) if x]
    hot_mirrors = [str(x) for x in (man.get("hot_mirrors") or []) if x]
    available = _ver(new) > _ver(current)
    min_hot = str(man.get("min_hot_version") or "")
    hot_url = str(man.get("hot_url") or "")
    hot_fp = str(man.get("hot_launcher_fingerprint") or "")
    can_hot = bool(available and hot_url and
                   (not min_hot or _ver(current) >= _ver(min_hot)) and
                   (not hot_fp or hot_fp == launcher_fingerprint()))
    with UPDATE_LOCK:
        UPDATE.update({
            "checked": True, "available": available, "version": new,
            "notes": str(man.get("notes") or ""),
            "setup_url": str(man.get("setup_url") or ""),
            "setup_mirrors": mirrors,
            "manifest_url": used,
            "sha256": str(man.get("sha256") or "").lower(),
            "kind": str(man.get("kind") or ("hot+full" if hot_url else "full")),
            "hot_url": hot_url, "hot_mirrors": hot_mirrors,
            "hot_sha256": str(man.get("hot_sha256") or "").lower(),
            "hot_size": int(man.get("hot_size") or 0),
            "min_hot_version": min_hot,
            "hot_launcher_fingerprint": hot_fp,
            "can_hot": can_hot,
            "skill": man.get("skill") if isinstance(man.get("skill"), dict) else {},
            "error": "", "status": "idle",
        })


def _download(url, dst, set_progress):
    req = urllib.request.Request(
        url, headers={"User-Agent": "changgong/" + VERSION})
    with urllib.request.urlopen(req, timeout=30) as r, open(str(dst), "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                set_progress(min(99, int(got * 100 / total)))


def _safe_extract_zip(zpath, dest):
    dest = Path(dest)
    root = str(dest.resolve())
    with zipfile.ZipFile(str(zpath)) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError("热更新包里有非法路径：%s" % name)
            target = (dest / p).resolve()
            if not (str(target) == root or str(target).startswith(root + os.sep)):
                raise RuntimeError("热更新包越界：%s" % name)
        z.extractall(str(dest))


def _prune_versions(keep):
    """只留当前版本和最近两个热版本；不碰非版本目录。"""
    root = config_dir() / "versions"
    if not root.is_dir():
        return
    rows = []
    for d in root.iterdir():
        if d.is_dir() and re.match(r"^\d+(\.\d+)+$", d.name):
            rows.append((d.stat().st_mtime, d))
    rows.sort(reverse=True)
    others = [d for _mt, d in rows if d.name != keep]
    for d in others[2:]:
        shutil.rmtree(str(d), ignore_errors=True)


def _pid_running(pid):
    """轻量判断进程是否还在；启动器退出等待用，别每 0.2 秒起一次 tasklist。"""
    try:
        pid = int(pid)
    except Exception:
        return False
    if os.name != "nt":
        return pid_alive(pid)
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259     # STILL_ACTIVE
    except Exception:
        return False


def _stop_for_update():
    """更新前的收尾：停工作台、停选择页、关窗口、清锁。

    顺序很重要：这些动作会让主线程从 win.wait() 返回并退出进程。
    必须等「更新助手 / 新版启动器」已经拉起来之后再做，否则本线程
    还没跑到 Popen 就会被进程退出杀掉。
    """
    try:
        RUNNING.stop_all()
    except Exception:
        pass
    try:
        PICKER.request_quit()
    except Exception:
        pass
    close_window()
    clear_lock()


def _restart_launcher():
    """热更新后重启启动器：先拉起新版（等本进程退出），再关窗口停服务。

    之前是反过来的：先 close_window()/request_quit()，主线程立刻退出，
    daemon 更新线程还没执行到 Popen 就被杀掉，表现就是「窗口关了但没重启」。
    """
    started = False
    try:
        py = runtime_python(console=False)
        subprocess.Popen(
            [str(py), str(APP / "launcher.py"),
             "--wait-pid", str(os.getpid())],
            cwd=str(APP), close_fds=True,
            creationflags=CREATE_NO_WINDOW | 0x00000008)
        started = True
    except Exception:
        started = False
    if not started:
        try:
            msg("更新已经装好，但没能自动重启。\n\n请重新打开 Trestle。")
        except Exception:
            pass
    _stop_for_update()
    os._exit(0)


def apply_hot_update(info):
    """下载 app 层热包 → 校验 → 解到 versions/<ver> → 切指针 → 重启。返回 True。"""
    version = str(info.get("version") or "")
    urls = [info.get("hot_url")] + list(info.get("hot_mirrors") or [])
    urls = [u for u in urls if u]
    if not version or not urls:
        return False
    want = info.get("hot_sha256")
    updir = config_dir() / "update"
    updir.mkdir(parents=True, exist_ok=True)
    zpath = updir / ("hot-%s.zip" % version)

    with UPDATE_LOCK:
        UPDATE["status"] = "downloading"
        UPDATE["progress"] = 0
        UPDATE["error"] = ""

    def set_progress(p):
        with UPDATE_LOCK:
            UPDATE["progress"] = p

    last_err = ""
    for i, url in enumerate(urls):
        try:
            _download(url, zpath, set_progress)
            if want:
                got = hashlib.sha256(zpath.read_bytes()).hexdigest()
                if got != want:
                    raise RuntimeError("热更新包校验失败")
            break
        except Exception as e:
            last_err = str(e)[:200]
            if i + 1 < len(urls):
                with UPDATE_LOCK:
                    UPDATE["progress"] = 0
                continue
            with UPDATE_LOCK:
                UPDATE["status"] = "idle"
                UPDATE["error"] = "热更新失败：%s" % last_err
            return False

    with UPDATE_LOCK:
        UPDATE["status"] = "installing"
        UPDATE["progress"] = 100

    tmp = updir / ("hot-%s.tmp" % version)
    if tmp.exists():
        shutil.rmtree(str(tmp), ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        _safe_extract_zip(zpath, tmp)
        meta = _jload(tmp / "hot.json", {}) or {}
        if str(meta.get("version") or "") != version:
            raise RuntimeError("hot.json 版本不匹配")
        fp = str(meta.get("launcher_fingerprint") or "")
        if fp and fp != launcher_fingerprint():
            raise RuntimeError("launcher 已变化，需要整包更新")
        if not (tmp / "app" / "ui" / "picker.html").is_file():
            raise RuntimeError("热更新包缺 app/ui/picker.html")
        if not (tmp / "app" / "workbench" / "workbench.py").is_file():
            raise RuntimeError("热更新包缺 app/workbench/workbench.py")
        target = config_dir() / "versions" / version
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(str(target), ignore_errors=True)
        shutil.move(str(tmp), str(target))
        _jwrite(current_ptr_path(), {
            "version": version, "dir": str(target),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        # 内置 skill 随热更新包一起替换到 app/skills，不复制到 Agent 用户目录。
        _prune_versions(version)
        _update_state("restarting", version=version)
    except Exception as e:
        with UPDATE_LOCK:
            UPDATE["status"] = "idle"
            UPDATE["error"] = "热更新失败：%s" % str(e)[:300]
        _update_state("failed", version=version, error=str(e)[:300])
        shutil.rmtree(str(tmp), ignore_errors=True)
        return False

    time.sleep(0.4)
    _restart_launcher()
    return True


def apply_update():
    """热更新优先（不需要管理员）；不行再走整包安装。"""
    info = update_info()
    if info.get("can_hot"):
        try:
            if apply_hot_update(info):
                return
        except Exception as e:
            with UPDATE_LOCK:
                UPDATE["error"] = "热更新失败：%s" % str(e)[:300]
                UPDATE["status"] = "idle"
    urls = [info.get("setup_url")] + list(info.get("setup_mirrors") or [])
    urls = [u for u in urls if u]
    if not urls:
        with UPDATE_LOCK:
            UPDATE["status"] = "error"
            if not UPDATE.get("error"):
                UPDATE["error"] = "没有可安装的更新"
        return
    updir = config_dir() / "update"
    updir.mkdir(parents=True, exist_ok=True)
    setup = updir / "setup.exe"
    want = info.get("sha256")

    with UPDATE_LOCK:
        UPDATE["status"] = "downloading"
        UPDATE["progress"] = 0

    def set_progress(p):
        with UPDATE_LOCK:
            UPDATE["progress"] = p

    last_err = ""
    for i, url in enumerate(urls):
        try:
            _download(url, setup, set_progress)
            if want:
                got = hashlib.sha256(setup.read_bytes()).hexdigest()
                if got != want:
                    raise RuntimeError("安装包校验失败")
            break
        except Exception as e:
            last_err = str(e)[:200]
            if i + 1 < len(urls):
                with UPDATE_LOCK:
                    UPDATE["progress"] = 0
                continue
            with UPDATE_LOCK:
                UPDATE["status"] = "error"
                UPDATE["error"] = "更新失败：%s" % last_err
            return

    full = not info.get("can_hot")
    with UPDATE_LOCK:
        UPDATE["status"] = "installing"
        UPDATE["progress"] = 100
        UPDATE["needs_admin"] = bool(full and not _dir_writable(str(APP)))
    # 整包更新可能弹 UAC；留几秒让用户看到提示，不要窗口一闪就没了。
    time.sleep(2.5 if full else 0.6)
    _unblock(setup)
    try:
        launched = _launch_installer(setup, info.get("version") or "")
    except Exception as e:
        launched = False
        with UPDATE_LOCK:
            UPDATE["error"] = "更新失败：%s" % str(e)[:300]
    if launched is False:
        # 安装器没起来：窗口不关，让用户看到错误后重试或手动运行安装包。
        _update_state("failed", version=info.get("version") or "",
                      error=UPDATE.get("error") or "更新程序没有启动")
        with UPDATE_LOCK:
            UPDATE["status"] = "error"
            if not UPDATE.get("error"):
                UPDATE["error"] = "更新程序没有启动，请重试"


def check_skill_update():
    """旧 ai-video-director 已删除，不再走 skill 单独更新通道。"""
    return


def _unblock(path):
    """去掉下载文件的 Zone.Identifier，避免 SmartScreen 抢在 UAC 前面拦截。"""
    try:
        os.remove(str(path) + ":Zone.Identifier")
    except OSError:
        pass


def _dir_writable(path):
    try:
        p = Path(path) / ".changji-write-test"
        p.write_text("x", encoding="utf-8")
        p.unlink()
        return True
    except Exception:
        return False


def _installer_parts(setup, app_dir):
    """装到当前目录；目录不可写（Program Files）就请求管理员，UAC 才会出现。"""
    app = str(Path(app_dir).resolve())
    return {
        "setup": str(Path(setup).resolve()),
        "app": app,
        "mode": "/CURRENTUSER" if _dir_writable(app) else "/ALLUSERS",
        "log": str(config_dir() / "update" / "install.log"),
        "exe": str(Path(app) / "Changji.exe"),
        "arg": "",
    }


def _installer_vbs(setup):
    """生成延迟安装脚本的文本；单独拆出来便于检查，不在这里启动进程。"""
    parts = _installer_parts(setup, APP)
    if not Path(parts["exe"]).is_file():
        # 源码目录没有 Changji.exe：退回用 launcher.py 启动
        parts["exe"] = str(runtime_python(console=False))
        parts["arg"] = ' & " " & q & "%s" & q' % (
            Path(parts["app"]) / "launcher.py")
    parts = {k: str(v).replace("%", "%%") for k, v in parts.items()}
    lines = [
        'Set sh = CreateObject("WScript.Shell")',
        'q = Chr(34)',
        'WScript.Sleep 2500',
        'cmd = q & "%(setup)s" & q & " /SILENT /NORESTART /CLOSEAPPLICATIONS'
        ' /DIR=" & q & "%(app)s" & q & " %(mode)s /LOG=" & q & "%(log)s" & q',
        'On Error Resume Next',
        'rc = sh.Run(cmd, 1, True)',
        'err = Err.Number',
        'On Error GoTo 0',
        'If err <> 0 Then',
        '  MsgBox "更新没有启动（错误 " & err & "）。" & vbCrLf & '
        '"请重试，或手动运行：" & vbCrLf & "%(setup)s" & vbCrLf & '
        '"日志：" & vbCrLf & "%(log)s", 48, "Trestle"',
        'ElseIf rc <> 0 Then',
        '  MsgBox "更新未完成（退出码 " & rc & "）。" & vbCrLf & '
        '"请重试，或手动运行：" & vbCrLf & "%(setup)s" & vbCrLf & '
        '"日志：" & vbCrLf & "%(log)s", 48, "Trestle"',
        'Else',
        '  WScript.Sleep 2000',
        'End If',
        'On Error Resume Next',
        'sh.Run q & "%(exe)s" & q%(arg)s, 1, False',
        '',
    ]
    return "\r\n".join(lines) % parts, parts


def update_helper_path():
    """更新助手：优先用安装包里的，源码目录回退到 app-src。"""
    cands = [APP / "app" / "update_apply.py"]
    ha = hot_app_dir()
    if ha:
        cands.append(ha / "update_apply.py")
    cands.append(APP / "app-src" / "update_apply.py")
    for p in cands:
        if p.is_file():
            return p
    return None


def _launch_installer(setup, version=""):
    """写一个延迟安装任务：等本进程和 Edge 退出后，可见地装，装完再启动新版。

    关键顺序：先拉起助手（它用 --wait-pid 等本进程退出），再关窗口停服务。
    反过来的话，close_window()/request_quit() 会让主线程立刻退出进程，
    这个 daemon 线程还没跑到 Popen 就被杀掉——窗口关了却什么都没发生。

    优先用 Python 更新助手（能拿 UAC 取消、退出码、失败回滚）；
    老版本没有 helper 时退回 VBS。返回 False 表示安装器根本没起来，
    调用方不要关窗口，留给用户重试或手动运行。
    """
    parts = _installer_parts(setup, APP)
    helper = update_helper_path()
    log = Path(parts["log"])
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log), "a", encoding="utf-8") as f:
            f.write("%s 启动器准备更新：%s（mode=%s）\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"),
                       parts["setup"], parts["mode"]))
    except Exception:
        pass
    _update_state("installing", version=version)

    started = False
    helper_err = ""
    if helper:
        try:
            py = runtime_python(console=False)
            cmd = [str(py), str(helper),
                   "--setup", str(parts["setup"]),
                   "--app-dir", str(parts["app"]),
                   "--mode", str(parts["mode"]).lstrip("/"),
                   "--log", str(parts["log"]),
                   "--wait-pid", str(os.getpid())]
            subprocess.Popen(cmd, close_fds=True,
                             creationflags=CREATE_NO_WINDOW | 0x00000008)
            started = True
        except Exception as e:
            helper_err = str(e)[:200]
            started = False
    if not started:
        # 老安装包没有 helper：退回原来的 VBS。
        script = config_dir() / "update" / "apply.vbs"
        script.parent.mkdir(parents=True, exist_ok=True)
        text, _parts = _installer_vbs(setup)
        script.write_text(text, encoding="utf-16")
        try:
            subprocess.Popen(["wscript.exe", "//Nologo", str(script)],
                             close_fds=True,
                             creationflags=CREATE_NO_WINDOW | 0x00000008)
            started = True
        except Exception as e:
            helper_err = helper_err or str(e)[:200]
            started = False
    if not started:
        _update_state("failed", version=version,
                      error=helper_err or "更新助手没有启动")
        try:
            msg("更新程序没有启动。\n\n请重试；仍不行就手动运行：\n%s\n\n日志：\n%s"
                % (parts["setup"], parts["log"]))
        except Exception:
            pass
        return False

    _stop_for_update()           # 助手已经起来并在等本进程退出，现在才收尾
    os._exit(0)


# ---------------------------------------------------------------- 入口

def main(argv):
    if "--wait-pid" in argv:
        # 热更新重启：新版先起来，但必须等旧进程真正退出再碰锁 / 端口，
        # 否则会看到旧锁、旧端口，开出一个连不上的窗口。
        try:
            old_pid = int(argv[argv.index("--wait-pid") + 1])
        except Exception:
            old_pid = 0
        if old_pid:
            deadline = time.time() + 60
            while time.time() < deadline and _pid_running(old_pid):
                time.sleep(0.2)

    if "--stop" in argv:
        st = _jload(lock_path(), None) or {}
        if st.get("port"):
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/api/quit" % int(st["port"]),
                    data=b"{}", method="POST",
                    headers={"X-CJ-Token": str(st.get("token") or "")})
                urllib.request.urlopen(req, timeout=2).read()
            except Exception:
                pass
        for r in load_recents():
            RUNNING.stop(r["path"])
        RUNNING.stop_all()
        clear_lock()
        print("已停止")
        return 0

    if "--status" in argv:
        st = _jload(lock_path(), None)
        if not st:
            print("Trestle：没有在运行")
            return 1
        print("Trestle：%s" % ("运行中" if pid_alive(st.get("pid")) else "已退出"))
        print("  选择页  http://127.0.0.1:%s/" % st.get("port"))
        for r in load_recents():
            ws = workbench_state(r["path"])
            up = bool(ws and port_alive(ws.get("port")))
            print("  %s %s" % ("●" if up else "○", r["path"]))
        return 0

    # 已经在跑：把窗口叫出来就行
    alive = instance_running()
    if alive:
        if picker_healthy(alive["port"]):
            open_window("http://127.0.0.1:%d/" % alive["port"])
            return 0
        # 旧进程正在退出：别急着打开它那个已经关掉的端口。
        # 等锁和端口都让出来，或者等到服务恢复；否则清锁起一个新实例。
        deadline = time.time() + 6
        while time.time() < deadline:
            if picker_healthy(alive["port"]):
                open_window("http://127.0.0.1:%d/" % alive["port"])
                return 0
            if (not pid_alive(alive.get("pid"))
                    and not port_alive(alive.get("port"), 0.15)):
                break
            time.sleep(0.2)
        clear_lock()

    if not runtime_python().exists():
        msg("缺少运行时，请重新安装。")
        return 1
    try:
        workbench_script()
    except Exception as e:
        msg(str(e))
        return 1

    picker = Picker()
    url = picker.start()
    global LAUNCHER_URL, PICKER
    LAUNCHER_URL = url
    PICKER = picker
    write_lock(picker.port, picker.token)
    prune_state()
    try:
        sync_guide_bridge()
    except Exception:
        pass
    threading.Thread(target=check_update, daemon=True).start()
    threading.Thread(target=check_skill_update, daemon=True).start()

    print("")
    print("  %s · %s" % (APP_NAME, APP_SUB))
    print("  %s" % url)
    print("")

    if "--no-window" in argv:
        try:
            while not picker.quit_event.is_set():
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        finally:
            RUNNING.stop_all()
            picker.stop()
            clear_lock()
        return 0

    win = open_window(url, "1120,780")
    global WINDOW
    WINDOW = win
    try:
        if win is not None:
            win.wait()
        else:
            msg("已在默认浏览器打开：\n%s\n\n关浏览器不会停服务。"
                "下次双击会直接重新打开。" % url)
            while not picker.quit_event.is_set():
                time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        RUNNING.stop_all()
        picker.stop()
        clear_lock()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        msg("启动失败：\n%s" % e)
        sys.exit(1)
