# -*- coding: utf-8 -*-
"""导出：把一集或一场写成一份 Markdown。只读工程，不改任何文件。

    build(snap, root)                 整集
    build(snap, root, scene="SC01")   单场
"""

import json
import re
import time
from pathlib import Path

SCENE_ROWS = [("goal", "目标"), ("obstacle", "障碍"),
              ("audience_in", "观众进"), ("audience_out", "观众出"),
              ("state_change", "状态变化"), ("beat_range", "节拍")]


def _cell(v):
    s = str(v if v is not None else "").strip()
    if not s:
        return "—"
    return s.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _sec(v):
    n = _num(v)
    return ("%.1f" % n).rstrip("0").rstrip(".") if n else "—"


def _dur(sec):
    sec = int(round(_num(sec)))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return "%d 小时 %d 分" % (h, m)
    return ("%d 分 %d 秒" % (m, s)) if m else ("%d 秒" % s)


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except Exception:
        return ""


def _strip_front(text):
    text = text or ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 4)
            return text[nl + 1:] if nl != -1 else ""
    return text


def _embed(text):
    """嵌入工程里的 md：去掉开头的一级标题，其余标题统一降一级。"""
    lines = _strip_front(text).strip("\n").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+", lines[0]):
        lines.pop(0)
    out = []
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.append("#" * min(6, len(m.group(1)) + 1) + " " + m.group(2))
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


def _prompts(root, unit):
    try:
        d = json.loads((Path(root) / "units" / unit / "prompts" / "prompts.json")
                       .read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    stills = {str(x.get("shot")): x for x in (d.get("stills") or [])
              if isinstance(x, dict) and x.get("shot")}
    motions = {str(x.get("shot")): x for x in (d.get("motions") or [])
               if isinstance(x, dict) and x.get("shot")}
    return stills, motions


def _scene_block(sc, by_id, stills, motions, level=3):
    out = ["%s %s %s" % ("#" * level, sc["id"], sc.get("name") or ""), ""]
    rows = []
    for key, label in SCENE_ROWS:
        v = sc.get(key)
        if v:
            rows.append((label, str(v)))
    rev = sc.get("reveal") or []
    hold = sc.get("hold") or []
    if rev:
        rows.append(("揭示", "；".join(str(x) for x in rev)))
    if hold:
        rows.append(("按住", "；".join(str(x) for x in hold)))
    if rows:
        out += ["| 项 | 内容 |", "| :--- | :--- |"]
        out += ["| %s | %s |" % (k, _cell(v)) for k, v in rows]
        out.append("")

    ids = [x for x in (sc.get("shots") or []) if x in by_id]
    if ids:
        out += ["**镜头**", "",
                "| 镜号 | 秒 | 景别 | 机位 | 功能 | 台词 | 画面 |",
                "| :--- | ---: | :--- | :--- | :--- | :--- | :--- |"]
        for sid in ids:
            s = by_id[sid]
            out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                _cell(sid), _sec(s.get("sec")),
                _cell(s.get("framing") or s.get("shot")),
                _cell(s.get("camera")), _cell(s.get("func")),
                _cell(s.get("line")), _cell(s.get("visual"))))
        out.append("")

        blocks = []
        for sid in ids:
            st = stills.get(sid) or {}
            mo = motions.get(sid) or {}
            if not (st.get("prompt") or mo.get("prompt")):
                continue
            blocks.append("**%s 静帧**" % sid)
            blocks.append("> " + (st.get("prompt") or "—").replace("\n", "\n> "))
            if st.get("negative"):
                blocks.append("")
                blocks.append("负面词：%s" % st["negative"])
            blocks.append("")
            blocks.append("**%s 运动**" % sid)
            blocks.append("> " + (mo.get("prompt") or "—").replace("\n", "\n> "))
            blocks.append("")
        if blocks:
            out += ["**提示词**", ""] + blocks
    return out


def build(snap, root, scene_id="", include_prompts=True):
    meta = snap.get("meta") or {}
    name = meta.get("name") or ""
    unit = meta.get("unit") or ""
    scenes = list(snap.get("scenes") or [])
    by_id = {s["id"]: s for s in (snap.get("shots") or [])}
    if scene_id:
        scenes = [s for s in scenes if s.get("id") == scene_id]

    total = sum(_num(x.get("seconds")) for x in scenes) or \
        sum(_num(s.get("sec")) for s in by_id.values()
            if not scene_id or s.get("scene") == scene_id)

    title = " · ".join(x for x in (name, unit) if x)
    if scene_id and scenes:
        title += " · %s %s" % (scenes[0]["id"], scenes[0].get("name") or "")

    out = ["# " + (title or "导出"), ""]
    if name:
        out.append("- 片名：%s" % name)
    out.append("- 集号：%s" % (unit or "—"))
    if scenes:
        out.append("- 时长：%s" % _dur(total))
        out.append("- 规模：%d 场 / %d 镜" % (
            len(scenes), sum(len(x.get("shots") or []) for x in scenes)))
    out.append("- 导出：%s" % time.strftime("%Y-%m-%d %H:%M"))
    out += ["", "> 一次性导出的副本，改了不会回写工程。", ""]

    stills, motions = _prompts(root, unit) if include_prompts else ({}, {})

    if not scene_id:
        note = _read(Path(root) / "units" / unit / "storyboard" / "director-note.md")
        if note.strip():
            out += ["## 导演阐述", "", _embed(note), ""]
        official = (snap.get("script") or {}).get("official") or {}
        rel = official.get("path") or ""
        text = _read(Path(root) / rel) if rel else ""
        if text.strip():
            out += ["## 剧本（定稿）", "", _embed(text), ""]

    for sc in scenes:
        out += _scene_block(sc, by_id, stills, motions,
                            level=2 if scene_id else 3)
    return "\n".join(out).rstrip() + "\n"


def filename(snap, scene_id=""):
    meta = snap.get("meta") or {}
    parts = [meta.get("name") or "工程", meta.get("unit") or ""]
    if scene_id:
        parts.append(scene_id)
    s = "-".join(p for p in parts if p)
    s = re.sub(r'[\\/:*?"<>|\r\n]+', "_", s).strip(" .")
    return (s[:80] or "export") + ".md"
