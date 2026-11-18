"""HTTP 服务：审片台路由 / SSE / 写接口 / 安全。

只绑 127.0.0.1。写操作要带进程级 token，写前备份，冲突返回 409。

    serve(root, unit, port, open_browser)
    stop(root) / status(root)
"""

import base64
import copy
import hashlib
import io
import json
import math
import mimetypes
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from . import export as export_mod
from . import snapshot as snap_mod
from . import (derive, graph as graph_mod, libtv_index, libtv_mcp,
               libtv_slots, md, watch)
from .engine import ready as engine_ready

HERE = Path(__file__).resolve().parent
BIN = HERE.parent
STATIC = HERE / "static"

ASSET_TYPES = {"characters": "CH", "scenes": "SC", "props": "PR",
               "brand": "BR", "style": "ST"}
UPLOAD_EXT = (".png", ".jpg", ".jpeg", ".webp")
MAX_UPLOAD = 16 * 1024 * 1024
VARIANT_KEY = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,32}$")

# 桌面版跑在 pythonw 里（没有控制台）。这时再起 tasklist / taskkill 这类
# 控制台程序，Windows 会给每个子进程单开一个黑窗口。一律不显示。
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2", ".woff": "font/woff",
    ".md": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

_FFMPEG = None
_FFMPEG_INFO = None


def _ffmpeg_bundled():
    """安装包 / 开发机自带的 ffmpeg；找不到返回空。"""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    root = HERE.parents[2]
    for p in (root / "runtime" / "ffmpeg" / exe,
              root / "vendor" / "ffmpeg" / exe):
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return ""


def _ffmpeg_path():
    """ffmpeg 在哪：CJ_FFMPEG → 安装包 runtime → 开发机 vendor → PATH。"""
    global _FFMPEG
    if _FFMPEG is not None:
        return _FFMPEG
    env = os.environ.get("CJ_FFMPEG")
    if env:
        p = Path(env)
        for x in ((p / "ffmpeg.exe") if p.is_dir() else p, p):
            try:
                if x.is_file():
                    _FFMPEG = str(x)
                    return _FFMPEG
            except OSError:
                continue
    _FFMPEG = _ffmpeg_bundled() or (shutil.which("ffmpeg") or "")
    return _FFMPEG


def _ffmpeg_info():
    """环境检查用：有没有、从哪来、什么版本。"""
    global _FFMPEG_INFO
    if _FFMPEG_INFO is not None:
        return _FFMPEG_INFO
    path = _ffmpeg_path()
    info = {"ready": False, "path": path, "version": "",
            "bundled": bool(path and path == _ffmpeg_bundled())}
    if path:
        try:
            r = subprocess.run([path, "-version"], capture_output=True,
                               text=True, timeout=8, creationflags=NO_WINDOW)
            first = (r.stdout or "").splitlines()[0] if r.stdout else ""
            info["version"] = first.replace("ffmpeg version ", "").split()[0] \
                if first.startswith("ffmpeg version ") else first[:80]
            info["ready"] = r.returncode == 0
        except Exception as e:
            info["error"] = str(e)[:160]
    _FFMPEG_INFO = info
    return info


def _edge_path():
    """启动器用 Edge 打开工作台；Windows 上通常自带。"""
    if os.name != "nt":
        return ""
    for p in (Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
              / "Microsoft" / "Edge" / "Application" / "msedge.exe",
              Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
              / "Microsoft" / "Edge" / "Application" / "msedge.exe"):
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return ""


def _disk_free_gb(path):
    try:
        return round(shutil.disk_usage(str(path)).free / 1073741824.0, 1)
    except Exception:
        return None


def _jload(p, d=None):
    p = Path(p)
    try:
        if not p.exists():
            return d
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return d


def _jwrite(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- 剧集（单元）

UNIT_TYPES = ("讲解", "剧情", "打斗", "广告", "纪录", "品牌")
UNIT_ID_RX = re.compile(r"^[^\s\\/:*?\"<>|]{1,40}$")
UNIT_DIRS = ("script/draft", "script/derived", "storyboard", "prompts",
             "production", "renders", "edit", "review", "keyframes",
             "deliver/final", "deliver/preview",
             "deliver/publish/douyin", "deliver/publish/bilibili")
UNIT_COPY_TPL = """# 台词定稿（站 2）

> 逐字稿。一行一句，句尾不留空格。净字数 = 去掉 // 标记后的字符数 ÷ 语速 = 时长。
> 定稿后不许再改；要改就重算下游站 6/7/8。
>
> 中间稿放 `draft/`，定稿只有一份 `copy.md`。不要再产生 `copy-v2.md`。
"""


def _unit_sort_key(uid):
    """S1-E02 按数字排，不要让 S1-E10 跑到 S1-E2 前面。"""
    s = str(uid or "")
    m = re.match(r"^S(\d+)-E(\d+)$", s, re.I)
    if m:
        return (0, int(m.group(1)), int(m.group(2)), s.lower())
    m = re.match(r"^E(\d+)$", s, re.I)
    if m:
        return (1, int(m.group(1)), 0, s.lower())
    return (2, 0, 0, s.lower())


def _unit_display_name(man):
    """模板里的说明句不是剧集名，别显示在切换菜单里。"""
    name = str((man or {}).get("entry") or (man or {}).get("title") or "").strip()
    if name.startswith("台账里的一行"):
        return ""
    return name


def _season_no(st):
    """季号只取数字；「第一季」「S2」「2」都能用。"""
    raw = str(((st or {}).get("meta") or {}).get("season") or "").strip()
    m = re.search(r"\d+", raw)
    return int(m.group()) if m else 1


def _next_unit_id(st, root=None):
    """下一集：按当前季的集号顺延；没有就 S1-E01。"""
    season = _season_no(st)
    max_n = 0
    rx = re.compile(r"^S(\d+)-E(\d+)$", re.I)
    ids = set((st or {}).get("units") or {})
    if root:
        base = Path(root) / "units"
        if base.is_dir():
            ids |= {d.name for d in base.iterdir()
                    if d.is_dir() and not d.name.startswith("_")}
    for uid in ids:
        m = rx.match(str(uid))
        if m and int(m.group(1)) == season:
            max_n = max(max_n, int(m.group(2)))
    return "S%d-E%02d" % (season, max_n + 1)


def _normalize_unit_id(raw, season):
    """S1-E2 / e02 / S1-E02 都归成 S1-E02；其它形式原样保留。"""
    s = str(raw or "").strip()
    m = re.fullmatch(r"(?i)e(\d{1,3})", s)
    if m:
        return "S%s-E%02d" % (season, int(m.group(1)))
    m = re.fullmatch(r"(?i)s(\d+)-e(\d{1,3})", s)
    if m:
        return "S%d-E%02d" % (int(m.group(1)), int(m.group(2)))
    return s


def _unit_state_for_new(st, utype):
    """新单元的站号状态：照旧单元的站号表复制一份，全部重置为 pending。

    优先同片型；没有同片型也拿任意一集当骨架——站号表和片型无关，
    `d gate` / `d done` 需要这些站存在。
    """
    rows = [u or {} for u in ((st or {}).get("units") or {}).values()]
    pick = None
    for prefer_same in (True, False):
        for u in rows:
            if not isinstance(u.get("stations"), dict) or not u["stations"]:
                continue
            if prefer_same and u.get("type") != utype:
                continue
            pick = u
            break
        if pick:
            break
    if not pick:
        return {"type": utype, "stations": {}, "next_action": ""}
    stations = copy.deepcopy(pick["stations"])
    for row in stations.values():
        if not isinstance(row, dict):
            continue
        row["status"] = "pending"
        row["artifact"] = ""
        for gate in row.get("gate") or []:
            if isinstance(gate, dict):
                gate["ok"] = False
    return {"type": utype, "stations": stations, "next_action": ""}


def _create_unit_skeleton(root, uid, utype, entry):
    """建一集的空壳。优先复制工程里的 units/_TEMPLATE。"""
    root = Path(root)
    dst = root / "units" / uid
    if dst.exists():
        raise ValueError("剧集 %s 已经存在" % uid)
    tpl = root / "units" / "_TEMPLATE"
    (root / "units").mkdir(parents=True, exist_ok=True)
    if tpl.is_dir():
        shutil.copytree(str(tpl), str(dst))
    else:
        dst.mkdir(parents=True)
    for rel in UNIT_DIRS:
        (dst / rel).mkdir(parents=True, exist_ok=True)

    man = _jload(dst / "manifest.json", {}) or {}
    man.update({"id": uid, "type": utype, "entry": entry or ""})
    for key, default in (("platform", ""), ("duration", ""), ("evidence", ""),
                         ("assets", []), ("files", {}), ("notes", "")):
        man.setdefault(key, default)
    _jwrite(dst / "manifest.json", man)

    defaults = {
        "storyboard/scenes.json": {"unit": uid, "scenes": []},
        "storyboard/shots.json": {"unit": uid, "fps": 25, "aspect": "16:9",
                                  "audio_rate": 4.4, "shots": [], "total_sec": 0},
        "prompts/prompts.json": {"unit": uid, "stills": [], "motions": []},
    }
    for rel, base in defaults.items():
        p = dst / rel
        doc = _jload(p, None)
        if not isinstance(doc, dict):
            doc = {}
        for key, value in base.items():
            doc.setdefault(key, copy.deepcopy(value))
        doc["unit"] = uid
        _jwrite(p, doc)

    copy_md = dst / "script" / "copy.md"
    if not copy_md.exists():
        copy_md.write_text(UNIT_COPY_TPL, encoding="utf-8", newline="\n")
    delivery = dst / "deliver" / "delivery.json"
    if not delivery.exists():
        _jwrite(delivery, {
            "unit": uid, "title": entry or "", "version": "v0",
            "status": "未开始", "duration_sec": 0, "aspect": "16:9",
            "resolution": "1920x1080",
            "platforms": ["douyin", "bilibili"],
            "files": {"final": "final/%s-final.mp4" % uid,
                      "subtitles": "final/%s-final.srt" % uid,
                      "poster": "final/%s-poster.jpg" % uid,
                      "audio": "final/audio-mix.wav"},
            "published": [],
        })
    return dst


def _mode_hint(root=None):
    mode = _project_mode(root) if root else "general"
    if mode == "short_drama":
        return ("按说好五段（vendor/shuohao-skills）：先判断当前在 outline / characters / "
                "art / script / staging / storyboard 哪一段，只推进我说的那一段；"
                "vendor 的 SKILL.md 和 validate 是准绳。")
    return ("按通用七步（创作思路 → 剧本/大纲 → 场 → 分镜 → 镜头 → 图谱 → 提示词）："
            "只做我说的那一步，不要自动往下写；先读 project.json 和当前单元。")


def _unit_takeover_prompt(root, uid):
    """点「用 WorkBuddy 打开」时，告诉它现在处理的是哪一集。"""
    man = _jload(Path(root) / "units" / uid / "manifest.json", {}) or {}
    utype = str(man.get("type") or "").strip() or "未定"
    entry = _unit_display_name(man)
    topic = ("这一集想讲：%s。\n" % entry) if entry else ""
    return _skill_prompt(
        "用 %s 接手这个 Trestle 工程，当前剧集是 %s（%s）。\n%s"
        "先读 project.json、units/%s/manifest.json 和 script/、storyboard/；"
        "%s 只做我说的那一步，不要自动往下写。"
        % (_active_skill(), uid, utype, topic, uid, _mode_hint(root)))


def _unit_new_prompt(root, uid, utype, entry):
    """新建一集后交给 WorkBuddy 的第一句话：只开这一集，不许一口气做完整集。"""
    topic = ("这一集想讲：%s。\n" % entry) if entry else ""
    return _skill_prompt(
        "工程里已经新建了剧集 %s（片型：%s），目录 units/%s/，目前只有空壳。\n%s"
        "用 %s 接手这一集：先读 project.json、units/%s/manifest.json 和 copy.md；"
        "%s\n"
        "每次只推进我问的那一步：scope=script 时只写 units/%s/script/copy.md，"
        "不要自动排分镜、不要写提示词。"
        % (uid, utype, uid, topic, _active_skill(), uid, _mode_hint(root), uid))


def _norm_name(text):
    """名字比对：去空白和分隔符，大小写不敏感。"""
    return re.sub(r"[\s·:：\-—_/]+", "", str(text or "")).lower()


# ---------------------------------------------------------------- 状态

def _free_port(start=3118, tries=8):
    """给 OAuth 回调挑一个空端口。"""
    import socket
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def _open_console(root, exe):
    """在工程根目录开一个新窗口跑这个程序。优先 Windows Terminal，退回落控制台。"""
    if os.name != "nt":
        subprocess.Popen([exe], cwd=root, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "pty"
    wt = shutil.which("wt")
    if not wt:
        cand = Path(os.environ.get("LOCALAPPDATA") or "") / "Microsoft" / "WindowsApps" / "wt.exe"
        if cand.is_file():
            wt = str(cand)
    if wt:
        try:
            subprocess.Popen([wt, "-d", root, "cmd", "/k", exe], close_fds=True)
            return "wt"
        except Exception:
            pass
    subprocess.Popen('start "" /D "%s" cmd /k "%s"' % (root, exe), shell=True)
    return "cmd"


AGENT_EXE = {
    "claude-code": ["claude", "claude.cmd", "claude.exe"],
    "workbuddy": ["workbuddy", "WorkBuddy", "workbuddy.cmd",
                  "workbuddy.exe", "WorkBuddy.exe"],
}
AGENT_EXTRA = {
    "claude-code": [
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        Path.home() / ".claude" / "local" / "claude.exe",
        Path("/usr/local/bin/claude"),
    ],
    "workbuddy": [
        Path.home() / "AppData" / "Local" / "Programs" / "WorkBuddy" / "WorkBuddy.exe",
        Path.home() / "AppData" / "Local" / "WorkBuddy" / "WorkBuddy.exe",
        Path(os.environ.get("PROGRAMFILES") or "C:/Program Files") / "WorkBuddy" / "WorkBuddy.exe",
        Path(os.environ.get("PROGRAMFILES(X86)") or "C:/Program Files (x86)") / "WorkBuddy" / "WorkBuddy.exe",
        Path.home() / ".workbuddy" / "bin" / "workbuddy",
    ],
}

WORKBUDDY_SKILL = "changji-director"
GUIDE_SKILL = "changji-guide"
PROMPT_SKILL = "changji-prompt"


def _active_skill():
    return os.environ.get("CJ_WORKBUDDY_SKILL") or WORKBUDDY_SKILL


def _bundled_skill_dir(name=None):
    """内置 skill 目录：优先 AIV_SKILL_DIR，其次安装包 / 源码目录。"""
    name = name or _active_skill()
    cands = []
    env = os.environ.get("AIV_SKILL_DIR")
    if env and Path(env).name == name:
        cands.append(Path(env))
    cands += [
        HERE.parents[1] / "skills" / name,          # 安装包：app/skills
        HERE.parents[1] / "skill" / name,           # 源码：内容包目录
        HERE.parents[2] / "app-src" / "skill" / name,
    ]
    for d in cands:
        try:
            if (Path(d) / "SKILL.md").is_file():
                return Path(d)
        except Exception:
            continue
    return None


def _skill_prompt(prompt, name=None):
    """给 Agent 的 prompt 前面挂内置 SKILL.md 的绝对路径，避免安装到用户目录。"""
    src = _bundled_skill_dir(name)
    text = str(prompt or "")
    if not src:
        return text
    prefix = "先读 %s 里的 SKILL.md，按这个 skill 的规则执行。\n\n" % src
    return text if text.startswith(prefix) else prefix + text


def _guide_bridge_text(guide):
    """桥接 skill 的全文：只放指针，教师规则和教程仍留在内置目录。"""
    return (
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
        % (guide / "SKILL.md", guide / "references")
    )


def _ensure_guide_bridge(targets=None):
    """给 WorkBuddy 一个极小的桥接 skill：只指向内置教师资料，不放核心内容。

    返回 workbuddy 目标是否可用。只有它能用，deeplink 才带 `skills=`、
    输入框才保持干净；写不进去时上层退回「prompt 里指向内置 SKILL.md」。
    """
    guide = _bundled_skill_dir(GUIDE_SKILL)
    if not guide:
        return False
    text = _guide_bridge_text(guide)
    if targets is None:
        targets = (
            Path.home() / ".workbuddy" / "skills" / GUIDE_SKILL,
            Path.home() / ".claude" / "skills" / GUIDE_SKILL,
        )
    else:
        targets = tuple(targets)
    changed = False
    workbuddy_ready = False
    for i, target in enumerate(targets):
        try:
            f = Path(target) / "SKILL.md"
            if not (f.is_file() and f.read_text(encoding="utf-8") == text):
                Path(target).mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
                changed = True
            if i == 0:
                workbuddy_ready = True
        except Exception:
            continue
    if changed:
        # 让 WorkBuddy 下次取技能列表时按目录重新扫，而不是读旧缓存。
        cache = Path(targets[0]).parent.parent / ".skill-list-cache.json"
        try:
            cache.unlink()
        except OSError:
            pass
    return workbuddy_ready


def _project_mode(root):
    """项目模式：general（通用七步） / short_drama（说好五段）。"""
    try:
        pj = json.loads((Path(root) / "project.json").read_text(encoding="utf-8"))
        return str((pj.get("meta") or {}).get("mode") or "general")
    except Exception:
        return "general"


def _mode_label(mode):
    return "短剧（说好五段）" if mode == "short_drama" else "通用（七步）"


def _registry_workbuddy():
    """workbuddy:// 协议注册里的客户端路径（支持装在非默认盘）。"""
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


def _workbuddy_link(root, skill=None, prompt=None, prefix=True, include_skill=False):
    """workbuddy://task：开新任务 → 切到工程目录 → 读内置 skill。

    changji-director 是内置核心资产：不挂 skills chip，也不复制到
    ~/.workbuddy/skills；prompt 第一段直接把内置 SKILL.md 的绝对路径交给客户端。
    Trestle 教师走 include_skill：只加载一个极小桥接 skill，输入框保持干净。

    WorkBuddy 读到 `skills=` 后会先查已安装技能（触发一次技能列表刷新），
    查不到再去技能市场按名字找，最后才提示未找到；prompt 仍然照常预填。
    所以桥接 skill 晚一步写入也不会让 deeplink 打不开。
    """
    root = str(root)
    skill = skill or _active_skill()
    prompt = prompt or (
        "用 %s 接手这个 Trestle 工程。先读 project.json 和当前单元，"
        "然后告诉我下一步做什么。" % skill)
    if prefix:
        prompt = _skill_prompt(prompt, skill)
    q = [("action", "start"), ("cwd", root), ("prompt", prompt)]
    if include_skill and skill:
        q.append(("skills", skill))
    return "workbuddy://task?" + urlencode(q)


def _wb_scope(action):
    """每个 WorkBuddy 入口的改动边界：只改这一类文件，别顺手扩写。"""
    a = action or ""
    if "怎么用" in a or "帮助" in a or "问 AI" in a or "Trestle 教师" in a:
        return ("只读应用帮助 / 教程文档，不写任何工程文件。",
                "不要生成、修改内容、布线、调用 LibTV、替用户点按钮。",
                "先确认当前页面；最多 5 步；每步一个动作；给原理 + 步骤 + 坑；"
                "涉及创作时转导演 skill，不自己写。")
    if "开始创作" in a or "第一次用" in a:
        return ("只问 3 个会改变结构的问题，并把答案写进 "
                "units/<unit>/design/导演简报.md。",
                "不要写剧本正文、不要建资产、不要切场、不要出图谱；等用户回答。",
                "这是第一次使用：先用 3 句话说明分工"
                "（WorkBuddy 写 / Trestle 看和接 / LibTV 出片），再问问题。")
    # 「完善角色 / 场景 / 道具 / 品牌 / 画风」必须落在当前资产目录，
    # 不能落到下面按类型新建资产的分支里（那会顺手建别的角色 / 场景）。
    if a.startswith("完善") and not any(
            k in a for k in ("剧本", "大纲", "创意", "思路")):
        return ("只改任务书点名的那个资产目录下的 manifest.json / prompts.md / "
                "build/<画布>/U*/unit.json。",
                "不要动其他资产、其他生成单元、剧本、分镜、图谱、LibTV、"
                ".workbuddy/ 日志或工作台源码。",
                "先确认目标单元：任务书点名了就只改它；没点名且有多个单元时，"
                "先问用户改哪一个，不要自己挑，也不要先做项目勘察。")
    if "装造" in a:
        return ("只改 assets/characters 下已有角色的 look / 装造 / 变体设定。",
                "不要新建角色，不要动场景、道具、画风、剧本正文、分镜。",
                "只补装造，不重写角色性格，不做下一步。")
    if "角色" in a:
        return ("只新建或修改 assets/characters 下的角色资产"
                "（manifest.json、prompts.md、变体）。",
                "不要动场景、道具、画风、剧本正文、分镜、提示词。",
                "只处理角色；选中内容没提到的角色不要建。")
    if "美术" in a or "艺术风格" in a:
        return ("只新建或修改 assets/style、assets/scenes、assets/props 下的美术设定"
                "（统一画风 style_bible / 场景锚点 / 叙事道具）。",
                "不要动角色、剧本正文、分镜、提示词。",
                "按 novel-art 跑：场景 + 叙事道具 + 统一画风一次做齐，"
                "validate 过了再导入 Trestle。")
    if "场景" in a:
        return ("只新建或修改 assets/scenes 下的场景资产。",
                "不要动角色、道具、画风、剧本正文、分镜。",
                "只处理场景；选中内容没提到的地点不要建。")
    if "道具" in a:
        return ("只改 assets/props 下的道具资产。",
                "不要动角色、场景、画风、剧本、分镜。",
                "只处理道具，不做下一步。")
    if "资产画布" in a or "资产布线" in a or "布线方案" in a:
        return ("只改 assets/<资产>/build/<画布>/canvas.json，以及该画布各 "
                "unit.json 的 refs / style / size / model；以任务书点名的画布为准。",
                "不要改 unit.json 的 prompt 正文（除非用户明确要求）、manifest、"
                "prompts.md、其他画布、其他资产；不要直接改 LibTV 节点。",
                "只重排这个画布的结构 / 布线方案；写完让用户回 Trestle 点"
                "「布线」或「推送」，不要自己去改 LibTV。")
    if "品牌" in a or "完善资产" in a or "资产设定" in a:
        return ("只改这个资产目录下的 manifest.json / prompts.md / "
                "build/<画布>/U*/unit.json；以任务书点名的目标文件为准。"
                "用户说「提示词」时默认改 unit.json 的 prompt / negative，"
                "不是 prompts.md。",
                "不要动其他资产、其他生成单元、剧本、分镜、图谱、LibTV、"
                ".workbuddy/ 日志或工作台源码。",
                "先确认目标单元：只有一个就改它，有多个先问用户改哪一个，"
                "不要自己挑，也不要先做项目勘察。")
    if "画风" in a:
        return ("只改 assets/style 下的画风设定。",
                "不要动角色、场景、道具、剧本、分镜。",
                "只处理画风，不做下一步。")
    if "大纲" in a or "延申思路" in a or "思路" in a:
        return ("只改 design/创意库 里这一份创意 / 大纲文件。",
                "不要动资产、剧本、分镜、提示词。",
                "只改大纲本身，不顺手写剧本或建角色。")
    if "切场" in a or ("生成场" in a and "场景" not in a):
        return ("只改 units/<unit>/storyboard/scenes.json 的场次，"
                "并为每个场建 units/<unit>/production/<场>/scene.json。",
                "不要动剧本正文、资产、shots.json、提示词；"
                "不要建分镜段目录。",
                "只切场：每场写 goal / obstacle / audience_in / audience_out / "
                "state_change；reveal ≤3、hold ≥1。不顺手拆分镜。")
    if "资产提示词" in a or "生成单元提示词" in a:
        return ("只改指定画布、指定生成单元的 unit.json 的 prompt / negative；"
                "没指定单元时才改 prompts.md 里对应的一节。",
                "不要改 manifest、canvas、libtv.json、其他单元、剧本、分镜、"
                "其他资产；不要写制作说明、判断、来源、路径或「之后 / 先 / 再」。",
                "这是会原样发给 LibTV 节点的出图提示词：只写画面本身"
                "（主体、外形、服装、构图、机位、光、背景、风格）；"
                "缺关键事实先问。")
    if "图谱" in a:
        return ("只改 units/<unit>/storyboard/graph.json。",
                "不要动剧本、场、镜头、提示词、资产。",
                "只出图谱：配方 / 节点 / 边；不建 LibTV 节点，不发起生成。")
    if "分镜提示词" in a or "生成提示词" in a:
        return ("只改 units/<unit>/prompts/prompts.json，以及 "
                "units/<unit>/storyboard/graph.json 里各节点的 prompt。",
                "不要动剧本正文、场次结构、资产设定、镜号。",
                "只补 / 改提示词，不重切场、不重排镜；图谱节点提示词也要写，"
                "布线读的是 graph.json。")
    if "从剧本生成分镜" in a or "生成分镜" in a or "分镜" in a:
        return ("只改 units/<unit>/storyboard/shots.json、"
                "units/<unit>/storyboard/scenes.json 的 shots[] 归属，"
                "以及 production/<场>/<分镜>/storyboard.json。",
                "不要动剧本正文、scenes.json 的场分析字段（goal / obstacle / "
                "audience_in / audience_out / reveal / hold）、资产设定、提示词文件。",
                "只拆分镜：镜号连续、每镜六栏齐；新镜头要回写 scenes.json 的 shots[]；"
                "不顺手写提示词。")
    if "写剧本" in a:
        return ("只写当前集的 units/<unit>/script/copy.md。",
                "不要动资产、分镜、提示词、其他集。",
                "只写剧本正文，不顺手切分镜。")
    if "完善剧本" in a or "改这场" in a:
        return ("只改当前剧本 units/<unit>/script/copy.md 的对应场次。",
                "不要动资产、分镜、提示词、其他场次。",
                "只改这一场 / 这一处，不顺手改角色设定。")
    if "这一镜" in a:
        return ("只改当前这一镜的分镜 / 提示词文件。",
                "不要动剧本、角色、场景、其他镜。",
                "只处理这一镜，不顺手重跑别的镜；用户点名某个节点时按单节点模式"
                "只改该节点的 prompt，没点名就先问清楚是首帧、尾帧还是视频节点。")
    return ("只改当前节点对应的文件。",
            "不要动其他节点、其他资产、其他集。",
            "只做当前任务，不做下一步。")


def _wb_intent_block(intent, focus="", first_version=False):
    """任务书最前面先放用户原话，别让模型在几十行规则里自己找意图。"""
    s = str(intent or "").strip()
    if s:
        # 长文多半是用户直接贴的草稿或整段要求，不是一句修改指令。
        draft = len(s) >= 120 or (len(s) >= 60 and s.count("。") >= 2)
        out = [("【用户给的草稿 / 要求（作为基稿，可据此重写）】" if draft
                else "【用户要改的（原话，优先满足）】"), s]
        if focus:
            out.append("本轮只先改：%s。" % focus)
        out.append(
            "按这段重写；只补它没覆盖的关键画面事实，不要另起一套设定。"
            if draft else
            "只改这句话直接涉及的字段；没提到的地方保持原样，"
            "不要顺手重写整段。")
        return out
    if first_version:
        return [
            "【用户要改的】还没有具体说明。",
            "这是第一次写：按现有分镜 / 资产设定写第一版；"
            "缺关键事实时最多问 2 个会改变结果的问题。",
        ]
    return [
        "【用户要改的】还没说。",
        "先不要改文件：先问用户「要改哪里、想改成什么样」，最多 2 个问题，"
        "等用户回答后再动手。不要自己猜，也不要先做项目勘察。",
    ]


def _node_style_rows(node):
    """从 LibTV 节点里读用户选的设计风格（LoRA / 风格节点）。"""
    data = (node or {}).get("data") if isinstance(node, dict) else {}
    data = data if isinstance(data, dict) else {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    fine = params.get("fineTuneModels")
    rows = []
    if isinstance(fine, list):
        for x in fine:
            if isinstance(x, dict) and x.get("name"):
                rows.append({
                    "name": str(x.get("name")),
                    "uuid": str(x.get("uuid") or ""),
                    "weight": x.get("weight"),
                    "from_style_node": bool(x.get("_fromStyleNode")),
                })
    return rows


def _meta_prompt_reason(text):
    """判断一段文字是不是「制作说明 / 判断」，而不是能直接出图的提示词。

    资产画布会把 unit.json 的 prompt 原样交给 LibTV；如果这里写的是
    「母图出来之后……以…为准……」，模型收到的是流程而不是画面，结果就会
    和预期完全不一样。宁可在布线前拦下，也不要白花积分。
    """
    p = re.sub(r"\s+", " ", str(text or "")).strip()
    if not p:
        return "提示词是空的"
    low = p.lower()
    visual = ("画面", "背景", "主体", "人物", "服装", "材质", "构图", "镜头",
              "景别", "光线", "光", "颜色", "实心", "居中", "留白", "正面",
              "侧面", "特写", "全景", "中景", "视角", "纹理", "质感",
              "正方形", "圆角", "阴影", "高光", "底色", "线条", "轮廓")
    strong = ("出来之后", "先做", "先建", "再按", "为准", "路径", "收口",
              "验收", "清单", "提示词", "节点", "参数", "工作台", "libtv",
              "这里只", "本页", "不要", "注意", ".md", "markdown")
    visual_n = sum(1 for k in visual if k in p)
    meta_n = sum(1 for k in strong if k in low)
    # 只拦真正的文件名 / 文档标记；「工作台」「LibTV」可能只是画面上要讲的
    # 品牌内容（例如 Trestle 自己的工作台定位），不能一刀切。
    if re.search(r"\.md\b|markdown", low):
        return "提示词里不要写文件名 / markdown"
    if (re.match(r"^(母图|主图|先|注意|这条|本提示词|模型|以)", p)
            and visual_n < 3):
        return "这条更像制作说明 / 判断，不是出图描述"
    if re.search(r"(出来之后|先.{0,20}再|以.{0,12}为准|路径.{0,12}(收口|定)|仍以)",
                 p) and visual_n < 3:
        return "这条更像制作说明 / 判断，不是出图描述"
    if meta_n >= 3 and visual_n <= 1:
        return "这条更像制作说明 / 判断，不是出图描述"
    return ""


def _launch_workbuddy(root, exe, prompt=None):
    """把 deeplink 交给客户端：优先 exe + URL，失败再走协议注册。"""
    url = _workbuddy_link(root, prompt=prompt)
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "ELECTRON_RUN_AS_NODE"}
    try:
        subprocess.Popen([exe, url], cwd=str(root), close_fds=True, env=env,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
        return "deeplink"
    except Exception:
        pass
    if os.name == "nt":
        os.startfile(url)
    return "deeplink"


def _agent_overrides(root):
    """工作台自己的入口覆盖：agents.json。不写进 project.json。"""
    if not root:
        return {}
    d = _jload(aux_dir(root) / "agents.json", {}) or {}
    return d if isinstance(d, dict) else {}


def _agent_exe(aid, root=None):
    """找 Agent 的启动入口。顺序：手工指定 → 协议注册 → PATH → 常见安装位置。"""
    ov = _agent_overrides(root).get(aid) or {}
    p = str(ov.get("path") or "").strip()
    if p and Path(p).exists():
        return p
    if aid == "workbuddy":
        reg = _registry_workbuddy()
        if reg:
            return reg
    for n in AGENT_EXE.get(aid, []):
        hit = shutil.which(n)
        if hit:
            return hit
    for p in AGENT_EXTRA.get(aid, []):
        if Path(p).exists():
            return str(p)
    return ""


def _agent_set_path(root, aid, path):
    p = Path(str(path or "").strip().strip('"'))
    if not p.is_file():
        raise ValueError("这个路径不是一个文件：%s" % path)
    f = aux_dir(root) / "agents.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    d = _jload(f, {}) or {}
    d.setdefault(aid, {})["path"] = str(p)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def _node_prompt(node):
    """从 LibTV 节点里尽量稳地掏出提示词，字段名可能随版本变。"""
    d = (node or {}).get("data") or {}
    for path in (("prompt",), ("text",), ("params", "prompt"), ("input", "prompt")):
        cur = d
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    v = (node or {}).get("prompt")
    return v.strip() if isinstance(v, str) else ""


def aux_dir(root):
    """机器级小状态放哪（Agent 入口、LibTV 授权）。

    设了 AIV_STATE_DIR（桌面版）就放到工程外，客户看不到；否则留在工程里。
    """
    env = os.environ.get("AIV_STATE_DIR")
    if env:
        return Path(env)
    return Path(root).resolve() / ".workbuddy" / "tmp"


def state_path(root):
    """工作台运行态文件放哪。

    设了 AIV_STATE_DIR（桌面版）就放到工程外的目录，按工程路径散列命名，
    客户打开工程文件夹看不到运行态。没设就留在工程里（命令行 / npm run dev）。
    """
    root = Path(root).resolve()
    env = os.environ.get("AIV_STATE_DIR")
    if env:
        key = hashlib.sha1(str(root).lower().encode("utf-8")).hexdigest()[:16]
        return Path(env) / (key + "-workbench.json")
    return aux_dir(root) / "workbench.json"


def _migrate_idea_dir(root):
    """把旧项目的 design/剧本库 迁到 design/创意库。

    创意页叫「创意」，文件也放「创意库」，不再和正式剧本混在一个名字下。
    只挪不删：目标已有同名文件就留在原处（创意页仍能读旧目录）。
    """
    root = Path(root)
    old = root / "design" / "剧本库"
    new = root / "design" / "创意库"
    if not old.is_dir():
        return ""
    note = ""
    if not new.exists():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            note = "rename"
        except OSError:
            pass
    if not note:
        moved = 0
        for p in sorted(old.rglob("*")):
            if not p.is_file():
                continue
            dst = new / p.relative_to(old)
            if dst.exists():
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dst))
                moved += 1
            except OSError:
                pass
        for d in sorted((x for x in old.rglob("*") if x.is_dir()),
                        key=lambda x: len(x.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            old.rmdir()
        except OSError:
            pass
        note = "merge:%d" % moved
    try:
        log = root / ".workbuddy" / "migration.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write("%s  design/剧本库 -> design/创意库  %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), note))
    except OSError:
        pass
    return note


class State:
    def __init__(self, root, unit, token):
        self.root = Path(root).resolve()
        self.unit = unit
        self.token = token
        self.lock = threading.RLock()
        self.clients = set()
        self._snaps = {}
        self._at = {}
        self._prompts = {}
        self.versions = {s: 0 for s in snap_mod.SECTIONS}
        self.started = time.time()
        self.watcher = None
        self.state_file = state_path(self.root)
        self.backup_root = self.root / ".workbuddy" / "backup"
        self.history_root = self.root / ".workbuddy" / "history" / "prompts"
        self._gate_cache = (0.0, None)
        self._procs_cache = (0.0, None)    # (ts, 进程表) —— tasklist 太贵，短缓存
        self._login = None                 # 正在跑的授权子进程
        self._probe_cache = (0.0, None)    # (ts, (ok, info))
        try:
            _st = _jload(self.root / "project.json", {}) or {}
            self._project_unit = str((_st.get("meta") or {}).get("active_unit") or "")
        except Exception:
            self._project_unit = ""
        _migrate_idea_dir(self.root)

    # ---------- 快照 ----------
    def snapshot(self, unit=None, fresh=False):
        unit = unit or self.unit
        with self.lock:
            now = time.time()
            if (not fresh and unit in self._snaps
                    and now - self._at.get(unit, 0) < 0.5):
                return self._snaps[unit]
            snap = snap_mod.build(self.root, unit)
            self._snaps[unit] = snap
            self._at[unit] = now
            self._prompts[unit] = _jload(
                self.root / "units" / unit / "prompts" / "prompts.json", {}) or {}
            return snap

    def invalidate(self, sections=None):
        with self.lock:
            # WorkBuddy / d 命令切换剧集后，工作台跟着 project.json 走。
            # 只在 active_unit 真的变了时才切，避免把 -u 指定的当前集顶掉。
            if sections is None or "status" in sections:
                try:
                    st = _jload(self.root / "project.json", {}) or {}
                    au = str((st.get("meta") or {}).get("active_unit") or "")
                    if au and au != self._project_unit and au in (st.get("units") or {}):
                        self._project_unit = au
                        self.unit = au
                except Exception:
                    pass
            self._snaps.clear()
            self._at.clear()
            for s in (sections or list(self.versions)):
                self.versions[s] = self.versions.get(s, 0) + 1
        self.broadcast(sections or list(self.versions))

    # ---------- 提示词历史 ----------
    def archive_prompts(self, sections):
        if "prompts" not in sections:
            return
        with self.lock:
            cache = dict(self._prompts)
        for unit, old in cache.items():
            if not old:
                continue
            cur = _jload(self.root / "units" / unit / "prompts" / "prompts.json", {}) or {}
            if cur == old:
                continue
            stamp = time.strftime("%Y%m%d-%H%M%S")
            d = self.history_root / stamp
            n = 1
            while d.exists():
                n += 1
                d = self.history_root / ("%s-%d" % (stamp, n))
            _jwrite(d / "prompts.json", old)

    # ---------- SSE ----------
    def broadcast(self, sections):
        msg = ("event: change\ndata: %s\n\n" % json.dumps(
            {"sections": list(sections), "v": dict(self.versions),
             "at": time.strftime("%H:%M:%S")}, ensure_ascii=False)).encode("utf-8")
        with self.lock:
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.clients.discard(q)


def _backup(state, p):
    p = Path(p)
    if not p.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = state.backup_root / stamp / p.relative_to(state.root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
    batches = sorted([d for d in state.backup_root.iterdir() if d.is_dir()])
    for d in batches[:-20]:
        shutil.rmtree(d, ignore_errors=True)


def _name_conflict(exc):
    """LibTV 同一 project 内显示名唯一，create_node 撞名会返回这个码。"""
    return "NODE_NAME_CONFLICT" in str(exc or "")


def _revision_conflict(exc):
    """读快照与写入之间画布被改过。不同工具/版本叫法不一样。"""
    msg = str(exc or "").upper()
    return ("REVISION_CONFLICT" in msg or
            "SNAPSHOT_REVISION_MISMATCH" in msg or
            ("REVISION" in msg and "MISMATCH" in msg))


def _call_write(client, name, args, timeout=120, attempts=3):
    """写画布：规划前画布变了就重试，别让一次瞬时冲突整条布线失败。"""
    for attempt in range(1, attempts + 1):
        try:
            return client.call(name, args, timeout=timeout)
        except Exception as e:
            if not _revision_conflict(e) or attempt >= attempts:
                raise
            time.sleep(0.3)


def _adopt_named_node(client, project_id, name, node_type="", err=None):
    """接管画布上已有的同名节点；找不到就把原错误抛回去。

    上一次布线可能已经建成功但本地没落盘 node_id，重试就会撞名。接管后改走
    update_node，既保住原来的节点和输出，也不会一直卡在冲突上。
    """
    nid = client.find_node_id(project_id, name, node_type)
    if nid:
        return nid
    raise err if err is not None else RuntimeError(
        "画布上已有同名节点，但读取不到它的 ID：%s" % name)


# ---------------------------------------------------------------- 处理器

class Handler(BaseHTTPRequestHandler):
    state = None
    server_version = "WB/2.0"

    def log_message(self, *a):
        pass

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, obj)

    def _download(self, body, name, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         "attachment; filename=\"export\"; filename*=UTF-8''%s"
                         % quote(name, safe=""))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if n > MAX_UPLOAD * 2:
            raise ValueError("请求体太大")
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _unit(self, q):
        u = (q.get("unit") or [""])[0]
        if u:
            return u
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "wb_unit" and v:
                return unquote(v)
        return self.state.unit

    def _safe(self, rel):
        root = self.state.root
        p = (root / unquote(rel)).resolve()
        try:
            if not p.is_relative_to(root):
                return None
        except AttributeError:
            if not str(p).startswith(str(root) + os.sep):
                return None
        return p if p.is_file() else None

    def _authed(self):
        return self.headers.get("X-WB-Token") == self.state.token

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query, keep_blank_values=True)

        # 旧版地址 / 旧分页接口：统一回新版首页或资产库。
        if path in ("/characters", "/art"):
            return self._redirect("/assets")
        if path.startswith("/frag/") or path == "/next" or path.startswith("/next/"):
            return self._redirect("/")

        if path == "/api/ping":
            snap = self.state.snapshot(self._unit(q))
            return self._json(200, {
                "ok": True, "unit": snap["meta"]["unit"],
                "shots": snap["stats"]["shots"],
                "storyboards": snap["stats"]["storyboards"] + snap["stats"]["suggested"],
                "versions": self.state.versions,
                "files": self.state.watcher.file_count if self.state.watcher else 0,
                "started": self.state.started,
            })
        if path == "/api/onboard":
            return self._onboard_get()
        if path == "/api/version":
            return self._json(200, {"sections": self.state.versions})
        if path == "/api/diag":
            return self._json(200, self._diag(q))
        if path == "/api/events":
            return self._sse()
        if path == "/api/state":
            snap = self.state.snapshot(self._unit(q))
            sync_readme(self.state.root, snap)
            # 前端靠 versions 判断要不要重拉 /api/structure 和图谱；snapshot 本身没有这个键。
            out = dict(snap)
            out["versions"] = self.state.versions
            return self._json(200, out)
        if path == "/api/idea/detail":
            snap = self.state.snapshot(self._unit(q))
            ip = str((q.get("idea") or [""])[0]).replace("\\", "/")
            idea = next((x for x in snap["script"]["ideas"]
                         if x["path"] == ip), None)
            if idea is None:
                return self._json(404, {"ok": False, "error": "找不到这篇创意"})
            d = derive._idea_derived(snap, idea)
            return self._json(200, {
                "ok": True, "idea": idea,
                "scripts": derive.scripts_for_idea(snap, idea),
                "assets": {
                    "characters": [a["id"] for a in d["chars"]],
                    "scenes": [a["id"] for a in d["scenes"]],
                    "props": [a["id"] for a in d["props"]],
                    "styles": [a["id"] for a in d["styles"]],
                },
            })
        if path == "/api/gate":
            return self._json(200, self._gate_info(
                fresh=str((q.get("fresh") or [""])[0]) == "1"))
        if path == "/api/engine/ready":
            unit = str((q.get("unit") or [""])[0]).strip() or None
            try:
                return self._json(200, engine_ready.check(self.state.root, unit))
            except SystemExit:
                return self._json(400, {
                    "ok": False, "error": "这里不是工程（缺 project.json）"})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:500]})
        if path == "/api/storyboard/graph":
            return self._graph_read(q)
        if path == "/api/storyboard/history":
            return self._history(q)
        if path == "/api/structure":
            return self._structure(q)
        if path == "/api/deliver/info":
            return self._deliver_info(q)
        if path == "/api/deliver/tree":
            return self._deliver_tree(q)
        if path == "/api/deliver/film":
            return self._deliver_film(q)
        if path == "/api/deliver/poster":
            return self._deliver_poster(q)
        if path == "/api/deliver/zip":
            return self._deliver_zip(q)
        if path == "/api/libtv/index":
            return self._libtv_index_read(q)
        if path == "/api/libtv/link":
            return self._libtv_link_get(q)
        if path == "/api/libtv/project/nodes":
            return self._libtv_project_nodes(q)
        if path == "/api/libtv/session":
            return self._json(200, self._libtv_session(
                net=str((q.get("net") or [""])[0]) == "1"))
        if path == "/api/conn":
            net = str((q.get("net") or [""])[0]) == "1"
            out = self._agent_conn(fresh=net)
            out["libtv"] = self._libtv_session(net=net)
            out["graph"] = self._graph_conn_summary()
            return self._json(200, out)
        if path == "/api/units":
            active = self._unit(q)
            st = _jload(self.state.root / "project.json", {}) or {}
            return self._json(200, {
                "ok": True, "units": self._units(active), "active": active,
                "season": str(_season_no(st)),
                "next": _next_unit_id(st, self.state.root),
            })
        if path == "/api/export/outline":
            idea = str((q.get("idea") or [""])[0])
            p, rel = self._idea_path(idea)
            if not p.exists():
                return self._json(404, {"ok": False, "error": "找不到大纲"})
            return self._download(p.read_bytes(), p.stem + ".md",
                                  "text/markdown; charset=utf-8")
        if path == "/api/export/storyboard":
            snap = self.state.snapshot(self._unit(q))
            return self._download(_storyboard_md(snap), "分镜-%s.md" % snap["meta"]["unit"],
                                  "text/markdown; charset=utf-8")
        if path == "/api/export/assets":
            snap = self.state.snapshot(self._unit(q))
            return self._download(_assets_md(snap), "资产清单-%s.md" % snap["meta"]["unit"],
                                  "text/markdown; charset=utf-8")
        if path == "/api/export/prompts":
            snap = self.state.snapshot(self._unit(q))
            p = (self.state.root / "units" / snap["meta"]["unit"] /
                 "prompts" / "prompts.json")
            if not p.is_file():
                return self._json(404, {"ok": False, "error": "没有 prompts.json"})
            return self._download(p.read_bytes(), "提示词-%s.json" % snap["meta"]["unit"],
                                  "application/json; charset=utf-8")
        if path == "/api/export":
            unit = self._unit(q)
            snap = self.state.snapshot(unit)
            scene = str((q.get("scene") or [""])[0])
            body = export_mod.build(snap, self.state.root, scene_id=scene).encode("utf-8")
            name = export_mod.filename(snap, scene)
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             "attachment; filename=\"export.md\"; filename*=UTF-8''%s"
                             % quote(name, safe=""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
            return

        if path.startswith("/static/"):
            p = (STATIC / unquote(path[len("/static/"):])).resolve()
            try:
                ok = p.is_relative_to(STATIC.resolve())
            except AttributeError:
                ok = str(p).startswith(str(STATIC.resolve()) + os.sep)
            if not ok or not p.is_file():
                return self._json(404, {"ok": False})
            return self._file(p)
        if path.startswith("/img/"):
            p = self._safe(path[len("/img/"):])
            if not p or p.suffix.lower() not in MIME:
                return self._json(404, {"ok": False})
            return self._file(p)
        if path.startswith("/raw/"):
            p = self._safe(path[len("/raw/"):])
            if not p or p.suffix.lower() not in MIME:
                return self._json(404, {"ok": False})
            return self._file(p)

        # 新版是单页应用：所有非 API / 静态资源路径都交给 React Router。
        p = STATIC / "app" / "index.html"
        if p.is_file():
            html = p.read_text(encoding="utf-8")
            meta = ('<meta name="wb-token" content="%s">' % self.state.token)
            launcher = _launcher_url()
            if launcher:
                meta += ('<meta name="launcher-url" content="%s">'
                         % launcher.replace('"', "&quot;"))
            html = html.replace("</head>", meta + "</head>", 1)
            return self._send(200, html, "text/html; charset=utf-8")
        return self._json(404, {"ok": False, "error": "workbench not built"})

    def _file(self, p):
        ctype = MIME.get(p.suffix.lower()) or mimetypes.guess_type(str(p))[0] \
            or "application/octet-stream"
        try:
            data = p.read_bytes()
        except OSError:
            return self._json(404, {"ok": False})
        return self._send(200, data, ctype)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue()
        with self.state.lock:
            self.state.clients.add(q)
        try:
            hello = ("event: hello\ndata: %s\n\n" % json.dumps(
                {"v": self.state.versions, "unit": self.state.unit})).encode("utf-8")
            self.wfile.write(hello)
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if item is None:
                    break
                self.wfile.write(item)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self.state.lock:
                self.state.clients.discard(q)

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authed():
            return self._json(403, {"ok": False, "error": "token"})
        try:
            body = self._body()
            if path == "/api/review":
                return self._review(body)
            if path == "/api/idea/save":
                return self._idea_save(body)
            if path == "/api/idea/new":
                return self._idea_new(body)
            if path == "/api/idea/script/new":
                return self._json(200, self._idea_script_new(body))
            if path == "/api/idea/script":
                return self._idea_script(body)
            if path == "/api/idea/open":
                return self._idea_open(body)
            if path == "/api/idea/delete":
                return self._idea_delete(body)
            if path == "/api/idea/preview":
                return self._json(200, {"ok": True, "html": md.render(
                    str(body.get("text") or ""))})
            if path == "/api/idea/assets":
                return self._idea_assets(body)
            if path == "/api/unit/assets":
                return self._unit_assets(body)
            if path == "/api/wb/open":
                return self._json(200, self._wb_open(body))
            if path == "/api/asset/upload":
                return self._asset_upload(body)
            if path == "/api/asset/variant":
                return self._asset_variant(body)
            if path == "/api/asset/promote":
                return self._asset_promote(body)
            if path == "/api/asset/new":
                return self._asset_new(body)
            if path == "/api/asset/manifest":
                return self._asset_manifest(body)
            if path == "/api/asset/canvas":
                return self._asset_canvas_ensure(body)
            if path == "/api/asset/canvas/new":
                return self._asset_canvas_new(body)
            if path == "/api/asset/wire":
                return self._asset_wire(body)
            if path == "/api/asset/push":
                return self._asset_push(body)
            if path == "/api/asset/libtv/state":
                return self._asset_libtv_state(body)
            if path == "/api/asset/libtv/pull":
                return self._asset_libtv_pull(body)
            if path == "/api/asset/sync":
                return self._asset_sync(body)
            if path == "/api/asset/unit/prompt":
                return self._asset_unit_prompt(body)
            if path == "/api/scene/assets":
                return self._scene_assets(body)
            if path == "/api/storyboard/assets":
                return self._storyboard_assets(body)
            if path == "/api/libtv/link":
                return self._libtv_link(body)
            if path == "/api/libtv/login":
                return self._json(200, self._libtv_login())
            if path == "/api/libtv/logout":
                return self._json(200, self._libtv_logout())
            if path == "/api/agent/launch":
                return self._json(200, self._agent_launch(body))
            if path == "/api/agent/path":
                return self._json(200, self._agent_path(body))
            if path == "/api/reveal":
                return self._reveal(body)
            if path == "/api/deliver/export":
                return self._deliver_export(body)
            if path == "/api/deliver/open":
                return self._deliver_open(body)
            if path == "/api/film/new":
                return self._film_new(body)
            if path == "/api/units/new":
                return self._json(200, self._unit_new(body))
            if path == "/api/units/select":
                return self._json(200, self._unit_select(body))
            if path == "/api/project/mode":
                return self._project_mode(body)
            if path == "/api/libtv/index/rebuild":
                return self._libtv_index_rebuild(body)
            if path == "/api/libtv/ensure":
                return self._json(200, self._libtv_ensure(body))
            if path == "/api/libtv/sync-scene":
                return self._json(200, self._libtv_ensure(body))
            if path == "/api/libtv/archive":
                return self._json(200, self._libtv_archive(body))
            if path == "/api/storyboard/graph/save":
                return self._graph_write(body, self._graph_save)
            if path == "/api/storyboard/graph/plan":
                return self._graph_plan(body)
            if path == "/api/storyboard/graph/bind":
                return self._graph_write(body, self._graph_bind)
            if path == "/api/storyboard/graph/unbind":
                return self._graph_write(body, self._graph_unbind)
            if path == "/api/storyboard/graph/version":
                return self._graph_write(body, self._graph_version)
            if path == "/api/storyboard/graph/generate":
                return self._graph_write(body, self._graph_generate)
            if path == "/api/storyboard/graph/wire":
                return self._graph_write(body, self._graph_wire)
            if path == "/api/storyboard/graph/push":
                return self._graph_write(body, self._graph_push)
            if path == "/api/storyboard/graph/sync":
                return self._graph_write(body, self._graph_sync)
            if path == "/api/storyboard/graph/state":
                return self._graph_write(body, self._graph_state)
            if path == "/api/storyboard/graph/pull":
                return self._graph_write(body, self._graph_pull)
            if path == "/api/storyboard/graph/review":
                return self._graph_write(body, self._graph_review)
            if path == "/api/storyboard/stills":
                return self._storyboard_stills(body)
            if path == "/api/storyboard/stills/sync":
                return self._storyboard_stills_sync(body)
            if path == "/api/storyboard/wire":
                return self._storyboard_wire(body)
            if path == "/api/storyboard/sync":
                return self._storyboard_sync(body)
            if path == "/api/storyboard/pull":
                return self._json(200, self._storyboard_pull(body))
            if path == "/api/onboard":
                return self._onboard_set(body)
            if path == "/api/setup/dismiss":
                return self._json(200, self._setup_dismiss(body))
            if path == "/api/gate/start":
                return self._json(200, self._gate_start(body))
            if path == "/api/keyframe/gen":
                return self._keyframe_gen(body)
            if path == "/api/shutdown":
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self._json(404, {"ok": False, "error": "unknown api"})
        except LibTVError as e:
            return self._json(400, {"ok": False, "error": str(e),
                                    "code": "libtv", "kind": e.kind, "fix": e.fix})
        except Conflict as e:
            return self._json(409, {"ok": False, "error": str(e), "code": 409})
        except Exception as e:
            msg = str(e)
            kind = libtv_mcp.classify(msg)
            # 没有 token / token 失效时，LibTV 有时不给 401 而是 400，body 还是空的。
            # 这种情况别把 Python 的 "HTTP Error 400: Bad Request" 原样丢给用户，
            # 先把「去连接 LibTV」这一步说清楚。
            if isinstance(e, libtv_mcp.LibTVHTTPError) and \
                    kind == "unknown" and e.status in (400, 401, 403):
                business = ("NODE_NAME_CONFLICT", "IDEMPOTENCY_CONFLICT",
                            "REVISION_CONFLICT", "SNAPSHOT_REVISION_MISMATCH",
                            "invalid_argument", "validation error")
                if not any(x in msg for x in business):
                    kind = "unauthorized"
                    msg = (msg + "。如果还没连接 LibTV，请先到「设置 → LibTV」"
                                  "点「去连接」；刚换过账号或授权过期时也重新连一次。")
            if kind != "unknown":
                return self._json(400, {"ok": False, "error": msg, "code": "libtv",
                                        "kind": kind, "fix": libtv_mcp.fix_for(kind)})
            return self._json(400, {"ok": False, "error": msg})

    def _review(self, body):
        st = self.state
        uid = body.get("unit") or st.unit
        p = st.root / "units" / uid / "review" / "shot-review.json"
        mt = int(body.get("mtime") or 0)
        if mt and p.exists() and p.stat().st_mtime_ns != mt:
            raise Conflict("shot-review.json 已被别处修改，已刷新")
        cur = {}
        d = _jload(p, None)
        if isinstance(d, dict) and isinstance(d.get("rows"), list):
            for r in d["rows"]:
                sid = str(r.get("shot") or "").zfill(3)
                if sid:
                    cur[sid] = {"verdict": r.get("verdict") or "",
                                "note": r.get("note") or ""}
        sid = str(body.get("id") or "").zfill(3)
        if not sid:
            raise ValueError("没有镜号")
        row = cur.get(sid, {"verdict": "", "note": ""})
        if "verdict" in body:
            row["verdict"] = str(body.get("verdict") or "")
        if "note" in body:
            row["note"] = str(body.get("note") or "")
        if row["verdict"] or row["note"]:
            cur[sid] = row
        else:
            cur.pop(sid, None)
        rows = [{"shot": k, "verdict": cur[k]["verdict"], "note": cur[k]["note"]}
                for k in sorted(cur)]
        _backup(st, p)
        _jwrite(p, {"unit": uid,
                    "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "rows": rows})
        st.invalidate(["review", "shots"])
        return self._json(200, {"ok": True, "rows": len(rows),
                                "mtime": p.stat().st_mtime_ns})

    # ---------- 资产 ----------
    def _asset(self, aid):
        """按资产 ID 找目录。ID 来自 manifest，缺 manifest 时用目录名前缀兜底。"""
        st = self.state
        aid = str(aid or "").strip()
        if not aid:
            raise ValueError("没有资产 ID")
        base = st.root / "assets"
        if base.is_dir():
            for tkey in ASSET_TYPES:
                folder = base / tkey
                if not folder.is_dir():
                    continue
                for d in sorted(folder.iterdir()):
                    if not d.is_dir() or d.name.startswith((".", "_")):
                        continue
                    m = _jload(d / "manifest.json", {}) or {}
                    x = str(m.get("id") or "").strip()
                    if not x:
                        hit = re.match(r"^([A-Z]{2}-[A-Z0-9]+-\d+)", d.name)
                        x = hit.group(1) if hit else ""
                    if x == aid:
                        return d.resolve(), d.relative_to(st.root).as_posix(), m
        raise ValueError("找不到资产：%s" % aid)

    def _safe_key(self, key):
        key = str(key or "").strip()
        if key in (".", "..") or not VARIANT_KEY.match(key):
            raise ValueError("变体 ID 不能含路径分隔符或控制字符（最多 32 位）")
        return key

    def _new_key(self, adir, label):
        slug = re.sub(r"[^a-z0-9]+", "-", str(label or "").lower()).strip("-")[:24]
        vroot = adir / "variants"
        if slug and not (vroot / slug).exists():
            return slug
        n = 1
        while (vroot / ("v%02d" % n)).exists():
            n += 1
        return "v%02d" % n

    def _write_variant(self, vdir, meta):
        vdir.mkdir(parents=True, exist_ok=True)
        p = vdir / "variant.json"
        _backup(self.state, p)
        meta["id"] = vdir.name
        meta["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _jwrite(p, meta)

    def _clear_primary(self, adir, keep):
        vroot = adir / "variants"
        if not vroot.is_dir():
            return
        for d in vroot.iterdir():
            if not d.is_dir() or d.name == keep:
                continue
            p = d / "variant.json"
            m = _jload(p, None)
            if isinstance(m, dict) and m.get("primary"):
                _backup(self.state, p)
                m["primary"] = False
                _jwrite(p, m)

    def _project_file(self, rel):
        root = self.state.root.resolve()
        p = (root / str(rel or "").lstrip("/")).resolve()
        try:
            ok = p.is_relative_to(root)
        except AttributeError:
            ok = str(p).startswith(str(root) + os.sep)
        if not ok or not p.is_file():
            raise ValueError("文件不存在：%s" % rel)
        if p.suffix.lower() not in snap_mod.IMAGE_EXT:
            raise ValueError("只收图片文件")
        return p

    def _asset_upload(self, body):
        st = self.state
        adir, rel, _m = self._asset(body.get("asset"))
        label = str(body.get("label") or "").strip()
        key = str(body.get("variant") or "").strip()
        key = self._safe_key(key) if key else self._new_key(adir, label or "v")
        data = str(body.get("data") or "")
        if data.strip().lower().startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ValueError("图片数据不合法")
        if not raw:
            raise ValueError("图片是空的")
        if len(raw) > MAX_UPLOAD:
            raise ValueError("图片超过 16MB")
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif raw[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = ".webp"
        else:
            raise ValueError("只收 PNG / JPG / WebP")

        vdir = adir / "variants" / key
        vdir.mkdir(parents=True, exist_ok=True)
        for old in vdir.glob("image.*"):
            if old.suffix.lower() in snap_mod.IMAGE_EXT:
                _backup(st, old)
                old.unlink()
        image = vdir / ("image" + ext)
        tmp = image.with_suffix(image.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(image)

        meta = _jload(vdir / "variant.json", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("label", label or key)
        if label:
            meta["label"] = label
        meta.setdefault("status", "草稿")
        meta["image"] = image.name
        if body.get("primary"):
            meta["primary"] = True
            self._clear_primary(adir, key)
        elif not any(v.get("primary") for v in self._all_variant_meta(adir)):
            meta["primary"] = True
        self._write_variant(vdir, meta)
        st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": True, "asset": body.get("asset"), "variant": key,
            "label": meta.get("label"), "primary": bool(meta.get("primary")),
            "path": "%s/variants/%s/%s" % (rel, key, image.name),
            "mtime": (vdir / "variant.json").stat().st_mtime_ns,
        })

    def _all_variant_meta(self, adir):
        out = []
        vroot = adir / "variants"
        if vroot.is_dir():
            for d in vroot.iterdir():
                if d.is_dir() and not d.name.startswith((".", "_")):
                    m = _jload(d / "variant.json", None)
                    if isinstance(m, dict):
                        out.append(m)
        return out

    def _asset_variant(self, body):
        st = self.state
        adir, rel, _m = self._asset(body.get("asset"))
        rawkey = str(body.get("variant") or "").strip()
        key = self._safe_key(rawkey) if rawkey else \
            self._new_key(adir, body.get("label") or "v")
        vdir = adir / "variants" / key
        action = str(body.get("action") or "save")
        if action == "primary":
            if not vdir.is_dir():
                raise ValueError("这个变体还没有固化，先收为变体")
            meta = _jload(vdir / "variant.json", {}) or {}
            meta["primary"] = True
            self._write_variant(vdir, meta)
            self._clear_primary(adir, key)
            st.invalidate(["assets", "shots"])
            return self._json(200, {"ok": True, "variant": key, "primary": True})
        if action == "delete":
            if not vdir.is_dir():
                raise ValueError("变体不存在")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dst = st.root / ".workbuddy" / "trash" / stamp / rel / "variants" / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(vdir), str(dst))
            st.invalidate(["assets", "shots"])
            return self._json(200, {"ok": True, "deleted": key,
                                    "trash": dst.relative_to(st.root).as_posix()})
        meta = _jload(vdir / "variant.json", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        for f in ("label", "status", "note", "prompt"):
            if f in body:
                meta[f] = str(body.get(f) or "")
        if body.get("primary"):
            meta["primary"] = True
            self._clear_primary(adir, key)
        self._write_variant(vdir, meta)
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "variant": key,
                                "label": meta.get("label") or key})

    def _asset_promote(self, body):
        """把已有图片（refs 散图 / 生成单元产物 / 锚点图）收成变体。"""
        st = self.state
        adir, rel, _m = self._asset(body.get("asset"))
        src = self._project_file(body.get("from") or body.get("path"))
        label = str(body.get("label") or src.stem).strip()
        rawkey = str(body.get("key") or "").strip()
        try:
            key = self._safe_key(rawkey) if rawkey else self._new_key(adir, label or src.stem)
        except ValueError:
            key = self._new_key(adir, label or src.stem)
        vdir = adir / "variants" / key
        vdir.mkdir(parents=True, exist_ok=True)
        ext = src.suffix.lower()
        for old in vdir.glob("image.*"):
            if old.suffix.lower() in snap_mod.IMAGE_EXT:
                _backup(st, old)
                old.unlink()
        image = vdir / ("image" + ext)
        shutil.copy2(src, image)
        meta = _jload(vdir / "variant.json", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        meta.update({
            "label": label,
            "status": str(body.get("status") or "参考"),
            "image": image.name,
            "origin": src.relative_to(st.root.resolve()).as_posix(),
        })
        if body.get("canvas") or body.get("unit"):
            meta["canvas"] = str(body.get("canvas") or "")
            meta["unit"] = str(body.get("unit") or "")
        if body.get("primary") or not any(
                v.get("primary") for v in self._all_variant_meta(adir)):
            meta["primary"] = True
            self._clear_primary(adir, key)
        self._write_variant(vdir, meta)
        st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": True, "asset": body.get("asset"), "variant": key,
            "label": label, "primary": bool(meta.get("primary")),
            "path": "%s/variants/%s/%s" % (rel, key, image.name),
        })

    def _asset_new(self, body):
        st = self.state
        tkey = str(body.get("type") or "").strip()
        if tkey not in ASSET_TYPES:
            raise ValueError("类型只能是：%s" % "、".join(ASSET_TYPES))
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("先给资产起个名字")
        name = re.sub(r'[\\/:*?"<>|]', "", name).strip().rstrip(".")[:40]
        if not name:
            raise ValueError("名字里没有可用字符")
        code = re.sub(r"[^A-Za-z0-9]", "", str(body.get("code") or "")).upper()
        if len(code) < 2:
            raise ValueError("短码至少两位（例：JX / ARC）")
        if code == "XX":
            raise ValueError("短码不能用 XX 占位")
        code = code[:8]
        base = st.root / "assets" / tkey
        used = []
        if base.is_dir():
            for d in base.iterdir():
                if not d.is_dir() or d.name.startswith((".", "_")):
                    continue
                m = _jload(d / "manifest.json", {}) or {}
                x = str(m.get("id") or "")
                hit = re.match(r"^([A-Z]{2}-[A-Z0-9]+-\d+)", d.name)
                x = x or (hit.group(1) if hit else "")
                if x:
                    used.append(x)
        prefix = ASSET_TYPES[tkey]
        n = 1
        aid = "%s-%s-%02d" % (prefix, code, n)
        while aid in used:
            n += 1
            aid = "%s-%s-%02d" % (prefix, code, n)
        adir = base / ("%s-%s" % (aid, name))
        if adir.exists():
            raise ValueError("目录已存在：assets/%s/%s" % (tkey, adir.name))
        adir.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%d %H:%M")
        manifest = {
            "id": aid, "name": name, "type": tkey, "status": "草稿",
            "level": "L1", "used_by": [], "files": {}, "notes": "",
            "created": now,
        }
        _jwrite(adir / "manifest.json", manifest)
        subs = ["refs", "prompts", "build", "variants"]
        if tkey == "characters":
            subs += ["design", "voice"]
        if tkey == "scenes":
            subs += ["layout", "lighting"]
        for s in subs:
            d = adir / s
            d.mkdir(parents=True, exist_ok=True)
            if not any(d.iterdir()):
                (d / ".gitkeep").write_text("", encoding="utf-8")
        _jwrite(adir / "build" / "canvases.json", {
            "_generated": True, "_source": "build/*/canvas.json",
            "_note": "生成物，不要手改。", "asset": aid, "canvases": [],
        })
        if tkey == "scenes":
            tpl = st.root / "assets" / "scenes" / "_TEMPLATE" / "space.json"
            sp = _jload(tpl, None)
            if isinstance(sp, dict):
                sp["id"] = aid
                sp["name"] = name
                _jwrite(adir / "space.json", sp)
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "id": aid,
                                "dir": adir.relative_to(st.root).as_posix()})

    def _asset_manifest(self, body):
        st = self.state
        adir, rel, m = self._asset(body.get("asset"))
        if "status" in body:
            m["status"] = str(body.get("status") or "").strip()[:60]
        if "notes" in body:
            m["notes"] = str(body.get("notes") or "")
        if "name" in body:
            name = str(body.get("name") or "").strip()
            if name:
                m["name"] = name[:60]
        p = adir / "manifest.json"
        _backup(st, p)
        _jwrite(p, m)
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "asset": m.get("id"),
                                "status": m.get("status") or "",
                                "mtime": p.stat().st_mtime_ns})

    # ---------- 资产画布：单独布线 / 人去生成 / 同步结果 ----------

    def _asset_canvas(self, aid, canvas="C01"):
        adir, rel, m = self._asset(aid)
        canvas = re.sub(r"[^A-Za-z0-9_-]", "", str(canvas or "C01")) or "C01"
        if not canvas.startswith("C"):
            canvas = "C01"
        return adir, adir / "build" / canvas, rel, m, canvas

    def _asset_unit_dirs(self, cdir):
        out = []
        if cdir.is_dir():
            for d in sorted(cdir.iterdir()):
                if d.is_dir() and re.match(r"^U\d+", d.name):
                    out.append(d)
        return out

    def _asset_default_unit(self, type_key):
        return {
            "characters": ("定妆正面", "3:4"),
            "scenes": ("母图", "16:9"),
            "props": ("正面", "1:1"),
            "brand": ("主图", "1:1"),
            "style": ("参考图", "16:9"),
        }.get(type_key, ("主图", "16:9"))

    @staticmethod
    def _prompt_sections(text):
        """把 prompts.md 按 ## 标题拆段；只给建画布时挑默认单元用。"""
        rows, cur = [], None
        for line in str(text or "").splitlines():
            m = re.match(r"^\s*#{2,4}\s+(.+?)\s*$", line)
            if m:
                if cur:
                    rows.append(cur)
                cur = {"title": m.group(1), "lines": []}
            elif cur is not None:
                cur["lines"].append(line)
        if cur:
            rows.append(cur)
        return rows

    def _asset_seed_unit(self, adir, unit_name=""):
        """从 prompts.md 里找和默认单元最匹配的一节，返回 (prompt, negative)。

        以前直接抽第一段，常常把「只写这一张长什么样」「母图出来之后……」这类
        说明抽进 unit.json，再原样发给 LibTV，导致生成图完全跑偏。
        """
        text = ""
        for p in (adir / "prompts.md", adir / "prompts" / "prompts.md"):
            try:
                if p.is_file():
                    text = p.read_text(encoding="utf-8")
                    break
            except Exception:
                continue
        if not text:
            return "", ""

        name = str(unit_name or "").strip()
        aliases = []
        if any(k in name for k in ("母图", "主图", "主视觉")):
            aliases = ["母图", "主图", "主视觉", "master", "key visual"]
        elif any(k in name for k in ("定妆", "正面", "角色")):
            aliases = ["定妆", "正面", "三视图", "角色", "character"]
        elif "参考" in name:
            aliases = ["母图", "参考", "reference", "master"]
        else:
            aliases = [name] if name else []

        visual = ("画面", "背景", "主体", "人物", "服装", "材质", "构图",
                  "镜头", "景别", "光线", "光", "颜色", "实心", "居中",
                  "留白", "正面", "侧面", "特写", "全景", "中景", "视角",
                  "纹理", "质感", "正方形", "圆角", "阴影", "高光")
        best, best_score = None, -1.0
        for sec in self._prompt_sections(text):
            title = str(sec.get("title") or "")
            body = "\n".join(sec.get("lines") or [])
            score = sum(2.0 for a in aliases if a and a.lower() in title.lower())
            score += sum(0.25 for k in visual if k in body)
            if name and name in title:
                score += 5.0
            if score > best_score:
                best, best_score = sec, score
        if not best or best_score <= 0:
            # 没有小节标题：退回原来的摘录，但绝不把制作说明当提示词。
            fallback = snap_mod._prompt_excerpt(text, 400)
            return ("", "") if _meta_prompt_reason(fallback) else (fallback, "")

        prompt_lines, negative = [], ""
        for line in best.get("lines") or []:
            s = line.strip()
            if not s or s.startswith((">", "|", "---", "```")):
                continue
            m = re.match(r"^\*{0,2}负面\*{0,2}\s*[：:]\s*(.*)$", s)
            if m:
                negative = m.group(1).strip()
                break
            prompt_lines.append(s)
        prompt = "\n".join(prompt_lines).strip()
        prompt = re.sub(r"\*\*|`", "", prompt).strip()
        if _meta_prompt_reason(prompt):
            return "", negative
        return prompt[:1200], negative[:600]

    def _asset_seed_prompt(self, adir):
        """兼容旧调用：只要提示词正文。"""
        return self._asset_seed_unit(adir)[0]

    def _asset_ensure_canvas_data(self, aid, canvas="C01"):
        st = self.state
        adir, cdir, rel, m, canvas = self._asset_canvas(aid, canvas)
        tkey = adir.parent.name
        type_label = {"characters": "角色", "scenes": "场景", "props": "道具",
                      "brand": "品牌", "style": "风格"}.get(tkey, tkey)
        name = str(m.get("name") or aid)
        cdir.mkdir(parents=True, exist_ok=True)
        cpath = cdir / "canvas.json"
        cm = _jload(cpath, {}) or {}
        if not isinstance(cm, dict):
            cm = {}
        if not cm:
            cm = {
                "id": canvas, "title": "%s · 参考生成" % name,
                "purpose": "一个画布生成这个资产的全部参考单元",
                "model": "z-image", "project_id": "", "status": "未连线",
                "units": [], "_note": "canvas 只管画布与模型；提示词在 unit.json。",
            }
        units = [d.name for d in self._asset_unit_dirs(cdir)]
        if not units:
            uname, usize = self._asset_default_unit(tkey)
            seed, negative = self._asset_seed_unit(adir, uname)
            udir = cdir / "U01"
            (udir / "refs").mkdir(parents=True, exist_ok=True)
            (udir / "renders").mkdir(parents=True, exist_ok=True)
            _jwrite(udir / "unit.json", {
                "id": "U01", "name": uname, "output": "refs/%s.png" % uname,
                "size": usize, "prompt": seed, "negative": negative, "model": "",
                "refs": [], "status": "待生成" if seed else "未写",
                "libtv": {"node_id": "", "wired": False},
                "_note": "提示词的唯一来源；工作台布线时注入 LibTV。",
            })
            units = ["U01"]
        cm["units"] = units
        cm.setdefault("model", "z-image")
        lpath = cdir / "libtv.json"
        lib = _jload(lpath, {}) or {}
        if not isinstance(lib, dict):
            lib = {}
        lib["asset"] = aid
        lib["canvas"] = canvas
        lib.setdefault("model", cm.get("model") or "z-image")
        refs = [r for r in (lib.get("refs") or []) if isinstance(r, dict)]
        nodes = [n for n in (lib.get("nodes") or []) if isinstance(n, dict)]
        for i, uid in enumerate(units, 1):
            if not any(n.get("unit") == uid for n in nodes):
                nodes.append({"id": "N%02d" % i, "unit": uid,
                              "node_id": "", "refs": []})
        lib["nodes"] = nodes

        self._need_libtv()
        client = libtv_mcp.LibTV(st.root)
        pid = str(lib.get("project_id") or cm.get("project_id") or "")
        space = int(lib.get("project_space_id") or lib.get("folder_id") or 0)
        if not pid:
            r = client.call("create_project", {
                "name": ("%s %s · %s 生成" % (aid, name, canvas))[:180],
                "description": "%s · %s · %s · 资产参考生成" % (
                    st.unit, type_label, name),
                "idempotencyKey": libtv_mcp.safe_key(
                    "wb-asset-%s-%s-project-v1" % (aid, canvas)),
                "openInBrowser": False,
            }, timeout=60)
            pid, space = libtv_mcp.project_meta(r)
            if not pid:
                raise RuntimeError("create_project 没返回 projectId")
            unit_lib = _jload(st.root / "units" / st.unit / "libtv.json", {}) or {}
            root_folder = int(unit_lib.get("folder_id")
                              or unit_lib.get("project_space_id") or 0)
            if root_folder:
                try:
                    client.call("update_project", {
                        "projectId": pid, "folderId": root_folder,
                        "idempotencyKey": libtv_mcp.safe_key(
                            "wb-asset-%s-%s-move-v1" % (aid, canvas)),
                    }, timeout=30)
                    space = root_folder
                except Exception:
                    pass
            lib["project_id"] = pid
            lib["project_space_id"] = space
            lib["folder_id"] = space
            lib["status"] = "已建"
        cm["project_id"] = pid
        cm["status"] = "已建" if pid else cm.get("status") or "未连线"
        pj = _jload(st.root / "project.json", {}) or {}
        cfg = snap_mod._libtv_cfg(pj)
        lib["url"] = snap_mod._fmt_url(
            cfg.get("project_url"), pid, "", cfg.get("web_base") or "")
        gid = str(lib.get("group_id") or "")
        gname = ("%s %s 生成" % (canvas, name))[:180]
        if not gid:
            node_ids = [str(n.get("node_id") or "") for n in nodes
                        if n.get("node_id")]
            r = client.call("manage_group", {
                "action": "create", "projectId": pid, "name": gname,
                "nodes": node_ids,
                "idempotencyKey": libtv_mcp.safe_key(
                    "wb-asset-%s-%s-group-v1" % (aid, canvas)),
            }, timeout=30)
            gid = libtv_mcp.group_meta(r)
            if not gid:
                raise RuntimeError("manage_group 没返回 groupId")
            lib["group_id"] = gid
        lib["group_name"] = self._group_name(client, pid, gid, gname)
        lib["refs"] = refs
        lib["nodes"] = nodes
        _backup(st, cpath)
        _jwrite(cpath, cm)
        _backup(st, lpath)
        _jwrite(lpath, lib)
        st.invalidate(["assets", "shots"])
        return {"ok": True, "asset": aid, "canvas": canvas,
                "project_id": pid, "group_id": gid,
                "group_name": lib["group_name"], "units": units,
                "adir": adir, "cdir": cdir, "cm": cm, "lib": lib,
                "client": client, "name": name, "type": tkey}

    def _asset_canvas_ensure(self, body):
        data = self._asset_ensure_canvas_data(
            body.get("asset"), body.get("canvas") or "C01")
        return self._json(200, {k: data[k] for k in (
            "ok", "asset", "canvas", "project_id", "group_id",
            "group_name", "units")})

    def _asset_canvas_new(self, body):
        """只建本地生成画布，不碰 LibTV；布线时再建远程项目。"""
        st = self.state
        adir, cdir, rel, m, canvas = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        name = str(m.get("name") or m.get("id") or "")
        tkey = adir.parent.name
        units = [d.name for d in self._asset_unit_dirs(cdir)]
        if not units:
            uname, usize = self._asset_default_unit(tkey)
            seed, negative = self._asset_seed_unit(adir, uname)
            udir = cdir / "U01"
            (udir / "refs").mkdir(parents=True, exist_ok=True)
            (udir / "renders").mkdir(parents=True, exist_ok=True)
            _jwrite(udir / "unit.json", {
                "id": "U01", "name": uname, "output": "refs/%s.png" % uname,
                "size": usize, "prompt": seed, "negative": negative, "model": "",
                "refs": [], "status": "待生成" if seed else "未写",
                "libtv": {"node_id": "", "wired": False},
                "_note": "提示词的唯一来源；工作台布线时注入 LibTV。",
            })
            units = ["U01"]
        cpath = cdir / "canvas.json"
        cm = _jload(cpath, {}) or {}
        if not isinstance(cm, dict) or not cm:
            cm = {
                "id": canvas, "title": "%s · 参考生成" % name,
                "purpose": "一个画布生成这个资产的全部参考单元",
                "model": "z-image", "project_id": "", "status": "未连线",
                "units": units, "_note": "canvas 只管画布与模型；提示词在 unit.json。",
            }
        cm["units"] = units
        _backup(st, cpath)
        _jwrite(cpath, cm)
        idxp = adir / "build" / "canvases.json"
        idx = _jload(idxp, {}) or {}
        if not isinstance(idx, dict):
            idx = {}
        rows = [x for x in (idx.get("canvases") or []) if isinstance(x, dict)]
        if not any(str(x.get("id") or "") == canvas for x in rows):
            rows.append({"id": canvas, "title": cm.get("title") or canvas,
                         "status": cm.get("status") or "未连线"})
        idx.update({"_generated": True, "_source": "build/*/canvas.json",
                    "_note": "生成物，不要手改。", "asset": m.get("id") or "",
                    "canvases": rows})
        _backup(st, idxp)
        _jwrite(idxp, idx)
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "asset": m.get("id") or "",
                                "canvas": canvas, "units": units,
                                "dir": cdir.relative_to(st.root).as_posix()})

    def _asset_unit_prompt(self, body):
        st = self.state
        adir, cdir, rel, m, canvas = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        uid = str(body.get("unit") or "").strip()
        if not re.match(r"^U\d+$", uid):
            raise ValueError("没有生成单元号")
        up = cdir / uid / "unit.json"
        if not up.exists():
            raise ValueError("找不到生成单元：%s" % uid)
        um = _jload(up, {}) or {}
        if not isinstance(um, dict):
            um = {}
        if "prompt" in body:
            um["prompt"] = str(body.get("prompt") or "")
        if "negative" in body:
            um["negative"] = str(body.get("negative") or "")
        if "model" in body:
            um["model"] = str(body.get("model") or "")
        if "style" in body:
            style = body.get("style")
            if not isinstance(style, dict):
                style = {"mode": str(style or "libtv")}
            mode = str(style.get("mode") or "libtv").strip()
            if mode not in ("libtv", "asset", "none", "custom"):
                mode = "libtv"
            st_cfg = {"mode": mode}
            if mode == "asset":
                sid = str(style.get("asset") or "").strip()
                if sid:
                    try:
                        sadir, _srel, _sm = self._asset(sid)
                        if sadir.parent.name == "style":
                            st_cfg["asset"] = sid
                    except Exception:
                        pass
                if "asset" not in st_cfg:
                    st_cfg["mode"] = "libtv"
            elif mode == "custom":
                st_cfg["text"] = str(style.get("text") or "")[:500]
            um["style"] = st_cfg
        if "palette" in body:
            rows = []
            for x in (body.get("palette") or [])[:8]:
                if isinstance(x, str):
                    x = {"hex": x.strip()}
                if not isinstance(x, dict):
                    continue
                hexv = str(x.get("hex") or "").strip()
                if hexv and not re.match(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                                         hexv):
                    continue
                name = str(x.get("name") or "").strip()[:40]
                usage = str(x.get("usage") or "").strip()[:80]
                if not hexv and not name:
                    continue
                rows.append({"name": name, "hex": hexv, "usage": usage})
            um["palette"] = rows
        if um.get("prompt"):
            um["status"] = "待生成"
        _backup(st, up)
        _jwrite(up, um)
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "asset": body.get("asset"),
                                "canvas": canvas, "unit": uid,
                                "status": um.get("status") or ""})

    def _asset_ref_file(self, root, base, value):
        s = str(value or "").strip().replace("\\", "/")
        if not s:
            return ""
        for c in (base / s, root / s):
            try:
                c = c.resolve()
                if c.is_file() and c.suffix.lower() in snap_mod.IMAGE_EXT:
                    return c.relative_to(root.resolve()).as_posix()
            except Exception:
                continue
        return ""

    def _asset_push(self, body):
        """只把本地 unit.json 的 prompt 推到这个单元已有的 LibTV 节点。

        和「布线」不同：不动 model / params / 参考图 / 风格设置 / 画布结构。
        用户在 LibTV 里选的**设计风格**属于用户权限，这里必须保留。
        """
        st = self.state
        aid = str(body.get("asset") or "").strip()
        canvas = str(body.get("canvas") or "C01").strip()
        uid = str(body.get("unit") or "").strip()
        if not aid or not uid:
            raise ValueError("要指定资产和生成单元")
        if not body.get("confirm"):
            raise ValueError("更新到 LibTV 需要 confirm=true")
        adir, cdir, _rel, _m, canvas = self._asset_canvas(aid, canvas)
        up = cdir / uid / "unit.json"
        if not up.is_file():
            raise ValueError("找不到生成单元：%s/%s" % (canvas, uid))
        um = _jload(up, {}) or {}
        if not isinstance(um, dict):
            um = {}
        prompt = str(um.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("这个生成单元还没有提示词")
        reason = _meta_prompt_reason(prompt)
        if reason:
            raise ValueError("提示词不是出图描述（%s）。先改提示词再推。" % reason)
        node_id = str((um.get("libtv") or {}).get("node_id") or "")
        if not node_id:
            raise ValueError("这个单元还没布线：先点「仅布线」，再用「更新到 LibTV」")
        lib = _jload(cdir / "libtv.json", {}) or {}
        if not isinstance(lib, dict):
            lib = {}
        pid = str(lib.get("project_id") or "")
        if not pid:
            raise ValueError("这个画布还没有 LibTV 项目，先布线")
        self._need_libtv()
        sig = snap_mod.asset_prompt_hash(
            prompt, um.get("negative"), um.get("size"))
        key = libtv_mcp.safe_key("wb-asset-push-%s-%s-%s-%s" % (
            aid, canvas, uid, sig[-16:]))
        client = libtv_mcp.LibTV(st.root)
        client.call("update_node", {
            "node": node_id, "projectId": pid, "idempotencyKey": key,
            "prompt": prompt, "run": False,
        }, timeout=120)
        um.setdefault("libtv", {})
        um["libtv"].update({
            "prompt_hash": sig,
            "pushed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if isinstance(um["libtv"].get("state"), dict):
            um["libtv"]["state"]["prompt"] = prompt
        _backup(st, up)
        _jwrite(up, um)
        lib.setdefault("nodes", [])
        hit = next((n for n in lib["nodes"] if n.get("unit") == uid), None)
        if hit is not None:
            hit.update({"prompt": prompt, "prompt_hash": sig, "node_id": node_id})
            _backup(st, cdir / "libtv.json")
            _jwrite(cdir / "libtv.json", lib)
        st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": True, "asset": aid, "canvas": canvas, "unit": uid,
            "node_id": node_id, "prompt_hash": sig,
            "message": "已更新到 LibTV；只改了提示词，风格 / 参考图 / 参数保持原样。"
                       "已有输出不会自动重跑，需要时在 LibTV 重新生成。",
        })

    @staticmethod
    def _libtv_canvas_structure(pr):
        """把 read_project(view=nodes) 压成画布结构：节点坐标 + 连线。

        组节点的子节点坐标是相对组的，这里换算成画布绝对坐标，
        前端才能像 LibTV 画布一样摆。
        """
        def num(v, d=0.0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return d

        raw = pr.get("nodes") if isinstance(pr, dict) else None
        raw = raw if isinstance(raw, list) else []
        groups = {}
        for n in raw:
            if not isinstance(n, dict) or n.get("type") != "group":
                continue
            d = n.get("data") if isinstance(n.get("data"), dict) else {}
            for cid in (d.get("childNodeIds") or []):
                groups[str(cid)] = n
        out = []
        for n in raw:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id") or n.get("nodeId") or "")
            d = n.get("data") if isinstance(n.get("data"), dict) else {}
            info = d.get("taskInfo") if isinstance(d.get("taskInfo"), dict) else {}
            params = d.get("params") if isinstance(d.get("params"), dict) else {}
            urls = d.get("url")
            if isinstance(urls, str):
                urls = [urls]
            urls = [u for u in (urls or []) if isinstance(u, str) and u]
            loading = bool(info.get("loading"))
            failed = str(info.get("failedReason") or "")
            if n.get("type") == "group":
                status = "group"
            elif loading:
                status = "generating"
            elif failed:
                status = "failed"
            elif urls:
                status = "done"
            else:
                status = "idle"
            pos = n.get("position") if isinstance(n.get("position"), dict) else {}
            x, y = num(pos.get("x")), num(pos.get("y"))
            parent = groups.get(nid)
            if parent:
                pp = parent.get("position") if isinstance(
                    parent.get("position"), dict) else {}
                x += num(pp.get("x"))
                y += num(pp.get("y"))
            prompt = ""
            if isinstance(params.get("prompt"), str):
                prompt = params["prompt"]
            elif isinstance(d.get("prompt"), str):
                prompt = d["prompt"]
            out.append({
                "id": nid,
                "type": str(n.get("type") or d.get("type") or ""),
                "name": str(d.get("name") or n.get("name") or ""),
                "x": x, "y": y,
                "w": num(n.get("width")) or (390 if n.get("type") == "group" else 170),
                "h": num(n.get("height")) or (446 if n.get("type") == "group" else 92),
                "status": status,
                "group": str(
                    ((parent or {}).get("data") or {}).get("name") or "")
                if parent else "",
                "style_name": str(d.get("styleName") or ""),
                "prompt": prompt,
                "has_url": bool(urls),
            })
        edges = []
        for e in (pr.get("edges") or []):
            if not isinstance(e, dict):
                continue
            s = str(e.get("source") or "")
            t = str(e.get("target") or "")
            if s and t:
                edges.append({"id": str(e.get("id") or ""),
                              "source": s, "target": t})
        counts = {
            "nodes": len(out),
            "edges": len(edges),
            "groups": len([n for n in out if n["type"] == "group"]),
        }
        return out, edges, counts

    @staticmethod
    def _same_libtv_state(a, b):
        """两份节点快照是不是同一份（忽略读取时间，避免每 30 秒写盘）。"""
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        for k in ("status", "prompt", "model", "params", "style", "urls",
                  "error", "loading"):
            if (a.get(k) or None) != (b.get(k) or None):
                return False
        return True

    def _asset_libtv_state(self, body):
        """读 LibTV 上每个生成单元的最新状态和方案，缓存进 unit.json。

        只读、不生成。本地提示词和 LibTV 上不一致时，返回里带
        remote_changed=true，由用户决定要不要「采用 LibTV 版本」。
        """
        st = self.state
        _adir, cdir, _rel, _m, canvas = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        cpath = cdir / "libtv.json"
        lib = _jload(cpath, {}) or {}
        pid = str(lib.get("project_id") or "")
        if not pid:
            raise ValueError("这个资产画布还没连线 LibTV：先布线")
        units = [d.name for d in self._asset_unit_dirs(cdir)]
        only = str(body.get("unit") or "").strip()
        if only:
            if only not in units:
                raise ValueError("找不到生成单元：%s" % only)
            units = [only]
        self._need_libtv()
        client = libtv_mcp.LibTV(st.root)
        rows, errors, changed = [], [], False
        for uid in units:
            up = cdir / uid / "unit.json"
            um = _jload(up, {}) or {}
            if not isinstance(um, dict):
                um = {}
            node_id = str((um.get("libtv") or {}).get("node_id") or "")
            if not node_id:
                rows.append({"unit": uid, "status": "unwired",
                             "message": "还没布线"})
                continue
            try:
                r = client.call("read_node", {
                    "projectId": pid, "node": node_id}, timeout=30)
            except Exception as e:
                errors.append({"unit": uid, "error": str(e)[:200]})
                rows.append({"unit": uid, "node_id": node_id,
                             "status": "missing",
                             "message": "LibTV 节点读不到（可能已删）"})
                continue
            state = self._libtv_node_state(r.get("node") or r)
            prev = (um.get("libtv") or {}).get("state")
            if not self._same_libtv_state(prev, state):
                um.setdefault("libtv", {})["state"] = state
                _backup(st, up)
                _jwrite(up, um)
                changed = True
            local = str(um.get("prompt") or "").strip()
            remote = str(state.get("prompt") or "").strip()
            rows.append({
                "unit": uid, "name": um.get("name") or uid,
                "node_id": node_id, "status": state["status"],
                "prompt": remote, "local_prompt": local,
                "remote_changed": bool(remote and remote != local),
                "model": state["model"], "params": state["params"],
                "style": state["style"], "urls": state["urls"],
                "at": state["at"],
            })
        # 画布结构：节点的真实坐标 + 连线，前端照着 LibTV 画布摆。
        canvas_nodes, edges, counts = [], [], {}
        try:
            pr = client.call("read_project", {
                "projectId": pid, "view": "nodes"}, timeout=30)
            canvas_nodes, edges, counts = self._libtv_canvas_structure(pr)
        except Exception as e:
            errors.append({"unit": "画布",
                           "error": "读画布结构失败：" + str(e)[:160]})
        if canvas_nodes:
            sig = {"nodes": canvas_nodes, "edges": edges}
            if lib.get("remote_canvas") != sig:
                lib["remote_canvas"] = sig
                lib["remote_canvas_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _backup(st, cpath)
                _jwrite(cpath, lib)
                changed = True

        if changed:
            st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": not errors, "asset": body.get("asset"), "canvas": canvas,
            "counts": counts, "canvas_nodes": canvas_nodes, "edges": edges,
            "rows": rows, "errors": errors,
            "message": "已读回 LibTV 最新状态" if not errors
                       else "部分单元没读到：%s" % errors[0]["error"],
        })

    def _asset_libtv_pull(self, body):
        """把 LibTV 上改过的提示词/参数写回本地 unit.json（方案同步下来）。"""
        st = self.state
        _adir, cdir, _rel, _m, canvas = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        uid = str(body.get("unit") or "").strip()
        if not uid:
            raise ValueError("要指定生成单元")
        if not body.get("confirm"):
            raise ValueError("采用 LibTV 方案会覆盖本地 unit.json，需要 confirm=true")
        up = cdir / uid / "unit.json"
        if not up.is_file():
            raise ValueError("找不到生成单元：%s" % uid)
        um = _jload(up, {}) or {}
        if not isinstance(um, dict):
            um = {}
        lib = _jload(cdir / "libtv.json", {}) or {}
        pid = str(lib.get("project_id") or "")
        node_id = str((um.get("libtv") or {}).get("node_id") or "")
        if not pid or not node_id:
            raise ValueError("这个单元还没布线：先布线再到 LibTV 改")
        self._need_libtv()
        client = libtv_mcp.LibTV(st.root)
        r = client.call("read_node", {"projectId": pid, "node": node_id},
                        timeout=30)
        state = self._libtv_node_state(r.get("node") or r)
        remote = str(state.get("prompt") or "").strip()
        if not remote:
            raise ValueError("LibTV 这个节点读不到提示词，没法拉回")
        um["prompt"] = remote
        um.setdefault("libtv", {})["state"] = state
        if body.get("params"):
            # 只拉回能对应上的创作参数；风格仍是用户在 LibTV 里的选择。
            p = state.get("params") or {}
            if p.get("ratio"):
                um["size"] = str(p["ratio"])
            if state.get("model"):
                um["model"] = str(state["model"])
        um["libtv"]["prompt_hash"] = snap_mod.asset_prompt_hash(
            um.get("prompt"), um.get("negative"), um.get("size"))
        um["libtv"]["pulled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _backup(st, up)
        _jwrite(up, um)
        lib.setdefault("nodes", [])
        hit = next((x for x in lib["nodes"]
                    if isinstance(x, dict) and x.get("unit") == uid), None)
        if hit is not None:
            hit.update({"prompt": um["prompt"], "prompt_hash":
                        um["libtv"]["prompt_hash"], "node_id": node_id})
            _backup(st, cdir / "libtv.json")
            _jwrite(cdir / "libtv.json", lib)
        st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": True, "asset": body.get("asset"), "canvas": canvas,
            "unit": uid, "prompt": um["prompt"],
            "model": um.get("model") or "", "size": um.get("size") or "",
            "message": "已采用 LibTV 的提示词 / 参数；要换图还得在 LibTV 重新生成",
        })

    def _asset_wire(self, body):
        st = self.state
        dry = bool(body.get("dry_run", True))
        # run=true：布线后让 LibTV 直接开生成（会消耗积分）。
        # 生成只能由用户在界面上点：必须同时带 confirm + confirm_generate。
        run_now = bool(body.get("run"))
        if run_now and not dry and not body.get("confirm"):
            raise ValueError("触发生成需要 confirm=true")
        if run_now and not dry and not body.get("confirm_generate"):
            raise ValueError(
                "生成会消耗积分，只能由用户在界面上确认（缺 confirm_generate=true）")
        # 先检查提示词，避免「建了组里面没东西」
        _adir0, cdir0, _rel0, _m0, _c0 = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        uids0 = [d.name for d in self._asset_unit_dirs(cdir0)]
        only0 = str(body.get("unit") or "").strip()
        if only0:
            if only0 not in uids0:
                raise ValueError("找不到生成单元：%s" % only0)
            uids0 = [only0]
        ready0 = []
        for uid in uids0:
            um = _jload(cdir0 / uid / "unit.json", {}) or {}
            if str(um.get("prompt") or "").strip():
                ready0.append(uid)
        if not ready0:
            raise ValueError("这个画布还没有可布线的提示词：先在资产页写生成单元的提示词")
        data = self._asset_ensure_canvas_data(
            body.get("asset"), body.get("canvas") or "C01")
        aid, canvas, cdir = data["asset"], data["canvas"], data["cdir"]
        pid, gid, gname = data["project_id"], data["group_id"], data["group_name"]
        client, lib, cm = data["client"], data["lib"], data["cm"]
        units = data["units"]
        only = str(body.get("unit") or "").strip()
        if only:
            if only not in units:
                raise ValueError("找不到生成单元：%s" % only)
            units = [only]
        snap = st.snapshot()
        arow = next((x for x in (snap.get("assets") or []) if x["id"] == aid), {})
        primary = str(arow.get("primary_image") or "")
        refs_rows = [r for r in (lib.get("refs") or []) if isinstance(r, dict)]
        by_file = {str(r.get("file") or ""): r for r in refs_rows}
        upload_errors, uploaded = [], 0

        def ensure_ref(file_rel, label):
            nonlocal uploaded
            file_rel = self._asset_ref_file(st.root, cdir, file_rel)
            if not file_rel:
                return ""
            hit = by_file.get(file_rel)
            if hit and hit.get("node_id"):
                return str(hit["node_id"])
            absimg = st.root / file_rel
            try:
                nid = client.upload_image(
                    pid, absimg, label or absimg.name, x=-1200, y=600)
                uploaded += 1
                row = {"key": file_rel, "file": file_rel,
                       "label": label or absimg.name, "node_id": nid}
                refs_rows.append(row)
                by_file[file_rel] = row
                return nid
            except Exception as e:
                upload_errors.append("%s：%s" % (label or file_rel,
                                                 str(e)[:160]))
                return ""

        plan = []
        for uid in units:
            up = cdir / uid / "unit.json"
            um = _jload(up, {}) or {}
            if not isinstance(um, dict):
                um = {}
            prompt = str(um.get("prompt") or "").strip()
            if not prompt:
                plan.append({"unit": uid, "skip": "没写提示词"})
                continue
            ref_files = []
            if primary:
                ref_files.append(primary)
            for x in (um.get("refs") or []):
                f = self._asset_ref_file(st.root, cdir / uid, x)
                if f and f not in ref_files:
                    ref_files.append(f)
            left = []
            for f in ref_files:
                nid = ensure_ref(f, Path(f).stem)
                if nid:
                    left.append(nid)
            plan.append({
                "unit": uid, "prompt": prompt, "left": left,
                "sig": snap_mod.asset_prompt_hash(
                    prompt, um.get("negative"), um.get("size")),
                "unit_json": um, "unit_dir": cdir / uid})
        prompt_warnings = []
        for p in plan:
            if p.get("skip"):
                continue
            reason = _meta_prompt_reason(p.get("prompt"))
            if reason:
                prompt_warnings.append({
                    "unit": p["unit"], "reason": reason,
                    "prompt": p["prompt"][:240],
                })
        if dry:
            return self._json(200, {
                "ok": True, "dry_run": True, "asset": aid, "canvas": canvas,
                "run": run_now, "project_id": pid, "group_id": gid,
                "units": [{k: v for k, v in p.items()
                           if k in ("unit", "skip", "left")} for p in plan],
                "uploads": uploaded, "upload_errors": upload_errors,
                "warnings": prompt_warnings,
            })
        if prompt_warnings:
            w = prompt_warnings[0]
            raise ValueError(
                "生成单元 %s 的提示词像制作说明，不是出图描述（%s）。"
                "先改成画面本身：主体、外形 / 服装 / 材质、构图 / 机位、"
                "光、背景、风格；改完再布线。现在发给 LibTV 的会是你写的"
                "这段文字，生成结果当然会和预期不一样。" % (
                    w["unit"], w["reason"]))

        wired = skipped = 0
        errors = []
        for p in plan:
            if p.get("skip"):
                skipped += 1
                continue
            uid, um, udir = p["unit"], p["unit_json"], p["unit_dir"]
            prompt, left = p["prompt"], p["left"]
            sig = p.get("sig") or snap_mod.asset_prompt_hash(
                prompt, um.get("negative"), um.get("size"))
            model = str(um.get("model") or cm.get("model") or "z-image")
            node_id = str((um.get("libtv") or {}).get("node_id") or "")
            old = []
            if node_id:
                try:
                    cur = client.call("read_node", {
                        "projectId": pid, "node": node_id}, timeout=30)
                    nd = cur.get("node") or cur
                    params = (nd.get("data") or {}).get("params") or {}
                    old = [str(x.get("nodeId") or "")
                           for x in (params.get("imageList") or [])
                           if isinstance(x, dict)]
                except Exception:
                    node_id = ""
            digest = hashlib.sha1(
                (prompt + "|" + model + "|" + "|".join(left)).encode("utf-8")
            ).hexdigest()[:10]
            params = {"quality": "1K",
                      "ratio": str(um.get("size") or "16:9"), "count": 1}
            # 只撤掉 Trestle 自己上传过的旧参考图；用户在 LibTV 里加的
            # 设计风格 / 风格参考不在 refs_rows 里，必须保留。
            managed_ids = {str(r.get("node_id") or "") for r in refs_rows}
            managed_ids |= {x for x in left if x}
            remove = [x for x in old if x in managed_ids and x not in left]
            try:
                if node_id:
                    if remove:
                        key = libtv_mcp.safe_key(
                            "wb-asset-remove-%s-%s-%s-%s" % (
                                aid, canvas, uid, digest))
                        _call_write(client, "update_node", {
                            "node": node_id, "idempotencyKey": key,
                            "projectId": pid, "prompt": prompt,
                            "model": model, "params": params,
                            "leftRemove": remove, "run": False,
                        }, timeout=120)
                    if left:
                        key = libtv_mcp.safe_key(
                            "wb-asset-update-%s-%s-%s-%s" % (
                                aid, canvas, uid, digest))
                        _call_write(client, "update_node", {
                            "node": node_id, "idempotencyKey": key,
                            "projectId": pid, "prompt": prompt,
                            "model": model, "params": params,
                            "leftEnsure": left, "run": run_now,
                        }, timeout=120)
                    else:
                        key = libtv_mcp.safe_key(
                            "wb-asset-update-%s-%s-%s-%s" % (
                                aid, canvas, uid, digest))
                        _call_write(client, "update_node", {
                            "node": node_id, "idempotencyKey": key,
                            "projectId": pid, "prompt": prompt,
                            "model": model, "params": params, "run": run_now,
                        }, timeout=120)
                else:
                    key = libtv_mcp.safe_key(
                        "wb-asset-create-%s-%s-%s-%s" % (
                            aid, canvas, uid, digest))
                    name = ("%s %s" % (uid, um.get("name") or ""))[:180]
                    try:
                        r = _call_write(client, "create_node", {
                            "name": name,
                            "nodeType": "image", "model": model,
                            "idempotencyKey": key, "projectId": pid,
                            "prompt": prompt, "params": params, "left": left,
                            "run": run_now, "x": 100, "y": 700,
                        }, timeout=120)
                        node_id = str(
                            (r.get("plan") or {}).get("nodeId") or
                            (r.get("operationResult") or {}).get(
                                "subjectNodeId") or "")
                    except Exception as e:
                        if not _name_conflict(e):
                            raise
                        # 上一次其实已建好、只是本地没记下 ID：接管它，改走更新。
                        node_id = _adopt_named_node(
                            client, pid, name, "image", e)
                        upd = {"node": node_id,
                               "idempotencyKey": libtv_mcp.safe_key(
                                   "wb-asset-adopt-%s-%s-%s-%s" % (
                                       aid, canvas, uid, digest)),
                               "projectId": pid, "newName": name,
                               "prompt": prompt, "model": model,
                               "params": params, "run": run_now}
                        if left:
                            upd["leftEnsure"] = left
                        _call_write(client, "update_node", upd, timeout=120)
                if not node_id:
                    raise RuntimeError("没有返回节点 ID")
                # 先落盘节点 ID：后面建组/回读失败时，下次布线才能走 update
                # 而不是再撞同名。
                um.setdefault("libtv", {})
                um["libtv"].update({"node_id": node_id, "wired": True,
                                    "prompt_hash": sig})
                um["status"] = "生成中" if run_now else "已布线"
                _backup(st, udir / "unit.json")
                _jwrite(udir / "unit.json", um)
                berr = self._bind_group_verified(
                    client, pid, gname, node_id,
                    "wb-asset-bind-%s-%s-%s" % (aid, canvas, uid))
                if berr:
                    errors.append("%s：%s" % (uid, berr))
                um.setdefault("libtv", {})
                um["libtv"].update({"node_id": node_id, "wired": True,
                                    "group_id": gid, "status": "已布线",
                                    "prompt_hash": sig})
                # 刚写上去的提示词就是 LibTV 现状，别让旧缓存把它标成「改过」。
                if isinstance(um["libtv"].get("state"), dict):
                    um["libtv"]["state"]["prompt"] = prompt
                um["status"] = "生成中" if run_now else "已布线"
                _backup(st, udir / "unit.json")
                _jwrite(udir / "unit.json", um)
                lib.setdefault("nodes", [])
                hit = next((n for n in lib["nodes"]
                            if n.get("unit") == uid), None)
                if hit is None:
                    hit = {"id": "N%02d" % (len(lib["nodes"]) + 1),
                           "unit": uid}
                    lib["nodes"].append(hit)
                hit.update({"node_id": node_id, "prompt": prompt,
                            "prompt_hash": sig, "refs": left,
                            "status": "生成中" if run_now else "已布线"})
                wired += 1
            except Exception as e:
                errors.append("%s：%s" % (uid, str(e)[:200]))
        lib["refs"] = refs_rows
        lib["status"] = "已布线" if wired else (lib.get("status") or "未连线")
        _backup(st, cdir / "libtv.json")
        _jwrite(cdir / "libtv.json", lib)
        st.invalidate(["assets", "shots"])
        return self._json(200, {
            "ok": True, "dry_run": False, "asset": aid, "canvas": canvas,
            "run": run_now, "wired": wired, "skipped": skipped,
            "uploads": uploaded,
            "upload_errors": upload_errors, "errors": errors,
            "project_id": pid, "group_id": gid,
        })

    @staticmethod
    def _image_urls(node):
        data = (node or {}).get("data") or {}
        urls = []

        def add(u):
            if not u or not isinstance(u, str):
                return
            if not re.match(r"^https?://", u, re.I):
                u = "https://libtv-res.liblib.art/" + u.lstrip("/")
            if u not in urls:
                urls.append(u)

        raw = data.get("url") or []
        if isinstance(raw, str):
            raw = [raw]
        for u in raw if isinstance(raw, list) else []:
            add(u)
        tr = data.get("taskResult")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except Exception:
                tr = {}
        for bucket in (data, tr if isinstance(tr, dict) else {}):
            for x in (bucket.get("images") or []):
                if isinstance(x, dict):
                    add(x.get("previewPath") or x.get("originalPath") or
                        x.get("storagePath"))
        out = [u for u in urls if re.search(
            r"\.(png|jpg|jpeg|webp)(\?|#|$)", u, re.I)]
        return out or urls

    def _libtv_node_state(self, node, kind="image"):
        """把 read_node 的返回读成「最新状态 + 方案」。

        工作台只展示、不据此改本地状态机；拉回本地由用户显式点「采用」。
        """
        node = node if isinstance(node, dict) else {}
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        info = data.get("taskInfo") if isinstance(data.get("taskInfo"), dict) else {}
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        urls = self._video_urls(node) if kind == "video" else self._image_urls(node)
        urls = list(urls or [])
        bucket = data.get("videos") if kind == "video" else data.get("images")
        for x in (bucket or []):
            if not isinstance(x, dict):
                continue
            u = (x.get("previewPath") or x.get("originalPath")
                 or x.get("storagePath"))
            if isinstance(u, str) and u:
                if not re.match(r"^https?://", u, re.I):
                    u = "https://libtv-res.liblib.art/" + u.lstrip("/")
                if u not in urls:
                    urls.append(u)
        loading = bool(info.get("loading"))
        failed = str(info.get("failedReason") or "")
        if loading:
            status = "generating"
        elif failed:
            status = "failed"
        elif urls:
            status = "done"
        else:
            status = "idle"
        keep = {}
        for k in ("ratio", "resolution", "quality", "duration", "count",
                  "enableSound", "seed"):
            if params.get(k) not in (None, ""):
                keep[k] = params[k]
        return {
            "status": status, "loading": loading, "error": failed[:300],
            "prompt": _node_prompt(node),
            "model": str(data.get("model") or node.get("model") or ""),
            "params": keep,
            "style": _node_style_rows(node),
            "urls": urls[:4],
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _asset_sync(self, body):
        st = self.state
        adir, cdir, rel, m, canvas = self._asset_canvas(
            body.get("asset"), body.get("canvas") or "C01")
        cpath = cdir / "libtv.json"
        lib = _jload(cpath, {}) or {}
        if not isinstance(lib, dict):
            lib = {}
        pid = str(lib.get("project_id") or "")
        if not pid:
            raise ValueError("这个资产画布还没建：先点「建画布」")
        units = [d.name for d in self._asset_unit_dirs(cdir)]
        only = str(body.get("unit") or "").strip()
        if only:
            if only not in units:
                raise ValueError("找不到生成单元：%s" % only)
            units = [only]
        client = libtv_mcp.LibTV(st.root)
        rows = []
        for uid in units:
            up = cdir / uid / "unit.json"
            um = _jload(up, {}) or {}
            if not isinstance(um, dict):
                um = {}
            node_id = str((um.get("libtv") or {}).get("node_id") or "")
            if not node_id:
                rows.append({"unit": uid, "status": "未布线",
                             "message": "先布线"})
                continue
            try:
                r = client.call("read_node", {
                    "projectId": pid, "node": node_id}, timeout=30)
                node = r.get("node") or r
                data = node.get("data") or {}
                styles = _node_style_rows(node)
                if styles:
                    cur_lib = um.setdefault("libtv", {})
                    if cur_lib.get("styles") != styles:
                        cur_lib["styles"] = styles
                        _backup(st, up)
                        _jwrite(up, um)
                info = data.get("taskInfo") or {}
                urls = self._image_urls(node)
                loading = bool(info.get("loading"))
                failed = str(info.get("failedReason") or "")
                if loading:
                    rows.append({"unit": uid, "status": "generating",
                                 "message": "LibTV 还在生成"})
                    continue
                if failed:
                    rows.append({"unit": uid, "status": "error",
                                 "message": failed[:300]})
                    continue
                if not urls:
                    rows.append({"unit": uid, "status": "pending",
                                 "message": "还没有生成结果；去 LibTV 点生成"})
                    continue
                url = urls[0]
                ext = Path(urlparse(url).path).suffix.lower()
                if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                    ext = ".png"
                rdir = cdir / uid / "renders"
                rdir.mkdir(parents=True, exist_ok=True)
                out = rdir / ("%s-r1%s" % (uid, ext))
                tmp = out.with_suffix(out.suffix + ".part")
                req = urllib.request.Request(
                    url, headers={"User-Agent": "workbench/1"})
                with urllib.request.urlopen(req, timeout=300) as resp, \
                        open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                tmp.replace(out)
                um["output"] = out.relative_to(cdir / uid).as_posix()
                um["status"] = "已生成"
                um.setdefault("libtv", {})
                um["libtv"].update({
                    "node_id": node_id, "wired": True, "generated": True,
                    "url": url, "task_id": str(info.get("taskId") or ""),
                    "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                _backup(st, up)
                _jwrite(up, um)
                rows.append({"unit": uid, "status": "done",
                             "file": out.relative_to(st.root).as_posix(),
                             "url": url})
            except Exception as e:
                rows.append({"unit": uid, "status": "error",
                             "message": str(e)[:300]})
        st.invalidate(["assets", "shots"])
        return self._json(200, {"ok": True, "asset": body.get("asset"),
                                "canvas": canvas, "units": rows})

    def _scene_dir(self, uid, scene_id):
        base = self.state.root / "units" / uid / "production"
        if not base.is_dir():
            return None
        idx = _jload(base / "index.json", {}) or {}
        for row in idx.get("scenes") or []:
            if str(row.get("id")) == str(scene_id):
                d = base / str(row.get("dir") or "")
                if d.is_dir():
                    return d
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name.startswith(str(scene_id) + "-"):
                return d
        return None

    def _storyboard_dir(self, uid, sb_id):
        base = self.state.root / "units" / uid / "production"
        if base.is_dir():
            for sdir in sorted(base.iterdir()):
                if not sdir.is_dir():
                    continue
                for sbdir in sorted(sdir.iterdir()):
                    if not sbdir.is_dir():
                        continue
                    if sbdir.name == sb_id:
                        return sbdir
                    sj = _jload(sbdir / "storyboard.json", {}) or {}
                    if str(sj.get("id") or "") == str(sb_id):
                        return sbdir
        d = self.state.root / "units" / uid / "segments" / sb_id
        return d if d.is_dir() else None

    def _sync_libtv_refs(self, path, refs):
        """把分镜 / 场引用的资产同步进 libtv.json 的 refs，保留已连的 node_id。"""
        st = self.state
        path = Path(path)
        lib = _jload(path, {}) or {}
        if not isinstance(lib, dict):
            lib = {}
        old = [r for r in (lib.get("refs") or []) if isinstance(r, dict)]
        by_key = {str(r.get("key") or ""): r.get("node_id") or "" for r in old}
        by_file = {str(r.get("file") or ""): r.get("node_id") or "" for r in old}
        kinds = {"cast": "character", "characters": "character", "scene": "scene",
                 "scenes": "scene", "prop": "prop", "props": "prop",
                 "style": "style", "brand": "brand"}
        out = []
        for r in refs:
            aid = str(r.get("id") or "")
            if not aid:
                continue
            try:
                adir, _rel, m = self._asset(aid)
            except ValueError:
                continue
            variants = snap_mod._variants(st.root, adir, m)
            vid = str(r.get("variant") or "")
            v = next((x for x in variants if x["id"] == vid), None) if vid else \
                next((x for x in variants if x.get("primary")), None)
            image = (v or {}).get("image") or \
                (next((x["image"] for x in variants if x.get("primary")), "") or "")
            role = str(r.get("role") or "")
            kind = kinds.get(role) or ("character" if m.get("type") == "characters"
                                       else m.get("type") or "other")
            name = str(m.get("name") or aid)
            label = name
            if v and v.get("label"):
                label = "%s · %s" % (name, v["label"])
            key = aid if not vid else "%s-%s" % (aid, vid)
            item = {
                "key": key, "label": label, "file": image, "kind": kind,
                "asset_id": aid,
            }
            nid = by_key.get(key) or by_file.get(image) or ""
            if nid:
                item["node_id"] = nid
            out.append(item)
        lib["refs"] = out
        keys = [r["key"] for r in out if r.get("key")]
        nodes = lib.get("nodes")
        if keys and isinstance(nodes, list) and nodes:
            # 空 refs 的视频节点默认吃这一组资产；已有连线方案不动
            for node in nodes:
                if isinstance(node, dict) and not (node.get("refs") or []):
                    node["refs"] = list(keys)
        lib.setdefault("status", "未连线")
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(st, path)
        _jwrite(path, lib)
        return len(out)

    def _scene_assets(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        p = st.root / "units" / uid / "storyboard" / "scenes.json"
        if not p.exists():
            raise ValueError("这一集还没有 storyboard/scenes.json")
        mt = int(body.get("mtime") or 0)
        if mt and p.stat().st_mtime_ns != mt:
            raise Conflict("scenes.json 已被别处修改，已刷新")
        doc = _jload(p, None)
        if not isinstance(doc, dict) or not isinstance(doc.get("scenes"), list):
            raise ValueError("scenes.json 格式不对")
        sid = str(body.get("scene") or "").strip()
        scene = next((s for s in doc["scenes"] if str(s.get("id")) == sid), None)
        if scene is None:
            raise ValueError("找不到场：%s" % sid)

        def clean(v):
            out, seen = [], set()
            for x in (v or []):
                if isinstance(x, str):
                    aid, variant = x.strip(), ""
                elif isinstance(x, dict):
                    aid = str(x.get("id") or x.get("asset") or "").strip()
                    variant = str(x.get("variant") or "").strip()
                else:
                    continue
                if not aid or (aid, variant) in seen:
                    continue
                seen.add((aid, variant))
                out.append({"id": aid, "variant": variant} if variant else aid)
            return out

        scene["cast"] = clean(body.get("cast") or [])
        scene["assets"] = clean(body.get("assets") or [])
        _backup(st, p)
        _jwrite(p, doc)
        nrefs = 0
        sdir = self._scene_dir(uid, sid)
        if sdir:
            nrefs = self._sync_libtv_refs(
                sdir / "libtv.json",
                [{"id": x.get("id"), "variant": x.get("variant"), "role": "cast"}
                 for x in scene["cast"] if isinstance(x, dict)] +
                [{"id": x.get("id"), "variant": x.get("variant"), "role": "asset"}
                 for x in scene["assets"] if isinstance(x, dict)] +
                [{"id": x, "variant": "", "role": "asset"}
                 for x in scene["cast"] + scene["assets"] if isinstance(x, str)])
        st.invalidate(["scenes", "shots", "assets"])
        return self._json(200, {"ok": True, "scene": sid,
                                "cast": scene["cast"], "assets": scene["assets"],
                                "refs": nrefs,
                                "mtime": p.stat().st_mtime_ns})

    def _storyboard_assets(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sb_id = str(body.get("sb") or "").strip()
        if not sb_id:
            raise ValueError("没有分镜号")
        sbdir = self._storyboard_dir(uid, sb_id)
        if not sbdir:
            raise ValueError("找不到分镜目录：%s（先建分镜：d segment new %s --scene <场号>）"
                             % (sb_id, sb_id))
        sjp = sbdir / "storyboard.json"
        if not sjp.exists():
            raise ValueError("这个分镜还没有 storyboard.json（老段请先转 production）")
        mt = int(body.get("mtime") or 0)
        if mt and sjp.stat().st_mtime_ns != mt:
            raise Conflict("storyboard.json 已被别处修改，已刷新")
        sj = _jload(sjp, None)
        if not isinstance(sj, dict):
            raise ValueError("storyboard.json 格式不对")

        def clean(v):
            out, seen = [], set()
            for x in (v or []):
                if isinstance(x, str):
                    aid, variant = x.strip(), ""
                elif isinstance(x, dict):
                    aid = str(x.get("id") or x.get("asset") or "").strip()
                    variant = str(x.get("variant") or "").strip()
                else:
                    continue
                if not aid or (aid, variant) in seen:
                    continue
                seen.add((aid, variant))
                out.append({"id": aid, "variant": variant} if variant else aid)
            return out

        sj["cast"] = clean(body.get("cast") or [])
        sj["assets"] = clean(body.get("assets") or [])
        _backup(st, sjp)
        _jwrite(sjp, sj)
        refs = ([{"id": x.get("id"), "variant": x.get("variant"), "role": "cast"}
                 for x in sj["cast"] if isinstance(x, dict)] +
                [{"id": x.get("id"), "variant": x.get("variant"), "role": "asset"}
                 for x in sj["assets"] if isinstance(x, dict)] +
                [{"id": x, "variant": "", "role": "asset"}
                 for x in sj["cast"] + sj["assets"] if isinstance(x, str)])
        nrefs = self._sync_libtv_refs(sbdir / "libtv.json", refs)
        st.invalidate(["storyboards", "assets", "shots"])
        return self._json(200, {"ok": True, "sb": sb_id,
                                "cast": sj["cast"], "assets": sj["assets"],
                                "refs": nrefs,
                                "mtime": sjp.stat().st_mtime_ns})

    # ---------- 开工门禁 / LibTV 建设 ----------
    def _procs(self, fresh=False):
        """当前进程表（小写）。读不到就返回空串——调用方要能区分「没有」和「读不到」。

        tasklist 一次要 0.4 秒，而 /api/conn、/api/gate 一请求里会查好几次。
        缓存 3 秒：页面轮询不重复付这个钱，用户点「重新检测」走 fresh 绕过。
        """
        now = time.time()
        cache = getattr(self.state, "_procs_cache", None)
        if not fresh and cache and cache[1] is not None and now - cache[0] < 3.0:
            return cache[1]
        try:
            if os.name == "nt":
                r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                   capture_output=True, timeout=6,
                                   creationflags=NO_WINDOW)
            else:
                r = subprocess.run(["ps", "-A", "-o", "comm="],
                                   capture_output=True, timeout=6)
            val = (r.stdout or b"").decode("utf-8", "ignore").lower()
        except Exception:
            val = None
        self.state._procs_cache = (now, val)
        return val

    def _agent_states(self, fresh=False):
        """两个 Agent 各自独立判定。

        以前是「命中一个就 return」，服务只要从 Claude Code 里启动，
        CLAUDECODE 就先命中，WorkBuddy 永远检测不到。现在分开算。
        """
        env = os.environ
        procs = self._procs(fresh)
        forced = str(env.get("WB_AGENT") or "").strip().lower()
        forced_id = {"claude": "claude-code", "claude-code": "claude-code",
                     "claude_code": "claude-code", "workbuddy": "workbuddy",
                     "work-buddy": "workbuddy", "work_buddy": "workbuddy"}.get(forced, "")

        out = {}

        ev = ""
        if forced_id == "claude-code":
            ev = "WB_AGENT"
        elif (env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID")
              or str(env.get("AI_AGENT") or "").startswith("claude")):
            ev = "env"
        elif procs and "claude" in procs:
            ev = "process"
        out["claude-code"] = {"id": "claude-code", "name": "Claude Code",
                              "ready": bool(ev), "evidence": ev,
                              "hint": "" if ev else
                              ("进程表里没找到 claude" if procs is not None
                               else "读不到进程表")}

        ev = ""
        if forced_id == "workbuddy":
            ev = "WB_AGENT"
        elif any(k.startswith("WORKBUDDY") for k in env):
            ev = "env"
        elif procs and ("workbuddy" in procs or "work buddy" in procs):
            ev = "process"
        out["workbuddy"] = {"id": "workbuddy", "name": "WorkBuddy",
                            "ready": bool(ev), "evidence": ev,
                            "hint": "" if ev else
                            ("进程表里没找到 WorkBuddy.exe" if procs is not None
                             else "读不到进程表")}
        return out

    def _agent_info_from(self, st):
        for aid in ("claude-code", "workbuddy"):
            if st[aid]["ready"]:
                return st[aid]
        return {"id": "", "name": "未检测到 Agent", "ready": False, "evidence": ""}

    def _agent_info(self, fresh=False):
        return self._agent_info_from(self._agent_states(fresh))

    def _skill_info(self, agent):
        home = Path.home()
        cands = []
        bundled = _bundled_skill_dir()
        if bundled:
            cands.append(bundled)
        env_dir = os.environ.get("AIV_SKILL_DIR")
        if env_dir:
            cands.append(Path(env_dir))
        names = []
        for name in (_active_skill(),):
            if name and name not in names:
                names.append(name)
        if agent.get("id") == "workbuddy":
            for name in names:
                cands.append(home / ".workbuddy" / "skills" / name)
        for name in names:
            cands.append(home / ".claude" / "skills" / name)
        for name in names:
            cands.append(home / ".workbuddy" / "skills" / name)
            cands.append(self.state.root.parent.parent / "skills" / name)
            cands.append(self.state.root / "skills" / name)
        seen, rows = set(), []
        for d in cands:
            try:
                d = d.resolve()
            except Exception:
                continue
            if d in seen:
                continue
            seen.add(d)
            f = d / "SKILL.md"
            if not f.exists():
                continue
            ver = ""
            try:
                for line in f.read_text(encoding="utf-8").splitlines()[:20]:
                    if line.startswith("version:"):
                        ver = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass
            lib_support = (d / "references" / "LibTV-MCP.md").exists() or \
                (d / "bin" / "libtv_oauth.py").exists()
            rows.append({"path": str(d), "version": ver,
                         "libtv_support": lib_support, "ready": True})
        pick = rows[0] if rows else None
        if pick:
            pick["ready"] = bool(pick.get("libtv_support"))
        return pick or {"path": "", "version": "", "libtv_support": False,
                        "ready": False}

    def _libtv_connector(self):
        cfg = _jload(self.state.root / ".mcp.json", {}) or {}
        srv = (cfg.get("mcpServers") or {}).get("libtv") or {}
        configured = str(srv.get("url") or "") == "https://mcp.liblib.tv/mcp"
        claude_auth = False
        try:
            d = _jload(Path.home() / ".claude" / ".credentials.json", {}) or {}
            for k, v in (d.get("mcpOAuth") or {}).items():
                if k.startswith("libtv|") and v.get("accessToken"):
                    claude_auth = True
                    break
        except Exception:
            pass
        state = libtv_mcp.state_path(self.state.root)
        return {"configured": configured, "claude_auth": claude_auth,
                "state_file": str(state), "state_exists": state.exists()}

    def _gate_build(self, snap):
        scenes = snap.get("scenes") or []
        sbs = [x for x in (snap.get("storyboards") or []) if not x.get("suggested")]
        rl = _jload(self.state.root / "units" / snap["meta"]["unit"] / "libtv.json", {}) or {}
        rf = int(rl.get("folder_id") or rl.get("project_space_id") or 0)
        return {
            "scenes": len(scenes),
            "projects": sum(1 for sc in scenes
                            if (sc.get("libtv") or {}).get("project_id")),
            "folders": sum(1 for sc in scenes
                           if (sc.get("libtv") or {}).get("asset_folder_id")),
            "storyboards": len(sbs),
            "groups": sum(1 for x in sbs
                          if (x.get("libtv") or {}).get("group_id")),
            "container_project_id": str(rl.get("project_id") or ""),
            "container_folder_id": rf,
            "container_name": str(rl.get("name") or ""),
            "projects_in_folder": sum(
                1 for sc in scenes
                if rf and int((sc.get("libtv") or {}).get("folder_id") or 0) == rf),
        }

    # ---------- Agent 连接 ----------
    def _units(self, active=None):
        root = self.state.root
        st = _jload(root / "project.json", {}) or {}
        reg = st.get("units") or {}
        ids = {str(x) for x in reg}
        d = root / "units"
        if d.is_dir():
            ids |= {sub.name for sub in d.iterdir()
                    if sub.is_dir() and not sub.name.startswith("_")}
        active = active if active is not None else self.state.unit
        out = []
        for uid in sorted(ids, key=_unit_sort_key):
            sub = d / uid
            man = _jload(sub / "manifest.json", {}) or {}
            if not man and uid not in reg:
                continue
            sj = _jload(sub / "storyboard" / "shots.json", {}) or {}
            u = reg.get(uid) or {}
            out.append({
                "id": uid,
                "name": _unit_display_name(man),
                "type": str(man.get("type") or u.get("type") or ""),
                "duration": str(man.get("duration") or ""),
                "shots": len(sj.get("shots") or []),
                "active": uid == active,
            })
        return out

    def _film_new(self, body):
        """一条片的事务：创意 + 剧集 + 导演简报，一次建好。"""
        st = self.state
        line = str(body.get("line") or "").strip()
        if not line:
            raise ValueError("先写一句：这条片要让人记住什么？")
        utype = str(body.get("type") or "广告").strip()
        if utype not in UNIT_TYPES:
            raise ValueError("片型只能是：%s" % "、".join(UNIT_TYPES))
        duration = str(body.get("duration") or "20s").strip()
        aspect = str(body.get("aspect") or "9:16").strip()
        kind = str(body.get("kind") or utype).strip()
        audience = str(body.get("audience") or "").strip()
        action = str(body.get("action") or "").strip()
        moments = str(body.get("moments") or "").strip()

        safe = re.sub(r'[\\/:*?"<>|]', "", line[:40]).strip().strip(".")
        if not safe:
            safe = "新片"
        rel = "design/创意库/%s.md" % safe
        ip = st.root / rel
        if not ip.exists():
            now = time.strftime("%Y-%m-%d %H:%M")
            block = _fm_block("", {
                "type": "创意", "category": "整片", "status": "draft",
                "title": line[:60], "created": now, "updated": now,
            })
            ip.parent.mkdir(parents=True, exist_ok=True)
            _idea_clear_deleted(st.root, rel)
            ip.write_text(block + "\n\n# 一句话主旨\n\n" + line + "\n",
                          encoding="utf-8")

        # 当前单元还是空的就复用它；已有内容才开新一集。
        uid = st.unit
        udir = st.root / "units" / uid
        copy = udir / "script" / "copy.md"
        used = False
        if copy.exists():
            try:
                txt = copy.read_text(encoding="utf-8")
            except OSError:
                txt = ""
            used = len(txt.strip()) > 200 and "## " in txt
        if used:
            res = self._unit_new({"type": utype, "entry": line[:80],
                                  "activate": True})
            uid = res["unit"]["id"]
        else:
            if not udir.exists():
                _create_unit_skeleton(st.root, uid, utype, line[:80])
            else:
                man = _jload(udir / "manifest.json", {}) or {}
                man.update({"id": uid, "type": utype, "entry": line[:80]})
                _jwrite(udir / "manifest.json", man)
            pj = _jload(st.root / "project.json", {}) or {}
            units = pj.get("units")
            if not isinstance(units, dict):
                units = pj["units"] = {}
            row = units.get(uid) if isinstance(units.get(uid), dict) else {}
            row["type"] = utype
            units[uid] = row
            meta = pj.get("meta")
            if not isinstance(meta, dict):
                meta = pj["meta"] = {}
            meta["active_unit"] = uid
            _jwrite(st.root / "project.json", pj)
            st.unit = uid

        brief_rel = "units/%s/design/导演简报.md" % uid
        brief = st.root / brief_rel
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(chr(10).join([
            "# 导演简报", "",
            "- 一句话：%s" % line,
            "- 受众 / 场合：%s" % (audience or "待补"),
            "- 承诺 / 行动：%s" % (action or "待补"),
            "- 3 个必留瞬间：%s" % (moments or "待补"),
            "- 片型：%s" % kind,
            "- 时长 / 画幅：%s / %s" % (duration, aspect),
            "",
        ]), encoding="utf-8")
        st.invalidate()
        return self._json(200, {
            "ok": True, "unit": uid, "idea": rel, "brief": brief_rel,
            "next": "写剧本",
        })

    def _unit_new(self, body):
        root = self.state.root
        st = _jload(root / "project.json", {}) or {}
        meta = st.get("meta")
        if not isinstance(meta, dict):
            meta = st["meta"] = {}
        units = st.get("units")
        if not isinstance(units, dict):
            units = st["units"] = {}
        season = str(_season_no(st))
        raw = body.get("id")
        uid = _normalize_unit_id(raw, season) or _next_unit_id(st, root)
        if not UNIT_ID_RX.match(uid) or uid in (".", "..") or uid.startswith(("_", ".")):
            raise ValueError("集号只能用字母、数字、汉字和 . _ -，不能带空格或路径符号")
        if uid in units or (root / "units" / uid).exists():
            raise ValueError("剧集 %s 已经存在" % uid)
        utype = str(body.get("type") or "讲解").strip()
        if utype not in UNIT_TYPES:
            raise ValueError("片型只能是：%s" % "、".join(UNIT_TYPES))
        entry = str(body.get("entry") or "").strip()

        _create_unit_skeleton(root, uid, utype, entry)
        units[uid] = _unit_state_for_new(st, utype)
        activate = body.get("activate") is not False
        if activate:
            meta["active_unit"] = uid
            self.state.unit = uid
        meta["updated"] = time.strftime("%Y-%m-%d")
        _jwrite(root / "project.json", st)
        self.state.invalidate()

        active_uid = meta.get("active_unit") or self.state.unit
        row = next((x for x in self._units(active_uid) if x["id"] == uid),
                   {"id": uid, "name": entry, "type": utype,
                    "duration": "", "shots": 0, "active": activate})
        return {
            "ok": True, "unit": row, "units": self._units(active_uid),
            "active": active_uid,
            "next": _next_unit_id(st, root),
            "prompt": _unit_new_prompt(root, uid, utype, entry),
        }

    def _project_mode(self, body):
        """项目模式：general / short_drama。只改 project.json.meta.mode。"""
        st = self.state
        mode = str(body.get("mode") or "").strip()
        if mode not in ("general", "short_drama"):
            raise ValueError("mode 只能是 general / short_drama")
        p = st.root / "project.json"
        pj = _jload(p, {}) or {}
        meta = pj.get("meta") if isinstance(pj.get("meta"), dict) else {}
        if str(meta.get("mode") or "general") == mode:
            return self._json(200, {"ok": True, "mode": mode})
        _backup(st, p)
        meta["mode"] = mode
        meta["updated"] = time.strftime("%Y-%m-%d")
        pj["meta"] = meta
        _jwrite(p, pj)
        st.invalidate(["status"])
        return self._json(200, {"ok": True, "mode": mode})

    def _unit_select(self, body):
        uid = str(body.get("id") or "").strip()
        if not UNIT_ID_RX.match(uid) or uid in (".", "..") or uid.startswith(("_", ".")):
            raise ValueError("集号不合法")
        root = self.state.root
        st = _jload(root / "project.json", {}) or {}
        units = st.get("units")
        if not isinstance(units, dict):
            units = st["units"] = {}
        if uid not in units:
            # 老工程可能只有目录没登记；能读到 manifest 就补登记，别让切换按钮失效。
            man = _jload(root / "units" / uid / "manifest.json", {}) or {}
            if not man:
                raise ValueError("没有这一集：%s" % uid)
            units[uid] = _unit_state_for_new(st, str(man.get("type") or "讲解"))
        if not (root / "units" / uid).is_dir():
            raise ValueError("没有这一集：%s" % uid)
        meta = st.get("meta")
        if not isinstance(meta, dict):
            meta = st["meta"] = {}
        meta["active_unit"] = uid
        meta["updated"] = time.strftime("%Y-%m-%d")
        _jwrite(root / "project.json", st)
        self.state.unit = uid
        self.state.invalidate(["status"])
        return {"ok": True, "unit": uid}

    def _graph_conn_summary(self):
        """三处状态用的本集图谱计数：本地改过未推、已布线、缺提示词。"""
        uid = self.state.unit
        try:
            g = graph_mod.normalize(
                graph_mod.load(self.state.root, uid), uid)
        except Exception:
            return {"unit": uid, "nodes": 0, "wired": 0,
                    "needs_push": 0, "missing_prompt": 0, "exists": False}
        total = wired = dirty = missing = 0
        for seg in g.get("segments") or []:
            for n in seg.get("nodes") or []:
                if str(n.get("role") or "") in ("reference", "reference_video"):
                    continue
                total += 1
                if _node_key(n):
                    wired += 1
                if not str(n.get("prompt") or "").strip():
                    missing += 1
                if (_node_key(n) and n.get("input_hash")
                        and self._graph_current_hash(seg, n) != n["input_hash"]):
                    dirty += 1
        return {"unit": uid, "nodes": total, "wired": wired,
                "needs_push": dirty, "missing_prompt": missing,
                "exists": graph_mod.path(self.state.root, uid).is_file()}

    def _agent_conn(self, fresh=False):
        st = self._agent_states(fresh)
        info = self._agent_info_from(st)
        rows = []
        for aid in ("claude-code", "workbuddy"):
            row = dict(st[aid])
            exe = _agent_exe(aid, self.state.root)
            row["installed"] = bool(exe)
            row["path"] = exe
            row["action"] = ("none" if row["ready"]
                             else ("launch" if exe else "install"))
            rows.append(row)
        return {"ok": True, "detected": info, "agents": rows,
                "skill": self._skill_info(info)}

    def _agent_path(self, body):
        aid = str(body.get("id") or "").strip()
        if aid not in AGENT_EXE:
            raise ValueError("未知的 Agent：%s" % aid)
        p = _agent_set_path(self.state.root, aid, body.get("path"))
        return {"ok": True, "id": aid, "path": p}

    def _agent_launch(self, body):
        aid = str(body.get("id") or "").strip()
        if aid not in AGENT_EXE:
            raise ValueError("未知的 Agent：%s" % aid)
        exe = _agent_exe(aid, self.state.root)
        if not exe:
            return {"ok": False, "code": "needs_install", "id": aid}
        root = str(self.state.root)
        unit = str(body.get("unit") or self.state.unit or "")
        prompt = str(body.get("prompt") or "").strip()
        if aid == "workbuddy" and not prompt:
            prompt = _unit_takeover_prompt(root, unit)
        try:
            if aid == "workbuddy":
                # WorkBuddy 是 GUI：用 deeplink 让它直接打开工程并挂上导演 skill，
                # 而不是在终端里再开一个客户端。
                how = _launch_workbuddy(root, exe, prompt=prompt)
            else:
                how = _open_console(root, exe)
        except Exception as e:
            return {"ok": False, "code": "spawn_failed", "error": str(e)[:300]}
        return {"ok": True, "id": aid, "path": exe, "launched": True,
                "how": how, "unit": unit,
                "skill": _active_skill() if aid == "workbuddy" else "",
                "prompt": prompt if aid == "workbuddy" else "",
                "url": _workbuddy_link(root, prompt=prompt)
                if aid == "workbuddy" else ""}

    # ---------- LibTV 会话 ----------
    def _net_probe(self, force=False):
        st = self.state
        ts, res = st._probe_cache
        if not force and res and time.time() - ts < 20:
            return res
        ok, info = libtv_mcp.probe(st.root)
        st._probe_cache = (time.time(), (ok, info))
        return ok, info

    def _need_libtv(self):
        """动作前的快检，只看本地——不联网，不拖慢点击。

        真正连不上的情况由动作自己抛，do_POST 统一 classify 成修复卡。
        """
        loc = libtv_mcp.local_session(self.state.root)
        if loc["state"] == "signed_out":
            raise LibTVError("no_token", "LibTV 未连接：没有 LibTV token")
        if loc["state"] == "expired" and not loc.get("can_refresh"):
            raise LibTVError("expired", "LibTV 授权已过期")
        return loc

    def _libtv_session(self, net=False):
        """会话态。net=False 时只看本地（秒回），net=True 才联网核实。"""
        st = self.state
        loc = libtv_mcp.local_session(st.root)
        base = {
            "ok": True,
            "local": loc,
            "source": loc.get("source", ""),
            "can_refresh": bool(loc.get("can_refresh")),
            "expires_in": loc.get("expires_in"),
            "checked": int(time.time()),
        }
        job = st._login
        if job is not None:
            base["login"] = job.info()
            if job.alive():
                base["state"] = "authorizing"
                return base
            st._login = None            # 跑完了：结果以 probe 为准
            st._probe_cache = (0.0, None)
            net = True

        lstate = loc.get("state")
        if lstate == "signed_out":
            base.update(state="signed_out", kind="no_token",
                        fix=libtv_mcp.fix_for("no_token"))
            return base
        if lstate == "expired" and not loc.get("can_refresh"):
            base.update(state="expired", kind="expired",
                        fix=libtv_mcp.fix_for("expired"))
            return base

        ts, _cached = st._probe_cache
        if net or (ts and time.time() - ts < 20):
            ok, info = self._net_probe(force=net)
            if ok:
                base.update(state="online", kind="ok",
                            source=info.get("source") or base["source"],
                            account={"nickname": info.get("nickname", ""),
                                     "account_id": info.get("account_id", "")})
            else:
                kind = info.get("kind") or "unknown"
                if kind in ("expired", "unauthorized", "no_token"):
                    state = "expired"
                elif kind == "account":
                    state = "account"
                elif kind == "network":
                    state = "offline"
                else:
                    state = "error"
                base.update(
                    state=state, kind=kind, reason=info.get("reason", ""),
                    fix=info.get("fix") or libtv_mcp.fix_for(kind))
            return base

        base.update(state="unverified", kind="")
        return base

    def _libtv_login(self):
        st = self.state
        if st._login is not None and st._login.alive():
            return self._libtv_session(False)
        if self._libtv_session(False).get("state") == "online":
            return self._libtv_session(False)      # 已经连着，别再开一个授权进程
        skill = self._skill_info(self._agent_info())
        if not skill.get("ready"):
            return {"ok": False, "code": "needs_skill", "skill": skill,
                    "error": "没找到可用的导演 skill"}
        script = Path(str(skill.get("path") or "")) / "bin" / "libtv_oauth.py"
        if not script.exists():
            return {"ok": False, "code": "needs_skill", "skill": skill,
                    "error": "skill 里没有 bin/libtv_oauth.py"}
        port = _free_port(3118)
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script), "--port", str(port), "--timeout", "180"],
                cwd=str(st.root), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True,
                creationflags=NO_WINDOW)
        except Exception as e:
            return {"ok": False, "code": "spawn_failed", "error": str(e)[:300]}
        st._login = _LoginJob(proc, port)
        st._probe_cache = (0.0, None)
        return {"ok": True, "state": "authorizing", "port": port, "url": ""}

    def _libtv_logout(self):
        st = self.state
        cleared = []
        for kind, path, s, writable in libtv_mcp.sources(st.root):
            if not writable or not Path(path).exists():
                continue
            if not (s.get("access_token") or s.get("refresh_token")):
                continue
            s.pop("access_token", None)
            s.pop("refresh_token", None)
            s.pop("expires_at", None)
            s["signed_out_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(json.dumps(s, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
                cleared.append(kind)
            except Exception:
                pass
        st._probe_cache = (0.0, None)
        out = self._libtv_session(False)
        out["cleared"] = cleared
        return out

    def _setup_path(self):
        """「首次配置指引已关掉」的标记。放运行态目录，不放客户工程。"""
        root = Path(self.state.root).resolve()
        env = os.environ.get("AIV_STATE_DIR")
        if env:
            key = hashlib.sha1(str(root).lower().encode("utf-8")).hexdigest()[:16]
            return Path(env) / (key + "-setup.json")
        return aux_dir(root) / "setup.json"

    def _setup_dismissed(self):
        return self._setup_path().is_file()

    def _setup_dismiss(self, body):
        p = self._setup_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "dismissed": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": str(body.get("mode") or "later"),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True}

    def _onboard_path(self):
        return _config_dir() / "onboard.json"

    def _onboard_get(self):
        d = _jload(self._onboard_path(), {}) or {}
        if not isinstance(d, dict):
            d = {}
        return self._json(200, {
            "ok": True,
            "firstLessonAt": str(d.get("firstLessonAt") or ""),
            "coaches": d.get("coaches") if isinstance(
                d.get("coaches"), dict) else {},
            "checklistOpen": bool(d.get("checklistOpen")),
        })

    def _onboard_set(self, body):
        p = self._onboard_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        d = _jload(p, {}) or {}
        if not isinstance(d, dict):
            d = {}
        if "firstLessonAt" in body:
            d["firstLessonAt"] = str(body.get("firstLessonAt") or "")
        if isinstance(body.get("coaches"), dict):
            d["coaches"] = body["coaches"]
        if "checklistOpen" in body:
            d["checklistOpen"] = bool(body.get("checklistOpen"))
        _jwrite(p, d)
        return self._json(200, {"ok": True, **self._onboard_json(d)})

    @staticmethod
    def _onboard_json(d):
        return {
            "firstLessonAt": str(d.get("firstLessonAt") or ""),
            "coaches": d.get("coaches") if isinstance(
                d.get("coaches"), dict) else {},
            "checklistOpen": bool(d.get("checklistOpen")),
        }

    def _diag(self, q=None):
        """客户可复制的诊断信息：只放状态，不放剧本 / 提示词 / token。"""
        g = self._gate_info(fresh=False)
        snap = self.state.snapshot()
        ff = g.get("env", {}).get("ffmpeg") or {}
        edge = g.get("env", {}).get("edge") or {}
        disk = g.get("env", {}).get("disk") or {}
        agent = g.get("agent") or {}
        skill = g.get("skill") or {}
        sess = g.get("session") or {}
        import platform
        lines = [
            "Trestle 诊断信息 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "版本：%s" % (os.environ.get("AIV_APP_VERSION") or "未知"),
            "系统：%s" % platform.platform(),
            "工程：%s" % self.state.root,
            "单元：%s · %d 场 / %d 分镜 / %d 镜" % (
                snap["meta"]["unit"], snap["stats"]["scenes"],
                snap["stats"]["storyboards"], snap["stats"]["shots"]),
            "Agent：%s%s" % (agent.get("name") or "未检测到",
                             "（已连接）" if agent.get("ready") else "（未连接）"),
            "导演 skill：%s%s" % ("已就绪" if skill.get("ready") else "未找到",
                                  (" v" + str(skill.get("version"))) if skill.get("version") else ""),
            "LibTV：%s" % (sess.get("state") or "未知"),
            "ffmpeg：%s%s" % (
                ("可用 " if ff.get("ready") else "不可用 "),
                ("内置 " if ff.get("bundled") else "系统 ") + (ff.get("version") or "")),
            "Edge：%s" % ("已找到" if edge.get("ready") else "未找到"),
            "磁盘：%s" % (("剩 %.1f GB" % disk["free_gb"])
                         if disk.get("free_gb") is not None else "读不到"),
            "缺项：%s" % ("、".join(g.get("missing") or []) or "无"),
            "",
            "不含：剧本正文、提示词、素材文件、LibTV token。",
            "",
            "反馈请补充：",
            "1. 卡在哪一步",
            "2. 期望什么",
            "3. 实际发生了什么",
            "4. 截图或录屏",
            "5. 是否愿意再用",
        ]
        return {"ok": True, "text": "\n".join(lines),
                "version": os.environ.get("AIV_APP_VERSION") or "",
                "env": g.get("env") or {}, "missing": g.get("missing") or []}

    def _gate_info(self, fresh=False):
        now = time.time()
        cached = self.state._gate_cache
        if not fresh and cached[1] and now - cached[0] < 20:
            return cached[1]
        agent = self._agent_info(fresh)
        skill = self._skill_info(agent)
        connector = self._libtv_connector()
        sess = self._libtv_session(net=fresh)
        lib_ok = sess.get("state") == "online"
        lib_detail = {
            "reason": sess.get("reason") or (sess.get("local") or {}).get("path", ""),
            "kind": sess.get("kind") or "",
            "source": sess.get("source") or "",
            "nickname": (sess.get("account") or {}).get("nickname", ""),
            "account_id": (sess.get("account") or {}).get("account_id", ""),
        }
        snap = self.state.snapshot()
        started = _jload(self.state.root / ".workbuddy" / "tmp" / "started.json", None)
        project_ok = (self.state.root / "project.json").exists()
        ffmpeg = _ffmpeg_info()
        edge = _edge_path()
        free_gb = _disk_free_gb(self.state.root)
        missing = []
        if not agent.get("ready"):
            missing.append("Agent（Claude Code / WorkBuddy）")
        if not skill.get("ready"):
            missing.append("导演 skill（含 LibTV 支持）")
        if not lib_ok:
            missing.append("LibTV 授权")
        if not project_ok:
            missing.append("project.json")
        if not ffmpeg.get("ready"):
            missing.append("ffmpeg（拼全片 / 抽封面）")
        if os.name == "nt" and not edge:
            missing.append("Edge / WebView2")
        if free_gb is not None and free_gb < 2:
            missing.append("磁盘空间（剩 %.1fGB，建议 ≥2GB）" % free_gb)
        rl = _jload(self.state.root / "units" / snap["meta"]["unit"] / "libtv.json", {}) or {}
        root_pid = str(rl.get("project_id") or "")
        root_folder = int(rl.get("folder_id") or rl.get("project_space_id") or 0)
        container = {
            "project_id": root_pid,
            "folder_id": root_folder,
            "name": str(rl.get("name") or ""),
            "url": snap_mod._editor_url(root_pid, root_folder) or str(rl.get("url") or ""),
        }
        links = []
        for sc in snap.get("scenes") or []:
            sc_lib = sc.get("libtv") or {}
            sbs = []
            for sb in snap.get("storyboards") or []:
                if sb.get("scene") != sc["id"] or sb.get("suggested"):
                    continue
                sl = sb.get("libtv") or {}
                sbs.append({
                    "id": sb["id"], "name": sb["name"],
                    "project_id": sl.get("project_id") or "",
                    "group_id": sl.get("group_id") or "",
                    "url": sl.get("url") or "",
                })
            links.append({
                "id": sc["id"], "name": sc["name"],
                "project_id": sc_lib.get("project_id") or "",
                "project_space_id": sc_lib.get("project_space_id") or 0,
                "asset_folder_id": sc_lib.get("asset_folder_id") or 0,
                "url": sc_lib.get("url") or "",
                "storyboards": sbs,
            })
        data = {
            "ok": True,
            "ready": not missing,
            "started": bool(started),
            "started_at": (started or {}).get("started") if isinstance(started, dict) else "",
            "setup_dismissed": self._setup_dismissed(),
            "agent": agent,
            "skill": skill,
            "libtv": {
                "ready": bool(lib_ok),
                "connector": connector,
                "detail": lib_detail,
            },
            "session": sess,
            "env": {
                "ffmpeg": ffmpeg,
                "edge": {"ready": bool(edge), "path": edge},
                "disk": {"free_gb": free_gb,
                         "ready": free_gb is None or free_gb >= 2},
            },
            "project": {
                "unit": snap["meta"]["unit"],
                "scenes": snap["stats"]["scenes"],
                "storyboards": snap["stats"]["storyboards"],
            },
            "build": self._gate_build(snap),
            "links": {"container": container, "scenes": links},
            "missing": missing,
        }
        self.state._gate_cache = (now, data)
        return data

    # ------------------------------------------------------------ LibTV 槽位
    def _libtv_projects(self, client):
        """当前账号可见的项目。只读；失败交给调用方决定是否降级。"""
        r = client.call("list_projects", {"page": 1, "pageSize": 100},
                        timeout=60)
        rows = (r.get("data") or {}).get("projectMetaList")
        if rows is None:
            rows = r.get("projects") or r.get("projectMetaList") or []
        out = []
        for p in rows or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("uuid") or p.get("projectId") or p.get("id") or "")
            if not pid:
                continue
            out.append({
                "project_id": pid,
                "name": str(p.get("name") or ""),
                "folder_id": int(p.get("folderId") or 0),
                "space_id": int(p.get("projectSpaceId") or 0),
                "description": str(p.get("description") or ""),
            })
        return out

    def _libtv_groups(self, client, pid):
        """一个场画布里的组。None 表示没读到（网络/权限），不要据此清空本地。"""
        if not pid:
            return None
        try:
            r = client.call("read_project", {
                "projectId": pid, "view": "groups"}, timeout=60)
            return [g for g in (r.get("groups") or []) if isinstance(g, dict)]
        except Exception:
            return None

    def _libtv_local_pids(self, root):
        """工程里所有 libtv.json 指向的项目；这些不是“旧项目”。"""
        out = set()
        root = Path(root)
        for p in root.rglob("libtv.json"):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if any(x.startswith(".") for x in rel.parts):
                continue
            if rel.parts and rel.parts[0] in ("legacy", "dist", "skill-backups"):
                continue
            if "_backup" in rel.parts:
                continue
            d = _jload(p, {}) or {}
            if not isinstance(d, dict):
                continue
            canvas = d.get("canvas")
            if not isinstance(canvas, dict):
                canvas = {}
            pid = str(d.get("project_id") or d.get("projectId")
                      or canvas.get("projectId") or "")
            if pid:
                out.add(pid)
        return out

    def _pick_orphan_project(self, projects, root_folder, used, keep, cur):
        """在根目录里找可复用的旧场项目，避免重建时无限新增画布。"""
        sid = str(cur.get("id") or "")
        best = None
        for p in projects:
            pid = str(p.get("project_id") or "")
            if not pid or pid in used or pid in keep:
                continue
            if root_folder and int(p.get("folder_id") or 0) != root_folder:
                continue
            name = str(p.get("name") or "")
            if not re.match(r"^SC\d+(?:\s|$)", name):
                continue
            score = None
            if sid and name.startswith(sid + " "):
                score = 0
            elif _norm_name(name) == _norm_name(cur.get("name")):
                score = 1
            if score is not None and (best is None or score < best[0]):
                best = (score, p)
        return best[1] if best else None

    def _libtv_slot_plan(self, snap, client, only=None):
        """本地槽位 + 线上项目/组 → 本轮的复用/新建计划。

        不写任何文件；dry-run 和真跑共用。返回 (map, by_scene, orphan_projects,
        projects_read_ok)。只读选中场的 group，避免单场同步拖慢整集。
        """
        st = self.state
        unit = snap["meta"]["unit"]
        mapd = libtv_slots.load(st.root, unit)
        root_path = st.root / "units" / unit / "libtv.json"
        rlib = _jload(root_path, {}) or {}
        root_pid = str(rlib.get("project_id") or "")
        root_folder = int(rlib.get("folder_id")
                          or rlib.get("project_space_id") or 0)

        cur_scenes, scene_sbs = [], {}
        for sc in snap.get("scenes") or []:
            sdir = self._scene_dir(unit, sc["id"])
            if not sdir:
                continue
            lib = _jload(sdir / "libtv.json", {}) or {}
            if not isinstance(lib, dict):
                lib = {}
            cur_scenes.append({
                "id": sc["id"], "name": sc["name"], "order": sc.get("order"),
                "key": str(lib.get("lib_key") or ""),
                "project_id": str(lib.get("project_id") or ""),
                "asset_folder_id": int(lib.get("asset_folder_id") or 0),
                "folder_id": int(lib.get("folder_id")
                                or lib.get("project_space_id") or 0),
            })
            sbs = []
            rows = [x for x in (snap.get("storyboards") or [])
                    if x.get("scene") == sc["id"] and x.get("dir")
                    and not x.get("suggested")]
            rows.sort(key=lambda x: (float(x.get("start") or 0), x.get("id") or ""))
            for i, sb in enumerate(rows):
                sl = {}
                if sb.get("libtv_path"):
                    sl = _jload(st.root / sb["libtv_path"], {}) or {}
                if not isinstance(sl, dict):
                    sl = {}
                sbs.append({
                    "id": sb["id"], "name": sb.get("name") or "",
                    "scene": sc["id"], "shots": sb.get("shots") or [],
                    "index": i, "key": str(sl.get("lib_key") or ""),
                    "group_id": str(sl.get("group_id") or ""),
                    "group_name": str(sl.get("group_name") or ""),
                })
            scene_sbs[sc["id"]] = sbs

        pairs = libtv_slots.apply_scenes(mapd, cur_scenes)
        try:
            projects = self._libtv_projects(client)
            projects_ok = True
        except Exception:
            projects, projects_ok = [], False
        by_id = {p["project_id"]: p for p in projects}
        keep = self._libtv_local_pids(st.root)
        if root_pid:
            keep.add(root_pid)

        used = set()
        for cur, slot in pairs:
            sid = cur["id"]
            old_pid = str(cur.get("project_id") or "")
            pid = str(slot.get("project_id") or old_pid)
            if projects_ok and pid and pid not in by_id:
                pid = ""
                slot["project_id"] = ""
                slot["project_missing"] = True
                slot["project_epoch"] = int(slot.get("project_epoch") or 1) + 1
            if not pid and projects_ok:
                cand = self._pick_orphan_project(
                    projects, root_folder, used, keep, cur)
                if cand:
                    pid = cand["project_id"]
                    slot["project_id"] = pid
                    slot["adopted"] = True
            if pid:
                slot["project_id"] = pid
                used.add(pid)
            else:
                # 新画布：旧 project 里的 group_id 不能带过去。
                for sb in slot.get("storyboards") or []:
                    sb["group_id"] = ""
                    sb["group_name"] = ""
                for sb in scene_sbs.get(sid) or []:
                    sb["group_id"] = ""
                    sb["group_name"] = ""
            if old_pid and pid and old_pid != pid:
                for sb in slot.get("storyboards") or []:
                    sb["group_id"] = ""
                    sb["group_name"] = ""
                for sb in scene_sbs.get(sid) or []:
                    sb["group_id"] = ""
                    sb["group_name"] = ""
            groups = self._libtv_groups(client, pid) \
                if pid and (not only or sid in only) else None
            if groups is not None:
                libtv_slots.merge_remote_groups(slot, groups)
            libtv_slots.apply_storyboards(slot, scene_sbs.get(sid) or [])
            libtv_slots.assign_groups(slot, remote_ok=groups is not None)

        active_pids = {str(sc.get("project_id") or "")
                       for sc in mapd.get("scenes") or []
                       if sc.get("active") and sc.get("project_id")}
        keep |= active_pids
        orphans = []
        if projects_ok:
            for p in projects:
                pid = p["project_id"]
                name = p["name"]
                if pid in keep or pid == root_pid:
                    continue
                if root_folder and int(p.get("folder_id") or 0) != root_folder:
                    continue
                if not re.match(r"^SC\d+(?:\s|$)", name):
                    continue
                if name.startswith("[归档]"):
                    continue
                orphans.append({"project_id": pid, "name": name})
        return mapd, {cur["id"]: slot for cur, slot in pairs}, orphans, projects_ok

    def _libtv_archive(self, body):
        """把不再对应当前场的旧项目移进 [归档] 容器。

        MCP 没有删除项目/组的能力；归档是唯一能自动做的清理。组不能移动，
        所以只归档项目，旧组留给后续分镜复用或在 LibTV 网页手动删。
        """
        st = self.state
        dry = bool(body.get("dry_run", True))
        if not dry and not body.get("confirm"):
            raise ValueError("正式归档需要 confirm=true")
        self._need_libtv()
        snap = st.snapshot()
        client = libtv_mcp.LibTV(st.root)
        unit = snap["meta"]["unit"]
        root_path = st.root / "units" / unit / "libtv.json"
        rlib = _jload(root_path, {}) or {}
        root_pid = str(rlib.get("project_id") or "")
        root_folder = int(rlib.get("folder_id")
                          or rlib.get("project_space_id") or 0)
        projects = self._libtv_projects(client)
        keep = self._libtv_local_pids(st.root)
        if root_pid:
            keep.add(root_pid)
        # 本地 libtv.json 可能还没落盘，槽位表里的 active 项目同样不能归档。
        try:
            smap = libtv_slots.load(st.root, unit)
            for sc in smap.get("scenes") or []:
                if sc.get("active") and sc.get("project_id"):
                    keep.add(str(sc["project_id"]))
        except Exception:
            pass
        orphan = []
        for p in projects:
            pid = p["project_id"]
            name = p["name"]
            if pid in keep:
                continue
            if root_folder and int(p.get("folder_id") or 0) != root_folder:
                continue
            if not re.match(r"^SC\d+(?:\s|$)", name):
                continue
            if name.startswith("[归档]"):
                continue
            orphan.append(p)
        if not orphan:
            return {"ok": True, "dry_run": dry, "archived": [],
                    "archive_project": {}, "reason": "没有旧项目"}

        arch_path = st.root / "units" / unit / "libtv-archive.json"
        arch = _jload(arch_path, {}) or {}
        if not isinstance(arch, dict):
            arch = {}
        a_pid = str(arch.get("project_id") or "")
        a_space = int(arch.get("folder_id") or arch.get("project_space_id") or 0)
        a_action = "exists"
        if not a_pid:
            if dry:
                a_action = "create"
            else:
                try:
                    r = client.call("create_project", {
                        "name": ("[归档] %s · %s" % (
                            snap["meta"]["name"], unit))[:180],
                        "description": "Trestle 归档：不再对应当前场结构的旧场项目",
                        "idempotencyKey": "wb-%s-archive-v1" % unit,
                        "openInBrowser": False,
                    })
                    a_pid, a_space = libtv_mcp.project_meta(r)
                    if not a_pid:
                        raise RuntimeError("create_project 没返回 projectId")
                    if not a_space:
                        try:
                            info = client.call("read_project", {
                                "projectId": a_pid, "view": "summary"})
                            _, a_space = libtv_mcp.project_meta(info)
                        except Exception:
                            pass
                    arch["project_id"] = a_pid
                    if a_space:
                        arch["project_space_id"] = a_space
                        arch["folder_id"] = a_space
                    arch["name"] = "[归档] %s · %s" % (snap["meta"]["name"], unit)
                    _backup(st, arch_path)
                    _jwrite(arch_path, arch)
                    a_action = "created"
                except Exception as e:
                    return {"ok": False, "dry_run": dry, "archived": [],
                            "archive_project": {"action": "error"},
                            "error": str(e)[:300]}

        rows = []
        for p in orphan:
            name = p["name"]
            new_name = ("[归档] " + name)[:180]
            row = {"project_id": p["project_id"], "name": name,
                   "new_name": new_name, "action": "plan" if dry else "archived"}
            if not dry:
                try:
                    client.call("update_project", {
                        "projectId": p["project_id"],
                        "folderId": a_space or None,
                        "name": new_name,
                        "description": ("[Trestle 归档] %s" % (
                            p.get("description") or name))[:5000],
                        "idempotencyKey": "wb-%s-archive-%s-v1" % (
                            unit, p["project_id"]),
                    }, timeout=30)
                except Exception as e:
                    row["action"] = "error"
                    row["reason"] = str(e)[:300]
            rows.append(row)
        return {"ok": True, "dry_run": dry, "archived": rows,
                "archive_project": {"project_id": a_pid,
                                    "folder_id": a_space,
                                    "action": a_action}}

    def _libtv_ensure(self, body):
        st = self.state
        dry = bool(body.get("dry_run", True))
        if not dry and not body.get("confirm"):
            raise ValueError("非 dry-run 需要 confirm=true")
        self._need_libtv()
        snap = st.snapshot()
        client = libtv_mcp.LibTV(st.root)
        pj = _jload(st.root / "project.json", {}) or {}
        cfg = snap_mod._libtv_cfg(pj)
        unit = snap["meta"]["unit"]
        only = {str(x) for x in (body.get("scenes") or []) if str(x).strip()}
        if str(body.get("scene") or "").strip():
            only.add(str(body["scene"]).strip())
        container_only = bool(body.get("container_only"))
        report = []
        root_path = st.root / "units" / unit / "libtv.json"
        rlib = _jload(root_path, {}) or {}
        if not isinstance(rlib, dict):
            rlib = {}
        root_name = ("%s · %s 项目文件夹" % (snap["meta"]["name"], unit)).strip()[:180]
        root_pid = str(rlib.get("project_id") or "")
        root_folder = int(rlib.get("folder_id") or rlib.get("project_space_id") or 0)
        root_action = "exists"
        if not root_pid:
            if dry:
                root_action = "create"
            else:
                try:
                    r = client.call("create_project", {
                        "name": root_name,
                        "description": "项目文件夹：%s · %s；场项目都收在这里" % (
                            snap["meta"]["name"], unit),
                        "idempotencyKey": "wb-%s-root-v1" % unit,
                        "openInBrowser": False,
                    })
                    root_pid, root_folder = libtv_mcp.project_meta(r)
                    if not root_pid:
                        raise RuntimeError("create_project 没返回 projectId")
                    rlib["project_id"] = root_pid
                    if root_folder:
                        rlib["project_space_id"] = root_folder
                        rlib["folder_id"] = root_folder
                    rlib["name"] = root_name
                    rlib["url"] = snap_mod._fmt_url(
                        cfg.get("project_url"), root_pid, "", cfg.get("web_base"))
                    rlib["status"] = "项目文件夹"
                    _backup(st, root_path)
                    _jwrite(root_path, rlib)
                    root_action = "created"
                except Exception as e:
                    root_action = "error"
                    report.append({"id": "ROOT", "name": root_name,
                                   "project": "error", "folder": "skip",
                                   "reason": str(e)[:300]})
        elif not root_folder:
            try:
                info = client.call("read_project", {
                    "projectId": root_pid, "view": "summary"})
                _, root_folder = libtv_mcp.project_meta(info)
                if root_folder:
                    rlib["project_space_id"] = root_folder
                    rlib["folder_id"] = root_folder
                    _backup(st, root_path)
                    _jwrite(root_path, rlib)
            except Exception:
                pass

        if container_only:
            mapd, scene_slots, orphan_projects, projects_ok = {}, {}, [], False
        else:
            mapd, scene_slots, orphan_projects, projects_ok = \
                self._libtv_slot_plan(snap, client, only=only or None)

        for sc in ([] if container_only else (snap.get("scenes") or [])):
            sid = sc["id"]
            if only and sid not in only:
                continue
            sdir = self._scene_dir(unit, sid)
            if not sdir:
                report.append({"id": sid, "name": sc["name"],
                               "project": "skip", "folder": "skip",
                               "reason": "没有 production 场目录"})
                continue
            slot = scene_slots.get(sid) or {}
            name = ("%s %s" % (sid, sc["name"])).strip()[:180]
            desc = "%s · %s · %s" % (snap["meta"]["name"], unit, name)
            path = sdir / "libtv.json"
            lib = _jload(path, {}) or {}
            if not isinstance(lib, dict):
                lib = {}
            changed = False
            if slot.get("key") and lib.get("lib_key") != slot["key"]:
                lib["lib_key"] = slot["key"]
                changed = True
            proj_action = "exists"
            adopted = bool(slot.get("adopted"))
            pid = str(slot.get("project_id") or "")
            if not pid:
                if dry:
                    proj_action = "create"
                else:
                    try:
                        r = client.call("create_project", {
                            "name": name, "description": desc,
                            "idempotencyKey": libtv_mcp.safe_key(
                                "wb-%s-scene-%s-project-v%d" % (
                                    unit, slot.get("key") or sid,
                                    int(slot.get("project_epoch") or 1))),
                            "openInBrowser": False,
                        })
                        pid, space = libtv_mcp.project_meta(r)
                        if not pid:
                            raise RuntimeError("create_project 没返回 projectId")
                        if not space:
                            try:
                                info = client.call("read_project", {
                                    "projectId": pid, "view": "summary"})
                                _, space = libtv_mcp.project_meta(info)
                            except Exception:
                                pass
                        slot["project_id"] = pid
                        slot.pop("project_missing", None)
                        lib["project_id"] = pid
                        if space:
                            lib["project_space_id"] = space
                            lib["folder_id"] = space
                        lib["name"] = name
                        lib["url"] = snap_mod._fmt_url(
                            cfg.get("project_url"), pid, "", cfg.get("web_base"))
                        lib["status"] = "已建"
                        changed = True
                        proj_action = "created"
                    except Exception as e:
                        proj_action = "error"
                        report.append({"id": sid, "name": name,
                                       "project": proj_action, "folder": "skip",
                                       "reason": str(e)[:300]})
                        continue
            if pid:
                if lib.get("project_id") != pid:
                    lib["project_id"] = pid
                    changed = True
                if lib.get("name") != name:
                    if dry:
                        proj_action = "plan" if proj_action == "exists" else proj_action
                    else:
                        try:
                            client.call("update_project", {
                                "projectId": pid, "name": name,
                                "description": desc,
                                "idempotencyKey": "wb-%s-scene-%s-rename-%s" % (
                                    unit, slot.get("key") or sid,
                                    hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]),
                            }, timeout=30)
                            lib["name"] = name
                            changed = True
                            if proj_action == "exists":
                                proj_action = "renamed"
                        except Exception as e:
                            report.append({"id": sid, "name": name,
                                           "project": proj_action,
                                           "folder": "skip",
                                           "rename": "error",
                                           "reason": str(e)[:300]})
                            continue
                if root_folder and int(lib.get("folder_id") or 0) != root_folder:
                    if dry:
                        move_action = "plan"
                    else:
                        try:
                            client.call("update_project", {
                                "projectId": pid, "folderId": root_folder,
                                "idempotencyKey": "wb-%s-scene-%s-move-v1"
                                % (unit, slot.get("key") or sid),
                            })
                            lib["project_space_id"] = root_folder
                            lib["folder_id"] = root_folder
                            changed = True
                            move_action = "moved"
                        except Exception as e:
                            move_action = "error"
                            report.append({"id": sid, "name": name,
                                           "project": proj_action,
                                           "folder": "skip", "move": move_action,
                                           "reason": str(e)[:300]})
                            continue
                else:
                    move_action = "exists"
            else:
                move_action = "after-root" if dry and not root_pid else "exists"

            folder_action = "exists"
            fid = int(lib.get("asset_folder_id") or 0)
            if not fid:
                if dry:
                    folder_action = "create"
                else:
                    try:
                        r = client.call("manage_asset_folder", {
                            "action": "create", "name": name, "description": desc,
                            "projectId": pid or None,
                            "idempotencyKey": "wb-%s-scene-%s-folder-v1"
                            % (unit, slot.get("key") or sid),
                            "parentFolderId": 0,
                        })
                        fid = libtv_mcp.folder_meta(r)
                        if not fid:
                            raise RuntimeError("manage_asset_folder 没返回 folderId")
                        lib["asset_folder_id"] = fid
                        changed = True
                        folder_action = "created"
                    except Exception as e:
                        folder_action = "error"
                        if changed and not dry:
                            _backup(st, path)
                            _jwrite(path, lib)
                        report.append({"id": sid, "name": name,
                                       "project": proj_action,
                                       "folder": folder_action,
                                       "reason": str(e)[:300],
                                       "project_id": pid, "asset_folder_id": fid})
                        continue

            g_new = g_exist = g_plan = g_err = g_orphan = g_stale = 0
            g_reused_orphan = 0
            sbs = [x for x in (snap.get("storyboards") or [])
                   if x.get("scene") == sid and x.get("dir")
                   and not x.get("suggested")]
            sbs.sort(key=lambda x: (float(x.get("start") or 0), x.get("id") or ""))
            for sb in sbs:
                sbslot = libtv_slots.active_sb(slot, sb["id"])
                if not sbslot:
                    g_err += 1
                    continue
                auto_gname = ("%s %s" % (sb["id"], sb["name"]))[:180]
                desired = str(sbslot.get("desired_name") or auto_gname)
                action = str(sbslot.get("group_action") or "")
                gid = str(sbslot.get("group_id") or "")
                actual = str(sbslot.get("group_name") or "")
                sp = st.root / sb["dir"] / "libtv.json"
                sl = _jload(sp, {}) or {}
                if not isinstance(sl, dict):
                    sl = {}
                sb_changed = False

                if not gid:
                    action = "create"
                elif not action:
                    action = "reuse"
                if action in ("reuse", "reuse_orphan"):
                    g_exist += 1
                    if action == "reuse_orphan":
                        g_reused_orphan += 1
                    if sbslot.get("group_stale"):
                        g_stale += 1
                    if sl.get("lib_key") != sbslot.get("key"):
                        sl["lib_key"] = str(sbslot.get("key") or "")
                        sb_changed = True
                    if sl.get("group_id") != gid:
                        sl["group_id"] = gid
                        sb_changed = True
                    if actual and sl.get("group_name") != actual:
                        sl["group_name"] = actual
                        sb_changed = True
                    if sl.get("group_label") != desired:
                        sl["group_label"] = desired
                        sb_changed = True
                    stale = bool(sbslot.get("group_stale"))
                    if bool(sl.get("group_stale")) != stale:
                        sl["group_stale"] = stale
                        sb_changed = True
                    if pid and sl.get("project_id") != pid:
                        sl["project_id"] = pid
                        sb_changed = True
                    if fid and sl.get("asset_folder_id") != fid:
                        sl["asset_folder_id"] = fid
                        sb_changed = True
                    if sb_changed and not dry:
                        _backup(st, sp)
                        _jwrite(sp, sl)
                    vnid = str((sl.get("video") or {}).get("node_id") or "")
                    if vnid and pid and not dry:
                        berr = self._bind_group_verified(
                            client, pid, actual or desired, vnid,
                            "wb-sync-bind-%s-%s-%s" % (unit, sb["id"], vnid))
                        if berr:
                            g_err += 1
                    continue

                if action == "create":
                    if dry:
                        g_plan += 1
                        continue
                    if not pid:
                        g_err += 1
                        continue
                    try:
                        nodes = [str(n.get("node_id") or n.get("nodeId"))
                                 for n in (sl.get("nodes") or [])
                                 if isinstance(n, dict)
                                 and (n.get("node_id") or n.get("nodeId"))]
                        vnid = str((sl.get("video") or {}).get("node_id") or "")
                        if vnid and vnid not in nodes:
                            nodes.append(vnid)
                        r = client.call("manage_group", {
                            "action": "create", "projectId": pid,
                            "name": desired, "nodes": nodes,
                            "idempotencyKey": "wb-%s-sb-%s-group-v%d" % (
                                unit, sbslot.get("key") or sb["id"],
                                int(sbslot.get("group_epoch") or 1)),
                        })
                        gid = libtv_mcp.group_meta(r)
                        if not gid:
                            raise RuntimeError("manage_group 没返回 groupId")
                        sl["group_id"] = gid
                        sl["group_name"] = desired
                        sl["group_label"] = desired
                        sl["group_stale"] = False
                        sl["group_auto"] = True
                        sl["lib_key"] = str(sbslot.get("key") or "")
                        if pid:
                            sl["project_id"] = pid
                        if fid:
                            sl["asset_folder_id"] = fid
                        libtv_slots.note_group_created(
                            slot, str(sbslot.get("key") or ""), gid, desired)
                        _backup(st, sp)
                        _jwrite(sp, sl)
                        g_new += 1
                    except Exception:
                        g_err += 1
                    continue
                g_err += 1

            g_orphan = len(libtv_slots.orphan_groups(slot))
            if changed and not dry:
                _backup(st, path)
                _jwrite(path, lib)
            report.append({"id": sid, "name": name,
                           "project": proj_action, "folder": folder_action,
                           "move": move_action, "rename": "exists",
                           "adopted": adopted,
                           "slot_key": slot.get("key") or "",
                           "project_id": pid, "asset_folder_id": fid,
                           "groups_new": g_new, "groups_existing": g_exist,
                           "groups_reused": g_exist,
                           "groups_reused_orphan": g_reused_orphan,
                           "groups_renamed": 0,
                           "groups_orphan": g_orphan,
                           "groups_stale": g_stale,
                           "groups_bind_errors": g_err,
                           "groups_planned": g_plan, "groups_errors": g_err})

        if not dry and not container_only:
            map_path = st.root / "units" / unit / "libtv-map.json"
            if map_path.exists():
                _backup(st, map_path)
            libtv_slots.save(st.root, unit, mapd)
            st.invalidate(["scenes", "storyboards", "assets"])
            st._gate_cache = (0.0, None)
        orphan_groups = []
        for sid, slot in scene_slots.items():
            for sb in libtv_slots.orphan_groups(slot):
                orphan_groups.append({
                    "scene": sid, "group_id": str(sb.get("group_id") or ""),
                    "name": str(sb.get("group_name") or ""),
                })
        return {
            "ok": True, "dry_run": dry,
            "root": {"project_id": root_pid, "folder_id": root_folder,
                     "name": root_name, "action": root_action},
            "scenes": report,
            "orphan_projects": orphan_projects,
            "orphan_groups": orphan_groups,
            "projects_read_ok": projects_ok,
            "created_projects": sum(1 for r in report if r["project"] == "created"),
            "reused_projects": sum(1 for r in report if r.get("adopted")),
            "created_folders": sum(1 for r in report if r["folder"] == "created"),
            "created_groups": sum(r.get("groups_new", 0) for r in report),
            "reused_groups": sum(r.get("groups_reused", 0) for r in report),
            "reused_orphan_groups": sum(
                r.get("groups_reused_orphan", 0) for r in report),
            "renamed_groups": 0,
            "moved_projects": sum(1 for r in report if r.get("move") == "moved"),
            "errors": [r for r in report
                       if "error" in (r["project"], r["folder"], r.get("move"),
                                      r.get("rename"))
                       or r.get("groups_errors") or r.get("groups_bind_errors")],
        }

    def _storyboard_stills_plan(self, snap, sb):
        """每镜首帧：算出要挂哪些资产、每镜的静帧提示词、建/改哪些图节点。"""
        base = self._storyboard_wire_plan(snap, sb, method="image")
        base.pop("_scene", None)
        uid = snap["meta"]["unit"]
        doc = _jload(self.state.root / "units" / uid / "prompts" /
                     "prompts.json", {}) or {}
        still_by = {}
        for x in doc.get("stills") or []:
            if isinstance(x, dict) and x.get("shot"):
                still_by[str(x["shot"]).zfill(3)] = str(x.get("prompt") or "")
        shots = {s["id"]: s for s in (snap.get("shots") or [])}
        rows = []
        errors = []
        for sid in (sb.get("shots") or []):
            sid = str(sid).zfill(3)
            prompt = still_by.get(sid) or \
                str((shots.get(sid) or {}).get("visual") or "")
            if not prompt.strip():
                errors.append("%s：没有静帧提示词" % sid)
            rows.append({"shot": sid, "prompt": prompt.strip()})
        pj = _jload(self.state.root / "project.json", {}) or {}
        lcfg = ((pj.get("adapters") or {}).get("libtv") or {})
        return {
            "ok": not errors,
            "sb": sb["id"],
            "scene": sb.get("scene") or "",
            "name": sb.get("name") or "",
            "shots": rows,
            "errors": errors,
            "refs": base.get("refs") or [],
            "uploads": base.get("uploads") or [],
            "missing": base.get("missing") or [],
            "project_id": base.get("project_id") or "",
            "group_id": base.get("group_id") or "",
            "group_name": base.get("group_name") or "",
            "asset_folder_id": base.get("asset_folder_id") or 0,
            "needs_sync": base.get("needs_sync"),
            "model": str(lcfg.get("image_model") or "z-image"),
            "quality": str(lcfg.get("image_quality") or "1K"),
            "ratio": str(lcfg.get("image_ratio")
                         or lcfg.get("video_ratio") or "16:9"),
        }

    def _storyboard_wire_plan(self, snap, sb, method="auto",
                              first_frame="", last_frame=""):
        """算分镜要连哪些资产、传哪些图、建/改哪个视频节点。不发写请求。"""
        st = self.state
        scene = next((x for x in (snap.get("scenes") or [])
                      if x["id"] == sb.get("scene")), None)
        sl = _jload(st.root / sb["libtv_path"], {}) if sb.get("libtv_path") else {}
        sl = sl if isinstance(sl, dict) else {}
        lib = sb.get("libtv") or {}
        old_refs = [r for r in (sl.get("refs") or []) if isinstance(r, dict)]
        sdir = self._scene_dir(snap["meta"]["unit"], sb.get("scene") or "")
        scene_lib = _jload(sdir / "libtv.json", {}) if sdir else {}
        scene_lib = scene_lib if isinstance(scene_lib, dict) else {}
        scene_refs = [r for r in (scene_lib.get("refs") or [])
                      if isinstance(r, dict)]
        by_key = {str(r.get("key") or ""): str(r.get("node_id") or "")
                  for r in old_refs + scene_refs}
        by_file = {str(r.get("file") or ""): str(r.get("node_id") or "")
                   for r in old_refs + scene_refs}
        refs, uploads, missing, ignored = [], [], [], []
        seen = set()
        for r in (sb.get("asset_refs") or []):
            aid = str(r.get("id") or "").strip()
            if not aid:
                continue
            vid = str(r.get("variant") or "").strip()
            key = aid if not vid else "%s-%s" % (aid, vid)
            if key in seen:
                continue
            seen.add(key)
            image = str(r.get("image") or "").strip()
            nid = by_key.get(key) or by_file.get(image) or ""
            absimg = (st.root / image) if image else None
            kind = {
                "cast": "character", "scene": "scene", "prop": "prop",
                "style": "style", "brand": "brand",
            }.get(str(r.get("role") or ""), str(r.get("type") or "other"))
            item = {
                "key": key, "label": str(r.get("name") or aid) +
                ((" · " + str(r.get("variant_label"))) if r.get("variant_label") else ""),
                "file": image, "kind": kind,
                "asset_id": aid, "variant": vid,
            }
            if kind in ("style", "brand"):
                ignored.append({"key": key, "label": item["label"],
                                "reason": "风格 / 品牌只进提示词，不挂视频参考图"})
                continue
            if nid:
                item["node_id"] = nid
                refs.append(item)
            elif image and absimg and absimg.is_file():
                refs.append(item)
                uploads.append(item)
            else:
                missing.append({"key": key, "label": item["label"],
                                "asset_id": aid, "reason": "没有可用主图"})
        # 首尾帧候选：只认分镜自己的 stills / renders，线稿关键帧不能当首尾帧
        frame_candidates, seen_frames = [], set()
        for m in (sb.get("media") or []):
            p = str(m.get("path") or "").replace("\\", "/")
            if not p or m.get("kind") != "image":
                continue
            low = p.lower()
            if "/keyframes/" in low or low.endswith("/keyframes"):
                continue
            if Path(p).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            if p in seen_frames:
                continue
            seen_frames.add(p)
            frame_candidates.append({
                "file": p, "label": m.get("name") or Path(p).name})
        video = sl.get("video") if isinstance(sl.get("video"), dict) else {}
        old_frames = video.get("frames") if isinstance(video.get("frames"), dict) else {}

        def frame_item(role, path):
            path = str(path or "").replace("\\", "/").strip()
            if not path:
                return None
            old = old_frames.get(role) if isinstance(old_frames, dict) else {}
            nid = ""
            if isinstance(old, dict) and old.get("file") == path:
                nid = str(old.get("node_id") or "")
            return {"role": role, "file": path,
                    "label": Path(path).name, "node_id": nid}

        selected_frames = {
            "first": frame_item("first", first_frame),
            "last": frame_item("last", last_frame),
        }
        frame_files = {f["file"] for f in frame_candidates}
        first_f, last_f = (selected_frames["first"] or {}), (selected_frames["last"] or {})
        ff, lf = first_f.get("file") or "", last_f.get("file") or ""
        if len(frame_candidates) < 2:
            frame_error = "这个分镜还没有两张实景首尾帧（stills/renders）"
        elif not ff or not lf:
            frame_error = "先选首帧和尾帧"
        elif ff == lf:
            frame_error = "首帧和尾帧不能是同一张"
        elif ff not in frame_files or lf not in frame_files:
            frame_error = "首尾帧不在这个分镜的实景图里"
        else:
            frame_error = ""
        frames_ready = not frame_error
        raw_method = str(method or "auto").strip().lower()
        mode = {"text2video": "text", "image2video": "image",
                "frames2video": "frames"}.get(raw_method, raw_method)
        if mode not in ("auto", "text", "image", "frames"):
            mode = "auto"
        recommended = "image" if refs else "text"
        recommended_reason = (
            "有 %d 张可用参考图（%s），先用多参考图" % (
                len(refs), "、".join(r["label"] for r in refs[:4]))
            if refs else "没有可用参考图，先用纯文本跑")
        if mode == "auto":
            mode = recommended
        methods = {
            "text": {"available": True, "reason": ""},
            "image": {"available": bool(refs),
                      "reason": "" if refs else "没有可用参考图，补图或改用纯文本"},
            "frames": {"available": frames_ready, "reason": frame_error},
        }
        if mode == "frames" and not frames_ready:
            method_ready, method_error = False, frame_error
        elif mode == "image" and not refs:
            method_ready, method_error = False, "没有可用参考图"
        else:
            method_ready, method_error = True, ""
        prompt_orig = str(sb.get("prompt") or "").strip()
        ref_block = str(sb.get("prompt_ref_block") or "").strip() or \
            snap_mod.material_ref_block(sb.get("asset_refs") or [])
        if mode == "image":
            # 智能参考：按上传顺序写 @Image 锚定段；用户自己写过引用就不覆盖。
            prompt = str(sb.get("prompt_final") or "").strip() or \
                snap_mod.apply_ref_block(prompt_orig, ref_block)
        else:
            prompt = snap_mod.apply_ref_block(prompt_orig, "")
        ref_block_used = ref_block if (mode == "image" and
                                      snap_mod.REF_BLOCK_START in prompt) else ""
        ph = snap_mod.prompt_hash(prompt)
        rh = snap_mod.refs_hash(refs)
        node_id = str(video.get("node_id") or sl.get("node_id") or "")
        if not node_id:
            node_id = next((str(n.get("node_id") or n.get("nodeId") or "")
                            for n in (sl.get("nodes") or [])
                            if isinstance(n, dict) and
                            (n.get("node_id") or n.get("nodeId"))), "")
        pj = _jload(st.root / "project.json", {}) or {}
        lcfg = ((pj.get("adapters") or {}).get("libtv") or {})
        mode_key = {"image": str(lcfg.get("video_mode_image") or "mixed2video"),
                    "frames": "frames2video", "text": "text2video"}[mode]
        settings = dict(video.get("settings") or {})
        # modeType 由 LibTV 按实际参考边派生；作为入参传会被拒
        # （Remove gateway-managed fields from params: modeType）。
        settings.pop("modeType", None)
        settings.update({
            "resolution": str(lcfg.get("video_resolution") or "480p"),
            "ratio": str(lcfg.get("video_ratio") or "16:9"),
            "duration": min(30, max(4, int(math.ceil(float(sb.get("sec") or 4))))),
            "enableSound": str(lcfg.get("video_sound") or "off"),
            "count": 1,
        })
        gate = sb.get("wire_gate") if isinstance(sb.get("wire_gate"), dict) else \
            snap_mod._wire_gate(scene, sb)
        assets_ready = bool(gate.get("ready"))
        group_name = str(lib.get("group_name") or
                         ("%s %s" % (sb["id"], sb.get("name") or "")))[:180]
        pid = str(lib.get("project_id") or "")
        gid = str(lib.get("group_id") or "")
        frame_uploads = [f for f in (selected_frames["first"],
                                     selected_frames["last"])
                         if f and not f.get("node_id")]
        return {
            "ok": True, "dry_run": True, "sb": sb["id"],
            "scene": sb.get("scene") or "", "name": sb.get("name") or "",
            "project_id": pid, "group_id": gid, "group_name": group_name,
            "asset_folder_id": int(lib.get("asset_folder_id") or 0),
            "node_id": node_id,
            "action": "update" if node_id else "create",
            "needs_sync": bool(not pid or not gid),
            "prompt_len": len(prompt), "prompt_hash": ph, "refs_hash": rh,
            "prompt_orig": prompt_orig, "prompt_final": prompt,
            "prompt_ref_block": ref_block_used,
            "mode_type": mode_key,
            "assets_ready": assets_ready,
            "assets_required": int(gate.get("required") or 0),
            "assets_blockers": gate.get("blockers") or [],
            "refs": refs, "uploads": uploads, "missing": missing,
            "ignored": ignored,
            "method": mode, "requested_method": raw_method,
            "old_method": str(video.get("method") or ""),
            "recommended_method": recommended,
            "recommended_reason": recommended_reason,
            "methods": methods, "method_ready": method_ready,
            "method_error": method_error,
            "frames": selected_frames, "frame_candidates": frame_candidates,
            "frame_uploads": frame_uploads, "frames_ready": frames_ready,
            "frame_error": frame_error,
            "text_only": mode == "text",
            "model": str(video.get("model") or lcfg.get("video_model")
                         or "star-video2.5-draft"),
            "settings": settings,
            "existing_output": str(video.get("file") or ""),
            "needs_sync_reason": (
                "缺场项目" if not pid else ("缺分镜 group" if not gid else "")),
            "_scene": scene,
        }

    def _group_name(self, client, pid, gid, fallback):
        """按 group_id 取真实组名，兼容用户自己建的组。"""
        if not gid:
            return fallback
        try:
            r = client.call("read_project", {
                "projectId": pid, "view": "groups"}, timeout=30)
            for g in (r.get("groups") or []):
                if str(g.get("nodeId") or g.get("id") or "") == gid:
                    return str(g.get("name") or fallback)
        except Exception:
            pass
        return fallback

    def _remember_scene_refs(self, st, uid, sb, refs):
        """把已上传参考图的 node_id 缓存到场 libtv.json。

        上传和建视频节点之间失败时，下一轮才知道这些图已经在画布上，不会重传。
        """
        sdir = self._scene_dir(uid, sb.get("scene") or "")
        if not sdir:
            return
        scp = sdir / "libtv.json"
        try:
            scd = _jload(scp, {}) or {}
            if not isinstance(scd, dict):
                scd = {}
            rows = [r for r in (scd.get("refs") or []) if isinstance(r, dict)]
            changed = False
            for r in refs:
                nid = str(r.get("node_id") or "")
                if not nid:
                    continue
                hit = next((x for x in rows if
                            (r.get("key") and x.get("key") == r.get("key"))
                            or (r.get("file") and x.get("file") == r.get("file"))),
                           None)
                if hit is None:
                    rows.append({
                        "key": r.get("key") or r.get("asset_id") or "",
                        "label": r.get("label") or "",
                        "file": r.get("file") or "",
                        "kind": r.get("kind") or "",
                        "asset_id": r.get("asset_id") or "",
                        "variant": r.get("variant") or "",
                        "node_id": nid,
                    })
                    changed = True
                elif hit.get("node_id") != nid:
                    hit["node_id"] = nid
                    changed = True
            if changed:
                scd["refs"] = rows
                _backup(st, scp)
                _jwrite(scp, scd)
        except Exception:
            pass

    def _remember_video_node(self, st, sb, node_id, old_node_id=""):
        """节点一建好就先落盘 ID，别等整条布线成功。

        上次建完节点后若在建组/写文件前失败，本地没有 node_id，重试会用同一个
        显示名再 create_node → NODE_NAME_CONFLICT。先记下来，重试就走 update。
        """
        rel = str(sb.get("libtv_path") or "")
        if not rel or not node_id:
            return
        p = st.root / rel
        try:
            sl = _jload(p, {}) or {}
            if not isinstance(sl, dict):
                sl = {}
            video = sl.get("video") if isinstance(sl.get("video"), dict) else {}
            video["node_id"] = str(node_id)
            video["wired_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if old_node_id and str(old_node_id) != str(node_id):
                video["previous_node_id"] = str(old_node_id)
            video.setdefault("status", "wired")
            sl["video"] = video
            _backup(st, p)
            _jwrite(p, sl)
        except Exception:
            pass

    def _bind_group_verified(self, client, pid, gname, node_id, keybase):
        """把节点 bind 进 group，并回读校验；LibTV 偶发写入成功但未落组。"""
        try:
            g0 = client.call("manage_group", {
                "action": "read", "projectId": pid, "name": gname,
            }, timeout=30)
            if node_id in (g0.get("childNodeIds") or []):
                return ""
        except Exception:
            pass
        err = ""
        for attempt in (1, 2):
            try:
                pr = client.call("read_project", {
                    "projectId": pid, "view": "summary"}, timeout=30)
                rev = str((pr.get("revision") or {}).get("token") or "")
                args = {
                    "action": "bind", "projectId": pid, "name": gname,
                    "nodes": [node_id],
                    "idempotencyKey": libtv_mcp.safe_key(
                        "%s-%d" % (keybase, attempt)),
                }
                if rev:
                    args["expectedRevision"] = rev
                client.call("manage_group", args, timeout=30)
            except Exception as e:
                msg = str(e)
                if _revision_conflict(e) and attempt == 1:
                    continue
                return msg[:300]
            try:
                g = client.call("manage_group", {
                    "action": "read", "projectId": pid, "name": gname,
                }, timeout=30)
                if node_id in (g.get("childNodeIds") or []):
                    return ""
                err = "绑定后节点仍不在 group：%s" % node_id
            except Exception as e:
                err = str(e)[:300]
            time.sleep(0.4)
        return err

    def _storyboard_wire(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sb_id = str(body.get("sb") or "").strip()
        dry = bool(body.get("dry_run", True))
        method = str(body.get("method") or "auto").strip().lower()
        first_frame = str(body.get("first_frame") or "").strip()
        last_frame = str(body.get("last_frame") or "").strip()
        if not sb_id:
            raise ValueError("没有分镜号")
        if not dry and not body.get("confirm"):
            raise ValueError("正式布线需要 confirm=true")
        snap = st.snapshot(uid)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == sb_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % sb_id)
        if not str(sb.get("prompt") or "").strip():
            raise ValueError("这个分镜还没有提示词。先在页面下方「分镜提示词」里写一段，再回来布线。")
        plan = self._storyboard_wire_plan(
            snap, sb, method, first_frame, last_frame)
        if dry:
            plan.pop("_scene", None)
            return self._json(200, plan)

        if not plan.get("assets_ready"):
            blockers = plan.get("assets_blockers") or []
            names = "、".join(
                "%s（%s）" % (b.get("name") or b.get("id"),
                            b.get("build_state_label") or "缺图")
                for b in blockers[:6])
            more = "" if len(blockers) <= 6 else " 等 %d 个" % len(blockers)
            raise ValueError(
                "这一场还有引用资产没出图：%s%s。先到资产库把它们的生成画布"
                "「布线并生成」，回来点「同步」，资产完成后才能布线分镜。" % (
                    names, more))

        if plan["needs_sync"]:
            res = self._libtv_ensure({
                "dry_run": False, "confirm": True,
                "scenes": [sb.get("scene") or ""]})
            if res.get("errors"):
                raise ValueError("本场 LibTV 结构没建好：%s" % (
                    res["errors"][0].get("reason") or ""))
            snap = st.snapshot(uid, fresh=True)
            sb = next((x for x in (snap.get("storyboards") or [])
                       if x["id"] == sb_id), None)
            if not sb:
                raise ValueError("同步本场后仍找不到分镜：%s" % sb_id)
            plan = self._storyboard_wire_plan(
                snap, sb, method, first_frame, last_frame)

        if not plan.get("method_ready"):
            raise ValueError(plan.get("method_error") or "这个手法还缺输入")
        pid = plan["project_id"]
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        fid = int(plan.get("asset_folder_id") or 0)
        refs = plan["refs"]
        upload_errors = []
        uploaded = 0
        method = plan["method"]
        if method == "image":
            for i, r in enumerate(refs):
                if r.get("node_id"):
                    continue
                absimg = st.root / r["file"] if r.get("file") else None
                if not absimg or not absimg.is_file():
                    continue
                try:
                    nid = client.upload_image(
                        pid, absimg, r.get("label") or r["key"],
                        x=-1200, y=600 + i * 420)
                    r["node_id"] = nid
                    uploaded += 1
                    if fid:
                        try:
                            client.call("save_asset_from_node", {
                                "nodeId": nid, "folderId": fid,
                                "name": (r.get("label") or r["key"])[:180],
                                "idempotencyKey": libtv_mcp.safe_key(
                                    "wb-va-%s-%s-%s" % (uid, sb_id, r["key"])),
                            }, timeout=60)
                        except Exception:
                            pass
                except Exception as e:
                    upload_errors.append("%s：%s" % (
                        r.get("label") or r["key"], str(e)[:160]))
        frame_uploaded = 0
        frames = plan.get("frames") or {}
        if method == "frames":
            for i, role in enumerate(("first", "last")):
                f = frames.get(role)
                if not f:
                    continue
                if f.get("node_id"):
                    continue
                absimg = st.root / f["file"] if f.get("file") else None
                if not absimg or not absimg.is_file():
                    raise ValueError("首尾帧文件不存在：%s" % (f.get("file") or ""))
                try:
                    f["node_id"] = client.upload_image(
                        pid, absimg, f.get("label") or role,
                        x=-1200, y=600 + i * 420)
                    frame_uploaded += 1
                except Exception as e:
                    raise ValueError("首尾帧上传失败：%s" % str(e)[:200])
            if not (frames.get("first") or {}).get("node_id") or \
                    not (frames.get("last") or {}).get("node_id"):
                raise ValueError("首尾帧没上传成功")
        # 上传成功就先记回收到的节点 ID：后面建视频节点失败也不会下一轮重传。
        self._remember_scene_refs(st, uid, sb, refs)
        if method == "text":
            left = []
        elif method == "frames":
            left = [(frames.get("first") or {}).get("node_id") or "",
                    (frames.get("last") or {}).get("node_id") or ""]
            left = [x for x in left if x]
        else:
            left = [r["node_id"] for r in refs if r.get("node_id")]
        prompt = str(plan.get("prompt_final") or sb.get("prompt") or "").strip()
        settings = dict(plan["settings"])
        model = plan["model"]
        gname = plan["group_name"]
        gid = plan["group_id"]
        gname = self._group_name(client, pid, gid, gname)
        node_id = plan["node_id"]
        existing_left = []
        existing_mode = ""
        if node_id:
            try:
                cur = client.call("read_node", {
                    "projectId": pid, "node": node_id}, timeout=30)
                nd = cur.get("node") or cur
                params = (nd.get("data") or {}).get("params") or {}
                existing_mode = str(params.get("modeType") or "")
                for x in (params.get("imageList") or []):
                    nid = str(x.get("nodeId") or "") if isinstance(x, dict) else ""
                    if nid:
                        existing_left.append(nid)
                if not existing_left:
                    existing_left = [str(x) for x in
                                     (params.get("imageListOrder") or []) if x]
            except Exception as e:
                msg = str(e)
                if ("not found" in msg.lower() or "找不到" in msg or
                        "NOT_FOUND" in msg):
                    node_id = ""
                else:
                    raise
        existing_method = {"text2video": "text", "image2video": "image",
                           "singleImage2video": "image", "mixed2video": "image",
                           "frames2video": "frames"}.get(
                               existing_mode, str(plan.get("old_method") or ""))
        mode_switch = bool(node_id and existing_method and
                           existing_method != method)
        frame_sig = "|".join(
            (f or {}).get("file") or "" for f in (
                frames.get("first"), frames.get("last")))
        remove_left = [x for x in existing_left if x not in left]
        remove_sig = "" if method == "frames" else "|".join(remove_left)
        digest = hashlib.sha1(
            (prompt + "|" + method + "|" + plan["refs_hash"] + "|" +
             frame_sig + "|" + "|".join(left) + "|" + remove_sig).encode("utf-8")
        ).hexdigest()[:10]
        # 上传素材会改画布 revision；写节点前先读当前 revision，冲突时再读一次重试
        try:
            pr = client.call("read_project", {
                "projectId": pid, "view": "summary"}, timeout=30)
            revision = str((pr.get("revision") or {}).get("token") or "")
        except Exception:
            revision = ""

        def read_revision():
            try:
                pr2 = client.call("read_project", {
                    "projectId": pid, "view": "summary"}, timeout=30)
                return str((pr2.get("revision") or {}).get("token") or "")
            except Exception:
                return ""

        def write_node(args):
            nonlocal revision
            for attempt in (1, 2, 3, 4):
                if attempt == 4:
                    # 连续冲突：最后不带 expectedRevision 再试一次（单用户工作台，
                    # 并发保护不是必需的）。
                    args.pop("expectedRevision", None)
                elif revision:
                    args["expectedRevision"] = revision
                else:
                    args.pop("expectedRevision", None)
                try:
                    return client.call(
                        "update_node" if node_id else "create_node", args,
                        timeout=180)
                except Exception as e:
                    if _revision_conflict(e) and attempt < 4:
                        # 上一次写入/上传会推进 revision；重读快照再来一次。
                        revision = read_revision()
                        continue
                    if node_id and _name_conflict(e) and args.get("newName"):
                        # 已有节点改名撞上了另一个同名节点：保留原名，内容照常更新。
                        retry = dict(args)
                        retry.pop("newName", None)
                        key = str(retry.get("idempotencyKey") or "")
                        if key:
                            retry["idempotencyKey"] = libtv_mcp.safe_key(
                                key + "-noname")
                        args = retry
                        continue
                    raise
            raise RuntimeError("写节点失败：画布 revision 连续冲突")

        def resp_node_id(resp):
            nid = str((resp.get("plan") or {}).get("nodeId") or
                      (resp.get("operationResult") or {}).get("subjectNodeId")
                      or "")
            if not nid:
                created = (((resp.get("operationResult") or {}).get(
                    "affectedNodes") or {}).get("created") or [])
                nid = str(created[0] if created else "")
            return nid

        def adopt(e, name, node_type="video"):
            return _adopt_named_node(client, pid, name, node_type, e)

        old_node_id = node_id
        r = None
        if method == "frames":
            key = libtv_mcp.safe_key("wb-vframes-%s-%s-%s" % (
                uid, sb_id, digest))
            f_first = (frames.get("first") or {}).get("node_id") or ""
            f_last = (frames.get("last") or {}).get("node_id") or ""
            name = ("%s 视频 · 首尾帧" % sb_id)[:180]
            try:
                r = _call_write(client, "generate_video", {
                    "name": name,
                    "prompt": prompt, "model": model,
                    "intent": "interpolate", "startFrameNode": f_first,
                    "endFrameNode": f_last, "params": settings,
                    "projectId": pid, "run": False, "idempotencyKey": key,
                }, timeout=180)
                node_id = resp_node_id(r)
            except Exception as e:
                if not _name_conflict(e):
                    raise
                node_id = adopt(e, name)
                r = write_node({
                    "node": node_id,
                    "idempotencyKey": libtv_mcp.safe_key(
                        "wb-vframes-adopt-%s-%s-%s" % (uid, sb_id, digest)),
                    "projectId": pid, "newName": name, "prompt": prompt,
                    "model": model, "params": settings,
                    "leftEnsure": [x for x in (f_first, f_last) if x],
                    "run": False,
                })
        elif method == "text":
            if node_id and not mode_switch:
                key = libtv_mcp.safe_key("wb-vupdate-%s-%s-%s" % (
                    uid, sb_id, digest))
                r = write_node({
                    "node": node_id, "idempotencyKey": key, "projectId": pid,
                    "newName": ("%s 视频" % sb_id)[:180],
                    "prompt": prompt, "model": model, "params": settings,
                    "run": False,
                })
            else:
                key = libtv_mcp.safe_key("wb-vtext-%s-%s-%s" % (
                    uid, sb_id, digest))
                name = ("%s 视频 · 纯文本" % sb_id)[:180]
                try:
                    r = _call_write(client, "create_node", {
                        "name": name,
                        "nodeType": "video", "idempotencyKey": key,
                        "projectId": pid, "prompt": prompt, "model": model,
                        "params": settings, "run": False, "x": 100, "y": 700,
                    }, timeout=180)
                    node_id = resp_node_id(r)
                except Exception as e:
                    if not _name_conflict(e):
                        raise
                    node_id = adopt(e, name)
                    r = write_node({
                        "node": node_id,
                        "idempotencyKey": libtv_mcp.safe_key(
                            "wb-vtext-adopt-%s-%s-%s" % (
                                uid, sb_id, digest)),
                        "projectId": pid, "newName": name, "prompt": prompt,
                        "model": model, "params": settings, "run": False,
                    })
        else:
            if node_id and not mode_switch:
                # leftRemove 与 leftEnsure 同时给会互相覆盖；先移除，再补齐。
                if remove_left:
                    key = libtv_mcp.safe_key("wb-vremove-%s-%s-%s" % (
                        uid, sb_id, digest))
                    r = write_node({
                        "node": node_id, "idempotencyKey": key,
                        "projectId": pid,
                        "newName": ("%s 视频" % sb_id)[:180],
                        "prompt": prompt, "model": model, "params": settings,
                        "leftRemove": remove_left, "run": False,
                    })
                if left:
                    key = libtv_mcp.safe_key("wb-vupdate-%s-%s-%s" % (
                        uid, sb_id, digest))
                    r = write_node({
                        "node": node_id, "idempotencyKey": key,
                        "projectId": pid,
                        "newName": ("%s 视频" % sb_id)[:180],
                        "prompt": prompt, "model": model, "params": settings,
                        "leftEnsure": left, "run": False,
                    })
            else:
                key = libtv_mcp.safe_key("wb-vimage-%s-%s-%s" % (
                    uid, sb_id, digest))
                name = ("%s 视频" % sb_id)[:180]
                try:
                    r = _call_write(client, "create_node", {
                        "name": name, "nodeType": "video",
                        "idempotencyKey": key, "projectId": pid,
                        "prompt": prompt, "model": model, "params": settings,
                        "left": left, "run": False, "x": 100, "y": 700,
                    }, timeout=180)
                    node_id = resp_node_id(r)
                except Exception as e:
                    if not _name_conflict(e):
                        raise
                    node_id = adopt(e, name)
                    upd = {"node": node_id,
                           "idempotencyKey": libtv_mcp.safe_key(
                               "wb-vimage-adopt-%s-%s-%s" % (
                                   uid, sb_id, digest)),
                           "projectId": pid, "newName": name,
                           "prompt": prompt, "model": model,
                           "params": settings, "run": False}
                    if left:
                        upd["leftEnsure"] = left
                    r = write_node(upd)
        mode_switched = bool(old_node_id and node_id and old_node_id != node_id)
        if not node_id:
            raise ValueError("LibTV 没返回视频节点 ID：%s" % json.dumps(
                r or {}, ensure_ascii=False)[:300])
        # 先落盘节点 ID：后面建组/读回失败时，下次布线才会走 update，
        # 不会因为本地没有 ID 再建一个同名节点。
        self._remember_video_node(st, sb, node_id, old_node_id)
        group_error = ""
        if gid or gname:
            group_error = self._bind_group_verified(
                client, pid, gname, node_id,
                "wb-vbind-%s-%s-%s" % (uid, sb_id, node_id))

        # 素材节点缓存到场 libtv.json，下一分镜复用，不重复上传同一张图
        self._remember_scene_refs(st, uid, sb, refs)

        sp = st.root / sb["libtv_path"]
        sl = _jload(sp, {}) or {}
        if not isinstance(sl, dict):
            sl = {}
        old_video = sl.get("video") if isinstance(sl.get("video"), dict) else {}
        video = dict(old_video)
        output_ok = bool(
            old_video.get("file") and
            old_video.get("output_prompt_hash") == plan["prompt_hash"] and
            old_video.get("output_refs_hash") == plan["refs_hash"] and
            (not old_video.get("output_method") or
             old_video.get("output_method") == method))
        video.update({
            "node_id": node_id,
            "status": str(old_video.get("status") or "wired") if output_ok else "wired",
            "model": model,
            "method": method,
            "mode_type": str(plan.get("mode_type") or ""),
            "frames": {
                role: {k: frames[role].get(k) for k in
                       ("file", "label", "node_id")}
                for role in ("first", "last")
                if method == "frames" and frames.get(role)
            },
            "frame_sig": frame_sig if method == "frames" else "",
            "settings": settings, "prompt_hash": plan["prompt_hash"],
            "refs_hash": plan["refs_hash"],
            "rev": int(old_video.get("rev") or 0),
            "task_id": str(old_video.get("task_id") or ""),
            "file": str(old_video.get("file") or ""),
            "url": str(old_video.get("url") or ""),
            "output_prompt_hash": str(old_video.get("output_prompt_hash") or ""),
            "output_refs_hash": str(old_video.get("output_refs_hash") or ""),
            "wired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if mode_switched:
            video["previous_node_id"] = old_node_id
        if group_error:
            video["group_error"] = group_error
        else:
            video.pop("group_error", None)
        sl["video"] = video
        sl["refs"] = [{k: v for k, v in r.items() if k in (
            "key", "label", "file", "kind", "asset_id", "variant", "node_id")}
            for r in refs]
        sl["project_id"] = pid
        if gid:
            sl["group_id"] = gid
        sl["group_name"] = gname
        auto_gname = ("%s %s" % (sb_id, sb.get("name") or ""))[:180]
        if "group_auto" not in sl:
            sl["group_auto"] = (gname == auto_gname)
        if fid:
            sl["asset_folder_id"] = fid
        sl["model"] = model
        sl["status"] = "已布线"
        nodes = [n for n in (sl.get("nodes") or []) if isinstance(n, dict)]
        node = dict(nodes[0]) if nodes else {
            "id": "n1", "shots": list(sb.get("shots") or []), "prompt": prompt}
        node_refs = ([r["key"] for r in refs] if method == "image" else
                     (["frame:first", "frame:last"] if method == "frames" else []))
        node.update({
            "node_id": node_id, "prompt": prompt, "method": method,
            "refs": node_refs, "status": "已布线",
        })
        sl["nodes"] = [node] + nodes[1:]
        _backup(st, sp)
        _jwrite(sp, sl)
        st.invalidate(["storyboards", "scenes", "media", "shots"])
        return self._json(200, {
            "ok": True, "dry_run": False, "sb": sb_id,
            "node_id": node_id, "group_id": gid, "group_name": gname,
            "method": method, "frames": frames if method == "frames" else {},
            "refs": len(refs), "uploads": uploaded,
            "frame_uploads": frame_uploaded if method == "frames" else 0,
            "left": len(left), "missing": plan["missing"],
            "upload_errors": upload_errors, "group_error": group_error,
            "text_only": method == "text", "model": model, "settings": settings,
            "message": "已布线；去 LibTV 点生成，完成后回来同步结果",
        })

    @staticmethod
    def _video_urls(node):
        data = (node or {}).get("data") or {}
        urls = []

        def add(u):
            if not u or not isinstance(u, str):
                return
            if not re.match(r"^https?://", u, re.I):
                u = "https://libtv-res.liblib.art/" + u.lstrip("/")
            if u not in urls:
                urls.append(u)

        raw = data.get("url") or []
        if isinstance(raw, str):
            raw = [raw]
        for u in raw if isinstance(raw, list) else []:
            add(u)
        tr = data.get("taskResult")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except Exception:
                tr = {}
        for bucket in (data, tr if isinstance(tr, dict) else {}):
            for v in (bucket.get("videos") or []):
                if isinstance(v, dict):
                    add(v.get("previewPath") or v.get("originalPath") or
                        v.get("storagePath"))
        # 只留视频；没有扩展名时也保留，下载时再判断
        out = [u for u in urls if re.search(
            r"\.(mp4|webm|mov|m4v)(\?|#|$)", u, re.I)]
        return out or urls

    # ---------- 图谱 ----------
    def _graph_payload(self, uid, data=None, only=""):
        st = self.state
        raw = data if isinstance(data, dict) else graph_mod.load(st.root, uid)
        g = graph_mod.normalize(raw, uid)
        res = graph_mod.plan(
            g, exists=lambda rel: (st.root / rel).is_file(), only=only)
        return g, res

    @staticmethod
    def _graph_current_hash(seg, n):
        """节点按当前本地内容算出的指纹；和上次布线时的 input_hash 比。"""
        by = {x["id"]: x for x in (seg.get("nodes") or [])}
        refs = [_node_key(by[r]) or r for r in (n.get("refs") or []) if r in by]
        return graph_mod.input_hash(n, refs)

    def _graph_annotate_push(self, g):
        """只给读接口加一个临时字段：本地改过、但还没推到 LibTV。

        不写盘：graph.json 里只留布线时记下的 input_hash，避免把 UI 状态
        混进内容文件。
        """
        for seg in g.get("segments") or []:
            for n in seg.get("nodes") or []:
                # 上传 / 外部参考没有提示词可推；它们换图要重新布线。
                promptable = str(n.get("role") or "") not in (
                    "reference", "reference_video")
                n["needs_push"] = bool(
                    promptable and _node_key(n) and n.get("input_hash")
                    and self._graph_current_hash(seg, n) != n["input_hash"])
                n["libtv_wired"] = bool(_node_key(n))
                rem = n.get("remote") if isinstance(n.get("remote"), dict) else {}
                if rem:
                    remote_prompt = str(rem.get("prompt") or "")
                    n["remote_status"] = str(rem.get("status") or "")
                    n["remote_prompt"] = remote_prompt
                    n["remote_params"] = rem.get("params") or {}
                    n["remote_model"] = str(rem.get("model") or "")
                    n["remote_style"] = rem.get("style") or []
                    n["remote_at"] = str(rem.get("at") or "")
                    n["remote_changed"] = bool(
                        remote_prompt.strip()
                        and remote_prompt.strip() != str(n.get("prompt") or "").strip())
        return g

    def _graph_read(self, q):
        uid = self._unit(q)
        g, res = self._graph_payload(uid)
        self._graph_annotate_push(g)
        p = graph_mod.path(self.state.root, uid)
        return self._json(200, {
            "ok": True, "unit": uid, "path": graph_mod.rel_path(uid),
            "exists": p.is_file(), "graph": g, "plan": res,
        })

    def _graph_save(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        data = body.get("graph")
        if not isinstance(data, dict):
            raise ValueError("没有 graph 数据")
        g, res = self._graph_payload(uid, data)
        # needs_push / libtv_wired 只是读接口的临时视图，不写进内容文件。
        for seg in g.get("segments") or []:
            for n in seg.get("nodes") or []:
                n.pop("needs_push", None)
                n.pop("libtv_wired", None)
        p = graph_mod.path(st.root, uid)
        _backup(st, p)
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "shots", "prompts"])
        return self._json(200, {
            "ok": True, "unit": uid, "path": graph_mod.rel_path(uid),
            "graph": g, "plan": res,
        })

    def _graph_plan(self, body):
        uid = str(body.get("unit") or self.state.unit)
        g, res = self._graph_payload(
            uid, body.get("graph"), str(body.get("segment") or "").strip())
        res["graph"] = g
        return self._json(200, res)

    def _graph_write(self, body, fn):
        """同一单元的 graph 写操作串行化，避免 load→save 互相覆盖。"""
        uid = str(body.get("unit") or self.state.unit)
        with _graph_lock(uid):
            return fn(body)

    def _graph_sb(self, uid, sid):
        snap = self.state.snapshot(uid)
        return next((x for x in (snap.get("storyboards") or [])
                     if x.get("id") == sid), None)

    def _graph_find(self, g, sid, nid):
        seg = next((x for x in (g.get("segments") or [])
                    if x.get("id") == sid), None)
        if not seg:
            raise ValueError("找不到段：%s" % sid)
        node = next((x for x in (seg.get("nodes") or [])
                     if x.get("id") == nid), None)
        if not node:
            raise ValueError("找不到节点：%s" % nid)
        return seg, node

    def _graph_bind(self, body):
        """把外部素材绑定到 LibTV 节点：只写 node_id，不接收素材文件。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        libtv_node = str(body.get("node_id") or "").strip()
        if not sid or not nid or not libtv_node:
            raise ValueError("要 segment / node / node_id")
        sb = self._graph_sb(uid, sid)
        if not sb:
            raise ValueError("找不到分镜：%s" % sid)
        lib = sb.get("libtv") or {}
        pid = str(lib.get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目，先连线")
        client = libtv_mcp.LibTV(st.root)
        try:
            r = client.call("read_node", {"projectId": pid, "node": libtv_node},
                            timeout=30)
        except Exception as e:
            raise ValueError("LibTV 读不到这个节点：%s" % str(e)[:200])
        node = r.get("node") or r
        ntype = str(node.get("type") or "")
        data = node.get("data") or {}
        name = str(data.get("name") or "").strip()
        data = graph_mod.load(st.root, uid)
        g = graph_mod.normalize(data, uid)
        seg, n = self._graph_find(g, sid, nid)
        kind = str((n.get("external") or {}).get("kind") or n.get("type") or "image")
        if kind not in ("image", "video"):
            kind = "image"
        if ntype and ntype != kind:
            raise ValueError("节点类型不匹配：这个槽位要%s，选的是%s" % (
                "图片" if kind == "image" else "视频",
                "图片" if ntype == "image" else "视频" if ntype == "video" else ntype))
        ext = dict(n.get("external") or {})
        ext.update({"kind": kind, "node_id": libtv_node,
                    "name": name, "label": ext.get("label") or name,
                    "bound_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "missing": False})
        n["external"] = ext
        n["node_id"] = libtv_node
        n["status"] = "wired"
        _backup(st, graph_mod.path(st.root, uid))
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "shots"])
        space = lib.get("project_space_id") or lib.get("folder_id") or 0
        return self._json(200, {
            "ok": True, "unit": uid, "segment": sid, "node": n,
            "url": snap_mod._editor_url(pid, space, libtv_node),
        })

    def _graph_unbind(self, body):
        """解除绑定：只删引用，不动 LibTV 节点，保留历史版本。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        if not sid or not nid:
            raise ValueError("要 segment / node")
        data = graph_mod.load(st.root, uid)
        g = graph_mod.normalize(data, uid)
        seg, n = self._graph_find(g, sid, nid)
        ext = dict(n.get("external") or {})
        ext["node_id"] = ""
        ext["name"] = ""
        ext["bound_at"] = ""
        n["external"] = ext
        n["node_id"] = ""
        if n.get("status") not in graph_mod.DONE:
            n["status"] = "planned"
        _backup(st, graph_mod.path(st.root, uid))
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "shots"])
        return self._json(200, {"ok": True, "unit": uid, "node": n})

    def _graph_version(self, body):
        """切换当前版本：只改指针，不重新布线、不重新生成。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        try:
            rev = int(body.get("rev") or 0)
        except (TypeError, ValueError):
            raise ValueError("rev 不合法")
        data = graph_mod.load(st.root, uid)
        g = graph_mod.normalize(data, uid)
        seg, n = self._graph_find(g, sid, nid)
        hit = next((h for h in (n.get("history") or [])
                    if int(h.get("rev") or 0) == rev), None)
        if not hit:
            raise ValueError("这个节点没有 v%d" % rev)
        n["current_rev"] = rev
        n["rev"] = rev
        for k in ("url", "file", "input_hash"):
            if hit.get(k) or k in ("url", "file"):
                n[k] = hit.get(k) or ""
        if hit.get("status"):
            n["status"] = hit["status"]
        _backup(st, graph_mod.path(st.root, uid))
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "shots"])
        return self._json(200, {"ok": True, "unit": uid, "node": n})

    def _libtv_project_nodes(self, q):
        """列当前 LibTV 画布的图片 / 视频节点，供绑定 / 导入使用。"""
        st = self.state
        uid = self._unit(q)
        pid = str((q.get("project") or [""])[0]).strip()
        sid = str((q.get("segment") or [""])[0]).strip()
        group = str((q.get("group") or [""])[0]).strip()
        sb = self._graph_sb(uid, sid) if sid else None
        if not pid and sb:
            lib = sb.get("libtv") or {}
            pid = str(lib.get("project_id") or "")
            group = group or str(lib.get("group_id") or "")
        if not pid:
            for x in (st.snapshot(uid).get("storyboards") or []):
                lib = x.get("libtv") or {}
                if lib.get("project_id"):
                    pid = str(lib.get("project_id"))
                    break
        if not pid:
            return self._json(200, {"ok": False,
                                    "error": "这一场还没有 LibTV 项目，先连线"})
        client = libtv_mcp.LibTV(st.root)
        try:
            raw = client.nodes(pid)
        except Exception as e:
            return self._json(200, {"ok": False,
                                    "error": "读不到 LibTV 画布：%s" % str(e)[:200]})
        child = set()
        if group:
            gnode = next((x for x in raw if x.get("id") == group), None)
            child = set(((gnode or {}).get("data") or {}).get("childNodeIds") or [])
        rows = []
        for x in raw:
            t = str(x.get("type") or "")
            if t not in ("image", "video"):
                continue
            if group:
                if x.get("parentId") != group and x.get("id") not in child:
                    continue
            data = x.get("data") or {}
            urls = (self._video_urls(x) if t == "video"
                    else self._image_urls(x))
            rows.append({
                "id": str(x.get("id") or ""),
                "name": str(data.get("name") or "")[:120],
                "type": t,
                "url": urls[0] if urls else "",
                "has_result": bool(urls),
                "group_id": str(x.get("parentId") or ""),
                "prompt": str(data.get("params", {}).get("prompt") or "")[:200],
            })
        rows.sort(key=lambda x: (x["type"], x["name"] or x["id"]))
        return self._json(200, {"ok": True, "project_id": pid,
                                "group_id": group, "nodes": rows})

    @staticmethod
    def _graph_order(nodes):
        """按 refs 拓扑排序：参考 → 图 → 视频。有环就报错。"""
        by = {n["id"]: n for n in nodes}
        done, out, visiting = set(), [], set()

        def visit(n):
            nid = n["id"]
            if nid in done:
                return
            if nid in visiting:
                raise ValueError("图谱有环：%s" % nid)
            visiting.add(nid)
            for r in (n.get("refs") or []):
                if r in by:
                    visit(by[r])
            visiting.discard(nid)
            done.add(nid)
            out.append(n)

        for n in nodes:
            visit(n)
        return out

    def _history(self, q):
        st = self.state
        uid = self._unit(q)
        d = _jload(st.root / "units" / uid / "libtv-history.json", {}) or {}
        rows = d.get("entries") if isinstance(d.get("entries"), list) else []
        return self._json(200, {"ok": True, "unit": uid, "entries": rows})

    def _graph_generate(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        seg_id = str(body.get("segment") or "").strip()
        kind = str(body.get("kind") or "").strip()
        if kind not in ("frame", "video"):
            raise ValueError("kind 只能是 frame / video")
        shots = [str(x).zfill(3) for x in (body.get("shots") or [])
                 if str(x).strip()]
        if not shots:
            raise ValueError("没有选镜头")
        dry = bool(body.get("dry_run", False))
        if not dry and not body.get("confirm_generate"):
            raise ValueError("生成会消耗积分，必须由用户确认（缺 confirm_generate）")
        data = graph_mod.load(st.root, uid)
        if not (data.get("segments") or []):
            raise ValueError("没有 graph.json")
        g = graph_mod.normalize(data, uid)
        seg = next((x for x in g["segments"] if x["id"] == seg_id), None)
        if not seg:
            raise ValueError("找不到段：%s" % seg_id)
        by = {n["id"]: n for n in seg["nodes"]}
        targets = []
        for n in seg["nodes"]:
            if n["role"] in ("reference", "reference_video"):
                continue
            if graph_mod.shot_of(n) not in shots:
                continue
            if kind == "frame" and n["type"] != "image":
                continue
            if kind == "video" and n["role"] != "video":
                continue
            if not n["node_id"]:
                raise ValueError("节点 %s 还没布线，先布线再生成" % n["id"])
            if kind == "video":
                # 视频要等它引用的每一张首帧 / 尾帧都生成完（R2 插值镜有两张）。
                ups = [by[r] for r in (n.get("refs") or []) if r in by]
                frames = [x for x in ups
                          if x["role"] in ("first_frame", "last_frame")]
                missing = [x["id"] for x in frames
                           if x["status"] not in graph_mod.DONE]
                if missing:
                    raise ValueError("首帧 / 尾帧还没生成：%s" % "、".join(missing))
            targets.append(n)
        if not targets:
            raise ValueError("没有可生成的节点")
        cost = {"nodes": len(targets), "shots": len(
            {graph_mod.shot_of(n) for n in targets})}
        # 提示词 / 模型 / 参考图这类硬 blocker 在 dry-run 就拦住，别等扣了积分才失败。
        plan = graph_mod.plan(g, exists=lambda rel: (st.root / rel).is_file(),
                             only=seg_id)
        tids = {n["id"] for n in targets}
        hard = [b for b in (plan.get("blockers") or [])
                if not b.get("defer") and (not b.get("node") or b["node"] in tids)]
        if dry:
            return self._json(200, {
                "ok": not hard, "dry_run": True, "cost": cost,
                "hard_ok": not hard, "hard_blockers": hard,
                "nodes": [{"id": n["id"], "shot": graph_mod.shot_of(n),
                           "node_id": n["node_id"]} for n in targets],
            })
        if hard:
            raise ValueError("图谱还有 %d 个 blocker：%s" % (
                len(hard), hard[0].get("message") or hard[0].get("code")))
        self._need_libtv()
        snap = st.snapshot(uid, fresh=True)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == seg_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % seg_id)
        pid = str((sb.get("libtv") or {}).get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        results = []
        for n in targets:
            refs = [by[r].get("node_id") or r for r in (n.get("refs") or [])
                    if r in by]
            key = libtv_mcp.safe_key("wb-gen-%s-%s-%s-%s" % (
                uid, seg_id, n["id"], graph_mod.input_hash(n, refs)[-12:]))
            try:
                client.call("update_node", {
                    "node": n["node_id"], "projectId": pid,
                    "idempotencyKey": key,
                    "prompt": n["prompt"], "model": n["model"],
                    "params": n.get("params") or {}, "run": True,
                }, timeout=600)
                nd = client.call("read_node", {
                    "node": n["node_id"], "projectId": pid}, timeout=30)
                urls = ((nd.get("node") or {}).get("data") or {}).get("url") or []
                n["status"] = "generated" if urls else "generating"
                # 记下这次真正提交的输入指纹：同步时据此判 stale，
                # 否则「重试 → 同步 → 又 stale」会一直循环。
                n["input_hash"] = graph_mod.input_hash(n, refs)
                results.append({"node": n["id"],
                                "shot": graph_mod.shot_of(n),
                                "node_id": n["node_id"],
                                "status": n["status"]})
            except Exception as e:
                n["status"] = "failed"
                results.append({"node": n["id"], "error": str(e)[:300]})
            graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "media", "shots"])
        return self._json(200, {"ok": True, "dry_run": False,
                                "cost": cost, "results": results})

    def _graph_wire(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        only = str(body.get("segment") or "").strip()
        only_shot = str(body.get("shot") or "").strip()
        if only_shot.isdigit():
            only_shot = only_shot.zfill(3)
        dry = bool(body.get("dry_run", True))
        data = graph_mod.load(st.root, uid)
        if not (data.get("segments") or []):
            raise ValueError("没有 graph.json，或里面没有段")
        g, res = self._graph_payload(uid, data, only)
        # 只有「非 defer 的硬 blocker」才阻止布线；视频节点等首帧是合法中间态，
        # dry-run 不能因此报 ok=false，否则前端的「布线」按钮永远点不动。
        hard = [b for b in res["blockers"] if not b.get("defer")]
        if only_shot:
            # 这一镜的节点 + 它们引用的节点（参考图等）都算「这一镜」，
            # 否则参考图的硬 blocker 会被过滤掉，dry-run 绿了正式布线才失败。
            by_all = {n["id"]: n for seg in g["segments"] for n in seg["nodes"]}
            keep = set()

            def add_node(nid):
                if nid in keep or nid not in by_all:
                    return
                keep.add(nid)
                for r in by_all[nid].get("refs") or []:
                    add_node(r)

            for seg in g["segments"]:
                for n in seg["nodes"]:
                    if graph_mod.shot_of(n) == only_shot:
                        add_node(n["id"])
            hard = [b for b in hard
                    if not b.get("node") or b["node"] in keep]
        if dry:
            return self._json(200, {"ok": res["ok"], "dry_run": True,
                                    "hard_ok": not hard, "hard_blockers": hard,
                                    "plan": res, "graph": g})
        if not body.get("confirm"):
            raise ValueError("正式布线需要 confirm=true")
        if hard:
            b = hard[0]
            raise ValueError("图谱还有 %d 个 blocker：%s" % (
                len(hard), b.get("message") or b.get("code")))
        self._need_libtv()

        snap = st.snapshot(uid, fresh=True)
        sbs = {x["id"]: x for x in (snap.get("storyboards") or [])}
        segs = [s for s in g["segments"] if not only or s["id"] == only]
        missing = [s["id"] for s in segs if s["id"] not in sbs]
        if missing:
            raise ValueError("图谱里的段不在分镜表里：%s" % "、".join(missing))

        need_scenes = sorted({
            str((sbs[s["id"]].get("libtv") or {}).get("scene")
                or sbs[s["id"]].get("scene") or "")
            for s in segs
            if not str((sbs[s["id"]].get("libtv") or {}).get("project_id") or "")
        } - {""})
        if need_scenes:
            self._libtv_ensure({"dry_run": False, "confirm": True,
                                "scenes": need_scenes})
            snap = st.snapshot(uid, fresh=True)
            sbs = {x["id"]: x for x in (snap.get("storyboards") or [])}

        client = libtv_mcp.LibTV(st.root)
        report, errors = [], []
        for seg in segs:
            sid = seg["id"]
            sb = sbs[sid]
            sbl = sb.get("libtv") or {}
            pid = str(sbl.get("project_id") or "")
            if not pid:
                errors.append({"segment": sid,
                               "error": "这一场还没有 LibTV 项目"})
                continue
            gid = str(sbl.get("group_id") or "")
            rows = []
            try:
                ordered = self._graph_order(seg["nodes"])
            except Exception as e:
                errors.append({"segment": sid, "error": str(e)[:300]})
                continue
            if only_shot:
                by_id = {x["id"]: x for x in seg["nodes"]}
                keep = set()

                def add(nid):
                    if nid in keep or nid not in by_id:
                        return
                    keep.add(nid)
                    for r in (by_id[nid].get("refs") or []):
                        add(r)
                for x in seg["nodes"]:
                    if graph_mod.shot_of(x) == only_shot:
                        add(x["id"])
                ordered = [x for x in ordered if x["id"] in keep]
                if not ordered:
                    errors.append({"segment": sid,
                                   "error": "这一段没有镜 %s" % only_shot})
                    continue
            for i, n in enumerate(ordered):
                try:
                    nid = self._graph_wire_node(
                        client, st, uid, sid, pid, gid, n,
                        {x["id"]: x for x in seg["nodes"]}, i)
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": nid, "status": n["status"],
                                 "error": ""})
                except _GraphDeferred as e:
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": n.get("node_id") or "",
                                 "status": "planned", "deferred": True,
                                 "reason": str(e)[:200], "error": ""})
                except _NeedsBinding as e:
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": n.get("node_id") or "",
                                 "status": "planned", "deferred": True,
                                 "needs_binding": True,
                                 "reason": str(e)[:200], "error": ""})
                except _NeedsKeyframes as e:
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": n.get("node_id") or "",
                                 "status": "planned", "deferred": True,
                                 "needs_keyframes": True,
                                 "reason": str(e)[:200], "error": ""})
                except _NeedsFrame as e:
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": n.get("node_id") or "",
                                 "status": "planned", "deferred": True,
                                 "needs_frame": True, "frame": e.frame,
                                 "reason": str(e)[:200], "error": ""})
                except Exception as e:
                    n["status"] = "failed"
                    rows.append({"id": n["id"], "role": n["role"],
                                 "node_id": n.get("node_id") or "",
                                 "status": "failed", "error": str(e)[:300]})
                    break
                finally:
                    graph_mod.save(st.root, uid, g)
            report.append({"id": sid, "nodes": rows})
            errors += [dict(r, segment=sid) for r in rows if r.get("error")]

        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "scenes", "assets", "shots"])
        deferred = [dict(r, segment=seg_report["id"])
                    for seg_report in report
                    for r in (seg_report.get("nodes") or [])
                    if r.get("deferred")]
        return self._json(200, {
            "ok": not errors, "dry_run": False, "unit": uid,
            "segments": report, "errors": errors, "deferred": deferred,
        })

    def _graph_push(self, body):
        """把本地改过的节点提示词 / 参数推回 LibTV 上同一个节点。

        只 update 这一个节点，不重新布线整段，也不生成。用于：
        WorkBuddy 改完 graph.json → 用户回 Trestle 点「更新到 LibTV」。
        """
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        if not sid or not nid:
            raise ValueError("要指定段和节点")
        if not body.get("confirm"):
            raise ValueError("推送会改 LibTV 画布，需要 confirm=true")
        data = graph_mod.load(st.root, uid)
        g = graph_mod.normalize(data, uid)
        seg, n = self._graph_find(g, sid, nid)
        if not _node_key(n):
            raise ValueError("这个节点还没布线：先点「布线到 LibTV」，"
                             "再推提示词")
        if str(n.get("role") or "") in ("reference", "reference_video"):
            raise ValueError("上传 / 外部参考节点没有提示词可推")
        sb = self._graph_sb(uid, sid)
        if not sb:
            raise ValueError("找不到分镜：%s" % sid)
        pid = str((sb.get("libtv") or {}).get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目，先布线")
        self._need_libtv()
        client = libtv_mcp.LibTV(st.root)
        by = {x["id"]: x for x in seg["nodes"]}
        index = seg["nodes"].index(n)
        gid = str((sb.get("libtv") or {}).get("group_id") or "")
        before = n.get("input_hash") or ""
        nid_libtv = self._graph_wire_node(
            client, st, uid, sid, pid, gid, n, by, index)
        # 刚推上去的提示词就是 LibTV 现状，旧缓存不能再说「改过」。
        if isinstance(n.get("remote"), dict) and "prompt" in n["remote"]:
            n["remote"]["prompt"] = str(n.get("prompt") or "")
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "scenes", "assets", "shots", "prompts"])
        return self._json(200, {
            "ok": True, "unit": uid, "segment": sid, "node": nid,
            "node_id": nid_libtv, "status": n.get("status") or "",
            "changed": bool(before and before != (n.get("input_hash") or "")),
            "needs_push": False,
            "message": "已更新到 LibTV；已有输出不会自动重跑，需要时在 LibTV 重新生成",
        })

    def _graph_wire_node(self, client, st, uid, sid, pid, gid, n, by, index):
        """建 / 改一个节点；返回 node_id。只布线，不生成。"""
        role = n["role"]
        name = ("%s %s" % (sid, str(n.get("name") or n["id"])))[:180]
        if index < 40:
            x, y = {"reference": (-1600, index * 420),
                    "video": (1200, index * 420)}.get(
                        role, (0, index * 420))
        else:
            x, y = 0, 0

        old_hash = n.get("input_hash") or ""

        if role == "reference":
            ext = n.get("external") if isinstance(n.get("external"), dict) else {}
            if n.get("source") == "external":
                # 素材在 LibTV：只认 node_id，不上传、不读本地文件。
                key = str(ext.get("node_id") or n.get("node_id") or "").strip()
                if not key:
                    raise _NeedsBinding("先绑定外部素材：%s" % (
                        ext.get("label") or n["id"]))
                n["node_id"] = key
                n["status"] = "wired"
                n["input_hash"] = graph_mod.input_hash(n)
                return key
            if n["node_id"]:
                # 图已经在画布上：不重复上传。换图请清掉 node_id 再布线。
                n["input_hash"] = graph_mod.input_hash(n)
                return n["node_id"]
            src = (st.root / n["source"]).resolve()
            root = st.root.resolve()
            try:
                inside = src.is_relative_to(root)
            except AttributeError:
                inside = str(src).startswith(str(root) + os.sep)
            if not inside or not src.is_file():
                raise ValueError("参考图不存在：%s" % n["source"])
            nid = client.upload_image(
                pid, src, name=name, x=x, y=y,
                group=gid or None)
            n["node_id"] = nid
            n["status"] = "wired"
            n["input_hash"] = graph_mod.input_hash(n)
            return nid

        if role == "reference_video":
            ext = n.get("external") if isinstance(n.get("external"), dict) else {}
            if n.get("source") != "external":
                raise _NeedsKeyframes(
                    "参考视频需要客户在 LibTV 上传后绑定；不代劳抽帧")
            key = str(ext.get("node_id") or n.get("node_id") or "").strip()
            if not key:
                raise _NeedsBinding("先绑定参考视频：%s" % (
                    ext.get("label") or n["id"]))
            n["node_id"] = key
            n["status"] = "wired"
            n["input_hash"] = graph_mod.input_hash(n)
            return key

        left = [_node_key(by[r]) for r in (n.get("refs") or [])
                if r in by and _node_key(by[r])]
        hash_refs = [_node_key(by[r]) or r
                     for r in (n.get("refs") or []) if r in by]
        waiting = [r for r in (n.get("refs") or [])
                   if r in by and not _node_key(by[r])]
        ext_waiting = [r for r in waiting
                       if by[r].get("source") == "external"
                       or _is_ref_node(by[r])]
        if ext_waiting:
            raise _NeedsBinding("上游素材还没绑定：%s" % "、".join(ext_waiting))
        if waiting:
            raise ValueError("上游节点还没建：%s" % "、".join(waiting))
        params = dict(n.get("params") or {})
        params.pop("modeType", None)
        # idempotencyKey 绑定到具体操作：同内容的 create / update 不能共用一个 key，
        # 否则 LibTV 报 IDEMPOTENCY_CONFLICT（The key belongs to a different operation）。
        op = "update" if n["node_id"] else "create"
        digest = graph_mod.input_hash(n, hash_refs)[-16:]
        key = libtv_mcp.safe_key(
            "wb-graph-%s-%s-%s-%s-%s" % (uid, sid, n["id"], op, digest))

        if n["type"] == "image":
            if n["node_id"]:
                client.call("update_node", {
                    "node": n["node_id"], "idempotencyKey": key,
                    "projectId": pid, "newName": name,
                    "prompt": n["prompt"], "model": n["model"],
                    "params": params, "leftEnsure": left, "run": False,
                }, timeout=180)
                nid = n["node_id"]
            else:
                args = {
                    "name": name, "nodeType": "image", "model": n["model"],
                    "prompt": n["prompt"], "params": params, "left": left,
                    "projectId": pid, "run": False,
                    "idempotencyKey": key, "x": x, "y": y,
                }
                if gid:
                    args["group"] = gid
                try:
                    r = client.call("create_node", args, timeout=180)
                    nid = libtv_mcp.node_id_from(r)
                except Exception as e:
                    if not _name_conflict(e):
                        raise
                    nid = client.find_node_id(pid, name, "image")
                    if not nid:
                        raise
        else:
            first = next((by[r] for r in (n.get("refs") or [])
                          if r in by and by[r]["role"] == "first_frame"), None)
            last = next((by[r] for r in (n.get("refs") or [])
                         if r in by and by[r]["role"] == "last_frame"), None)
            if n["intent"] == "interpolate" and (
                    not first or not last
                    or not first.get("node_id") or not last.get("node_id")):
                raise ValueError("插值镜需要首帧和尾帧节点都先布线")
            ref_imgs = [by[r]["node_id"] for r in (n.get("refs") or [])
                        if r in by and by[r]["role"] == "reference"
                        and by[r].get("node_id")]
            ref_videos = [by[r] for r in (n.get("refs") or [])
                          if r in by and by[r]["role"] == "reference_video"
                          and by[r].get("node_id")]
            # 图谱先整体建好：首/尾帧还没生成也允许先把视频节点接上画布，
            # 用户在 LibTV 里按节点逐个生成。只有 LibTV 明确拒绝时才退化成 deferred。
            pending = [x["id"] for x in (first, last)
                       if x and x["status"] not in graph_mod.DONE]
            if not n["node_id"]:
                # LibTV 的视频预检要求首 / 尾帧有 url 或 assetId。先读一次，
                # 没有结果就直接告诉用户先出哪一帧，不要等 preflight 才报错。
                for fr in (first, last):
                    if not fr or not fr.get("node_id"):
                        continue
                    try:
                        rr = client.call("read_node",
                                         {"projectId": pid, "node": fr["node_id"]},
                                         timeout=30)
                        nd = rr.get("node") or rr
                        if not self._image_urls(nd):
                            raise _NeedsFrame(fr["id"])
                    except _NeedsFrame:
                        raise
                    except Exception:
                        # 读不到就交给 generate_video 预检，不在这里误判。
                        pass
            if n["node_id"]:
                client.call("update_node", {
                    "node": n["node_id"], "idempotencyKey": key,
                    "projectId": pid, "newName": name,
                    "prompt": n["prompt"], "model": n["model"],
                    "params": params, "leftEnsure": left, "run": False,
                }, timeout=180)
                nid = n["node_id"]
            else:
                args = {
                    "name": name, "prompt": n["prompt"], "model": n["model"],
                    "intent": n["intent"], "params": params,
                    "projectId": pid, "run": False, "idempotencyKey": key,
                    "x": x, "y": y,
                }
                if gid:
                    args["group"] = gid
                if ref_videos:
                    # 参考视频优先；和首尾帧 / 参考图互斥，混用时让用户自己拆。
                    args["referenceVideoNodes"] = [x["node_id"] for x in ref_videos]
                elif n["intent"] == "interpolate":
                    args["startFrameNode"] = first["node_id"]
                    args["endFrameNode"] = last["node_id"]
                elif first:
                    args["startFrameNode"] = first["node_id"]
                elif ref_imgs:
                    args["referenceImageNodes"] = ref_imgs
                try:
                    r = client.call("generate_video", args, timeout=180)
                    nid = libtv_mcp.node_id_from(r)
                except Exception as e:
                    if _name_conflict(e):
                        nid = client.find_node_id(pid, name, "video")
                        if not nid:
                            raise
                    elif ref_videos:
                        raise _NeedsKeyframes(
                            "LibTV 还不支持参考视频直连；请在 LibTV 上传第 0/1/2/3 秒关键帧")
                    elif pending:
                        raise _GraphDeferred(
                            "视频节点等 %s 生成后再补布（%s）" % (
                                "、".join(pending), str(e)[:120]))
                    else:
                        raise

        if not nid:
            raise ValueError("LibTV 没返回节点 ID")
        new_hash = graph_mod.input_hash(n, hash_refs)
        n["node_id"] = nid
        if n["status"] in graph_mod.DONE:
            # 已生成 / 已审过的节点：输入变了才置 stale，没变就别降级。
            n["status"] = "stale" if (old_hash and old_hash != new_hash) else n["status"]
        elif n["status"] == "stale":
            # 返修标记只能由「重新生成 + 同步」清除，布线不能把它抹掉。
            n["status"] = "stale"
        else:
            n["status"] = "wired"
        n["input_hash"] = new_hash
        return nid

    @staticmethod
    def _image_urls(node):
        d = node.get("data") or {}
        raw = d.get("url") or []
        if isinstance(raw, str):
            raw = [raw]
        return [u for u in raw if isinstance(u, str)
                and u.lower().startswith("http")]

    def _graph_sync(self, body):
        """读回每个节点的生成结果：状态 + URL + 下载。不改图谱结构。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        only = str(body.get("segment") or "").strip()
        dry = bool(body.get("dry_run", False))
        data = graph_mod.load(st.root, uid)
        if not (data.get("segments") or []):
            raise ValueError("没有 graph.json")
        g = graph_mod.normalize(data, uid)
        snap = st.snapshot(uid)
        sbs = {x["id"]: x for x in (snap.get("storyboards") or [])}
        client = libtv_mcp.LibTV(st.root)
        rows, errors, changed = [], [], False

        for seg in g["segments"]:
            if only and seg["id"] != only:
                continue
            sb = sbs.get(seg["id"]) or {}
            pid = str((sb.get("libtv") or {}).get("project_id") or "")
            if not pid:
                errors.append({"segment": seg["id"],
                               "error": "这一场还没有 LibTV 项目"})
                continue
            by = {n["id"]: n for n in seg["nodes"]}
            scene = str(sb.get("scene") or seg["id"].split("-")[0])
            for n in seg["nodes"]:
                nid = _node_key(n)
                if not nid:
                    if _is_ref_node(n) and n.get("source") == "external":
                        rows.append({"id": n["id"], "status": "unbound",
                                     "external": True, "bound": False,
                                     "downloaded": False,
                                     "needs_binding": True,
                                     "message": "素材待绑定"})
                    continue
                cur_hash = graph_mod.input_hash(
                    n, [_node_key(by[r]) or r
                        for r in (n.get("refs") or []) if r in by])
                hash_mismatch = bool(n.get("input_hash")
                                     and n["input_hash"] != cur_hash)
                if hash_mismatch and n["status"] in graph_mod.DONE:
                    n["status"] = "stale"
                    changed = True
                try:
                    r = client.call("read_node",
                                    {"projectId": pid, "node": nid}, timeout=30)
                except Exception as e:
                    if _is_ref_node(n) and n.get("source") == "external":
                        # 客户在 LibTV 删了节点：标 missing，不下载、不猜。
                        ext = dict(n.get("external") or {})
                        ext["missing"] = True
                        n["external"] = ext
                        n["status"] = "missing"
                        changed = True
                        rows.append({"id": n["id"], "node_id": nid,
                                     "status": "missing", "external": True,
                                     "bound": True, "missing": True,
                                     "downloaded": False,
                                     "message": "LibTV 节点已删除"})
                    else:
                        errors.append({"segment": seg["id"], "node": n["id"],
                                       "error": str(e)[:200]})
                    continue
                node = r.get("node") or r
                d = node.get("data") or {}
                info = d.get("taskInfo") or {}
                urls = (self._video_urls(node) if n["type"] == "video"
                        else self._image_urls(node))
                url = urls[0] if urls else ""

                if _is_ref_node(n):
                    # 参考素材不是生成结果：只同步元信息，不下载。
                    if n.get("source") == "external":
                        ext = dict(n.get("external") or {})
                        name = str(d.get("name") or "").strip()
                        if name and ext.get("name") != name:
                            ext["name"] = name
                            ext["label"] = ext.get("label") or name
                            changed = True
                        ext["node_id"] = nid
                        ext["missing"] = False
                        if n.get("status") in graph_mod.DONE:
                            n["status"] = "wired"
                            changed = True
                        n["external"] = ext
                    elif n["status"] == "generated":
                        n["status"] = "wired"      # 旧版本 sync 留下的错状态
                        changed = True
                    if url and n.get("url") != url:
                        n["url"] = url
                        changed = True
                    rows.append({"id": n["id"], "node_id": nid,
                                 "status": n["status"], "url": url,
                                 "external": n.get("source") == "external",
                                 "bound": bool(nid),
                                 "has_result": bool(url),
                                 "downloaded": False, "message": ""})
                    continue

                if info.get("loading"):
                    if n["status"] not in ("approved",):
                        n["status"] = "generating"
                    rows.append({"id": n["id"], "node_id": nid,
                                 "status": "generating", "url": "",
                                 "message": "还在生成"})
                    continue
                failed = str(info.get("failedReason") or "")
                if failed and not url:
                    n["status"] = "failed"
                    n["error"] = failed[:300]
                    rows.append({"id": n["id"], "node_id": nid,
                                 "status": "failed", "url": "",
                                 "message": failed[:200]})
                    continue
                if not url:
                    rows.append({"id": n["id"], "node_id": nid,
                                 "status": n["status"], "url": "",
                                 "message": "LibTV 还没有结果"})
                    continue
                if dry:
                    rows.append({"id": n["id"], "node_id": nid,
                                 "status": "would-sync", "url": url,
                                 "message": ""})
                    continue

                same = (n.get("url") == url and n.get("file")
                        and (st.root / n["file"]).is_file())
                downloaded = False
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                if not same:
                    ext = Path(urlparse(url).path).suffix.lower()
                    want = ((".mp4", ".webm", ".mov", ".m4v")
                            if n["type"] == "video"
                            else (".png", ".jpg", ".jpeg", ".webp"))
                    if ext not in want:
                        ext = want[0]
                    sub = "renders" if n["type"] == "video" else "stills"
                    hist = [h for h in (n.get("history") or [])
                            if isinstance(h, dict)]
                    rev = max([int(h.get("rev") or 0) for h in hist] +
                              [int(n.get("current_rev") or 0),
                               int(n.get("rev") or 0)]) + 1
                    # 用分镜的实际目录（可能带场名后缀），不要自己拼。
                    base_dir = (st.root / sb["dir"]) if sb.get("dir") else (
                        st.root / "units" / uid / "production" /
                        scene / seg["id"])
                    target = base_dir / sub / ("%s-r%d%s" % (
                        n["id"], rev, ext))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(target.suffix + ".part")
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "workbench/1"})
                    with urllib.request.urlopen(req, timeout=300) as resp, \
                            open(tmp, "wb") as fh:
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            fh.write(chunk)
                    tmp.replace(target)
                    downloaded = True
                    status = "stale" if hash_mismatch else "generated"
                    n["file"] = target.relative_to(st.root).as_posix()
                    n["rev"] = rev
                    n["current_rev"] = rev
                    n["url"] = url
                    n["status"] = status
                    n["history"] = hist + [{
                        "rev": rev, "node_id": nid, "url": url,
                        "file": n["file"], "input_hash": cur_hash,
                        "at": now, "status": status,
                    }]
                    changed = True
                elif n["status"] in ("wired", "generating", "failed"):
                    n["status"] = "generated"
                    changed = True
                    if not (n.get("history") or []) and n.get("file"):
                        rev = int(n.get("current_rev") or n.get("rev") or 1)
                        n["current_rev"] = rev
                        n["history"] = [{
                            "rev": rev, "node_id": nid, "url": url,
                            "file": n.get("file") or "", "input_hash": cur_hash,
                            "at": now, "status": n["status"],
                        }]
                n.pop("error", None)
                n["synced_at"] = now
                rows.append({"id": n["id"], "node_id": nid,
                             "status": n["status"], "url": url,
                             "file": n.get("file") or "", "message": "",
                             "downloaded": downloaded,
                             "current_rev": int(n.get("current_rev") or 0),
                             "history_count": len(n.get("history") or [])})

        if changed and not dry:
            graph_mod.save(st.root, uid, g)
            st.invalidate(["storyboards", "media", "shots"])
        return self._json(200, {
            "ok": not errors, "unit": uid, "dry_run": dry,
            "rows": rows, "errors": errors,
        })

    def _graph_state(self, body):
        """读回 LibTV 上每个节点的最新状态和方案，缓存到 graph.json 的 remote。

        只读：不改本地 prompt / 参数 / 状态；客户端在用户点「采用」时
        才走 _graph_pull。这样在 LibTV 里直接改的提示词不会被悄悄覆盖。
        """
        st = self.state
        uid = str(body.get("unit") or st.unit)
        only = str(body.get("segment") or "").strip()
        data = graph_mod.load(st.root, uid)
        if not (data.get("segments") or []):
            raise ValueError("没有 graph.json")
        g = graph_mod.normalize(data, uid)
        snap = st.snapshot(uid)
        sbs = {x["id"]: x for x in (snap.get("storyboards") or [])}
        client = libtv_mcp.LibTV(st.root)
        rows, errors, changed = [], [], False
        canvases = {}
        for seg in g["segments"]:
            if only and seg["id"] != only:
                continue
            sb = sbs.get(seg["id"]) or {}
            pid = str((sb.get("libtv") or {}).get("project_id") or "")
            if not pid:
                errors.append({"segment": seg["id"],
                               "error": "这一场还没有 LibTV 项目"})
                continue
            for n in seg["nodes"]:
                nid = _node_key(n)
                if not nid:
                    continue
                try:
                    r = client.call("read_node",
                                    {"projectId": pid, "node": nid}, timeout=30)
                except Exception as e:
                    errors.append({"segment": seg["id"], "node": n["id"],
                                   "error": str(e)[:200]})
                    continue
                state = self._libtv_node_state(
                    r.get("node") or r,
                    kind="video" if n.get("type") == "video" else "image")
                if not self._same_libtv_state(n.get("remote"), state):
                    n["remote"] = state
                    changed = True
                local = str(n.get("prompt") or "").strip()
                remote = str(state.get("prompt") or "").strip()
                rows.append({
                    "id": n["id"], "node_id": nid, "role": n.get("role") or "",
                    "status": state["status"], "prompt": remote,
                    "local_prompt": local,
                    "remote_changed": bool(remote and remote != local),
                    "model": state["model"], "params": state["params"],
                    "style": state["style"], "urls": state["urls"],
                    "at": state["at"],
                })
            # 这一段的 LibTV 画布结构（节点坐标 + 连线），给分镜页画双画布。
            try:
                pr = client.call("read_project", {
                    "projectId": pid, "view": "nodes"}, timeout=30)
                cn, ce, cc = self._libtv_canvas_structure(pr)
                canvases[seg["id"]] = {"nodes": cn, "edges": ce, "counts": cc}
            except Exception as e:
                errors.append({"segment": seg["id"],
                               "error": "读画布结构失败：" + str(e)[:160]})
        if changed:
            graph_mod.save(st.root, uid, g)
            st.invalidate(["storyboards"])
        return self._json(200, {
            "ok": not errors, "unit": uid, "segment": only,
            "rows": rows, "canvases": canvases, "errors": errors,
            "message": "已读回 LibTV 最新方案" if not errors
                       else "部分节点没读到：%s" % errors[0]["error"],
        })

    def _graph_pull(self, body):
        """采用 LibTV 上改过的提示词 / 参数，写回 graph.json 的节点。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        if not sid or not nid:
            raise ValueError("要指定段和节点")
        if not body.get("confirm"):
            raise ValueError("采用 LibTV 方案会覆盖本地节点，需要 confirm=true")
        data = graph_mod.load(st.root, uid)
        g = graph_mod.normalize(data, uid)
        seg, n = self._graph_find(g, sid, nid)
        node_id = _node_key(n)
        if not node_id:
            raise ValueError("这个节点还没布线，没有 LibTV 方案可拉")
        sb = self._graph_sb(uid, sid)
        pid = str(((sb or {}).get("libtv") or {}).get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        r = client.call("read_node", {"projectId": pid, "node": node_id},
                        timeout=30)
        state = self._libtv_node_state(
            r.get("node") or r,
            kind="video" if n.get("type") == "video" else "image")
        remote = str(state.get("prompt") or "").strip()
        if not remote:
            raise ValueError("LibTV 这个节点读不到提示词，没法采用")
        n["prompt"] = remote
        if body.get("params"):
            for k, v in (state.get("params") or {}).items():
                n.setdefault("params", {})[k] = v
            if state.get("model"):
                n["model"] = state["model"]
        n["remote"] = state
        # 内容已经和 LibTV 一致，不要再标「待推」。
        n["input_hash"] = self._graph_current_hash(seg, n)
        n["pulled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards", "shots", "prompts"])
        return self._json(200, {
            "ok": True, "unit": uid, "segment": sid, "node": nid,
            "node_id": node_id, "prompt": remote,
            "status": n.get("status") or "",
            "message": "已采用 LibTV 的提示词 / 参数；已有输出不会变，"
                       "要换图 / 换片得在 LibTV 重新生成",
        })

    def _graph_review(self, body):
        """节点审片：ok → approved；no → stale，并向下游传播。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("segment") or "").strip()
        nid_key = str(body.get("node") or "").strip()
        verdict = str(body.get("verdict") or "").strip().lower()
        if verdict not in ("ok", "no", ""):
            raise ValueError("verdict 只能是 ok / no / 空")
        data = graph_mod.load(st.root, uid)
        if not (data.get("segments") or []):
            raise ValueError("没有 graph.json")
        g = graph_mod.normalize(data, uid)
        seg = next((s for s in g["segments"] if s["id"] == sid), None)
        if not seg:
            raise ValueError("找不到段：%s" % sid)
        n = next((x for x in seg["nodes"] if x["id"] == nid_key), None)
        if not n:
            raise ValueError("找不到节点：%s" % nid_key)
        if verdict in ("ok", "no") and n["status"] not in graph_mod.DONE:
            if n["status"] == "stale":
                raise ValueError("这个节点已标记返修，先在 LibTV 重新生成、同步后再审")
            raise ValueError("这个节点还没生成，不能标 ok / no")
        if verdict == "ok":
            n["status"] = "approved"
        elif verdict == "no":
            n["status"] = "stale"
            by = {x["id"]: x for x in seg["nodes"]}
            down, grew = {nid_key}, True
            while grew:
                grew = False
                for x in seg["nodes"]:
                    if x["id"] in down:
                        continue
                    if any(r in down for r in (x.get("refs") or [])):
                        down.add(x["id"])
                        if x["status"] in graph_mod.DONE:
                            x["status"] = "stale"
                        grew = True
        n["review"] = {"verdict": verdict,
                       "note": str(body.get("note") or "")[:500]}
        graph_mod.save(st.root, uid, g)
        st.invalidate(["storyboards"])
        return self._json(200, {
            "ok": True, "unit": uid, "segment": sid, "node": nid_key,
            "status": n["status"], "review": n["review"],
        })

    # ---------- 交付 ----------
    def _deliver_shot_videos(self, uid, snap):
        """按分镜表顺序取每一镜的成片：图谱 current_rev 优先，快照 media 兜底。"""
        st = self.state
        gmap = {}
        try:
            gd = graph_mod.normalize(graph_mod.load(st.root, uid), uid)
        except Exception:
            gd = {}
        for gseg in (gd.get("segments") or []):
            for n in (gseg.get("nodes") or []):
                if n.get("role") != "video":
                    continue
                sh = graph_mod.shot_of(n)
                cur = _node_current(n)
                f = str(cur.get("file") or n.get("file") or "").replace("\\", "/")
                if sh and f and (st.root / f).is_file():
                    gmap.setdefault(sh, f)
        shots = {s["id"]: s for s in (snap.get("shots") or [])}
        out, seen = [], set()
        for sb in (snap.get("storyboards") or []):
            if sb.get("suggested"):
                continue
            for shid in (sb.get("shots") or []):
                if shid in seen:
                    continue
                seen.add(shid)
                sh = shots.get(shid) or {}
                f = gmap.get(shid) or ""
                if not f:
                    media = sh.get("media") or []
                    f = next((m.get("path") for m in media
                              if m.get("kind") == "video"), "")
                f = str(f or "").replace("\\", "/")
                ok = bool(f and (st.root / f).is_file())
                out.append({
                    "shot": shid, "segment": sb["id"],
                    "scene": sb.get("scene") or "",
                    "sec": float(sh.get("sec") or 0),
                    "file": f, "ok": ok,
                    "size": (st.root / f).stat().st_size if ok else 0,
                })
        return out

    def _deliver_segments(self, snap):
        st = self.state
        uid = (snap.get("meta") or {}).get("unit") or st.unit
        shots = self._deliver_shot_videos(uid, snap)
        out = []
        for sb in (snap.get("storyboards") or []):
            if sb.get("suggested"):
                continue
            rows = [x for x in shots if x["segment"] == sb["id"]]
            ok = bool(rows) and all(x["ok"] for x in rows)
            files = [x["file"] for x in rows if x["ok"]]
            out.append({
                "id": sb["id"], "scene": sb.get("scene") or "",
                "sec": float(sb.get("sec") or 0),
                "file": files[0] if files else "", "ok": ok,
                "shots": rows,
                "missing_shots": [x["shot"] for x in rows if not x["ok"]],
                "size": sum(x["size"] for x in rows),
            })
        return out

    def _ffmpeg_concat(self, inputs, out):
        """把多个视频按顺序拼成一个；单文件直接复制。总是重建，避免旧片残留。"""
        out.parent.mkdir(parents=True, exist_ok=True)
        if not inputs:
            raise ValueError("没有可拼接的视频")
        if len(inputs) == 1:
            shutil.copy2(inputs[0], out)
            return out
        ff = _ffmpeg_path()
        if not ff:
            raise ValueError("没找到 ffmpeg：安装包应自带 runtime/ffmpeg；"
                             "开发机可放 vendor/ffmpeg 或用 CJ_FFMPEG 指定")
        lst = out.parent / ("_concat-%s.txt" % out.stem)

        def q(p):
            return "file '%s'" % str(p).replace("'", "'\\''")
        lst.write_text("\n".join(q(p) for p in inputs) + "\n", encoding="utf-8")
        tmp = out.with_suffix(".tmp.mp4")
        base = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        attempts = [
            ["-c", "copy"],
            ["-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-c:a", "aac"],
            ["-c:v", "h264_qsv", "-pix_fmt", "yuv420p", "-c:a", "aac"],
            ["-c:v", "h264_amf", "-pix_fmt", "yuv420p", "-c:a", "aac"],
            # LGPL 包没有 libx264；mpeg4 保证一定能出片
            ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
             "-c:a", "aac"],
        ]
        last = None
        try:
            for extra in attempts:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                r = subprocess.run(base + extra + [str(tmp)],
                                   capture_output=True, text=True,
                                   creationflags=NO_WINDOW)
                last = r
                if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                    tmp.replace(out)
                    return out
            raise ValueError("ffmpeg 拼接失败：%s" % (
                (last.stderr if last else "") or "")[-300:])
        finally:
            try:
                lst.unlink()
            except OSError:
                pass
        return out

    def _deliver_info(self, q):
        st = self.state
        uid = self._unit(q)
        snap = st.snapshot(uid)
        segs = self._deliver_segments(snap)
        missing = [x["id"] for x in segs if not x["ok"]]
        film_rel = "units/%s/deliver/film.mp4" % uid
        film = st.root / film_rel
        stale = False
        if film.is_file():
            shots = self._deliver_shot_videos(uid, snap)
            newest = max([st.root / x["file"] for x in shots
                          if x["ok"] and x["file"]] or [film],
                         key=lambda p: p.stat().st_mtime)
            stale = newest.stat().st_mtime > film.stat().st_mtime
        ver = _jload(st.root / "units" / uid / "version.json", {}) or {}
        return self._json(200, {
            "ok": True, "unit": uid,
            "title": (snap.get("meta") or {}).get("name") or "",
            "version": {"current": str(ver.get("current") or ""),
                        "snapshots": ver.get("snapshots") or []},
            "segments": segs,
            "ready": bool(segs) and not missing,
            "missing": missing,
            "film": {"file": film_rel, "exists": film.is_file(),
                     "stale": stale,
                     "size": film.stat().st_size if film.is_file() else 0},
            "review": snap.get("review") or {},
            "assets": len(snap.get("assets") or []),
            "prompts": (st.root / "units" / uid / "prompts" /
                        "prompts.json").is_file(),
        })

    @staticmethod
    def _asset_plan(a):
        t = a.get("type")
        if t == "characters":
            return {"recipe": "A1", "title": "剧本角色 → 母图 → 四宫格 → 四视图",
                    "why": "角色有多套装造：先母图定脸，再四宫格出转面，最后四视图 / 装造做视频参考。",
                    "steps": ["剧本角色", "母图", "四宫格", "四视图 / 装造"],
                    "actions": ["生成母图", "生成四宫格", "生成四视图"],
                    "cost": "3 张工作稿 · 约 60 积分"}
        if t == "scenes":
            return {"recipe": "A2", "title": "剧本场 → 母图 → 四视角 → 机位 / 时段",
                    "why": "先出空景母图，再派生机位和时段变体；分镜按机位引用。",
                    "steps": ["剧本场", "母图", "四视角", "机位 / 时段"],
                    "actions": ["生成母图", "生成四视角", "生成机位变体"],
                    "cost": "1 张母图 · 4 视角 · 约 80 积分"}
        if t == "props":
            return {"recipe": "A3", "title": "剧本道具 → 白底主图 → 状态 A/B",
                    "why": "白底主图定形，再出合上 / 打开等状态。",
                    "steps": ["剧本道具", "白底主图", "状态 A/B"],
                    "actions": ["生成白底主图", "生成状态"],
                    "cost": "1 张主图 · 2 个状态 · 约 40 积分"}
        if t == "style":
            return {"recipe": "A4", "title": "剧本气质 → 风格参考 → 全片统一",
                    "why": "风格只进提示词和风格参考，不挂成参考图。",
                    "steps": ["剧本气质", "风格参考", "全片统一"],
                    "actions": ["分析风格", "生成风格参考"],
                    "cost": "1 套风格 · 约 20 积分"}
        return {"recipe": "A5", "title": "剧本资产 → 母图 → 变体",
                "why": "先定母图，再按剧情派生变体。",
                "steps": ["剧本资产", "母图", "变体"],
                "actions": ["生成母图", "生成变体"],
                "cost": "1 张母图 · 约 30 积分"}

    def _structure(self, q):
        st = self.state
        uid = self._unit(q)
        snap = st.snapshot(uid)
        sd = _jload(st.root / "units" / uid / "storyboard" / "shots.json", {}) or {}
        shot_assets, shot_docs = {}, {}
        for s in (sd.get("shots") or []):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").zfill(3)
            shot_docs[sid] = s
            shot_assets[sid] = [str(x) for x in (s.get("assets") or [])
                                if str(x).strip()]
        shots = {s["id"]: s for s in (snap.get("shots") or [])}
        # graph.json：每镜的首帧 / 视频节点。总览镜卡靠它判断「待布线 / 待生成 / 已生成」，
        # 也靠它决定按钮该布线还是该生成；没有图谱时这里的节点是空的。
        graph_shots = {}
        try:
            gd = graph_mod.normalize(graph_mod.load(st.root, uid), uid)
        except Exception:
            gd = {}
        for gseg in (gd.get("segments") or []):
            for n in (gseg.get("nodes") or []):
                gsh = graph_mod.shot_of(n)
                if gsh:
                    graph_shots.setdefault(gsh, []).append(n)
        brief_paths = (st.root / "units" / uid / "design" / "导演简报.md",
                       st.root / "design" / "导演简报.md")
        brief_exists = any(p.is_file() for p in brief_paths)
        one_line = ""
        for brief in brief_paths:
            try:
                for line in brief.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- 一句话："):
                        one_line = line.split("：", 1)[1].strip()
                        break
                if one_line:
                    break
            except OSError:
                continue
        scenes = []
        scene_rows = snap.get("scenes") or []
        ids = [x["id"] for x in scene_rows] or sorted(
            {str(s.get("scene") or "") for s in (snap.get("storyboards") or [])
             if not s.get("suggested")} - {""})
        for sid in ids:
            sc = next((x for x in scene_rows if x["id"] == sid), {})
            segs = []
            for sb in (snap.get("storyboards") or []):
                if sb.get("suggested") or str(sb.get("scene") or "") != str(sid):
                    continue
                rows = []
                for shid in (sb.get("shots") or []):
                    sh = shots.get(shid) or {}
                    nodes = graph_shots.get(shid) or []
                    # 外部素材：客户在 LibTV 上传 / 生成的参考节点，只记引用。
                    external_refs = []
                    for n in nodes:
                        if n.get("source") != "external":
                            continue
                        ext = (n.get("external")
                               if isinstance(n.get("external"), dict) else {})
                        key = _node_key(n)
                        external_refs.append({
                            "id": n.get("id") or "",
                            "label": ext.get("label") or n.get("name")
                            or n.get("id") or "",
                            "kind": ext.get("kind") or n.get("type") or "image",
                            "node_id": key,
                            "bound": bool(key),
                            "stage": n.get("stage") or "reference",
                            "role": n.get("role") or "reference",
                            "status": n.get("status") or "planned",
                            "missing": bool(ext.get("missing")),
                        })
                    needs_binding = sum(
                        1 for x in external_refs if not x["bound"] or x["missing"])
                    # 首帧：first_frame > image2image(final) > last_frame；白膜不参与。
                    frame = None
                    for role in ("first_frame", "image2image", "last_frame"):
                        frame = next((n for n in nodes if n.get("type") == "image"
                                      and n.get("role") == role
                                      and (n.get("stage") or "final") != "greybox"),
                                     None)
                        if frame:
                            break
                    greybox = next((n for n in nodes if n.get("type") == "image"
                                    and n.get("stage") == "greybox"), None)
                    video_node = next((n for n in nodes if n.get("role") == "video"),
                                      None)
                    ref_video = next((n for n in nodes
                                      if n.get("role") == "reference_video"), None)
                    # R2/R3/R4 可能有首帧 + 尾帧：视频要等每一张都生成完。
                    frame_nodes = [n for n in nodes if n.get("type") == "image"
                                   and n.get("role") in ("first_frame", "last_frame")
                                   and (n.get("stage") or "final") != "greybox"]
                    frames_missing = [n.get("id") for n in frame_nodes
                                      if n.get("status") not in graph_mod.DONE]
                    # render 只给图片；视频文件单独给 video_file，避免 <img> 拿到 mp4。
                    media = sh.get("media") or []
                    frame_img = next((m.get("path") for m in media
                                      if m.get("kind") not in ("video", "keyframe")), "")
                    keyframe = next((m.get("path") for m in media
                                     if m.get("kind") == "keyframe"), "")
                    video_file = next((m.get("path") for m in media
                                       if m.get("kind") == "video"), "")
                    fcur = _node_current(frame or {})
                    vcur = _node_current(video_node or {})
                    gcur = _node_current(greybox or {})
                    frame_file = fcur.get("file") or (frame or {}).get("file") or frame_img
                    video_cur_file = (vcur.get("file") or (video_node or {}).get("file")
                                      or video_file)
                    greybox_file = (gcur.get("file") or (greybox or {}).get("file") or "")
                    rows.append({
                        "id": shid,
                        "framing": sh.get("framing") or "",
                        "camera": sh.get("move") or sh.get("camera") or "",
                        "sec": sh.get("sec") or 0,
                        "visual": sh.get("visual") or sh.get("shot") or "",
                        "line": sh.get("line") or "",
                        "key": bool(sh.get("key")),
                        "assets": shot_assets.get(shid) or [],
                        "render": frame_file,
                        "render_url": (fcur.get("url")
                                       or (frame or {}).get("url") or ""),
                        "keyframe": keyframe,
                        "video_file": video_cur_file,
                        "video_url": (vcur.get("url")
                                      or (video_node or {}).get("url") or ""),
                        "verdict": sh.get("verdict") or "",
                        "frame_status": (frame or {}).get("status") or "",
                        "video_status": (video_node or {}).get("status") or "",
                        "frame_wired": bool(_node_key(frame or {})),
                        "video_wired": bool(_node_key(video_node or {})),
                        "frame_id": (frame or {}).get("id") or "",
                        "video_id": (video_node or {}).get("id") or "",
                        "frame_node": _node_key(frame or {}),
                        "video_node": _node_key(video_node or {}),
                        "frame_rev": (int((frame or {}).get("current_rev") or 0)
                                      or (1 if (frame or {}).get("url")
                                          or (frame or {}).get("file") else 0)),
                        "video_rev": (int((video_node or {}).get("current_rev") or 0)
                                      or (1 if (video_node or {}).get("url")
                                          or (video_node or {}).get("file") else 0)),
                        "frame_history": (len((frame or {}).get("history") or [])
                                          or (1 if (frame or {}).get("url")
                                              or (frame or {}).get("file") else 0)),
                        "video_history": (len((video_node or {}).get("history") or [])
                                          or (1 if (video_node or {}).get("url")
                                              or (video_node or {}).get("file") else 0)),
                        "greybox_status": (greybox or {}).get("status") or "",
                        "greybox_wired": bool(_node_key(greybox or {})),
                        "greybox_node": _node_key(greybox or {}),
                        "greybox_file": greybox_file,
                        "greybox_rev": int((greybox or {}).get("current_rev") or 0),
                        "ref_video_status": (ref_video or {}).get("status") or "",
                        "ref_video_node": _node_key(ref_video or {}),
                        "external_refs": external_refs,
                        "needs_binding": needs_binding,
                        "frames_ready": not frames_missing,
                        "frames_missing": frames_missing,
                        "output_nodes": sum(1 for n in nodes
                                            if not _is_ref_node(n)),
                        "nodes": len(nodes),
                        "wired": bool(nodes) and all(
                            _node_key(n) for n in nodes if not _is_ref_node(n)),
                    })
                segs.append({"id": sb["id"],
                             "name": sb.get("name") or sb["id"],
                             "sec": sb.get("sec") or 0, "shots": rows,
                             "video": sb.get("video") or "",
                             "libtv": sb.get("libtv") or {}})
            scenes.append({"id": sid, "name": sc.get("name") or sid,
                           "libtv": sc.get("libtv") or {},
                           "seconds": round(sum(float(x.get("sec") or 0)
                                                for x in segs), 1),
                           "segments": segs})
        assets = []
        for a in (snap.get("assets") or []):
            used = [shid for shid, ids2 in shot_assets.items()
                    if a["id"] in ids2]
            files = [f.get("path") for f in (a.get("files") or [])
                     if isinstance(f, dict)]
            audio = next((f for f in files if str(f).lower().endswith(
                (".mp3", ".wav", ".m4a", ".ogg"))), "")
            assets.append({
                "id": a["id"], "name": a["name"], "type": a["type"],
                "type_label": a["type_label"],
                "status": a.get("status") or "",
                "image": a.get("primary_image")
                or next((x for x in (a.get("images") or []) if x), ""),
                "images": a.get("images") or [],
                "variants": [{"id": v.get("id"),
                              "label": v.get("label") or v.get("id"),
                              "image": v.get("image") or ""}
                             for v in (a.get("variants") or [])],
                "prompts": a.get("prompts_text") or "",
                "shots": used, "plan": self._asset_plan(a),
                "audio": audio, "files": files,
            })
        official = ((snap.get("script") or {}).get("official") or {})
        ep = snap.get("episode") or {}
        return self._json(200, {
            "ok": True, "unit": uid,
            "title": (snap.get("meta") or {}).get("name") or "",
            "one_line": one_line,
            "brief": brief_exists,
            "meta": {"type": ep.get("type") or "",
                     "duration": ep.get("duration") or "",
                     "scenes": len(scenes),
                     "segments": sum(len(x["segments"]) for x in scenes),
                     "shots": sum(len(y["shots"]) for x in scenes
                                  for y in x["segments"]),
                     "assets": len(assets)},
            "script": {"title": official.get("title") or "",
                       "status": official.get("status") or "",
                       "text": official.get("text") or ""},
            "scenes": scenes, "assets": assets,
            "film": {"file": "units/%s/deliver/film.mp4" % uid,
                     "exists": (st.root / "units" / uid / "deliver" /
                                "film.mp4").is_file()},
        })

    def _deliver_tree(self, q):
        st = self.state
        uid = self._unit(q)
        snap = st.snapshot(uid)
        sbs = [x for x in (snap.get("storyboards") or [])
               if not x.get("suggested")]
        first_shot = next((str(x) for sb in sbs
                           for x in (sb.get("shots") or [])[:1]), "")
        scene_rows = snap.get("scenes") or []
        ids = [s["id"] for s in scene_rows] or sorted(
            {str(s.get("scene") or "") for s in sbs} - {""})
        # 有图谱时按节点的 current_rev 交付；不再把 r1/r2 全量列出来。
        graph_videos = {}
        try:
            gd = graph_mod.normalize(graph_mod.load(st.root, uid), uid)
        except Exception:
            gd = {}
        for gseg in (gd.get("segments") or []):
            for n in (gseg.get("nodes") or []):
                if n.get("role") != "video":
                    continue
                cur = _node_current(n)
                f = cur.get("file") or n.get("file") or ""
                if not f:
                    continue
                graph_videos[(gseg.get("id"), graph_mod.shot_of(n))] = {
                    "file": f,
                    "rev": int(n.get("current_rev") or n.get("rev") or 1),
                    "node_id": _node_key(n),
                }

        # 视频列表封面：优先用这一镜的首帧图，其次用视频自己抽第一帧。
        # 图谱里的 first_frame > image2image > last_frame；白膜不算首帧。
        shot_posters = {}
        try:
            for gseg in (gd.get("segments") or []):
                for n in (gseg.get("nodes") or []):
                    if n.get("type") != "image":
                        continue
                    role = str(n.get("role") or "")
                    rank = {"first_frame": 0, "image2image": 1,
                            "last_frame": 2}.get(role)
                    if rank is None or (n.get("stage") or "final") == "greybox":
                        continue
                    sh = graph_mod.shot_of(n)
                    if not sh:
                        continue
                    cur = _node_current(n)
                    f = str(cur.get("file") or n.get("file") or "").replace("\\", "/")
                    if not f or not (st.root / f).is_file():
                        continue
                    old = shot_posters.get(sh)
                    if old is None or rank < old[0]:
                        shot_posters[sh] = (rank, f)
        except Exception:
            pass
        for sh in (snap.get("shots") or []):
            sid = str(sh.get("id") or "")
            if not sid or sid in shot_posters:
                continue
            media = sh.get("media") or []
            f = next((m.get("path") for m in media
                      if m.get("kind") not in ("video", "keyframe")), "")
            if not f:
                f = next((m.get("path") for m in media
                          if m.get("kind") == "keyframe"), "")
            f = str(f or "").replace("\\", "/")
            if f and (st.root / f).is_file():
                shot_posters[sid] = (9, f)
        shot_posters = {k: v[1] for k, v in shot_posters.items()}

        def poster_url(shot, video_rel=""):
            # 首帧图也走缩略图端点：列表里 128×72 的格子不需要几 MB 的原图。
            rel = shot_posters.get(str(shot or "")) or video_rel
            if rel:
                return "/api/deliver/poster?unit=%s&file=%s" % (
                    quote(uid), quote(rel))
            return ""

        scenes = []
        for sid in ids:
            sc = next((x for x in scene_rows if x["id"] == sid), {})
            segs = []
            for sb in sbs:
                if str(sb.get("scene") or "") != str(sid):
                    continue
                seg = {"id": sb["id"], "name": sb.get("name") or sb["id"],
                       "shots": sb.get("shots") or [],
                       "sec": sb.get("sec") or 0, "videos": []}
                seen = set()
                gv = {k: v for k, v in graph_videos.items()
                      if k[0] == sb["id"]}
                if gv:
                    for (_seg, shot), v in sorted(gv.items()):
                        f = st.root / v["file"]
                        if not f.is_file() or v["file"] in seen:
                            continue
                        seen.add(v["file"])
                        seg["videos"].append({
                            "shot": shot,
                            "name": ("镜 %s · 视频" % shot) if shot else f.stem,
                            "file": v["file"], "ok": True,
                            "size": f.stat().st_size,
                            "rev": v["rev"], "current": True,
                            "poster": poster_url(shot, v["file"]),
                        })
                sdir = (st.root / sb["dir"]) if sb.get("dir") else None
                if not gv and sdir and (sdir / "renders").is_dir():
                    for f in sorted((sdir / "renders").glob("*.mp4")):
                        rel = f.relative_to(st.root).as_posix()
                        if rel in seen:
                            continue
                        seen.add(rel)
                        m = re.search(r"(\d{3})", f.name)
                        shot = m.group(1) if m else ""
                        seg["videos"].append({
                            "shot": shot,
                            "name": ("镜 %s · 视频" % shot) if shot else f.stem,
                            "file": rel, "ok": True,
                            "size": f.stat().st_size,
                            "rev": 0, "current": True,
                            "poster": poster_url(shot, rel),
                        })
                rel = str(sb.get("video") or "")
                if not gv and rel and rel not in seen:
                    f = st.root / rel
                    if f.is_file():
                        seg["videos"].append({
                            "shot": "", "name": sb["id"] + " · 视频",
                            "file": rel, "ok": True,
                            "size": f.stat().st_size,
                            "rev": 0, "current": True,
                            "poster": poster_url("", rel),
                        })
                segs.append(seg)
            scenes.append({"id": sid, "name": sc.get("name") or sid,
                           "segments": segs})
        film_rel = "units/%s/deliver/film.mp4" % uid
        film = st.root / film_rel
        ver = _jload(st.root / "units" / uid / "version.json", {}) or {}
        cur = str(ver.get("current") or "")
        cur_rec = next((s for s in (ver.get("snapshots") or [])
                        if str(s.get("id")) == cur), {})
        pack = []

        def pack_label(rel):
            if rel == "全片.mp4" or rel == "film.mp4":
                return "全片"
            if rel == "视频介绍.md":
                return "视频介绍"
            if rel.startswith("segments/"):
                return "分段 " + Path(rel).stem
            if rel.startswith("shots/"):
                return "镜 " + Path(rel).stem
            if rel.startswith("audio/"):
                return "音频 " + Path(rel).stem
            return Path(rel).stem or rel

        def pack_short(rel):
            if rel == "全片.mp4" or rel == "film.mp4":
                return "全片"
            if rel == "视频介绍.md":
                return "介绍"
            return Path(rel).stem or rel

        if cur:
            pdir = st.root / "units" / uid / "deliver" / cur
            if pdir.is_dir():
                for f in sorted(pdir.rglob("*")):
                    if f.is_file():
                        name = f.relative_to(pdir).as_posix()
                        rel_file = f.relative_to(st.root).as_posix()
                        stem = Path(name).stem
                        if name.startswith("shots/"):
                            pshot = stem
                        elif name.startswith("segments/"):
                            pshot = next((str(x) for sb in sbs if sb["id"] == stem
                                          for x in (sb.get("shots") or [])[:1]), "")
                        elif name in ("全片.mp4", "film.mp4"):
                            pshot = first_shot
                        elif name.startswith("videos/"):
                            m = re.search(r"_(\d{3})(?:_|\.|$)", name) or \
                                re.search(r"(\d{3})", stem)
                            pshot = m.group(1) if m else ""
                        else:
                            pshot = ""
                        p = poster_url(pshot, rel_file) if Path(name).suffix.lower() in (
                            ".mp4", ".webm", ".mov", ".m4v") else ""
                        pack.append({
                            "name": name, "label": pack_label(name),
                            "short": pack_short(name),
                            "file": rel_file, "poster": p,
                            "ok": True, "size": f.stat().st_size,
                        })
        total = sum(len(x["videos"]) for sc in scenes for x in sc["segments"])
        return self._json(200, {
            "ok": True, "unit": uid,
            "title": (snap.get("meta") or {}).get("name") or "",
            "version": {"current": cur, "at": cur_rec.get("at") or "",
                        "snapshots": ver.get("snapshots") or []},
            "scenes": scenes,
            "film": {"file": film_rel, "exists": film.is_file(),
                     "size": film.stat().st_size if film.is_file() else 0,
                     "poster": poster_url(first_shot, film_rel),
                     "ready": total > 0},
            "pack": pack, "videos": total,
        })

    def _reveal(self, body):
        """在资源管理器里打开工程内的文件夹 / 选中文件。"""
        st = self.state
        rel = str(body.get("path") or "").replace("\\", "/").strip()
        if not rel:
            raise ValueError("没有路径")
        p = (st.root / rel).resolve()
        root = st.root.resolve()
        try:
            inside = p.is_relative_to(root)
        except AttributeError:
            inside = str(p).startswith(str(root) + os.sep)
        if not inside:
            raise ValueError("路径不在工程里")
        if not p.exists():
            raise ValueError("路径不存在：%s" % rel)
        is_dir = p.is_dir()
        try:
            if os.name == "nt":
                if is_dir:
                    subprocess.Popen(["explorer", str(p)], close_fds=True)
                else:
                    subprocess.Popen(["explorer", "/select,%s" % str(p)],
                                     close_fds=True)
            elif sys.platform == "darwin":
                cmd = ["open", str(p)] if is_dir else ["open", "-R", str(p)]
                subprocess.Popen(cmd, close_fds=True)
            else:
                subprocess.Popen(["xdg-open",
                                  str(p if is_dir else p.parent)],
                                 close_fds=True)
        except Exception as e:
            raise ValueError("打不开资源管理器：%s" % str(e)[:200])
        return self._json(200, {
            "ok": True, "path": p.relative_to(root).as_posix(),
            "kind": "dir" if is_dir else "file",
        })

    def _deliver_open(self, body):
        st = self.state
        rel = str(body.get("path") or "").replace("\\", "/").strip()
        if not rel:
            raise ValueError("没有路径")
        p = (st.root / rel).resolve()
        root = st.root.resolve()
        try:
            inside = p.is_relative_to(root)
        except AttributeError:
            inside = str(p).startswith(str(root) + os.sep)
        if not inside:
            raise ValueError("路径不在工程里")
        if not p.exists():
            raise ValueError("文件不存在：%s" % rel)
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,%s" % str(p)],
                                 close_fds=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(p.parent)], close_fds=True)
        except Exception as e:
            raise ValueError("打不开资源管理器：%s" % str(e)[:200])
        return self._json(200, {"ok": True, "path": rel})

    def _deliver_build_film(self, uid, snap):
        st = self.state
        shots = self._deliver_shot_videos(uid, snap)
        if not shots:
            raise ValueError("还没有镜头")
        missing = [x["shot"] for x in shots if not x["ok"]]
        if missing:
            raise ValueError("这些镜还没有视频：%s" % "、".join(missing))
        inputs = [st.root / x["file"] for x in shots]
        out = st.root / "units" / uid / "deliver" / "film.mp4"
        return self._ffmpeg_concat(inputs, out)

    def _deliver_film(self, q):
        st = self.state
        uid = self._unit(q)
        snap = st.snapshot(uid, fresh=True)
        p = self._deliver_build_film(uid, snap)
        if str((q.get("download") or [""])[0]) == "1":
            name = "%s_%s_全片.mp4" % (
                re.sub(r'[\\/:*?"<>|]', "-", str((snap.get("meta") or {}).get("name") or "片")),
                uid)
            return self._download(p.read_bytes(), name, "video/mp4")
        return self._file(p)

    def _deliver_poster(self, q):
        """封面缩略图：首帧图缩小；没有首帧图时抽视频第一帧。都缓存。"""
        st = self.state
        uid = self._unit(q)
        rel = str((q.get("file") or [""])[0]).replace("\\", "/").strip()
        src = self._safe(rel)
        if not src or not src.is_file():
            return self._json(404, {"ok": False})
        ext = src.suffix.lower()
        is_video = ext in (".mp4", ".webm", ".mov", ".m4v")
        is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".avif")
        if not (is_video or is_image):
            return self._json(404, {"ok": False})
        try:
            stat = src.stat()
        except OSError:
            return self._json(404, {"ok": False})
        key = hashlib.sha1(
            ("%s|%s|%s" % (src, stat.st_mtime_ns, stat.st_size)).encode("utf-8")
        ).hexdigest()[:16]
        # 缩略图是运行态缓存，放工程外（桌面版 AIV_STATE_DIR），不污染客户工程。
        cache = aux_dir(st.root) / "deliver-thumbs" / uid / (key + ".jpg")
        if not cache.is_file():
            ff = _ffmpeg_path()
            if not ff:
                return self._file(src) if is_image else self._json(
                    404, {"ok": False, "error": "no ffmpeg"})
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                return self._json(404, {"ok": False})
            tmp = cache.with_name(
                cache.stem + ".%d-%s.tmp.jpg" % (os.getpid(), secrets.token_hex(3)))

            def grab(seek):
                args = [ff, "-y"]
                if seek is not None:
                    args += ["-ss", str(seek)]
                args += ["-i", str(src), "-frames:v", "1",
                         "-vf", "scale=480:-2", "-q:v", "3", str(tmp)]
                return subprocess.run(args, capture_output=True, text=True,
                                      timeout=20, creationflags=NO_WINDOW)

            try:
                r = grab(0.1 if is_video else None)
                if is_video and (r.returncode != 0 or not tmp.is_file()):
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    r = grab(None)
                if r.returncode != 0 or not tmp.is_file():
                    if is_image:
                        return self._file(src)
                    return self._json(404, {"ok": False, "error": "ffmpeg failed"})
                tmp.replace(cache)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                if is_image:
                    return self._file(src)
                return self._json(404, {"ok": False, "error": "ffmpeg failed"})
        return self._file(cache)

    def _deliver_video_nodes(self, uid, snap):
        """所有 LibTV 生成好的视频节点（role=video），每节点一条、取 current_rev。"""
        st = self.state
        gd = {}
        try:
            gd = graph_mod.normalize(graph_mod.load(st.root, uid), uid)
        except Exception:
            gd = {}
        out = []
        for gseg in (gd.get("segments") or []):
            for n in (gseg.get("nodes") or []):
                if n.get("role") != "video":
                    continue
                cur = _node_current(n)
                f = str(cur.get("file") or n.get("file") or "").replace("\\", "/")
                ok = bool(f and (st.root / f).is_file())
                out.append({
                    "id": n.get("id") or "", "node_id": _node_key(n),
                    "name": n.get("name") or n.get("id") or "",
                    "segment": gseg.get("id") or "",
                    "shot": graph_mod.shot_of(n),
                    "rev": int(n.get("current_rev") or n.get("rev") or 0),
                    "file": f, "ok": ok,
                    "url": cur.get("url") or n.get("url") or "",
                    "size": (st.root / f).stat().st_size if ok else 0,
                })
        if out:
            return out
        # 没有图谱节点时，退回快照里的每镜视频
        for s in (snap.get("shots") or []):
            for m in (s.get("media") or []):
                if m.get("kind") != "video":
                    continue
                f = str(m.get("path") or "").replace("\\", "/")
                if f:
                    out.append({
                        "id": "video_%s" % s.get("id"), "node_id": "",
                        "name": "镜 %s 视频" % s.get("id"),
                        "segment": s.get("storyboard") or "",
                        "shot": s.get("id") or "", "rev": 1,
                        "file": f, "ok": (st.root / f).is_file(),
                        "url": "", "size": 0,
                    })
        return out

    @staticmethod
    def _safe_file(text, fallback="file"):
        s = re.sub(r'[\\/:*?"<>|\r\n]+', "-", str(text or "")).strip(" .-")
        return (s[:80] or fallback)

    def _libtv_audio_nodes(self, uid, snap):
        """扫 LibTV 画布上的 audio 节点，返回可下载的音频 URL。"""
        st = self.state
        pids = []
        for sb in (snap.get("storyboards") or []):
            if sb.get("suggested"):
                continue
            pid = str((sb.get("libtv") or {}).get("project_id") or "")
            if pid and pid not in pids:
                pids.append(pid)
        out, seen = [], set()
        if not pids:
            return out
        try:
            client = libtv_mcp.LibTV(st.root)
        except Exception:
            return out
        for pid in pids:
            try:
                rows = client.nodes(pid)
            except Exception:
                continue
            for n in rows:
                data = n.get("data") or {}
                t = str(n.get("type") or data.get("type") or "")
                if t != "audio":
                    continue
                urls = data.get("url") or data.get("audioUrls") or []
                if isinstance(urls, str):
                    urls = [urls]
                extra = data.get("audioList") or []
                for x in extra:
                    if isinstance(x, str):
                        urls.append(x)
                    elif isinstance(x, dict) and x.get("url"):
                        urls.append(x["url"])
                for url in urls:
                    if not isinstance(url, str) or not url.startswith("http"):
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    out.append({
                        "id": str(n.get("id") or ""),
                        "name": str(data.get("name") or n.get("id") or "音频"),
                        "url": url,
                    })
        return out

    def _download_to(self, url, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in (".mp3", ".wav", ".m4a", ".aac", ".ogg"):
            ext = ".mp3"
        if dst.suffix.lower() != ext:
            dst = dst.with_suffix(ext)
        req = urllib.request.Request(url, headers={"User-Agent": "workbench/1"})
        tmp = dst.with_suffix(dst.suffix + ".part")
        with urllib.request.urlopen(req, timeout=300) as resp, \
                open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(dst)
        return dst

    def _deliver_export(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        snap = st.snapshot(uid, fresh=True)
        videos = self._deliver_video_nodes(uid, snap)
        if not videos and not body.get("allow_missing"):
            raise ValueError("还没有 LibTV 生成的视频节点")
        missing = [x["id"] for x in videos if not x["ok"]]
        if missing and not body.get("allow_missing"):
            raise ValueError("这些视频节点还没有结果：%s" % "、".join(missing))

        vp = st.root / "units" / uid / "version.json"
        verdoc = _jload(vp, {}) or {}
        snaps = verdoc.get("snapshots")
        if not isinstance(snaps, list):
            snaps = []
        # 用户看到的就是「交付包」，不再用 v1 / v8 这种版本号命名。
        # 版本记录仍留在 version.json 里，但目录只是一个。
        version = "交付包"
        outdir = st.root / "units" / uid / "deliver" / version
        if outdir.is_dir():
            shutil.rmtree(outdir, ignore_errors=True)
        outdir.mkdir(parents=True, exist_ok=True)
        files = []
        warnings = []

        # 每一镜取 current_rev 的成片，先单独放一份，方便剪回单镜。
        shots = self._deliver_shot_videos(uid, snap)
        for x in shots:
            if not x["ok"]:
                continue
            ext = Path(x["file"]).suffix or ".mp4"
            dst = outdir / "shots" / (self._safe_file(x["shot"], "shot") + ext)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(st.root / x["file"], dst)
            files.append(dst)

        # 每个分段一个视频：按该段的镜顺序拼接。
        segments = self._deliver_segments(snap)
        seg_files = []
        for seg in segments:
            if not seg["ok"]:
                miss = "、".join(seg.get("missing_shots") or [])
                warnings.append("分段 %s 缺镜%s，未生成分段视频" % (
                    seg["id"], ("（%s）" % miss) if miss else ""))
                continue
            rows = [x for x in (seg.get("shots") or []) if x["ok"]]
            if not rows:
                continue
            ext = Path(rows[0]["file"]).suffix or ".mp4"
            dst = outdir / "segments" / (self._safe_file(seg["id"], "segment") + ext)
            try:
                self._ffmpeg_concat([st.root / x["file"] for x in rows], dst)
                files.append(dst)
                seg_files.append(dst)
            except Exception as e:
                warnings.append("分段 %s 拼接失败：%s" % (seg["id"], e))

        # 所有分段都齐，才拼一个全片；缺镜时保留分段视频并明确提示。
        film_dst = outdir / "全片.mp4"
        if (segments and all(seg["ok"] for seg in segments)
                and len(seg_files) == len(segments)):
            try:
                self._ffmpeg_concat(seg_files, film_dst)
                files.append(film_dst)
            except Exception as e:
                warnings.append("全片拼接失败：%s" % e)
        elif segments:
            warnings.append("有分段缺镜，未拼全片")

        # 单独做的音频节点 / 本地音频文件
        audio_seen = set()
        for x in self._libtv_audio_nodes(uid, snap):
            stem = self._safe_file("%s_%s" % (x["name"], x["id"]), "audio")
            try:
                p = self._download_to(x["url"], outdir / "audio" / stem)
                if p not in audio_seen:
                    audio_seen.add(p)
                    files.append(p)
            except Exception:
                continue
        for a in (snap.get("assets") or []):
            for f in (a.get("files") or []):
                rel = str((f or {}).get("path") or "").replace("\\", "/")
                if not rel or rel in audio_seen:
                    continue
                if Path(rel).suffix.lower() not in (".mp3", ".wav", ".m4a", ".aac", ".ogg"):
                    continue
                src = st.root / rel
                if not src.is_file():
                    continue
                stem = self._safe_file("%s_%s" % (a.get("name") or a.get("id"), src.stem), "audio")
                dst = outdir / "audio" / (stem + src.suffix.lower())
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                audio_seen.add(rel)
                files.append(dst)

        # 视频介绍：创意 / 剧本 / 内容 / 段 / 分镜，不带提示词
        body_md = export_mod.build(snap, st.root, include_prompts=False)
        lines = body_md.splitlines()
        if lines and lines[0].startswith("# "):
            body_md = "\n".join(lines[1:]).lstrip()
        # 段表
        seg_rows = ["## 段", "", "| 段 | 名称 | 秒 | 镜 |",
                    "| :--- | :--- | ---: | :--- |"]
        for sb in (snap.get("storyboards") or []):
            if sb.get("suggested"):
                continue
            seg_rows.append("| %s | %s | %s | %s |" % (
                sb.get("id") or "", sb.get("name") or "",
                sb.get("sec") or "", "、".join(sb.get("shots") or [])))
        seg_block = "\n".join(seg_rows) + "\n\n"
        # 分镜段落单独起一个二级标题，和创意 / 剧本 / 段并列
        for sc in (snap.get("scenes") or []):
            marker = "\n### %s" % sc.get("id")
            if sc.get("id") and marker in body_md:
                body_md = body_md.replace(
                    marker, "\n## 分镜\n" + marker, 1)
                break
        if "## 分镜" in body_md:
            body_md = body_md.replace("## 分镜", seg_block + "## 分镜", 1)
        else:
            body_md += "\n\n" + seg_block
        one_line = ""
        for brief in (st.root / "units" / uid / "design" / "导演简报.md",
                      st.root / "design" / "导演简报.md"):
            try:
                for line in brief.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- 一句话："):
                        one_line = line.split("：", 1)[1].strip()
                        break
                if one_line:
                    break
            except OSError:
                continue
        intro = "# 视频介绍\n\n## 创意\n\n"
        if one_line:
            intro += "> " + one_line + "\n\n"
        intro += body_md
        ip = outdir / "视频介绍.md"
        ip.write_text(intro, encoding="utf-8")
        files.append(ip)

        def finfo(p):
            h = hashlib.sha256()
            size = 0
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
                    size += len(chunk)
            return {"name": p.relative_to(outdir).as_posix(), "size": size,
                    "sha256": h.hexdigest()}
        manifest = {
            "version": version, "unit": uid,
            "title": (snap.get("meta") or {}).get("name") or "",
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": [finfo(p) for p in files],
            "videos": [x for x in shots if x["ok"]],
            "segments": [{
                "id": seg["id"], "ok": seg["ok"],
                "missing_shots": seg.get("missing_shots") or [],
            } for seg in segments],
            "film": {"file": "全片.mp4", "ok": film_dst.is_file(),
                     "size": film_dst.stat().st_size if film_dst.is_file() else 0},
            "missing": missing, "warnings": warnings,
            "review": snap.get("review") or {},
        }
        # manifest 只留在工程里做版本追踪，不进交付包
        mp = st.root / "units" / uid / "deliver" / ("%s-manifest.json" % version)
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

        rel = outdir.relative_to(st.root).as_posix()
        # 同一个「交付包」每次覆盖，版本记录只保留最新一条；旧的 vN 记录不动。
        snaps = [s for s in snaps if str(s.get("id")) != version]
        verdoc.update({
            "current": version, "status": "snapshot",
            "snapshots": snaps + [{
                "id": version, "at": manifest["exported_at"], "dir": rel,
                "manifest": mp.relative_to(st.root).as_posix(),
            }],
        })
        _jwrite(vp, verdoc)
        st.invalidate(["storyboards", "media"])
        return self._json(200, {
            "ok": True, "unit": uid, "version": version, "dir": rel,
            "files": [p.relative_to(outdir).as_posix() for p in files],
            "missing": missing, "warnings": warnings,
            "film": {"file": "全片.mp4", "ok": film_dst.is_file()},
            "download": "/api/deliver/zip?unit=%s&version=%s" % (
                quote(uid), quote(version)),
        })

    def _deliver_zip(self, q):
        st = self.state
        uid = self._unit(q)
        ver = str((q.get("version") or [""])[0]).strip()
        if not ver:
            vd = _jload(st.root / "units" / uid / "version.json", {}) or {}
            ver = str(vd.get("current") or "")
        if not ver:
            raise ValueError("还没有交付快照；先点「导出交付包」")
        base = st.root / "units" / uid / "deliver" / ver
        if not base.is_dir():
            raise ValueError("找不到交付快照：%s" % ver)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(base.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(base).as_posix()
                # 交付包：每一镜 / 每个分段 / 全片 / 音频 / 视频介绍。
                if not (rel == "视频介绍.md"
                        or rel == "全片.mp4"
                        or rel == "film.mp4"
                        or rel.startswith("segments/")
                        or rel.startswith("shots/")
                        or rel.startswith("videos/")
                        or rel.startswith("audio/")):
                    continue
                z.write(p, rel)
        name = "%s_%s_交付包.zip" % (
            re.sub(r'[\\/:*?"<>|]', "-", str(st.root.name)), uid)
        return self._download(buf.getvalue(), name, "application/zip")

    # ---------- LibTV 索引 ----------
    def _libtv_index_build(self, uid):
        st = self.state
        snap = st.snapshot(uid)
        pj = _jload(st.root / "project.json", {}) or {}
        cfg = snap_mod._libtv_cfg(pj)
        return libtv_index.build(st.root, uid, snap, cfg)

    def _libtv_index_read(self, q):
        uid = self._unit(q)
        idx = self._libtv_index_build(uid)
        p = self.state.root / "units" / uid / "libtv-index.json"
        if str((q.get("rebuild") or [""])[0]) == "1":
            _backup(self.state, p)
            _jwrite(p, idx)
        out = dict(idx)
        out["path"] = "units/%s/libtv-index.json" % uid
        out["exists"] = p.is_file()
        out["ok"] = True
        return self._json(200, out)

    def _libtv_index_rebuild(self, body):
        uid = str(body.get("unit") or self.state.unit)
        idx = self._libtv_index_build(uid)
        p = self.state.root / "units" / uid / "libtv-index.json"
        _backup(self.state, p)
        _jwrite(p, idx)
        out = dict(idx)
        out["path"] = "units/%s/libtv-index.json" % uid
        out["exists"] = True
        out["ok"] = True
        return self._json(200, out)

    def _libtv_link_get(self, q):
        uid = self._unit(q)
        kind = str((q.get("kind") or [""])[0]).strip()
        fid = str((q.get("id") or [""])[0]).strip()
        if not kind or not fid:
            raise ValueError("要 kind 和 id")
        idx = self._libtv_index_build(uid)
        e = next((x for x in (idx.get("entries") or [])
                  if x.get("local_kind") == kind
                  and x.get("local_id") == fid), None)
        if not e:
            raise ValueError("索引里没有：%s/%s" % (kind, fid))
        return self._json(200, {"ok": True, "entry": e,
                                "url": e.get("url") or ""})

    def _storyboard_stills(self, body):
        """给分镜里的每一镜建 / 改一个首帧图节点。

        只建图节点、挂参考图，不在本地出图；生成由用户在 LibTV 点，
        或 run=true 时由 LibTV 直接开跑。
        """
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sb_id = str(body.get("sb") or "").strip()
        dry = bool(body.get("dry_run", True))
        run_now = bool(body.get("run"))
        if not sb_id:
            raise ValueError("没有分镜号")
        if not dry and not body.get("confirm"):
            raise ValueError("建首帧节点需要 confirm=true")
        if run_now and not dry and not body.get("confirm_generate"):
            raise ValueError(
                "生成会消耗积分，只能由用户在界面上确认（缺 confirm_generate=true）")
        snap = st.snapshot(uid)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == sb_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % sb_id)
        plan = self._storyboard_stills_plan(snap, sb)
        if dry:
            return self._json(200, dict(plan, dry_run=True))
        if plan.get("missing"):
            names = "、".join(x.get("label") or x.get("key")
                             for x in plan["missing"][:6])
            raise ValueError("这些引用资产还没有可用图：%s" % names)
        if plan.get("errors"):
            raise ValueError("；".join(plan["errors"]))
        if plan.get("needs_sync"):
            res = self._libtv_ensure({
                "dry_run": False, "confirm": True,
                "scenes": [sb.get("scene") or ""]})
            if res.get("errors"):
                raise ValueError("本场 LibTV 结构没建好：%s" % (
                    res["errors"][0].get("reason") or ""))
            snap = st.snapshot(uid, fresh=True)
            sb = next((x for x in (snap.get("storyboards") or [])
                       if x["id"] == sb_id), None)
            if not sb:
                raise ValueError("同步本场后仍找不到分镜：%s" % sb_id)
            plan = self._storyboard_stills_plan(snap, sb)
        pid = str(plan.get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        fid = int(plan.get("asset_folder_id") or 0)
        refs = plan.get("refs") or []
        upload_errors = []
        for i, r in enumerate(refs):
            if r.get("node_id"):
                continue
            absimg = st.root / r["file"] if r.get("file") else None
            if not absimg or not absimg.is_file():
                continue
            try:
                nid = client.upload_image(
                    pid, absimg, r.get("label") or r["key"],
                    x=-1200, y=600 + i * 420)
                r["node_id"] = nid
                if fid:
                    try:
                        client.call("save_asset_from_node", {
                            "nodeId": nid, "folderId": fid,
                            "name": (r.get("label") or r["key"])[:180],
                            "idempotencyKey": libtv_mcp.safe_key(
                                "wb-still-ref-%s-%s-%s" % (
                                    uid, sb_id, r["key"])),
                        }, timeout=60)
                    except Exception:
                        pass
            except Exception as e:
                upload_errors.append("%s：%s" % (
                    r.get("label") or r["key"], str(e)[:160]))
        self._remember_scene_refs(st, uid, sb, refs)
        left = [r["node_id"] for r in refs if r.get("node_id")]
        sl_path = st.root / sb["libtv_path"]
        sl = _jload(sl_path, {}) or {}
        if not isinstance(sl, dict):
            sl = {}
        stills_meta = sl.get("stills")
        if not isinstance(stills_meta, dict):
            stills_meta = {}
        gname = self._group_name(client, pid, plan.get("group_id") or "",
                                 plan.get("group_name") or "")
        rows, errors = [], []
        for i, row in enumerate(plan["shots"]):
            sid, prompt = row["shot"], row["prompt"]
            if not prompt:
                errors.append("%s：没有静帧提示词" % sid)
                continue
            old = stills_meta.get(sid) if isinstance(
                stills_meta.get(sid), dict) else {}
            node_id = str(old.get("node_id") or "")
            ph = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:10]
            params = {"quality": plan["quality"], "ratio": plan["ratio"],
                      "count": 1}
            name = ("%s f%s 首帧" % (sb_id, sid))[:180]
            key = libtv_mcp.safe_key(
                "wb-still-%s-%s-%s-%s" % (uid, sb_id, sid, ph))
            try:
                if node_id:
                    args = {
                        "node": node_id, "idempotencyKey": key,
                        "projectId": pid, "prompt": prompt,
                        "model": plan["model"], "params": params,
                        "run": run_now,
                    }
                    if left:
                        args["leftEnsure"] = left
                    _call_write(client, "update_node", args, timeout=120)
                else:
                    args = {
                        "name": name, "nodeType": "image",
                        "idempotencyKey": key, "projectId": pid,
                        "prompt": prompt, "model": plan["model"],
                        "params": params, "left": left, "run": run_now,
                        "x": 100 + i * 320, "y": 900,
                    }
                    try:
                        r = _call_write(client, "create_node", args,
                                        timeout=120)
                    except Exception as e:
                        if not _name_conflict(e):
                            raise
                        node_id = _adopt_named_node(
                            client, pid, name, "image", e)
                        upd = {
                            "node": node_id, "idempotencyKey": libtv_mcp.safe_key(
                                "wb-still-adopt-%s-%s-%s-%s" % (
                                    uid, sb_id, sid, ph)),
                            "projectId": pid, "newName": name,
                            "prompt": prompt, "model": plan["model"],
                            "params": params, "run": run_now,
                        }
                        if left:
                            upd["leftEnsure"] = left
                        r = _call_write(client, "update_node", upd,
                                        timeout=120)
                    else:
                        node_id = str(
                            (r.get("plan") or {}).get("nodeId") or
                            (r.get("operationResult") or {}).get(
                                "subjectNodeId") or "")
                if not node_id:
                    raise RuntimeError("LibTV 没返回图片节点 ID")
                if gname:
                    gerr = self._bind_group_verified(
                        client, pid, gname, node_id,
                        "wb-still-bind-%s-%s-%s" % (uid, sb_id, sid))
                    if gerr:
                        errors.append("%s：%s" % (sid, gerr))
                stills_meta[sid] = {
                    "node_id": node_id, "prompt_hash": ph,
                    "status": "已布线", "updated_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S"),
                }
                rows.append({"shot": sid, "node_id": node_id,
                             "status": "已布线"})
            except Exception as e:
                errors.append("%s：%s" % (sid, str(e)[:200]))
                rows.append({"shot": sid, "status": "error",
                             "message": str(e)[:200]})
        sl["stills"] = stills_meta
        _backup(st, sl_path)
        _jwrite(sl_path, sl)
        st.invalidate(["storyboards", "media", "shots"])
        return self._json(200, {
            "ok": not errors, "sb": sb_id, "project_id": pid,
            "group_id": plan.get("group_id") or "",
            "group_name": gname, "shots": rows, "errors": errors,
            "upload_errors": upload_errors, "run": run_now,
        })

    def _storyboard_stills_sync(self, body):
        """把首帧图节点的生成结果下载到分镜的 stills/。"""
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sb_id = str(body.get("sb") or "").strip()
        if not sb_id:
            raise ValueError("没有分镜号")
        snap = st.snapshot(uid)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == sb_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % sb_id)
        if not sb.get("libtv_path"):
            raise ValueError("这个分镜没有 libtv.json")
        sl_path = st.root / sb["libtv_path"]
        sl = _jload(sl_path, {}) or {}
        stills_meta = sl.get("stills") if isinstance(sl, dict) else {}
        if not isinstance(stills_meta, dict) or not stills_meta:
            raise ValueError("这个分镜还没有首帧节点：先点「建首帧节点」")
        pid = str((sb.get("libtv") or {}).get("project_id") or "")
        if not pid:
            raise ValueError("这个分镜没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        rows = []
        outdir = st.root / sb["dir"] / "stills"
        for sid in sorted(stills_meta):
            meta = stills_meta[sid] if isinstance(
                stills_meta[sid], dict) else {}
            node_id = str(meta.get("node_id") or "")
            if not node_id:
                rows.append({"shot": sid, "status": "未布线"})
                continue
            try:
                r = client.call("read_node", {
                    "projectId": pid, "node": node_id}, timeout=30)
                node = r.get("node") or r
                data = node.get("data") or {}
                info = data.get("taskInfo") or {}
                urls = self._image_urls(node)
                loading = bool(info.get("loading"))
                failed = str(info.get("failedReason") or "")
                if loading:
                    rows.append({"shot": sid, "status": "generating",
                                 "message": "LibTV 还在生成"})
                    continue
                if failed:
                    rows.append({"shot": sid, "status": "error",
                                 "message": failed[:300]})
                    continue
                if not urls:
                    rows.append({"shot": sid, "status": "pending",
                                 "message": "还没有生成结果；去 LibTV 点生成"})
                    continue
                url = urls[0]
                ext = Path(urlparse(url).path).suffix.lower()
                if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                    ext = ".png"
                outdir.mkdir(parents=True, exist_ok=True)
                out = outdir / ("%s%s" % (sid, ext))
                tmp = out.with_suffix(out.suffix + ".part")
                req = urllib.request.Request(
                    url, headers={"User-Agent": "workbench/1"})
                with urllib.request.urlopen(req, timeout=300) as resp, \
                        open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                tmp.replace(out)
                meta.update({
                    "status": "已生成", "file": out.relative_to(st.root).as_posix(),
                    "url": url, "task_id": str(info.get("taskId") or ""),
                    "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                stills_meta[sid] = meta
                rows.append({"shot": sid, "status": "done",
                             "file": out.relative_to(st.root).as_posix(),
                             "url": url})
            except Exception as e:
                rows.append({"shot": sid, "status": "error",
                             "message": str(e)[:300]})
        sl["stills"] = stills_meta
        _backup(st, sl_path)
        _jwrite(sl_path, sl)
        st.invalidate(["storyboards", "media", "shots"])
        return self._json(200, {"ok": True, "sb": sb_id, "shots": rows})

    def _storyboard_pull(self, body):
        """同步线上：把 LibTV 上改过的节点提示词回读，别让本地覆盖线上。"""
        st = self.state
        sb_id = str(body.get("sb") or "").strip()
        if not sb_id:
            raise ValueError("没有分镜号")
        snap = st.snapshot(st.unit)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == sb_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % sb_id)
        if not sb.get("libtv_path"):
            raise ValueError("这个分镜没有 libtv.json")
        sl_path = st.root / sb["libtv_path"]
        sl = _jload(sl_path, {}) or {}
        video = sl.get("video") if isinstance(sl.get("video"), dict) else {}
        node_id = str(video.get("node_id") or "")
        pid = str((sb.get("libtv") or {}).get("project_id") or "")
        if not node_id:
            raise ValueError("这个分镜还没布线：先点「布线到 LibTV」")
        if not pid:
            raise ValueError("这个分镜没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        r = client.call("read_node", {"projectId": pid, "node": node_id}, timeout=30)
        node = r.get("node") or r
        remote = _node_prompt(node)
        if not remote:
            raise ValueError("线上这个节点读不到提示词（可能还没建文本节点）")
        local_nodes = sl.get("nodes") or []
        local = str((local_nodes[0] or {}).get("prompt") or "")
        changed = remote.strip() != local.strip()
        out = {
            "ok": True, "sb": sb_id, "changed": changed,
            "local_prompt": local, "remote_prompt": remote,
            "node_id": node_id,
        }
        if not changed:
            sl["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sl_path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
            out["message"] = "线上和本地一致，不用同步"
            return out
        if not body.get("apply"):
            out["message"] = "线上改过：确认后会把线上提示词写回本地"
            return out
        if not local_nodes:
            local_nodes = [{}]
        local_nodes[0]["prompt"] = remote
        local_nodes[0].setdefault("id", "n1")
        sl["nodes"] = local_nodes
        sl["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        sl["pulled_from"] = "libtv"
        sl_path.write_text(json.dumps(sl, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        out["message"] = "已把线上提示词写回本地"
        return out

    def _storyboard_sync(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sb_id = str(body.get("sb") or "").strip()
        if not sb_id:
            raise ValueError("没有分镜号")
        snap = st.snapshot(uid)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x["id"] == sb_id), None)
        if not sb:
            raise ValueError("找不到分镜：%s" % sb_id)
        if not sb.get("libtv_path"):
            raise ValueError("这个分镜没有 libtv.json")
        sl_path = st.root / sb["libtv_path"]
        sl = _jload(sl_path, {})
        sl = sl if isinstance(sl, dict) else {}
        video = sl.get("video") if isinstance(sl.get("video"), dict) else {}
        node_id = str(video.get("node_id") or "")
        pid = str((sb.get("libtv") or {}).get("project_id") or "")
        if not node_id:
            raise ValueError("这个分镜还没布线：先点「布线核心流程」")
        if not pid:
            raise ValueError("这个分镜没有 LibTV 项目")
        client = libtv_mcp.LibTV(st.root)
        r = client.call("read_node", {"projectId": pid, "node": node_id},
                        timeout=30)
        node = r.get("node") or r
        data = node.get("data") or {}
        info = data.get("taskInfo") or {}
        task_id = str(info.get("taskId") or video.get("task_id") or "")
        urls = self._video_urls(node)
        loading = bool(info.get("loading"))
        failed = str(info.get("failedReason") or "")
        if not urls and task_id and not loading:
            try:
                ts = client.call("task_status", {
                    "project_id": pid, "task_id": task_id}, timeout=30)
                urls = self._video_urls({"data": ts})
                if not urls:
                    tr = ts.get("result") if isinstance(ts, dict) else {}
                    urls = self._video_urls({"data": {"taskResult": tr}})
            except Exception:
                pass
        if loading:
            status, message = "generating", "LibTV 还在生成，稍后再同步"
        elif failed:
            status, message = "error", failed[:300]
        elif not urls:
            status, message = "pending", "LibTV 还没有生成结果；请先在 LibTV 里点生成"
        else:
            status, message = "done", "已同步生成结果"

        file_rel, url, rev = str(video.get("file") or ""), urls[0] if urls else "", \
            int(video.get("rev") or 0)
        if urls and not (video.get("url") == url and file_rel and
                         (st.root / file_rel).is_file()):
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in (".mp4", ".webm", ".mov", ".m4v"):
                ext = ".mp4"
            rev += 1
            target = st.root / sb["dir"] / "renders" / ("%s-r%d%s" % (
                sb_id, rev, ext))
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            req = urllib.request.Request(url, headers={"User-Agent": "workbench/1"})
            with urllib.request.urlopen(req, timeout=300) as resp, \
                    open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
            tmp.replace(target)
            file_rel = target.relative_to(st.root).as_posix()

        video.update({
            "node_id": node_id, "status": status,
            "task_id": task_id, "url": url, "file": file_rel, "rev": rev,
            "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if status == "done":
            video["output_prompt_hash"] = video.get("prompt_hash") or ""
            video["output_refs_hash"] = video.get("refs_hash") or ""
            video["output_method"] = video.get("method") or ""
            video.pop("error", None)
        elif status == "error":
            video["error"] = failed[:300]
        sl["video"] = video
        nodes = [n for n in (sl.get("nodes") or []) if isinstance(n, dict)]
        if nodes:
            nodes[0]["status"] = {
                "done": "已生成", "generating": "生成中",
                "pending": "待生成", "error": "失败",
            }.get(status, status)
            sl["nodes"] = nodes
        _backup(st, sl_path)
        _jwrite(sl_path, sl)
        st.invalidate(["storyboards", "media", "shots"])
        return self._json(200, {
            "ok": True, "sb": sb_id, "node_id": node_id,
            "status": status, "task_id": task_id, "url": url,
            "file": file_rel, "rev": rev, "message": message,
        })

    def _gate_start(self, body):
        gate = self._gate_info(fresh=True)
        if not gate.get("ready"):
            raise ValueError("还没就绪：%s" % "、".join(gate.get("missing") or []))
        ensure = None
        if body.get("auto_create", True):
            ensure = self._libtv_ensure({
                "dry_run": False, "confirm": True, "container_only": True})
            if ensure.get("errors"):
                raise ValueError("LibTV 建设失败：%s" % ensure["errors"][0].get("reason", ""))
        p = self.state.root / ".workbuddy" / "tmp" / "started.json"
        _jwrite(p, {
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "agent": gate.get("agent") or {},
            "skill": gate.get("skill") or {},
            "libtv": gate.get("libtv") or {},
            "ensure": ensure,
        })
        self.state._gate_cache = (0.0, None)
        gate = self._gate_info(fresh=True)
        gate["ensure"] = ensure
        return gate

    def _keyframe_prompt(self, snap, shot, scene):
        bits = ["黑白线稿关键帧，单一画面（不是漫画分格），干净墨线，白底，极少阴影，"
                "电影感 16:9 构图。"]
        framing = str(shot.get("framing") or "").strip()
        body = str(shot.get("shot") or "").strip()
        if framing:
            bits.append("景别 %s。" % framing)
            if body:
                bits.append("镜头：%s。" % body.rstrip("。"))
        elif body:
            # 旧工程把景别写在 shot 里，保持兼容。
            bits.append("景别 %s。" % body.rstrip("。"))
        if shot.get("visual"):
            bits.append("画面：%s。" % shot["visual"].rstrip("。"))
        if shot.get("action"):
            bits.append("动作：%s。" % shot["action"].rstrip("。"))
        if shot.get("emotion"):
            bits.append("情绪：%s。" % shot["emotion"].rstrip("。"))
        bits.append("只要一张完整的线稿画面，不要颜色、渐变、写实皮肤、文字、字幕、"
                    "水印、UI、漫画分格、气泡、纸张上的字。")
        return "".join(bits)

    def _keyframe_gen(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        sid = str(body.get("shot") or "").strip().zfill(3)
        if not sid:
            raise ValueError("没有镜号")
        snap = st.snapshot(uid)
        shot = next((s for s in snap["shots"] if s["id"] == sid), None)
        if not shot:
            raise ValueError("找不到镜头：%s" % sid)
        scene = next((sc for sc in snap["scenes"] if sid in sc["shots"]), None)
        if not scene:
            raise ValueError("镜头不在任何场里")
        lib = scene.get("libtv") or {}
        pid = str(lib.get("project_id") or "")
        if not pid:
            raise ValueError("这一场还没有 LibTV 项目，先在开工检查里建设")
        prompt = self._keyframe_prompt(snap, shot, scene)
        model = "z-image"
        meta_path = st.root / "units" / uid / "keyframes" / "keyframes.json"
        meta = _jload(meta_path, {}) or {}
        rows = {str(r.get("shot")): r for r in (meta.get("rows") or []) if isinstance(r, dict)}
        old = rows.get(sid)
        if body.get("dry_run"):
            return self._json(200, {
                "ok": True, "dry_run": True, "shot": sid, "project_id": pid,
                "model": model, "size": "1K", "prompt": prompt,
                "exists": bool(old and old.get("status") == "done"),
            })
        if old and old.get("status") == "done" and not body.get("force"):
            f = st.root / str(old.get("file") or "")
            if f.exists():
                return self._json(200, {"ok": True, "cached": True, **old})
        if not body.get("confirm"):
            raise ValueError("生成线稿需要 confirm=true（会消耗积分）")
        self._need_libtv()
        rev = int((old or {}).get("rev") or 0) + 1
        key = "wb-kf-%s-%s-%s-r%d" % (
            uid, sid, hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8], rev)
        client = libtv_mcp.LibTV(st.root)
        created = client.call("create_node", {
            "name": "%s 线稿关键帧 r%d" % (sid, rev), "nodeType": "image",
            "model": model, "projectId": pid, "prompt": prompt,
            "params": {"quality": "1K", "ratio": "16:9", "count": 1},
            "run": True, "idempotencyKey": key,
        }, timeout=180)
        node_id = str((created.get("plan") or {}).get("nodeId")
                      or (created.get("operationResult") or {}).get("subjectNodeId")
                      or "")
        task_id = str(created.get("task_id") or "")
        if not node_id or not task_id:
            raise ValueError("LibTV 没返回节点或任务：%s" % json.dumps(
                created, ensure_ascii=False)[:300])
        deadline = time.time() + 180
        outcome = ""
        while time.time() < deadline:
            ts = client.call("task_status", {"project_id": pid, "task_id": task_id},
                             timeout=30)
            outcome = str(ts.get("outcome") or ts.get("status") or "")
            if outcome.upper() in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
                break
            if outcome.upper() in ("FAILED", "ERROR", "CANCELED", "CANCELLED"):
                raise ValueError("LibTV 生成失败：%s" % (
                    ts.get("error") or ts.get("result") or outcome))
            time.sleep(3)
        else:
            raise ValueError("LibTV 生成超时（task %s）" % task_id)
        node = client.call("read_node", {"projectId": pid, "node": node_id}, timeout=30)
        data = (node.get("node") or {}).get("data") or {}
        urls = data.get("url") or []
        url = urls[0] if urls else ""
        if not url:
            raise ValueError("生成成功但没有图片 URL")
        # 关键帧节点挂进对应分镜的 group（分镜画布里的工作流）
        group_id, group_error = "", ""
        sb = next((x for x in snap["storyboards"]
                   if x["id"] == shot.get("storyboard")), None)
        if sb:
            group_id = str((sb.get("libtv") or {}).get("group_id") or "")
            gname = ("%s %s" % (sb["id"], sb["name"]))[:180]
            if group_id:
                try:
                    client.call("manage_group", {
                        "action": "bind", "projectId": pid, "name": gname,
                        "nodes": [node_id],
                        "idempotencyKey": "wb-kf-bind-%s-%s-r%d" % (uid, sid, rev),
                    }, timeout=30)
                except Exception as e:
                    group_error = str(e)[:300]
        with urllib.request.urlopen(url, timeout=90) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
        ext = ".png" if "png" in ctype or ".png" in url.lower() else ".jpg"
        outdir = st.root / "units" / uid / "keyframes"
        outdir.mkdir(parents=True, exist_ok=True)
        f = outdir / (sid + ext)
        if f.exists():
            _backup(st, f)
        f.write_bytes(raw)
        row = {
            "shot": sid, "file": f.relative_to(st.root).as_posix(),
            "project_id": pid, "node_id": node_id, "task_id": task_id,
            "resource_id": (data.get("resourceIds") or [""])[0], "url": url,
            "model": model, "size": "1K", "rev": rev, "status": "done",
            "style": "line-art", "prompt": prompt,
            "group_id": group_id, "group_error": group_error,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows[sid] = row
        meta = {"unit": uid, "model": model, "size": "1K", "style": "line-art",
                "rows": [rows[k] for k in sorted(rows)]}
        _backup(st, meta_path)
        _jwrite(meta_path, meta)
        st.invalidate(["media", "shots"])
        return self._json(200, {"ok": True, **row})

    def _libtv_link(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit)
        target = str(body.get("target") or "").strip()
        if target == "unit":
            path = st.root / "units" / uid / "libtv.json"
        elif target == "scene":
            sid = str(body.get("scene") or "").strip()
            sdir = self._scene_dir(uid, sid)
            if not sdir:
                raise ValueError("找不到场目录：%s" % sid)
            path = sdir / "libtv.json"
        elif target == "sb":
            sbdir = self._storyboard_dir(uid, str(body.get("sb") or "").strip())
            if not sbdir:
                raise ValueError("找不到分镜目录")
            path = sbdir / "libtv.json"
        elif target == "asset":
            adir, _rel, _m = self._asset(body.get("asset"))
            canvas = str(body.get("canvas") or "").strip()
            if not canvas:
                raise ValueError("没有画布号")
            path = adir / "build" / canvas / "libtv.json"
            if not path.parent.is_dir():
                raise ValueError("找不到画布：%s" % canvas)
        else:
            raise ValueError("未知 LibTV 目标")
        mt = int(body.get("mtime") or 0)
        if mt and path.exists() and path.stat().st_mtime_ns != mt:
            raise Conflict("libtv.json 已被别处修改，已刷新")
        url = str(body.get("url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("只接受 http/https 地址")
        lib = _jload(path, {}) or {}
        if not isinstance(lib, dict):
            lib = {}
        if target == "asset":
            lib.setdefault("canvas", str(body.get("canvas") or "").strip())
        pid = str(body.get("project_id") or "").strip()
        node = str(body.get("node_id") or "").strip()
        name = str(body.get("name") or "").strip()
        if pid:
            lib["project_id"] = pid
            canvas = lib.get("canvas")
            if isinstance(canvas, dict):
                canvas["projectId"] = pid
                lib["canvas"] = canvas
        if "url" in body:
            lib["url"] = url
            canvas = lib.get("canvas")
            if isinstance(canvas, dict):
                canvas["url"] = url
                lib["canvas"] = canvas
        if node and target == "sb":
            lib["node_id"] = node
        if name:
            lib["name"] = name
        lib.setdefault("status", "未连线")
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(st, path)
        _jwrite(path, lib)
        st.invalidate(["storyboards", "scenes", "assets"])
        return self._json(200, {"ok": True, "target": target,
                                "path": path.relative_to(st.root).as_posix(),
                                "project_id": lib.get("project_id") or "",
                                "node_id": lib.get("node_id") or "",
                                "url": lib.get("url") or "",
                                "mtime": path.stat().st_mtime_ns})

    # ---------- 创意（草稿）----------
    def _idea_path(self, rel):
        st = self.state
        rel = str(rel or "").strip().replace("\\", "/")
        if not rel:
            raise ValueError("没有文件路径")
        root = st.root.resolve()
        p = (root / rel).resolve()
        try:
            if not p.is_relative_to(root):
                raise ValueError("路径不合法")
        except AttributeError:
            if not str(p).startswith(str(root) + os.sep):
                raise ValueError("路径不合法")
        relp = p.relative_to(root).as_posix()
        ok = relp.startswith("design/创意库/") or \
            relp.startswith("design/剧本库/") or \
            relp.startswith("units/%s/script/draft/" % st.unit)
        if not ok or p.suffix.lower() != ".md":
            raise ValueError("只能写 design/创意库（旧项目 design/剧本库）或 script/draft 下的 .md")
        return p, relp

    def _idea_save(self, body):
        st = self.state
        p, rel = self._idea_path(body.get("path"))
        if not p.exists():
            raise ValueError("文件不存在：%s" % rel)
        mt = int(body.get("mtime") or 0)
        if mt and p.stat().st_mtime_ns != mt:
            raise Conflict("文件已被别处修改，已刷新")
        raw = p.read_text(encoding="utf-8")
        meta, _ = snap_mod._frontmatter(raw)
        if meta.get("status") == "final":
            raise ValueError("这是定稿，创意页不能改")
        title = str(body.get("title") or "").strip() or meta.get("title") or p.stem
        text = str(body.get("text") or "")
        block = _fm_block(raw, {
            "type": meta.get("type") or "script",
            "status": "draft",
            "title": title,
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        })
        _backup(st, p)
        p.write_text(block + chr(10) * 2 + text.rstrip() + chr(10), encoding="utf-8")
        st.invalidate(["script"])
        return self._json(200, {"ok": True, "path": rel,
                                "mtime": p.stat().st_mtime_ns})

    def _idea_script_new(self, body):
        """从一份创意新建一集剧本：建 unit，并在 copy.md 里记 source_idea。"""
        st = self.state
        p, rel = self._idea_path(body.get("idea"))
        if not p.exists():
            raise ValueError("找不到这篇创意：%s" % rel)
        meta, _ = snap_mod._frontmatter(p.read_text(encoding="utf-8"))
        title = str(body.get("title") or meta.get("title") or p.stem).strip()
        res = self._unit_new({
            "type": str(body.get("type") or "剧情"),
            "entry": title,
            "activate": True,
        })
        uid = res["unit"]["id"]
        copy = st.root / "units" / uid / "script" / "copy.md"
        raw = copy.read_text(encoding="utf-8")
        _, content = snap_mod._frontmatter(raw)
        block = _fm_block(raw, {
            "title": title,
            "source_idea": rel,
            "status": "draft",
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        })
        copy.write_text(block + "\n\n" + content.strip() + "\n", encoding="utf-8")
        st.invalidate()
        return {"ok": True, "unit": uid, "title": title,
                "path": copy.relative_to(st.root).as_posix(),
                "prompt": res.get("prompt") or ""}

    def _idea_script(self, body):
        """用选中的创意打开 WorkBuddy：先读创意，scope=script，只写 copy.md。"""
        st = self.state
        p, rel = self._idea_path(body.get("path"))
        if not p.exists():
            raise ValueError("找不到这篇创意：%s" % rel)
        raw = p.read_text(encoding="utf-8")
        meta, content = snap_mod._frontmatter(raw)
        title = str(meta.get("title") or p.stem)
        if meta.get("status") != "ready":
            block = _fm_block(raw, {
                "status": "ready",
                "updated": time.strftime("%Y-%m-%d %H:%M"),
            })
            _backup(st, p)
            p.write_text(block + "\n\n" + content.strip() + "\n", encoding="utf-8")
            st.invalidate(["script"])
        if _project_mode(st.root) == "short_drama":
            prompt = (
                "用 changji-director 的短剧包把创意「%s」（%s）落成剧本。\n"
                "核心大脑是 skill 目录下 vendor/shuohao-skills 的 7 个 skill；先读它们的 SKILL.md。\n"
                "只读这篇 + design/创意库 里的活跃创意；不要读 _归档/、"
                ".workbuddy/trash/、.workbuddy/backup/、.workbuddy/history/、legacy/。\n"
                "按五段顺序：outline（没有就补）→ characters → art → script；"
                "每段先跑它自己的 validate，过了再往下。\n"
                "script.json 过 novel-script 的 validate 后，用 skill 目录下 "
                "bin/changji-import.mjs script --json <script.json> --project <工程根> "
                "--unit %s --final 导入 copy.md。\n"
                "不要排分镜、不要写提示词；那属于 staging / storyboard 和经典场记。"
                % (title, rel, st.unit))
        else:
            prompt = (
                "用创意「%s」（%s）写剧本。\n"
                "只读这个文件，以及 design/创意库（旧项目 design/剧本库）"
                "里的活跃创意；不要读 _归档/、.workbuddy/trash/、"
                ".workbuddy/backup/、.workbuddy/history/、legacy/ 里的旧创意。\n"
                "如果 design/构建状态.md 提到的创意文件已经不存在，视为已删除，不要继续迭代。\n"
                "scope=script：只写 units/%s/script/copy.md，不要排分镜、不要写提示词。\n"
                "写前按 R65 先梳理：结构、每场谁想要什么、开篇钩子、结尾最后一句；"
                "缺关键项就先问我 2-3 个，不要直接套模板；我说直接写就列假设。\n"
                "在 copy.md 的 frontmatter 记 source_idea: %s。\n"
                "写完告诉我：这个创意里哪些已经落实、哪些还缺。"
                % (title, rel, st.unit, rel))
        prompt = _skill_prompt(prompt)
        target = "units/%s/script/copy.md（源创意：%s）" % (st.unit, rel)
        return self._json(200, self._wb_finish(
            prompt, "写剧本", target,
            str(body.get("intent") or ""), st.unit, first_version=True,
            path=rel, title=title))

    def _idea_open(self, body):
        """在 WorkBuddy 里继续迭代这篇创意：scope=talk，先聊、确认后再落盘。"""
        st = self.state
        p, rel = self._idea_path(body.get("path"))
        if not p.exists():
            raise ValueError("找不到这篇创意：%s" % rel)
        raw = p.read_text(encoding="utf-8")
        meta, content = snap_mod._frontmatter(raw)
        title = str(meta.get("title") or p.stem)
        if str(meta.get("status") or "") in ("", "draft"):
            block = _fm_block(raw, {
                "status": "developing",
                "updated": time.strftime("%Y-%m-%d %H:%M"),
            })
            _backup(st, p)
            p.write_text(block + "\n\n" + content.strip() + "\n", encoding="utf-8")
            st.invalidate(["script"])
        if _project_mode(st.root) == "short_drama":
            prompt = (
                "用 changji-director 的短剧包继续处理创意「%s」（%s）。\n"
                "核心大脑是 skill 目录下 vendor/shuohao-skills 的 7 个 skill；先读它们的 SKILL.md。\n"
                "只读这篇 + design/创意库 里的活跃创意；不要读 _归档/、"
                ".workbuddy/trash/、.workbuddy/backup/、.workbuddy/history/、legacy/。\n"
                "先判断这篇在五段的哪一段：outline / characters / art / script / staging / storyboard。\n"
                "只推进当前这一段；vendor 的 validate 过了，再用 skill 目录下 "
                "bin/changji-import.mjs 导入 Trestle。\n"
                "如果还缺关键结构，先按 vendor 的 outline 五件套问我 2-3 个问题；"
                "不要直接写一大段，也不要直接排分镜。\n"
                "用户说某篇已删除时，明确回「已删除，不再作为上下文」，不要引用它的内容。"
                % (title, rel))
        else:
            prompt = (
                "继续迭代创意「%s」（%s）。\n"
                "只读这个文件，以及 design/创意库（旧项目 design/剧本库）"
                "里的活跃创意；不要读 _归档/、.workbuddy/trash/、"
                ".workbuddy/backup/、.workbuddy/history/、legacy/ 里的旧创意。\n"
                "如果 design/构建状态.md 提到的创意文件已经不存在，视为已删除，不要继续迭代。\n"
                "用户说某篇已删除时，明确回「已删除，不再作为上下文」，不要引用它的内容。\n"
                "scope=talk：只聊创意、补细节、给 2-3 个引申方向；"
                "不写剧本、不排分镜、不写提示词。\n"
                "如果这篇还缺关键结构（谁想要什么 / 什么挡着 / 结尾 / 记忆点），"
                "先按 R65 做一次梳理：说清现在最缺什么，最多问我 3 个会改变结构的问题；"
                "不要直接写一大段。\n"
                "每轮把结论归并到这篇创意的对应板块"
                "（完整视频 / 角色设计 / 内容设计 / 场景与视觉 / 声音），"
                "先给我一行归并摘要确认，我说保存再写文件；"
                "不要按点新建碎文件，只有确认是另一个视频才新开一篇。\n"
                "如果这篇是旧的分类碎片（category 不是「整片/系列」），"
                "先告诉我它属于哪支视频，建议合并到那篇整片创意；不要继续在碎片上长。\n"
                "如果我想开始写剧本，先问我，不要自己切 scope。"
                % (title, rel))
        prompt = _skill_prompt(prompt)
        return self._json(200, self._wb_finish(
            prompt, "延申思路", rel,
            str(body.get("intent") or ""), st.unit, first_version=False,
            path=rel, title=title))

    def _wb_clean_tasks(self, tdir, keep=20):
        try:
            files = sorted(tdir.glob("wb-*.md"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[keep:]:
                try:
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _wb_target_label(self, body, unit, action, ctx):
        """给短输入 / 任务书用的一句话目标。任务书里另有完整边界。"""
        goals = body.get("targets")
        if isinstance(goals, list):
            vals = [str(x.get("value") or "").strip()
                    for x in goals if isinstance(x, dict)]
            vals = [x for x in vals if x]
            if vals:
                return "；".join(vals[:2])
        aid = str(body.get("asset") or "").strip()
        canvas = str(body.get("canvas") or "").strip()
        auid = str(body.get("asset_unit") or "").strip()
        focus = str(body.get("focus") or "").strip()
        node = str(body.get("node") or "").strip()
        seg = str(body.get("segment") or "").strip()
        shot = str(body.get("shot") or "").strip()
        if aid:
            base = "assets/%s" % aid
            try:
                _adir, arel, _am = self._asset(aid)
                base = arel
            except Exception:
                pass
            if auid:
                return "%s/build/%s/%s/unit.json → prompt / negative" % (
                    base, canvas or "C01", auid)
            if canvas:
                return ("%s/build/%s/（canvas.json + 各 unit.json 的 "
                        "refs / style / size / model）" % (base, canvas))
            if focus == "manifest":
                return base + "/manifest.json"
            if focus == "plan":
                return base + "/prompts.md（资产设计方案）"
            if focus == "unit":
                return base + "/build/<画布>/<单元>/unit.json → prompt / negative"
            return base
        if unit and node and seg:
            return ("units/%s/storyboard/graph.json → segments[%s]"
                    ".nodes[%s].prompt" % (unit, seg, node))
        if unit and shot and seg:
            return ("units/%s/storyboard/shots.json id=%s + graph.json 段 %s "
                    "的同镜节点" % (unit, shot, seg))
        if unit and seg and body.get("graph"):
            return "units/%s/storyboard/graph.json → segments[id=%s]" % (unit, seg)
        if unit and ("剧本" in action or "改这场" in action):
            return "units/%s/script/copy.md（对应场次）" % unit
        if "大纲" in action or "思路" in action:
            return "design/创意库（当前大纲 / 创意文件）"
        return ctx or "当前页面"

    def _wb_finish(self, full, action, target, intent, unit="", first_version=False,
                   **extra):
        """把完整任务书写进 .workbuddy/tasks，输入框只放短指令。

        WorkBuddy 的 deeplink 只有一个 prompt 字段；整份任务书塞进去会变成
        几十行。这里让输入框只保留「任务 / 对象 / 用户要求 / 任务书路径」，
        细节全在任务书文件里，先读再改。
        """
        st = self.state
        tdir = st.root / ".workbuddy" / "tasks"
        tdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(
            ("%s|%s|%s" % (action, target, full)).encode("utf-8")).hexdigest()[:6]
        path = tdir / ("wb-%s-%s.md" % (stamp, digest))
        project = ""
        try:
            pj = _jload(st.root / "project.json", {}) or {}
            project = str((pj.get("meta") or {}).get("name") or "")
        except Exception:
            project = ""
        s = str(intent or "").strip()
        if s:
            want_head = s
        elif first_version:
            want_head = "（还没说；按现有设定写第一版，缺关键事实先问）"
        else:
            want_head = "（还没说；先问用户要改哪里、想改成什么样，不要直接改）"
        head = [
            "# WorkBuddy 任务书",
            "",
            "- 任务：%s" % action,
            "- 对象：%s" % target,
            "- 用户要求：%s" % want_head,
            "- 工程：%s（%s）" % (project or "未命名", unit or st.unit or ""),
            "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "只按本文件执行；本文件点名的目标之外不要改。",
            "",
            "---",
            "",
            full,
            "",
        ]
        book = "\n".join(head)
        try:
            path.write_text(book, encoding="utf-8")
            self._wb_clean_tasks(tdir)
            rel = path.relative_to(st.root).as_posix()
        except OSError:
            rel = ""
        one = re.sub(r"\s+", " ", s).strip()
        if one and len(one) <= 100:
            want_short = "用户要求：%s" % one
        elif one:
            want_short = "用户要求：见任务书里的「用户要改的」一节。"
        elif first_version:
            want_short = ("用户要求：还没有具体说明；按任务书里的现有设定写第一版，"
                          "缺关键事实先问。")
        else:
            want_short = ("用户要求：还没说。先别改文件：先问我要改哪里、"
                          "想改成什么样，最多 2 个问题。")
        parts = [
            "用 %s 接手这个 Trestle 工程。" % _active_skill(),
            "任务：%s" % action,
            "对象：%s" % target,
        ]
        if unit:
            parts.append("单元：%s" % unit)
        parts.append(want_short)
        if rel:
            parts.append("完整任务书：%s" % rel)
            parts.append("先读这份任务书，只按它执行；不要做项目勘察。")
        short = "\n".join(parts)
        return {
            "ok": True,
            "url": _workbuddy_link(st.root, prompt=short),
            # 保留 prompt = 完整任务书，兼容旧调用和自检；
            # 真正进 WorkBuddy 输入框的是 short_prompt。
            "prompt": full,
            "short_prompt": short,
            "taskbook": book,
            "task_file": rel,
            "unit": unit,
            **extra,
        }

    def _wb_open(self, body):
        st = self.state
        action = str(body.get("action") or "接手当前节点").strip()
        ctx = str(body.get("ctx") or "").strip()
        intent = str(body.get("intent") or "").strip()
        sel = str(body.get("selection") or "").strip()
        if len(sel) > 2000:
            sel = sel[:2000] + "…"
        mode = _project_mode(st.root)
        unit = str(body.get("unit") or st.unit or "")
        allow, deny, extra = _wb_scope(action)
        for k in ("<unit>", "<集>"):
            allow = allow.replace(k, unit)
            deny = deny.replace(k, unit)
            extra = extra.replace(k, unit)
        try:
            pj = _jload(st.root / "project.json", {}) or {}
            pname = str((pj.get("meta") or {}).get("name") or "")
        except Exception:
            pname = ""
        help_mode = any(k in action for k in ("怎么用", "帮助", "问 AI", "Trestle 教师"))
        if body.get("asset") and body.get("asset_unit"):
            want = intent or str(body.get("want") or "").strip() or ctx
            return self._wb_asset_unit_prompt(body, want, unit, mode, pname)
        if body.get("node") and body.get("segment"):
            return self._wb_node_prompt(
                body, action, intent or ctx, unit, mode, pname)
        if body.get("shot") and body.get("segment"):
            return self._wb_shot_prompt(body, action, intent, unit, mode, pname)
        if body.get("graph") and body.get("segment"):
            return self._wb_graph_prompt(body, action, intent, unit, mode, pname)
        if help_mode:
            guide = _bundled_skill_dir(GUIDE_SKILL)
            user_text = ctx or "请告诉我 Trestle 怎么用、下一步点哪里。"
            bridge = _ensure_guide_bridge()
            if bridge:
                # 输入框只放用户要说的那句话；教师规则由桥接 skill 注入。
                prompt = user_text
                url = _workbuddy_link(st.root, skill=GUIDE_SKILL, prompt=prompt,
                                      prefix=False, include_skill=True)
            else:
                prompt = _skill_prompt(user_text, GUIDE_SKILL)
                url = _workbuddy_link(st.root, skill=GUIDE_SKILL, prompt=prompt,
                                      prefix=False)
            return {"ok": True, "url": url, "prompt": prompt,
                    "skill": GUIDE_SKILL, "guide": bool(guide), "bridge": bridge}
        asset_lines = []
        aid = str(body.get("asset") or "").strip()
        canvas_sel = str(body.get("canvas") or "").strip()
        focus = str(body.get("focus") or "").strip()
        if not aid:
            m = re.search(r"\b((?:CH|SC|PR|BR|ST)-\d+)\b", ctx)
            if m:
                aid = m.group(1)
        if aid and canvas_sel and not body.get("asset_unit"):
            asset_lines = [
                "目标画布（只改这个）= assets/%s/build/%s/：canvas.json，"
                "以及各生成单元 unit.json 的 refs / style / size / model。"
                % (aid, canvas_sel),
                "不要改 unit.json 的 prompt 正文（用户明确要求除外）、manifest、"
                "prompts.md、其他画布；不要直接改 LibTV 节点。",
                "改完让用户回 Trestle 点「布线」或「推送」，不要自己做下一步。",
            ]
        elif aid and not body.get("asset_unit"):
            try:
                adir, arel, _am = self._asset(aid)
                units = []
                for cdir in sorted((adir / "build").glob("C*")):
                    for udir in self._asset_unit_dirs(cdir):
                        up = udir / "unit.json"
                        units.append((cdir.name, udir.name,
                                      up.relative_to(st.root).as_posix()))
                if focus == "manifest":
                    asset_lines = [
                        "目标文件（只改这个）= %s/manifest.json。" % arel,
                        "只改这个资产的设定字段（identity / role / notes / "
                        "palette / 参考文件等）；不要改 prompts.md、unit.json、"
                        "其他资产。",
                    ]
                elif focus == "plan":
                    asset_lines = [
                        "目标文件（只改这个）= %s/prompts.md（资产设计方案）。" % arel,
                        "这是给人看的方案，不会原样发给 LibTV；不要把它当成 "
                        "unit.json 的 prompt，也不要顺手改其他文件。",
                    ]
                elif len(units) == 1:
                    _c, _u, target = units[0]
                    asset_lines = [
                        "如果用户这次要的是「提示词」：目标文件（只改这个）= %s，"
                        "字段是 prompt / negative；LibTV 节点读的是这里，"
                        "不是 prompts.md。" % target,
                        "如果用户这次要的是「资产设定」而没点名文件：先问一句"
                        "「改 manifest / prompts.md / 提示词哪一层」，"
                        "不要默认改 prompts.md。",
                    ]
                elif len(units) > 1:
                    asset_lines = [
                        "这个资产有多个生成单元：%s。"
                        % "、".join("%s/%s" % (c, u) for c, u, _p in units),
                        "先问用户改哪一个，不要自己选，也不要先改 prompts.md。",
                    ]
                else:
                    asset_lines = [
                        "这个资产还没有生成单元；要改 LibTV 提示词，先让用户"
                        "建生成画布 / 单元，不要改 prompts.md 冒充提示词。",
                    ]
            except Exception:
                pass

        edit_like = any(k in action for k in (
            "改", "完善", "接手", "修改", "重做", "重新设计", "延申", "延伸"))
        # 生成类动作（写剧本 / 生成场 / 出图谱）没写意图是正常的；
        # 修改类动作没写就先问，别让模型自己猜要改哪里。
        intent_lines = (_wb_intent_block(intent) if intent
                        else _wb_intent_block("") if edit_like else [])
        lines = [
            "【本次唯一任务】%s" % action,
        ] + intent_lines + [
            "【停止条件】改完目标文件 / 给出本轮结果后立即停下；不要做下一步，"
            "不要写工作日志，不要顺手优化其他文件。",
            ("先按上面的问题问清楚，再动手；不要先做项目勘察。"
             if (edit_like and not intent) else
             "开始前先用一句话复述：只改哪个文件 / 字段、按用户哪句话改；"
             "然后直接做，不要先做项目勘察。"),
            "用 %s 接手这个 Trestle 工程。" % _active_skill(),
            "项目：%s（%s）" % (pname or "未命名", _mode_label(mode)),
            "当前单元：%s" % (unit or "未定"),
            "当前位置：%s" % (ctx or "当前页面"),
            "任务：%s。" % action,
        ] + asset_lines + [
            "工作边界（必须遵守）：",
            "- 只允许改：%s" % allow,
            "- 不允许改：%s" % deny,
            "- %s" % extra,
        ]
        if sel:
            if "装造" in action:
                sel_task = ("只从这段选中内容里提取装造 / 造型信息，补到已有角色的 "
                            "look / 变体；不要新建角色或场景，"
                            "选中内容里提到的新角色也不要建。")
            elif "角色" in action:
                sel_task = ("只从这段选中内容里提取角色信息，新建或补齐角色资产；"
                            "选中内容里提到的地点、道具、画风一律不建，"
                            "最多写进角色备注。")
            elif "美术" in action or "艺术风格" in action:
                sel_task = ("只从这段选中内容里提取美术设定（场景 / 道具 / 画风），"
                            "新建或补齐对应资产；角色一律不建，剧本正文不改。")
            elif "场景" in action:
                sel_task = ("只从这段选中内容里提取场景信息，新建或补齐场景资产；"
                            "选中内容里提到的人一律不建角色，"
                            "最多写进场景备注。")
            else:
                sel_task = "只从这段选中内容里提取与任务相关的信息，不要扩大范围。"
            lines += ["选中内容（只处理这段，不要读全文扩写）：", sel, sel_task]
        if "美术" in action or "艺术风格" in action:
            if mode == "short_drama":
                lines.append(
                    "美术段按 vendor/shuohao-skills/skills/novel-art 跑："
                    "有 outline.json 先 seed，再逐场景 / 道具填设定，"
                    "validate 过了用 bin/changji-import.mjs art 导入 Trestle。"
                    "style_bible 写全：媒介 / 画幅 / 光 / 色 / 质感 / 镜头 / 禁止；"
                    "场景锚点 3–5 个、空景提示词无人、道具白底无手。")
            else:
                lines.append(
                    "按通用模式的画风规范写 assets/style/<ID>/style_bible.md；"
                    "场景 / 道具提示词不写画风，出图时统一附加。")
        if mode == "short_drama":
            rules = ("按说好五段（vendor JSON 是源，用 changji-import.mjs 导入 Trestle）"
                     "的规则做")
        else:
            rules = ("按通用七步（创作思路 → 剧本 / 内容大纲 → 场 → 分镜 → 镜头 → "
                     "选技法 + 图谱 → 节点提示词 → 布线）的规则做；只做当前这一步")
        lines.append(
            "只读完成当前任务必需的文件；不要做项目勘察：不要 `find .`、"
            "不要 `git log` / `git status`、不要读 README、交付目录、工作台源码、"
            "其他资产、其他集、.workbuddy/memory。")
        lines.append(
            "不要写 .workbuddy/ 下的日志 / 缓存 / memory；不要写 WorkBuddy "
            "空间链接；不要建 widget；不要用 Task 子代理做别的事。")
        lines.append(
            "自检：如果发现自己在读无关文件、准备改第二个文件、或准备写日志 / "
            "空间链接，立即停下，只回复「我跑偏了：…，要继续吗？」。")
        lines.append(
            "先读任务点名的目标文件（没点名时只读当前任务必需的文件），%s；"
            "只改我说的这一处；改完目标文件后立即停下，列出改了哪几个文件；"
            "不要做下一步，不要顺手优化。" % rules)
        prompt = _skill_prompt(chr(10).join(lines))
        return self._wb_finish(
            prompt, action,
            self._wb_target_label(body, unit, action, ctx),
            intent, unit, first_version=not edit_like)

    def _wb_node_prompt(self, body, action, want, unit, mode, pname):
        """只写一个图谱节点的提示词：把节点、上游、分镜和用户意图钉死。

        这一段是给 WorkBuddy 的任务书，不是给终端用户的文案。
        核心是把范围收窄到一个字段，避免它顺手重写整段或整镜。
        """
        st = self.state
        director = _bundled_skill_dir(WORKBUDDY_SKILL)
        sid = str(body.get("segment") or "").strip()
        nid = str(body.get("node") or "").strip()
        if not sid or not nid:
            raise ValueError("要指定段和节点")
        data = graph_mod.load(st.root, unit)
        g = graph_mod.normalize(data, unit)
        seg, n = self._graph_find(g, sid, nid)
        role = str(n.get("role") or "")
        if role in ("reference", "reference_video"):
            raise ValueError("这是上传 / 外部参考节点，没有提示词；"
                             "请对首帧、尾帧、图生图或视频节点写")
        by = {x["id"]: x for x in seg["nodes"]}
        snap = st.snapshot(unit)
        shot_id = graph_mod.shot_of(n)
        shot = next((x for x in (snap.get("shots") or [])
                     if x["id"] == shot_id), {}) or {}
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x.get("id") == sid), {}) or {}
        role_cn = {"image2image": "图生图", "first_frame": "首帧",
                   "last_frame": "尾帧", "video": "成片"}.get(role, role)

        def clip(v, n=220):
            s = re.sub(r"\s+", " ", str(v or "")).strip()
            return s if len(s) <= n else s[:n] + "…"

        refs = []
        for rid in n.get("refs") or []:
            rn = by.get(rid)
            if not rn:
                continue
            refs.append("- %s · %s · 模型 %s%s%s" % (
                rn["id"],
                {"reference": "参考图", "reference_video": "参考视频",
                 "image2image": "图生图", "first_frame": "首帧",
                 "last_frame": "尾帧", "video": "成片"}.get(
                     str(rn.get("role") or ""), str(rn.get("role") or "")),
                rn.get("model") or "—",
                " · 已布线" if _node_key(rn) else " · 未布线",
                (" · 提示词：" + clip(rn.get("prompt"), 120))
                if str(rn.get("prompt") or "").strip() else
                (" · 图：" + clip(rn.get("source"), 80))
                if str(rn.get("source") or "").strip() else ""))
        downstream = [x["id"] for x in seg["nodes"]
                      if nid in (x.get("refs") or [])]
        styles = []
        try:
            styles = sorted(
                p.relative_to(st.root).as_posix()
                for p in (st.root / "assets" / "style").glob("*/style_bible.md"))
        except OSError:
            styles = []

        has_prompt = bool(str(n.get("prompt") or "").strip())
        lines = [
            "只写一个节点的提示词。这是本轮唯一任务。",
        ] + _wb_intent_block(want, first_version=not has_prompt) + [
            "工程：%s（%s）" % (pname or "未命名", _mode_label(mode)),
            "单元：%s" % unit,
            "文件：units/%s/storyboard/graph.json" % unit,
            "段：%s%s" % (sid, (" · " + clip(seg.get("name"), 80))
                          if seg.get("name") else ""),
            "节点：%s · %s · %s · 模型 %s · intent %s" % (
                nid, role_cn, n.get("type") or "image",
                n.get("model") or "未填", n.get("intent") or "—"),
            "LibTV 节点：%s" % ("已布线（%s）" % _node_key(n)
                                if _node_key(n) else "尚未布线"),
            "节点当前提示词：%s" % (clip(n.get("prompt"), 600) or "（空）"),
            "上游节点：%s" % ("\n" + "\n".join(refs) if refs else "无"),
            "直接下游：%s" % ("、".join(downstream) if downstream else "无"),
            "分镜：%s · 景别 %s · 运镜 %s · 时长 %ss" % (
                sid, shot.get("framing") or "—", shot.get("camera") or "—",
                shot.get("sec") or "—"),
            "镜头动作：%s" % (clip(shot.get("action"), 240) or "—"),
            "画面：%s" % (clip(shot.get("visual"), 300) or "—"),
            "情绪：%s" % (clip(shot.get("emotion"), 160) or "—"),
            "台词：%s" % (clip(shot.get("line"), 300) or "无"),
            "段信息：%s" % (clip(seg.get("prompt") or sb.get("prompt"), 300)
                            or "无"),
            "段站位：%s" % (clip(seg.get("blocking") or sb.get("blocking"), 200)
                            or "无"),
            "画风锁定：%s" % ("、".join(styles) if styles
                              else "未找到 style_bible；按项目现有风格，"
                                   "提示词里不重复大段画风"),
            "",
            "工作边界（必须遵守）：",
            "- 只允许改：units/%s/storyboard/graph.json 里 "
            "segments[id=%s].nodes[id=%s].prompt。" % (unit, sid, nid),
            "- 不允许改：其他节点、graph 的 params / model / refs / intent / "
            "status / node_id / edges / recipe；剧本、分镜、场、资产；"
            "任何 prompts.json / shots.json / scenes.json。",
            "- 不布线、不生成、不调 LibTV；不替用户点生成。",
            "- 不要顺手补充其他节点提示词，也不要把整段分镜表抄进来。",
            "- 只读 graph.json、这一段的 storyboard / 分镜、相关的 prompts / "
            "风格文件；不要 `find .`、`git log/status`、README、交付、源码、"
            "其他资产、其他集。",
            "- 不要写 .workbuddy/ 下的日志 / 缓存 / memory；不要写 WorkBuddy "
            "空间链接；不要建 widget；不要用 Task 子代理做别的事。",
            "- 自检：发现自己在读无关文件、准备改第二个文件或写日志时，"
            "立即停下并说明，不要继续。",
            "- 改完这个节点的 prompt 后立即停下，只回复新提示词；不要做下一步。",
            "",
            "写法：",
            "1. 先读 %s/SKILL.md「第五步：节点提示词」，再按 "
            "craft/00-index.md 读对应原文；模型参数和语言看 "
            "craft/model-profiles.md。" % (director or "skill 目录"),
            "2. 按节点角色写：首帧 / 尾帧 / 图生图写「这一帧长什么样」"
            "（景别、机位、光、构图、人物与道具位置）；视频只写"
            "「什么在动 + 台词 + 时长」，不重复静态长相。",
            "3. 用户描述缺关键事实时，最多问 2 个会改变画面 / 动作的问题，"
            "不要猜；用户说「直接写」时才列假设往下写。",
            "4. 语言与项目一致；不写模型参数、节点 ID、modeType，不复制 KSR 示例。",
            "5. 写进文件后只回复：节点 %s 的新提示词全文 + 一句"
            "「回 Trestle 点『更新到 LibTV』才会进画布」。" % nid,
            "",
            "注意：graph.json 是 Trestle 本地源；LibTV 上已有的节点仍是旧提示词。"
            "只有用户在工作台点「更新到 LibTV」后，LibTV 才会变。",
        ]
        prompt = _skill_prompt("\n".join(lines), PROMPT_SKILL)
        target = ("units/%s/storyboard/graph.json → segments[%s].nodes[%s].prompt"
                  % (unit, sid, nid))
        return self._wb_finish(
            prompt, action, target, want, unit, first_version=not has_prompt,
            segment=sid, node=nid)

    def _wb_shot_prompt(self, body, action, intent, unit, mode, pname):
        """只处理一镜：目标锁到 shots.json 这一项 + graph.json 这一镜的节点。"""
        st = self.state
        director = _bundled_skill_dir(WORKBUDDY_SKILL)
        sid = str(body.get("segment") or "").strip()
        shot_key = str(body.get("shot") or "").strip()
        if not sid or not shot_key:
            raise ValueError("要指定段和镜头")
        shot_id = shot_key.zfill(3) if shot_key.isdigit() else shot_key
        focus = str(body.get("focus") or "").strip() or "all"
        focus_map = {
            "shot": "分镜描述 / 动作（shots.json）",
            "frame": "首帧 / 尾帧 / 图生图提示词",
            "video": "视频节点提示词",
            "all": "整镜（分镜描述 + 这一镜的节点提示词）",
            "ref": "参考图 / 参考视频（节点 refs）",
            "param": "节点参数（模型 / 时长 / 画幅）",
            "recipe": "布线配方 / 节点结构 / 连线",
        }
        if focus not in focus_map:
            focus = "all"
        focus_label = focus_map[focus]

        snap = st.snapshot(unit)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x.get("id") == sid), {}) or {}
        shot = next((x for x in (snap.get("shots") or [])
                     if str(x.get("id") or "") == shot_id), None)
        if shot is None:
            shot = next((x for x in (snap.get("shots") or [])
                         if str(x.get("h") or "") == shot_key), {}) or {}
        data = graph_mod.load(st.root, unit)
        g = graph_mod.normalize(data, unit)
        seg = next((x for x in (g.get("segments") or [])
                    if x.get("id") == sid), {}) or {}
        nodes = [n for n in (seg.get("nodes") or [])
                 if graph_mod.shot_of(n) == shot_id
                 or str(n.get("shot") or "") == shot_key]

        def clip(v, n=240):
            s = re.sub(r"\s+", " ", str(v or "")).strip()
            return s if len(s) <= n else s[:n] + "…"

        role_cn = {"reference": "参考图", "reference_video": "参考视频",
                   "image2image": "图生图", "first_frame": "首帧",
                   "last_frame": "尾帧", "video": "视频"}
        info = []
        for label, key in (
                ("类型", "type"), ("景别", "framing"), ("机位", "camera"),
                ("运镜", "move"), ("时长", "sec"), ("动作", "action"),
                ("画面", "visual"), ("台词", "line"), ("情绪", "emotion"),
                ("切点", "cut_at"), ("功能", "func"), ("记忆点", "memory"),
                ("资产", "assets")):
            v = shot.get(key)
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            v = clip(v, 300)
            if v:
                info.append("- %s：%s" % (label, v))
        node_lines = []
        for n in nodes:
            node_lines.append("- %s · %s · 模型 %s · %s%s" % (
                n.get("id"),
                role_cn.get(str(n.get("role") or ""), str(n.get("role") or "节点")),
                n.get("model") or "—",
                "已布线" if _node_key(n) else "未布线",
                (" · 当前提示词：" + clip(n.get("prompt"), 180))
                if str(n.get("prompt") or "").strip() else " · 提示词为空"))
        edges = []
        for e in (seg.get("edges") or []):
            if isinstance(e, dict):
                edges.append("%s → %s" % (e.get("from") or "?", e.get("to") or "?"))
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append("%s → %s" % (e[0], e[1]))
            else:
                edges.append(str(e))

        base = "units/%s/storyboard/" % unit
        if focus == "shot":
            allow = ("%sshots.json 里 id=%s 这一项（只改用户点到的字段）"
                     % (base, shot_id))
            deny = "graph.json、其他镜、其他段、剧本正文、场分析、资产、libtv.json。"
            first = not any(str(shot.get(k) or "").strip()
                            for k in ("shot", "action", "visual"))
        elif focus == "frame":
            allow = ("%sgraph.json 里段 %s 中 shot=%s 的首帧 / 尾帧 / 图生图节点的 prompt"
                     % (base, sid, shot_id))
            deny = ("shots.json、视频节点、其他镜、其他段、refs、model / params、"
                    "assets、libtv.json。")
            first = not any(str(n.get("prompt") or "").strip() for n in nodes)
        elif focus == "video":
            allow = ("%sgraph.json 里段 %s 中 shot=%s 的视频节点的 prompt"
                     % (base, sid, shot_id))
            deny = ("shots.json、首帧 / 尾帧 / 图生图节点、其他镜、其他段、"
                    "refs、model / params、assets、libtv.json。")
            first = not any(str(n.get("prompt") or "").strip() for n in nodes)
        elif focus == "ref":
            allow = ("%sgraph.json 里段 %s 中 shot=%s 节点的 refs；"
                     "需要新增参考时只加在本段，并写明图片来源" % (base, sid, shot_id))
            deny = ("prompt、model / params、edges、recipe、shots.json、"
                    "其他镜、其他段、assets、libtv.json。")
            first = not nodes
        elif focus == "param":
            allow = ("%sgraph.json 里段 %s 中 shot=%s 节点的 model / params"
                     "（ratio / resolution / duration / count / background）"
                     % (base, sid, shot_id))
            deny = ("prompt、refs、edges、recipe、shots.json、其他镜、其他段、"
                    "assets、libtv.json。")
            first = not nodes
        elif focus == "recipe":
            allow = ("%sgraph.json 里段 %s 的 recipe / stage / nodes / edges"
                     "（新增节点必须写 prompt）" % (base, sid))
            deny = ("已有节点的 prompt（用户明确说要改提示词时才动）、"
                    "shots.json、其他段、剧本、分镜、资产、libtv.json。")
            first = not nodes
        else:
            allow = ("%sshots.json 里 id=%s 这一项 + %sgraph.json 里段 %s 中 "
                     "shot=%s 的节点 prompt" % (base, shot_id, base, sid, shot_id))
            deny = ("其他镜、其他段、其他节点、剧本正文、场分析、资产、"
                    "libtv.json。")
            first = not any(str(n.get("prompt") or "").strip() for n in nodes) \
                and not any(str(shot.get(k) or "").strip()
                            for k in ("shot", "action", "visual"))

        lines = [
            "只处理这一镜。这是本轮唯一任务。",
        ] + _wb_intent_block(intent, focus_label, first_version=first) + [
            "工程：%s（%s）" % (pname or "未命名", _mode_label(mode)),
            "单元：%s" % unit,
            "位置：段 %s%s · 镜 %s" % (
                sid, (" · " + clip(sb.get("name"), 60)) if sb.get("name") else "",
                shot_id),
            "分镜文件：%sshots.json → shots[] 里 id=%s" % (base, shot_id),
            "图谱文件：%sgraph.json → segments[id=%s] 里 shot=%s 的节点"
            % (base, sid, shot_id),
            "这一镜的节点：%s" % ("\n" + "\n".join(node_lines) if node_lines else "无"),
            "这一镜现状：%s" % ("\n" + "\n".join(info) if info else "（分镜字段为空）"),
            "本段连线：%s" % ("、".join(edges) if edges else "无"),
            "",
            "工作边界（必须遵守）：",
            "- 只允许改：%s" % allow,
            "- 不允许改：%s" % deny,
            "- 不布线、不生成、不调 LibTV；不替用户点生成。",
            "- 只读这一镜相关的 shots.json / graph.json / 必要的 prompt 与风格文件；"
            "不要 `find .`、`git log/status`、README、交付、源码、其他资产、其他集。",
            "- 不要写 .workbuddy/ 下的日志 / 缓存 / memory；不要写 WorkBuddy "
            "空间链接；不要建 widget；不要用 Task 子代理做别的事。",
            "- 自检：发现自己在读无关文件、准备改第二个文件或写日志时，"
            "立即停下并说明，不要继续。",
            "- 改完立即停下，只回复「改了哪些字段 + 新内容」；不要做下一步。",
            "",
        ]
        if focus in ("ref", "param", "recipe"):
            lines += [
                "写法：",
                "1. 只改结构 / 引用 / 参数，不重写提示词；新增节点必须写 prompt，"
                "否则布线会阻塞。",
                "2. refs 只能指向本段已存在的节点；edges 不要留悬空节点。",
                "3. 缺关键事实先问，不要自己编参考图路径。",
            ]
        else:
            lines += [
                "写法：",
                "1. 先读 %s/SKILL.md 的对应步骤；按节点角色写：首帧 / 尾帧 / "
                "图生图写「这一帧长什么样」，视频只写「什么在动 + 台词 + 时长」。"
                % (director or "skill 目录"),
                "2. 用户描述缺关键事实时，最多问 2 个会改变画面 / 动作的问题，"
                "不要猜；用户说「直接写」时才列假设往下写。",
                "3. 语言与项目一致；不写模型参数、节点 ID、modeType。",
            ]
        prompt = _skill_prompt("\n".join(lines), PROMPT_SKILL)
        target = ("units/%s/storyboard/shots.json id=%s + graph.json 段 %s 的同镜节点"
                  % (unit, shot_id, sid))
        return self._wb_finish(
            prompt, action, target, intent, unit, first_version=first,
            segment=sid, shot=shot_id)

    def _wb_graph_prompt(self, body, action, intent, unit, mode, pname):
        """只重做一段的布线方案：目标锁到 graph.json 的单个 segment。"""
        st = self.state
        director = _bundled_skill_dir(WORKBUDDY_SKILL)
        sid = str(body.get("segment") or "").strip()
        if not sid:
            raise ValueError("要指定段")
        snap = st.snapshot(unit)
        sb = next((x for x in (snap.get("storyboards") or [])
                   if x.get("id") == sid), {}) or {}
        data = graph_mod.load(st.root, unit)
        g = graph_mod.normalize(data, unit)
        seg = next((x for x in (g.get("segments") or [])
                    if x.get("id") == sid), {}) or {}
        nodes = seg.get("nodes") or []
        edges = seg.get("edges") or []

        def clip(v, n=180):
            s = re.sub(r"\s+", " ", str(v or "")).strip()
            return s if len(s) <= n else s[:n] + "…"

        role_cn = {"reference": "参考图", "reference_video": "参考视频",
                   "image2image": "图生图", "first_frame": "首帧",
                   "last_frame": "尾帧", "video": "视频"}
        node_lines = []
        for n in nodes:
            node_lines.append("- %s · %s · 模型 %s · %s%s" % (
                n.get("id"),
                role_cn.get(str(n.get("role") or ""), str(n.get("role") or "节点")),
                n.get("model") or "—",
                "已布线" if _node_key(n) else "未布线",
                (" · 提示词：" + clip(n.get("prompt"), 100))
                if str(n.get("prompt") or "").strip() else " · 提示词为空"))
        edge_lines = []
        for e in edges:
            if isinstance(e, dict):
                edge_lines.append("%s → %s" % (e.get("from") or "?", e.get("to") or "?"))
            else:
                edge_lines.append(str(e))
        want_prompt = "提示词" in intent
        lines = [
            "只重做这一段的布线方案。这是本轮唯一任务。",
        ] + _wb_intent_block(intent, first_version=not nodes) + [
            "工程：%s（%s）" % (pname or "未命名", _mode_label(mode)),
            "单元：%s" % unit,
            "文件：units/%s/storyboard/graph.json → segments[id=%s]" % (unit, sid),
            "段：%s%s" % (sid, (" · " + clip(sb.get("name"), 60))
                          if sb.get("name") else ""),
            "来源镜：%s" % ("、".join(str(x) for x in (sb.get("shots") or [])) or "—"),
            "当前配方：%s · 阶段 %s · %s 个节点 · %s 条连线" % (
                seg.get("recipe") or "—", seg.get("stage") or "—",
                len(nodes), len(edges)),
            "当前节点：%s" % ("\n" + "\n".join(node_lines) if node_lines else "无"),
            "当前连线：%s" % ("、".join(edge_lines) if edge_lines else "无"),
            "用户在这段里看到的说明：%s" % (clip(seg.get("why"), 300) or "无"),
            "",
            "工作边界（必须遵守）：",
            "- 只允许改：units/%s/storyboard/graph.json 里 segments[id=%s] 的 "
            "recipe / why / cost / risk / stage / nodes / edges。" % (unit, sid),
            "- 不允许改：其他段、其他单元；shots.json、scenes.json、剧本正文、"
            "资产、manifest；libtv.json、node_id、status、input_hash；LibTV 节点。",
            ("- 用户明确提到提示词：可以改本段节点的 prompt，但不改其他段。"
             if want_prompt else
             "- 默认不改已有节点的 prompt；用户明确说要改提示词时才动。"),
            "- 不建 / 不改 LibTV 节点；不改模型账号；不替用户点生成。",
            "- 只读这一段相关的 graph.json、分镜 / 场、画布规则；不要 `find .`、"
            "`git log/status`、README、交付、源码、其他资产、其他集。",
            "- 不要写 .workbuddy/ 下的日志 / 缓存 / memory；不要写 WorkBuddy "
            "空间链接；不要建 widget；不要用 Task 子代理做别的事。",
            "- 自检：发现自己在读无关文件、准备改第二个文件或写日志时，"
            "立即停下并说明，不要继续。",
            "- 改完立即停下，只回复「改了哪些节点 / 连线」；不要做下一步。",
            "",
            "写法：",
            "1. 先读 %s/SKILL.md 的「配方 / 图谱」规则；只有在这段确有需要时才"
            "加节点，能用 2 个节点解决就不要 4 个。" % (director or "skill 目录"),
            "2. 新节点的 refs 只能指向本段已存在的节点；edges 不要悬空；"
            "每个新节点都要写 prompt。",
            "3. 缺关键事实先问，不要猜参考图来源。",
        ]
        prompt = _skill_prompt("\n".join(lines), PROMPT_SKILL)
        target = "units/%s/storyboard/graph.json → segments[id=%s]" % (unit, sid)
        return self._wb_finish(
            prompt, action, target, intent, unit, first_version=not nodes,
            segment=sid)

    def _wb_asset_unit_prompt(self, body, want, episode, mode, pname):
        """只写一个资产生成单元的出图提示词。

        这个 prompt 会被工作台原样传给 LibTV 节点；所以任务书必须把
        「写画面」和「写制作说明」分开，并把 prompts.md 里对应的那一节
        作为事实来源，而不是让模型自己猜。
        """
        st = self.state
        director = _bundled_skill_dir(WORKBUDDY_SKILL)
        aid = str(body.get("asset") or "").strip()
        canvas = str(body.get("canvas") or "C01").strip()
        auid = str(body.get("asset_unit") or "").strip()
        if not aid or not auid:
            raise ValueError("要指定资产和生成单元")
        adir, cdir, _rel, m, canvas = self._asset_canvas(aid, canvas)
        up = cdir / auid / "unit.json"
        if not up.is_file():
            raise ValueError("找不到生成单元：%s/%s" % (canvas, auid))
        um = _jload(up, {}) or {}
        if not isinstance(um, dict):
            um = {}
        tkey = adir.parent.name
        type_label = {"characters": "角色", "scenes": "场景",
                      "props": "道具", "brand": "品牌",
                      "style": "画风"}.get(tkey, tkey)
        name = str(m.get("name") or aid)
        uname = str(um.get("name") or auid)

        def clip(v, n=260):
            s = re.sub(r"\s+", " ", str(v or "")).strip()
            return s if len(s) <= n else s[:n] + "…"

        seed, seed_neg = self._asset_seed_unit(adir, uname)
        # 风格权限：有些资产（尤其品牌母图）明确写「纯形，不带风格」，
        # 风格由用户在 LibTV 里选。这时不能让提示词把画风 / 材质 / 工艺写死。
        style_docs = []
        try:
            for p in sorted(adir.glob("*.md")):
                try:
                    txt = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                if any(k in txt for k in (
                        "风格槽位", "风格由用户", "纯形，不带风格",
                        "不带任何呈现", "风格参考只允许")):
                    style_docs.append(p.name)
        except OSError:
            style_docs = []
        style_user = bool(style_docs) or any(
            k in seed for k in ("纯形", "不带风格", "不带任何呈现"))
        try:
            asnap = self.state.snapshot(episode)
            arow = next((x for x in (asnap.get("assets") or [])
                         if x.get("id") == aid), {})
            primary = str(arow.get("primary_image") or "")
        except Exception:
            primary = ""
        identity = m.get("identity")
        if isinstance(identity, dict):
            identity = "；".join("%s：%s" % (k, v) for k, v in identity.items())
        else:
            identity = str(identity or "")
        refs = []
        files = m.get("files") if isinstance(m.get("files"), dict) else {}
        for k, v in files.items():
            if v and str(v).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                refs.append("- %s：%s" % (k, v))
        for x in (um.get("refs") or []):
            refs.append("- 生成单元参考：%s" % x)
        styles = []
        try:
            styles = sorted(
                p.relative_to(st.root).as_posix()
                for p in (st.root / "assets" / "style").glob("*/style_bible.md"))
        except OSError:
            styles = []
        want_text = str(want or "").strip()
        has_prompt = bool(str(um.get("prompt") or "").strip())
        # 用户写的可能是一段制作说明，而不是画面。不要丢掉它，也不要原样
        # 塞进 prompt：告诉模型「这是约束」就够了。
        meta_reason = _meta_prompt_reason(want_text) if want_text else ""
        meta_lines = ([
            "注意：用户这段更像制作说明 / 约束（%s），不是画面描述。"
            "按它约束画面，但不要把流程、路径、文件名、「以…为准」写进 prompt。"
            % meta_reason,
        ] if meta_reason else [])

        style_cfg = um.get("style") if isinstance(um.get("style"), dict) else {}
        style_mode = str(style_cfg.get("mode") or "libtv")
        style_asset = str(style_cfg.get("asset") or "")
        style_text = str(style_cfg.get("text") or "")
        style_name, style_bible = "", ""
        asset_palette = []
        if style_mode == "asset" and style_asset:
            try:
                sadir, _srel, sm = self._asset(style_asset)
                style_name = str(sm.get("name") or style_asset)
                bp = sadir / "style_bible.md"
                if bp.is_file():
                    style_bible = bp.read_text(encoding="utf-8")[:1200]
                asset_palette = sm.get("palette") if isinstance(
                    sm.get("palette"), list) else []
            except Exception:
                style_mode = "libtv"
        palette = um.get("palette") if isinstance(um.get("palette"), list) else []
        palette = palette or asset_palette

        def _pal_text(rows):
            parts = []
            for x in rows or []:
                if isinstance(x, str):
                    parts.append(x)
                elif isinstance(x, dict):
                    bits = [str(x.get("name") or "").strip(),
                            str(x.get("hex") or "").strip()]
                    line = " ".join(b for b in bits if b)
                    if x.get("usage"):
                        line += "（%s）" % str(x.get("usage")).strip()
                    if line:
                        parts.append(line)
            return "；".join(parts[:8])

        style_lines = {
            "libtv": ("风格权限：设计风格 / 风格参考由用户在 LibTV 里选；"
                      "你只写画面事实，不写死画风，不改 LibTV 的风格设置。"),
            "none": ("风格权限：这一张纯形 / 无风格；不写画风、材质、工艺、"
                     "纸感、渲染质感，也不选风格。"),
            "custom": ("风格权限：用户在 Trestle 里指定了自定义风格「%s」；"
                       "按它写呈现，但不要改 LibTV 的风格设置。" % (
                           style_text or "自定义")),
            "asset": ("风格权限：用户选了项目风格资产「%s」（%s）。按它的 "
                      "style_bible 写呈现；最终风格参考仍可在 LibTV 覆盖，"
                      "以 LibTV 里选的为准，不要改 LibTV 风格设置。"
                      % (style_name or style_asset, style_asset)),
        }
        if style_user and style_mode in ("libtv", "none"):
            style_lines[style_mode] = (
                "风格权限：这一张是纯形，风格由用户在 LibTV 里选（依据：%s）。"
                "只写形 / 结构 / 比例 / 构图；不写画风、材质、纸感、工艺、"
                "墨色倾向、颗粒、光泽、渲染质感；不要替用户选风格，"
                "也不要改 LibTV 的风格设置。"
                % ("、".join(style_docs) or "资产设定要求纯形"))
        pure = style_mode == "none" or (style_mode == "libtv" and style_user)
        rule1_tail = ("；不写材质、纸感、工艺、渲染质感。"
                      if pure else "、画面风格（按上面风格权限写）。")
        if style_mode == "libtv":
            rule6 = ("设计风格 / 风格参考由用户在 LibTV 选；不要替用户选风格，"
                     "也不要在提示词里写死'某种风格'。")
        elif style_mode == "none":
            rule6 = ("这一张纯形 / 无风格：不写画风，也不替用户选风格。")
        else:
            rule6 = ("按用户选的风格写呈现；不要改 LibTV 的风格设置。")

        lines = [
            "只写一个资产生成单元的出图提示词。这是本轮唯一任务。",
        ] + _wb_intent_block(want_text, first_version=not has_prompt) + meta_lines + [
            "工程：%s（%s）" % (pname or "未命名", _mode_label(mode)),
            "集：%s" % episode,
            "资产：%s · %s · %s" % (name, type_label, aid),
            "画布：%s；生成单元：%s · %s · 画幅 %s" % (
                canvas, auid, uname, um.get("size") or "未定"),
            "目标文件：assets/%s/%s/build/%s/%s/unit.json" % (
                tkey, aid, canvas, auid),
            "要改的字段：prompt（画面描述）；negative（只放负面词）。",
            "文件定位：%s" % up.relative_to(st.root).as_posix(),
            "当前提示词：%s" % (clip(um.get("prompt"), 700) or "（空）"),
            "当前负面词：%s" % (clip(um.get("negative"), 400) or "（空）"),
            "资产设定（事实来源，不是提示词；可以取用，不要照抄成说明）：",
            "- 定位 / 备注：%s" % (clip(m.get("role") or m.get("notes"), 400) or "—"),
            "- 外形 / 结构：%s" % (clip(identity, 500) or "—"),
            "- prompts.md 里「%s」这一节（可作画面基础）：%s" % (
                uname, clip(seed, 1200) or "（没有找到匹配小节）"),
            "- 这一节的负面词：%s" % (clip(seed_neg, 400) or "（无）"),
            "参考图：%s" % ("\n" + "\n".join(refs) if refs else "无"),
            "布线时自动挂的主图：%s" % (primary or "（无；请在资产页设主图）"),
            style_lines.get(style_mode, style_lines["libtv"]),
            "风格资产 style_bible：%s" % (clip(style_bible, 700) or "—"),
            "项目画风参考：%s" % ("、".join(styles) if styles
                                  else "无（风格由 LibTV 选）"),
            "色卡：%s" % (_pal_text(palette) or "无"),
            "色卡用法：颜色只作为画面事实（主体 / 背景 / 点缀的具体颜色），"
            "融进描述；不要单独列色号清单，也不要写「色卡」两个字。",
            "",
            "工作边界（必须遵守）：",
            "- 只允许改这个 unit.json 的 prompt / negative。",
            "- 不允许改：其他生成单元、canvas.json、prompts.md、manifest.json、"
            "libtv.json、剧本、分镜、其他资产。",
            "- 风格选择 / 风格参考 / LibTV 风格参数归用户；不在你的权限内。",
            "- 不布线、不生成、不调 LibTV；不替用户点生成。",
            "- 不要顺手改模型、画幅、参考图或别的单元。",
            "- 只读这个 unit.json、对应的 prompts.md 小节、设计语言 / style_bible；"
            "不要 `find .`、`git log/status`、README、交付、工作台源码、"
            "其他资产、其他集。",
            "- 不要写 .workbuddy/ 下的日志 / 缓存 / memory；不要写 WorkBuddy "
            "空间链接；不要建 widget；不要用 Task 子代理做别的事。",
            "- 自检：发现自己在读无关文件、准备改第二个文件或写日志时，"
            "立即停下并说明，不要继续。",
            "- 改完这个 unit.json 后立即停下，只回复新 prompt / negative；"
            "不要做下一步，不要顺手优化。",
            "",
            "写法（这是会原样发给 LibTV 图像节点的提示词）：",
            "写法依据：%s/SKILL.md 第五步 + craft/00-index.md + "
            "craft/model-profiles.md。" % (director or "skill 目录"),
            "1. 只写画面本身：主体是什么、外形与比例、构图 / 机位 / 景别、"
            "光线、背景" + rule1_tail,
            "2. 不写制作流程、判断、来源、'母图出来之后'、'以…为准'、"
            "'先…再…'、'路径'、'收口'、文件名、markdown、引号或解释；"
            "这些会让模型收到流程而不是画面。",
            "3. 负面词只写进 negative；不要写进 prompt。",
            "4. 不写模型参数；模型和画幅由画布 / 布线决定。",
            "5. 用户描述缺关键事实时，最多问 2 个会改变画面的问题；不要猜。",
            "6. " + rule6,
            "7. 写进文件后只回复：新 prompt 全文 + 新 negative + 一句"
            "「回 Trestle 点『布线并生成』」。",
        ]
        prompt = _skill_prompt("\n".join(lines), PROMPT_SKILL)
        target = "%s → prompt / negative" % up.relative_to(st.root).as_posix()
        action = str(body.get("action") or "写生成单元提示词").strip()
        return self._wb_finish(
            prompt, action, target, want_text, episode,
            first_version=not has_prompt, asset=aid, canvas=canvas,
            asset_unit=auid)

    def _idea_assets(self, body):
        st = self.state
        p, rel = self._idea_path(body.get("path"))
        if not p.exists():
            raise ValueError("文件不存在：%s" % rel)
        raw = p.read_text(encoding="utf-8")
        meta, content = snap_mod._frontmatter(raw)
        if meta.get("status") == "final":
            raise ValueError("这是定稿，创意页不能改")
        ids = [str(x).strip() for x in (body.get("assets") or []) if str(x).strip()]
        block = _fm_block(raw, {
            "assets": "[%s]" % ", ".join(ids),
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        })
        _backup(st, p)
        p.write_text(block + chr(10) * 2 + content.strip() + chr(10), encoding="utf-8")
        st.invalidate(["script"])
        return self._json(200, {"ok": True, "path": rel, "assets": ids})

    def _unit_assets(self, body):
        st = self.state
        uid = str(body.get("unit") or st.unit).strip()
        p = st.root / "units" / uid / "manifest.json"
        if not p.is_file():
            raise ValueError("找不到这一集：%s" % uid)
        man = _jload(p, {}) or {}
        ids = [str(x).strip() for x in (body.get("assets") or []) if str(x).strip()]
        man["assets"] = ids
        _backup(st, p)
        _jwrite(p, man)
        st.invalidate(["assets", "script"])
        return self._json(200, {"ok": True, "unit": uid, "assets": ids})

    def _idea_delete(self, body):
        """删除一篇创意：不直接抹掉，移到 .workbuddy/trash 里可找回。"""
        st = self.state
        p, rel = self._idea_path(body.get("path"))
        if not p.exists():
            raise ValueError("文件不存在：%s" % rel)
        meta, _ = snap_mod._frontmatter(p.read_text(encoding="utf-8"))
        if meta.get("status") == "final":
            raise ValueError("这是定稿，创意页不能删")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = st.root / ".workbuddy" / "trash" / stamp / rel
        n = 1
        while dst.exists():
            dst = st.root / ".workbuddy" / "trash" / ("%s-%d" % (stamp, n)) / rel
            n += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))
        title = str(meta.get("title") or p.stem)
        _idea_record_delete(st.root, rel, title,
                            dst.relative_to(st.root).as_posix())
        st.invalidate(["script"])
        return self._json(200, {
            "ok": True, "path": rel, "title": title,
            "trash": dst.relative_to(st.root).as_posix(),
        })

    def _idea_new(self, body):
        st = self.state
        title = str(body.get("title") or "").strip()
        if not title:
            raise ValueError("先起一个名字")
        safe = re.sub(r'[\\/:*?"<>|]', "", title).strip().strip(".")
        if not safe:
            raise ValueError("名字里没有可用字符")
        p, rel = self._idea_path("design/创意库/%s.md" % safe)
        if p.exists():
            raise ValueError("已经有同名想法：%s" % rel)
        now = time.strftime("%Y-%m-%d %H:%M")
        block = _fm_block("", {
            "type": "创意", "category": "整片", "status": "draft", "title": title,
            "created": now, "updated": now,
        })
        p.parent.mkdir(parents=True, exist_ok=True)
        _idea_clear_deleted(st.root, rel)
        body = IDEA_TEMPLATE_BODY.format(date=time.strftime("%Y-%m-%d"))
        p.write_text(block + "\n\n" + body, encoding="utf-8")
        st.invalidate(["script"])
        return self._json(200, {"ok": True, "path": rel,
                                "mtime": p.stat().st_mtime_ns})


def _idea_deleted_registry(root):
    return Path(root) / ".workbuddy" / "idea-deleted.json"


def _idea_record_delete(root, rel, title, trash):
    """记一条删除：创意库不再把它当上下文；构建状态留一行删除记录。"""
    path = _idea_deleted_registry(root)
    doc = _jload(path, {}) or {}
    rows = doc.get("deleted") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        rows = []
    rows = [x for x in rows if not (isinstance(x, dict) and x.get("path") == rel)]
    rows.append({
        "path": rel, "title": title,
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "trash": trash,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    _jwrite(path, {"deleted": rows})
    state = Path(root) / "design" / "构建状态.md"
    if state.is_file():
        try:
            text = state.read_text(encoding="utf-8")
            line = "- %s：已删除创意《%s》（%s），不再作为上下文。\n" % (
                time.strftime("%Y-%m-%d %H:%M"), title, rel)
            if line not in text:
                with state.open("a", encoding="utf-8") as f:
                    if text and not text.endswith("\n"):
                        f.write("\n")
                    f.write(line)
        except OSError:
            pass


def _idea_clear_deleted(root, rel):
    path = _idea_deleted_registry(root)
    doc = _jload(path, {}) or {}
    rows = doc.get("deleted") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return
    left = [x for x in rows if not (isinstance(x, dict) and x.get("path") == rel)]
    if len(left) != len(rows):
        _jwrite(path, {"deleted": left})


# 与 skill 的 templates/创意.md 保持同一套板块；改这里也要改那边。
IDEA_TEMPLATE_BODY = """# 一句话主旨

（谁想要什么、什么挡着、结果变了什么）

# 完整视频

- 片型：
- 时长 / 平台：
- 结构：
- 开篇（0-3s 钩子）：
- 结尾（最后 5s）：

# 故事大纲

1.
2.
3.

# 角色设计

- 主角：
- 性格（三个词 + 一个具体行为）：
- 口头禅（一句原话，什么时候说）：
- 对手 / 阻力：
- 关系：
- 造型 / 表演：

# 内容设计

- 信息点：
- 关键桥段：
- 节奏：
- 记忆点：

# 场景与视觉

- 空间：
- 光 / 色：
- 关键道具：
- 视觉母题：

# 声音

- 环境声：
- 设计音：
- 静音：
- 音乐方向：

# 参考

（只给人看，不写进提示词）

# 引申方向

- 变体：
- 反转：
- 前史 / 扩展：

# 明确不做 / 红线

-

# 待定

-

# 决策记录

- {date}：新建创意，按 R64 标准模板。
"""


def _fm_block(raw, updates):
    """保留原 frontmatter 的其它字段，只改 updates 里的键。"""
    lines = (raw or "").replace("\r\n", "\n").split("\n")
    fm = []
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm = lines[1:i]
                break
    out, seen = [], set()
    for line in fm:
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k = line.split(":", 1)[0].strip()
            if k in updates:
                out.append("%s: %s" % (k, updates[k]))
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append("%s: %s" % (k, v))
    return "---\n" + "\n".join(out) + "\n---"


def _storyboard_md(snap):
    unit = snap["meta"]["unit"]
    shots = {s["id"]: s for s in (snap.get("shots") or [])}
    prompts = {}
    try:
        doc = json.loads((Path(snap["meta"]["root"]) / "units" / unit /
                          "prompts" / "prompts.json").read_text(encoding="utf-8"))
        for x in doc.get("stills") or []:
            prompts.setdefault(str(x.get("shot")), {})["still"] = x.get("prompt") or ""
        for x in doc.get("motions") or []:
            prompts.setdefault(str(x.get("shot")), {})["motion"] = x.get("prompt") or ""
    except (OSError, ValueError):
        pass
    lines = ["# 分镜 · %s" % unit, ""]
    for sb in (snap.get("storyboards") or []):
        if sb.get("suggested"):
            continue
        sid = sb.get("seg_id") or sb.get("id") or ""
        lines.append("## %s · %s · %.1fs" % (sid, sb.get("scene") or "", float(sb.get("sec") or 0)))
        if sb.get("prompt"):
            lines += ["", "> " + str(sb["prompt"]).replace(chr(10), " ")[:500], ""]
        for sh in sb.get("shots") or []:
            x = shots.get(sh)
            if not x:
                continue
            lines.append("- **%s** %s · %s · %.1fs" % (
                x["id"], x.get("framing") or "", x.get("camera") or "", float(x.get("sec") or 0)))
            if x.get("action"):
                lines.append("  - 动作：%s" % x["action"])
            if x.get("line"):
                lines.append("  - 台词：%s" % x["line"])
            pr = prompts.get(x["id"]) or {}
            if pr.get("still"):
                lines.append("  - 静帧提示词：%s" % pr["still"])
            if pr.get("motion"):
                lines.append("  - 运动提示词：%s" % pr["motion"])
        lines.append("")
    return chr(10).join(lines) + chr(10)


def _assets_md(snap):
    lines = ["# 资产清单 · %s" % snap["meta"]["unit"], "",
             "| 类型 | ID | 名字 | 等级 | 状态 | 引用 |",
             "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for a in (snap.get("assets") or []):
        refs = a.get("scene_count") or a.get("shot_count") or 0
        lines.append("| %s | %s | %s | %s | %s | %d |" % (
            a.get("type_label") or a.get("type"), a["id"], a["name"],
            a.get("level") or "—", a.get("status") or "—", refs))
    return chr(10).join(lines) + chr(10)


class Conflict(Exception):
    pass


class _GraphDeferred(Exception):
    """这个节点现在还不能建（上游没生成），跳过，不算失败。"""


class _NeedsBinding(Exception):
    """外部素材还没绑到 LibTV 节点：跳过，提示去绑定，不算失败。"""


class _NeedsKeyframes(Exception):
    """参考视频不能直连：给关键帧槽位，不代劳抽帧。"""


class _NeedsFrame(Exception):
    """视频节点的首帧 / 尾帧还没有 LibTV 结果：先出帧，再补布视频。"""

    def __init__(self, frame):
        self.frame = frame
        Exception.__init__(self, "先出首帧 %s，再补布视频" % frame)


# graph.json 的写操作按「单元」串行化：布线 / 生成 / 同步 / 绑定都是
# load → 改 → save，线程并发会互相覆盖 node_id 和 history。
_GRAPH_LOCKS = {}
_GRAPH_LOCKS_GUARD = threading.Lock()


def _graph_lock(uid):
    key = str(uid or "")
    with _GRAPH_LOCKS_GUARD:
        return _GRAPH_LOCKS.setdefault(key, threading.Lock())


def _node_key(n):
    """节点的 LibTV key：外部节点优先 external.node_id，旧节点用顶层 node_id。"""
    ext = n.get("external") if isinstance(n.get("external"), dict) else {}
    return str(ext.get("node_id") or n.get("node_id") or "").strip()


def _node_current(n):
    """当前版本的 history 条目；没有 history 的旧节点返回空。"""
    try:
        rev = int(n.get("current_rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    for h in reversed(n.get("history") or []):
        if not isinstance(h, dict):
            continue
        try:
            if int(h.get("rev") or 0) == rev:
                return h
        except (TypeError, ValueError):
            continue
    return {}


def _is_ref_node(n):
    return n.get("role") in ("reference", "reference_video")


class LibTVError(Exception):
    """LibTV 调用失败。带上分类，前端据此给不同的下一步。"""

    def __init__(self, kind, msg, fix=None):
        Exception.__init__(self, msg)
        self.kind = kind or "unknown"
        self.fix = fix or libtv_mcp.fix_for(self.kind)


class _LoginJob:
    """一次 LibTV 授权子进程。stdout 里会打出授权链接，捞出来给页面兜底用。"""

    def __init__(self, proc, port):
        self.proc = proc
        self.port = port
        self.started = time.time()
        self.lines = []
        self.url = ""
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self.lines.append(line)
                if not self.url and "打开浏览器授权：" in line:
                    self.url = line.split("打开浏览器授权：", 1)[1].strip()
        except Exception:
            pass

    def alive(self):
        return self.proc.poll() is None

    def info(self):
        return {
            "state": "authorizing" if self.alive() else "finished",
            "port": self.port,
            "url": self.url,
            "elapsed": int(time.time() - self.started),
            "exit": self.proc.poll(),
            "tail": self.lines[-3:],
        }


# ---------------------------------------------------------------- 启动 / 停止

def bind_server(handler, ports):
    for port in ports:
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), handler), port
        except OSError:
            continue
    return None, None


def write_state(state, port, url, bg=False):
    state.state_file.parent.mkdir(parents=True, exist_ok=True)
    _jwrite(state.state_file, {
        "pid": os.getpid(), "port": port, "unit": state.unit,
        "url": url, "token": state.token, "root": str(state.root),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bg": bool(bg),
    })


def clear_state(state):
    try:
        state.state_file.unlink()
    except OSError:
        pass


def readme_text(root, snap):
    """工程根目录那张索引。只写路径，不复制内容——一份真相还是原来的文件。"""
    root = Path(root)
    unit = snap["meta"]["unit"]
    u = "units/" + unit
    rows = [
        ("剧本（定稿）", u + "/script/copy.md", "改了这里，画面要跟着重排"),
        ("场", u + "/storyboard/scenes.json", "每一场要完成什么"),
        ("分镜", u + "/storyboard/shots.json", "每个镜头怎么拍"),
        ("提示词", u + "/prompts/prompts.json", "静帧写长相，运动写动静"),
        ("审片", u + "/review", "逐镜检查结果"),
        ("成片", u + "/deliver", "交付的片子放这里"),
        ("资产", "assets", "人物 / 场景 / 道具，全季共用"),
    ]
    out = ["# %s" % snap["meta"]["name"], "",
           "> 这个文件由工作台自动生成，手改会被覆盖。", "",
           "## 这一集：%s" % unit, ""]
    for label, path, hint in rows:
        mark = "（还没有）" if not (root / path).exists() else ""
        out.append("- **%s** `%s`%s — %s" % (label, path, mark, hint))
    out += ["", "## 其他集", "",
            "`units/` 下面每个文件夹是一集，结构一样。", ""]
    return "\n".join(out)


def sync_readme(root, snap):
    """只有桌面版（AIV_LAUNCHER_URL）才写，命令行工程不受影响。"""
    if not os.environ.get("AIV_LAUNCHER_URL"):
        return
    try:
        p = Path(root) / "README.md"
        text = readme_text(root, snap)
        if p.is_file() and p.read_text(encoding="utf-8") == text:
            return
        p.write_text(text, encoding="utf-8")
    except Exception:
        pass


def serve(root, unit=None, port=8462, open_browser=True, bg=False):
    root = Path(root).resolve()
    if not (root / "project.json").exists():
        print("这里没有工程（缺 project.json）", file=sys.stderr)
        return 1
    st = _jload(root / "project.json", {}) or {}
    unit = unit or (st.get("meta") or {}).get("active_unit") or ""
    units = st.get("units") or {}
    if unit not in units:
        print("没有这个单元：%s（现有：%s）" % (unit, "、".join(units) or "无"), file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(16)
    state = State(root, unit, token)
    Handler.state = state

    def on_change(secs):
        state.archive_prompts(secs)
        state.invalidate(secs)

    state.watcher = watch.Watcher(root, None, on_change=on_change)
    state.watcher.start()

    # 先建一次快照：既是启动检查，也让提示词历史有「上一版」可比
    snap0 = state.snapshot()
    sync_readme(state.root, snap0)

    ports = ([port] + [p for p in range(8463, 8471) if p != port]) if port == 8462 else [port]
    srv, actual = bind_server(Handler, ports)
    if not srv:
        print("端口被占用，换一个：--port 8500", file=sys.stderr)
        return 1

    url = "http://127.0.0.1:%d/" % actual
    write_state(state, actual, url, bg)

    if not bg:
        print("")
        print("  %s · %s · 审片台" % (snap0["meta"]["name"], unit))
        print("  %s" % url)
        print("  %d 场 / %d 分镜 / %d 镜" % (
            snap0["stats"]["scenes"], snap0["stats"]["storyboards"],
            snap0["stats"]["shots"]))
        print("  监听  project.json · units/%s/** · assets/** · design/**" % unit)
        print("  停止  Ctrl+C")
        print("")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.watcher.stop()
        with state.lock:
            for q in list(state.clients):
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
        srv.server_close()
        clear_state(state)
        if not bg:
            print("已停止")
    return 0


def _read_state(root):
    return _jload(state_path(root), None)


def _picker_alive(port, timeout=0.25):
    """启动器的选择页是否还活着（它和别的服务一样是本地 HTTP）。"""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/alive" % int(port),
                timeout=timeout) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def _config_dir():
    """本机配置目录：引导进度这类「跟人走、不跟工程走」的状态放这里。

    和启动器共用同一个目录；更新安装包不会清掉它，也不会写进客户工程。
    """
    cfg = os.environ.get("CJ_CONFIG_DIR")
    if cfg:
        return Path(cfg)
    base = os.environ.get("AIV_STATE_DIR")
    if base:
        return Path(base).parent
    if os.name == "nt":
        v = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if v:
            return Path(v) / "Changji"
        return Path.home() / ".changji"
    return Path.home() / ".changji"


def _launcher_url():
    """「← 项目」要回的地址。

    不只在进程启动时读 AIV_LAUNCHER_URL：工作台被启动器复用、或者先命令行起过
    再被启动器接管时，环境变量是旧的（甚至没有）。启动器每次启动都会把当前
    选择页地址写进 launcher.json，这里每次请求都读一遍，并确认选择页还活着。
    """
    cands = []
    base = os.environ.get("AIV_STATE_DIR")
    if base:
        cands.append(Path(base).parent / "launcher.json")
    cfg = os.environ.get("CJ_CONFIG_DIR")
    if cfg:
        cands.append(Path(cfg) / "launcher.json")
    if os.name == "nt":
        for k in ("LOCALAPPDATA", "APPDATA"):
            v = os.environ.get(k)
            if v:
                cands.append(Path(v) / "Changji" / "launcher.json")
                cands.append(Path(v) / "场记" / "launcher.json")
    else:
        cands.append(Path.home() / ".changji" / "launcher.json")
    for p in cands:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            port = int(d.get("port") or 0)
        except Exception:
            continue
        if port and _picker_alive(port):
            return "http://127.0.0.1:%d/" % port
    # 锁文件不在（例如开发模式早期版本）再看环境变量，同样要求选择页活着。
    env = os.environ.get("AIV_LAUNCHER_URL") or ""
    try:
        port = urlparse(env).port
    except Exception:
        port = None
    return env if port and _picker_alive(port) else ""


def _ping(port, timeout=1.0):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % int(port),
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def stop(root, quiet=False):
    root = Path(root).resolve()
    st = _read_state(root)
    if not st:
        if not quiet:
            print("工作台没有在跑（没有状态文件）")
        return 0
    port = st.get("port")
    token = st.get("token") or ""
    ok = False
    if port:
        import urllib.request
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/shutdown" % int(port), data=b"{}",
                headers={"Content-Type": "application/json", "X-WB-Token": token},
                method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                ok = r.status == 200
        except Exception:
            ok = False
    if not ok:
        pid = st.get("pid")
        if pid:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, creationflags=NO_WINDOW)
                else:
                    os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
    for _ in range(20):
        if port and _ping(port, 0.2) is None:
            break
        time.sleep(0.1)
    try:
        state_path(root).unlink()
    except OSError:
        pass
    if not quiet:
        print("已停止（端口 %s）" % (port or "?"))
    return 0


def status(root):
    root = Path(root).resolve()
    st = _read_state(root)
    if not st:
        print("工作台：未运行")
        return 1
    port = st.get("port")
    ping = _ping(port) if port else None
    if not ping:
        print("工作台：状态文件在，但端口 %s 没有响应（可能是脏状态）" % port)
        print("  清理：d dev --stop")
        return 1
    up = time.time() - float(ping.get("started") or time.time())
    print("工作台：运行中")
    print("  地址  %s" % st.get("url"))
    print("  单元  %s" % ping.get("unit"))
    print("  镜头  %d ｜ 分镜 %d" % (ping.get("shots", 0), ping.get("storyboards", 0)))
    print("  监听  %d 个文件" % ping.get("files", 0))
    print("  已跑  %s" % _uptime(up))
    return 0


def _uptime(sec):
    sec = int(sec)
    if sec < 60:
        return "%d 秒" % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return "%d 分 %d 秒" % (m, s)
    h, m = divmod(m, 60)
    return "%d 小时 %d 分" % (h, m)
