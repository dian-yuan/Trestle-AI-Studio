"""从工程文件推导「思路 → 资产 / 剧本」关系。运行时 API 用，不产 HTML。"""
import json
import re
from pathlib import Path

from .snapshot import _frontmatter, _plain, _mtime_ns


def _idea_asset_ids(text):
    """从大纲/创意的「资产清单」表里抓 ID。"""
    ids = set()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        for cell in s.strip("|").split("|"):
            cell = cell.strip()
            if re.match(r"^(CH|SC|PR|ST|BR)-", cell):
                ids.add(cell)
    return ids


def _idea_meta(snap, idea):
    try:
        text = (Path(snap["meta"]["root"]) / idea["path"]).read_text(encoding="utf-8")
        meta, _ = _frontmatter(text)
        return meta
    except OSError:
        return {}


def _idea_link_ids(snap, idea):
    raw = _idea_meta(snap, idea).get("assets") or ""
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set(re.findall(r"(?:CH|SC|PR|ST|BR)-[^,\]\s]+", str(raw)))


def _idea_derived(snap, idea):
    """一份大纲衍生出什么：资产 / 剧本 / 分镜。"""
    root = Path(snap["meta"]["root"])
    unit = snap["meta"]["unit"]
    assets = snap.get("assets") or []
    by_id = {a["id"]: a for a in assets}
    ids = _idea_asset_ids(idea.get("text") or "") | _idea_link_ids(snap, idea)
    official = snap["script"]["official"]
    script_match = False
    if official.get("path"):
        try:
            text = (root / official["path"]).read_text(encoding="utf-8")
            script_match = (("source_idea: %s" % idea["path"]) in text or
                            (not ids and (idea.get("title") or "") in
                             (official.get("title") or "")))
        except OSError:
            script_match = False
    only_idea = len(snap["script"]["ideas"]) == 1
    picked = [by_id[x] for x in ids if x in by_id]
    if not picked and (script_match or only_idea):
        unit_ids = set()
        try:
            man = json.loads((root / "units" / unit / "manifest.json")
                             .read_text(encoding="utf-8"))
            unit_ids = set(man.get("assets") or [])
        except (OSError, ValueError):
            unit_ids = set()
        if unit_ids:
            picked = [a for a in assets if a["id"] in unit_ids]
        else:
            picked = [a for a in assets if unit in (a.get("used_by") or [])]
    chars = [a for a in picked if a["type"] == "characters"]
    scenes = [a for a in picked if a["type"] == "scenes"]
    props = [a for a in picked if a["type"] == "props"]
    styles = [a for a in picked if a["type"] == "style"]
    title = idea.get("title") or idea.get("name") or ""
    key = re.sub(r"[\/:*?\"<>|\s]+", "-", title).strip("-")
    if not styles and key and (picked or script_match):
        styles = [a for a in assets if a["type"] == "style"
                  and (a["id"] == "ST-" + key or a["id"].startswith("ST-" + key))]
    sbs = [x for x in (snap.get("storyboards") or []) if not x.get("suggested")]
    shots = snap.get("shots") or []
    show_sb = script_match or (only_idea and not ids)
    return {
        "chars": chars, "scenes": scenes, "props": props, "styles": styles,
        "script": official if script_match else None,
        "storyboards": sbs if show_sb else [],
        "shots": shots if show_sb else [],
    }


def scripts_for_idea(snap, idea):
    """扫 units/*/script/copy.md，按 source_idea 认领这个思路下的剧本。"""
    root = Path(snap["meta"]["root"])
    units = root / "units"
    rows = []
    if not units.is_dir():
        return rows
    for d in sorted(units.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        copy = d / "script" / "copy.md"
        if not copy.is_file():
            continue
        try:
            meta, stext = _frontmatter(copy.read_text(encoding="utf-8"))
        except OSError:
            continue
        src = str(meta.get("source_idea") or "").replace("\\", "/")
        title = str(meta.get("title") or "")
        if src == idea["path"] or (not src and (idea.get("title") or "") in title):
            man = {}
            try:
                man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            desc = str(meta.get("thesis") or "").strip() or _plain(stext)
            rows.append({
                "unit": d.name, "title": title or d.name,
                "status": meta.get("status") or "draft",
                "path": copy.relative_to(root).as_posix(),
                "duration": man.get("duration") or "",
                "desc": desc[:80],
                "mtime": _mtime_ns(copy),
            })
    rows.sort(key=lambda x: x["unit"])
    return rows
