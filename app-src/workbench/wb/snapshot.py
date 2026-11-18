"""快照层：工程 → 场 / 分镜 / 镜头 / 剧本 / 生成物。

分镜是 LibTV 生成单元（一般 ≤15 秒），提示词属于分镜，不属于单镜。
分镜优先读 segments/SEG-*；没建段时按 ≤15 秒给只读建议，不写文件。

    build(root, unit=None) -> dict
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path

SECTIONS = ("script", "scenes", "storyboards", "shots", "media", "review",
            "status", "prompts", "assets")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}
DOC_EXT = {".md", ".html", ".htm"}

ASSET_TYPES = (
    ("characters", "角色"),
    ("scenes", "场景"),
    ("props", "道具"),
    ("brand", "品牌"),
    ("style", "风格"),
)
ASSET_TYPE_LABEL = dict(ASSET_TYPES)
ASSET_ORDER = {k: i for i, (k, _l) in enumerate(ASSET_TYPES)}


def prompt_hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def refs_hash(refs):
    rows = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        image = r.get("image") or r.get("file") or ""
        if r.get("missing") or not image:
            continue
        kind = str(r.get("kind") or r.get("type") or "")
        if kind in ("style", "brand"):
            continue
        rows.append("%s|%s" % (
            r.get("id") or r.get("asset_id") or "", image))
    return hashlib.sha1("\n".join(sorted(rows)).encode("utf-8")).hexdigest()
ASSET_PREFIX = {"characters": "CH", "scenes": "SC", "props": "PR",
                "brand": "BR", "style": "ST"}

GRAPHIC_TYPES = {"MG", "T", "片头", "片尾", "字卡", "封面"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy", ".bak", "legacy",
             "_归档", "_vendor"}

# 视频节点里的参考图锚定块。带标记是为了重复布线时能先删旧的再加新的，
# 不会叠出两份；@Image 的编号就是布线时的上传顺序（imageListOrder）。
REF_BLOCK_START = "【参考素材 · 自动引用】"
REF_BLOCK_END = "【/参考素材 · 自动引用】"
REF_ROLE_LABEL = {"cast": "角色", "character": "角色", "scene": "场景",
                  "prop": "道具", "asset": "素材", "ref": "素材"}
REF_ROLE_USE = {
    "cast": "作为角色参考，锁脸与服装",
    "scene": "作为场景参考，锁空间结构与材质",
    "prop": "作为道具参考，锁形状与材质",
}

NEW_WINDOW = 24 * 3600
MAX_SEG_SEC = 15.0


# ---------------------------------------------------------------- 基础

def _j(p, d=None):
    p = Path(p)
    try:
        if not p.exists():
            return d
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return d


def _text(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except Exception:
        return ""


def _mtime(p):
    try:
        return Path(p).stat().st_mtime
    except OSError:
        return 0.0


def _mtime_ns(p):
    try:
        return Path(p).stat().st_mtime_ns
    except OSError:
        return 0


def _size(p):
    try:
        return Path(p).stat().st_size
    except OSError:
        return 0


def _is_new(m):
    return bool(m) and (time.time() - m) < NEW_WINDOW


def _rel(root, p):
    return Path(p).relative_to(root).as_posix()


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _sec(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return float(s)
    except ValueError:
        return 0.0


def _frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0].strip() != "---":
        return {}, text
    meta = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return {}, text


def _plain(md):
    t = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    t = re.sub(r"^---.*?---", " ", t, flags=re.S)
    t = re.sub(r"[#>*_`|]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _station(u):
    for k in sorted((u.get("stations") or {}), key=lambda x: _num(x)):
        if (u["stations"][k] or {}).get("status", "pending") not in ("done", "skipped"):
            return _num(k)
    return 9.0


# ---------------------------------------------------------------- 剧本

HEAD_RX = re.compile(
    r"^###\s+界\s*(\d+)\s*[·\-]?\s*(.*?)\s+"
    r"(\d+:\d+(?:\.\d+)?)\s*[–—\-~]\s*(\d+:\d+(?:\.\d+)?)\s*$"
)
# 说好五段的分场写法：`## 场 1 · 前台接待区 · 下午`，可以没有时间码。
SCENE_HEAD_RX = re.compile(r"^(?:##|###)\s+场\s*(\d+)\s*[·\-]?\s*(.*?)\s*$")


def _script_sections(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out, cur = [], None
    for line in lines:
        stripped = line.strip()
        m = HEAD_RX.match(stripped)
        sm = SCENE_HEAD_RX.match(stripped) if not m else None
        if m or sm:
            if cur:
                out.append(cur)
            if m:
                cur = {"n": int(m.group(1)), "name": m.group(2).strip(),
                       "start": _sec(m.group(3)), "end": _sec(m.group(4)),
                       "text": []}
            else:
                cur = {"n": int(sm.group(1)), "name": sm.group(2).strip(),
                       "start": 0.0, "end": 0.0, "text": []}
            continue
        if line.startswith("## ") and cur:
            out.append(cur)
            cur = None
            continue
        if cur is not None:
            cur["text"].append(line)
    if cur:
        out.append(cur)
    for s in out:
        s["text"] = "\n".join(s["text"]).strip()
    return out


def _official_script(root, uid):
    sdir = root / "units" / uid / "script"
    rows = []
    if sdir.exists():
        for p in sdir.rglob("*.md"):
            if any(x in p.parts for x in ("draft", ".bak")):
                continue
            meta, body = _frontmatter(_text(p))
            rows.append({"path": _rel(root, p), "name": p.name, "meta": meta,
                         "body": body, "mtime": _mtime(p)})
    finals = [r for r in rows if r["meta"].get("status") == "final"]
    pick = next((r for r in finals if r["name"] == "copy.md"), None)
    if pick is None and finals:
        pick = sorted(finals, key=lambda x: x["mtime"], reverse=True)[0]
    if pick is None:
        pick = next((r for r in rows if r["name"] == "copy.md"), None)
    if pick is None:
        return {"path": "", "name": "", "title": "", "status": "", "final": False,
                "text": "", "sections": [], "mtime": 0, "source_idea": "",
                "thesis": "", "runtime": ""}
    return {
        "path": pick["path"], "name": pick["name"],
        "title": pick["meta"].get("title") or pick["name"],
        "status": pick["meta"].get("status") or "未标状态",
        "final": pick["meta"].get("status") == "final",
        "text": pick["body"], "sections": _script_sections(pick["body"]),
        "mtime": pick["mtime"],
        "source_idea": str(pick["meta"].get("source_idea") or "").replace("\\", "/"),
        "thesis": str(pick["meta"].get("thesis") or ""),
        "runtime": str(pick["meta"].get("runtime") or ""),
    }


IDEA_REQUIRED = ["一句话主旨", "完整视频", "故事大纲", "角色设计",
                 "内容设计", "场景与视觉", "声音"]
IDEA_PLACEHOLDERS = {"（待填）", "（待定）", "（空）", "TODO", "TBD", "—", "-", ""}


def _idea_sections(body):
    lines = (body or "").replace("\r\n", "\n").split("\n")
    heads = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,2})\s+(.+?)\s*$", line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    if not heads:
        return {}
    base_level = min(h[1] for h in heads)
    rows = {}
    for idx, (i, level, title) in enumerate(heads):
        if level != base_level:
            continue
        end = len(lines)
        for j in range(idx + 1, len(heads)):
            if heads[j][1] <= base_level:
                end = heads[j][0]
                break
        rows[title] = "\n".join(lines[i + 1:end]).strip()
    return rows


def _idea_has_content(text):
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*+]\s+", "", s).strip()
        if s in IDEA_PLACEHOLDERS:
            continue
        if re.fullmatch(r"（[^）]*）", s):
            continue
        if re.fullmatch(r"[^：:]{1,16}[：:]", s):
            continue
        return True
    return False


def _idea_level(meta, body):
    sections = _idea_sections(body)
    present, filled = set(), set()
    for key in IDEA_REQUIRED:
        for title, text in sections.items():
            if key in title:
                present.add(key)
                if _idea_has_content(text):
                    filled.add(key)
                break
    missing = [k for k in IDEA_REQUIRED if k not in filled]
    absent = [k for k in IDEA_REQUIRED if k not in present]
    if "一句话主旨" not in present or "完整视频" not in present:
        level = "碎片"
    elif len(filled) >= len(IDEA_REQUIRED) and meta.get("status") == "ready":
        level = "L3"
    elif filled & {"故事大纲", "角色设计", "内容设计"}:
        level = "L2"
    else:
        level = "L1"
    return level, missing, absent


def _ideas(root, uid):
    # design/创意库 是新位置；design/剧本库 是旧项目兼容（启动时一般会迁移）
    dirs = [root / "design" / "创意库", root / "design" / "剧本库",
            root / "units" / uid / "script" / "draft"]
    out = []
    for base in dirs:
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            if any(x in p.parts for x in SKIP_DIRS):
                continue
            if "模板" in p.name or "TEMPLATE" in p.name.upper():
                continue
            meta, body = _frontmatter(_text(p))
            if meta.get("status") == "final":
                continue
            m = _mtime(p)
            rel = p.relative_to(base).as_posix()
            category = str(meta.get("category") or "").strip()
            if not category:
                if "/" in rel:
                    category = rel.split("/")[0]
                elif meta.get("type") == "创意" and base.name == "创意库":
                    category = "整片"
                else:
                    category = "未分类"
            tags = meta.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in tags.strip("[]").split(",") if x.strip()]
            level, missing, absent = _idea_level(meta, body)
            out.append({
                "path": _rel(root, p), "name": p.stem,
                "title": meta.get("title") or p.stem,
                "status": meta.get("status") or "未标状态",
                "kind": meta.get("type") or "想法",
                "category": category,
                "tags": tags,
                "parent": str(meta.get("parent") or "").strip(),
                "relation": str(meta.get("relation") or "").strip(),
                "ready": meta.get("status") == "ready",
                "level": level, "missing": missing, "absent": absent,
                "mtime": m, "size": _size(p), "is_new": _is_new(m),
                "excerpt": _plain(body)[:160], "text": body,
                "assets": meta.get("assets") or "",
                "thesis": str(meta.get("thesis") or ""),
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


# ---------------------------------------------------------------- 场

def _ref_items(v):
    """场里的资产引用：字符串或 {id, variant} 都收，保持顺序去重。"""
    out, seen = [], set()
    for x in v if isinstance(v, list) else []:
        if isinstance(x, str):
            item = {"id": x.strip(), "variant": ""}
        elif isinstance(x, dict):
            aid = x.get("id") or x.get("asset") or ""
            item = {"id": str(aid).strip(),
                    "variant": str(x.get("variant") or x.get("look") or "").strip()}
        else:
            continue
        if not item["id"]:
            continue
        key = (item["id"], item["variant"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _scene_start_end(shots, ids):
    sel = [s for s in shots if s["id"] in ids]
    if not sel:
        return 0.0, 0.0
    return min(_num(s.get("start")) for s in sel), max(_num(s.get("end")) for s in sel)


def _wired_refs(refs):
    """真正会挂到视频节点上的参考图，顺序就是 @Image 的编号。

    风格 / 品牌只进提示词不挂图；没图的资产也挂不上，所以一起剔掉。
    """
    out, seen = [], set()
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        aid = str(r.get("id") or "").strip()
        if not aid:
            continue
        kind = str(r.get("role") or r.get("type") or "")
        if kind in ("style", "brand"):
            continue
        if not str(r.get("image") or "").strip():
            continue
        key = (aid, str(r.get("variant") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def material_ref_block(refs):
    """把引用资产写成 @Image 锚定段，跟 LibTV 的智能参考模式一起用。"""
    wired = _wired_refs(refs)
    if not wired:
        return ""
    lines = [REF_BLOCK_START]
    for i, r in enumerate(wired, 1):
        kind = str(r.get("role") or r.get("type") or "")
        label = REF_ROLE_LABEL.get(kind, "素材")
        name = str(r.get("name") or r.get("label") or r.get("id") or "").strip()
        variant = str(r.get("variant_label") or "").strip()
        if variant:
            name = "%s（%s）" % (name, variant)
        use = REF_ROLE_USE.get(kind, "作为素材参考")
        lines.append("@Image%d %s：%s｜%s" % (i, label, name, use))
    styles = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("role") or r.get("type") or "")
        name = str(r.get("name") or r.get("label") or r.get("id") or "").strip()
        if kind in ("style", "brand") and name:
            styles.append(name)
    if styles:
        lines.append("画风参考：%s｜只学色调 / 光 / 质感，不复制画面内容" %
                     "、".join(styles))
    lines.append("以上各图均为唯一参考，禁止偏离；每张图只控制上述指定属性。")
    lines.append(REF_BLOCK_END)
    return "\n".join(lines)


def apply_ref_block(prompt, block):
    """把自动引用块放到提示词最前；重复布线先删旧块，避免叠两份。

    用户自己写过 @Image / {{Image N}} 的提示词保持原样，不覆盖。
    """
    text = str(prompt or "").strip()
    text = re.sub(re.escape(REF_BLOCK_START) + r".*?" +
                  re.escape(REF_BLOCK_END), "", text, flags=re.S).strip()
    if not text:
        return ""
    if not block:
        return text
    if re.search(r"@Image\s?\d+|{{Image\s?\d+}}", text):
        return text
    return block + "\n\n" + text


def _build_state(asset):
    """资产的构建状态：已生成 / 待生成 / 待布线 / 未构建。

    风格 / 品牌不靠出图，写成 style_bible / prompts.md 就算完成。
    """
    if asset.get("type") in ("style", "brand"):
        if asset.get("primary_image") or str(asset.get("style_bible") or "").strip() \
                or str(asset.get("prompts_text") or "").strip():
            return "done", "已设定"
        return "empty", "未设定"
    if asset.get("primary_image"):
        return "done", "已生成"
    units = [u for g in (asset.get("generations") or [])
             for u in (g.get("units") or [])]
    if any(str(u.get("output") or "").strip() for u in units):
        return "done", "已生成"
    if any(u.get("wired") for u in units):
        return "wired", "待生成"
    if any(str(u.get("prompt") or "").strip() for u in units) or \
            str(asset.get("prompts_text") or "").strip():
        return "ready", "待布线"
    return "empty", "未构建"


def _wire_gate(scene, sb):
    """布线门禁：这一场引用的角色 / 场景 / 道具都得先有图。"""
    required, blockers, seen = 0, [], set()
    for refs in ((scene or {}).get("asset_refs") or [],
                 (sb or {}).get("asset_refs") or []):
        for r in refs:
            kind = str(r.get("role") or r.get("type") or "")
            if kind in ("style", "brand"):
                continue
            key = (str(r.get("id") or ""), str(r.get("variant") or ""))
            if key in seen:
                continue
            seen.add(key)
            required += 1
            if str(r.get("image") or "").strip() and not r.get("missing"):
                continue
            blockers.append({
                "id": str(r.get("id") or ""),
                "name": str(r.get("name") or r.get("id") or ""),
                "type": str(r.get("type") or ""),
                "type_label": str(r.get("type_label") or ""),
                "variant": str(r.get("variant") or ""),
                "status": str(r.get("status") or ""),
                "build_state": str(r.get("build_state") or "missing"),
                "build_state_label": str(r.get("build_state_label") or "未登记"),
                "missing": bool(r.get("missing")),
            })
    return {"ready": not blockers, "required": required,
            "blockers": blockers}


def _scenes_from_script(script):
    """剧本已经有场次标题、但还没生成 scenes.json 时的只读场列表。"""
    rows = []
    for i, sec in enumerate(script.get("sections") or []):
        name = str(sec.get("name") or "").strip() or "场 %d" % (i + 1)
        start, end = _num(sec.get("start")), _num(sec.get("end"))
        rows.append({
            "id": "SC%02d" % (i + 1), "name": name, "chapter": name,
            "order": i, "goal": "", "obstacle": "", "audience_in": "",
            "audience_out": "", "reveal": [], "hold": [], "state_change": "",
            "resistance": "", "beat_range": "", "character_knows": {},
            "cast": [], "assets": [], "props": [], "shots": [],
            "start": start, "end": end,
            "seconds": round(max(0.0, end - start), 1), "script_only": True,
        })
    return rows


def _scenes(root, uid, shots):
    doc = _j(root / "units" / uid / "storyboard" / "scenes.json", None)
    rows = []
    if isinstance(doc, dict) and isinstance(doc.get("scenes"), list):
        for i, s in enumerate(doc["scenes"]):
            ids = [str(x).zfill(3) for x in (s.get("shots") or [])]
            rows.append({
                "id": str(s.get("id") or "SC%02d" % (i + 1)),
                "name": s.get("name") or s.get("chapter") or "",
                "chapter": s.get("chapter") or s.get("name") or "",
                "order": i,
                "goal": s.get("goal") or "",
                "obstacle": s.get("obstacle") or "",
                "audience_in": s.get("audience_in") or "",
                "audience_out": s.get("audience_out") or "",
                "reveal": s.get("reveal") or [],
                "hold": s.get("hold") or [],
                "state_change": s.get("state_change") or "",
                "resistance": s.get("resistance") or "",
                "beat_range": s.get("beat_range") or "",
                "character_knows": s.get("character_knows") or {},
                "cast": _ref_items(s.get("cast")),
                "assets": _ref_items(s.get("assets")),
                "props": _ref_items(s.get("props")),
                "shots": ids,
            })
    if not rows:
        groups = {}
        for s in shots:
            groups.setdefault(s.get("chapter") or "未分场", []).append(s["id"])
        for i, (name, ids) in enumerate(groups.items()):
            rows.append({"id": "SC%02d" % (i + 1), "name": name, "chapter": name,
                         "order": i, "goal": "", "obstacle": "", "audience_in": "",
                         "audience_out": "", "reveal": [], "hold": [],
                         "state_change": "", "resistance": "", "beat_range": "",
                         "character_knows": {}, "cast": [], "assets": [],
                         "props": [], "shots": ids})
    for s in rows:
        st, en = _scene_start_end(shots, set(s["shots"]))
        s["start"], s["end"] = st, en
        s["seconds"] = round(sum(_num(x.get("sec")) for x in shots
                                 if x["id"] in set(s["shots"])), 1)
    return rows


def _map_script(scenes, script):
    secs = script.get("sections") or []
    if not secs:
        for s in scenes:
            s["official_text"] = ""
            s["script_section"] = None
        return
    if len(secs) == len(scenes):
        for s, sec in zip(scenes, secs):
            s["official_text"] = sec["text"]
            s["script_section"] = sec
        return
    for s in scenes:
        best, score = None, 0
        for sec in secs:
            overlap = max(0, min(s["end"], sec["end"]) - max(s["start"], sec["start"]))
            if overlap > score:
                best, score = sec, overlap
        s["official_text"] = (best or {}).get("text", "")
        s["script_section"] = best


# ---------------------------------------------------------------- 分镜

def _segment_shots(segdir):
    m = _j(segdir / "manifest.json", {}) or {}
    ids = [str(x).zfill(3) for x in (m.get("shots") or [])]
    if not ids:
        doc = _j(segdir / "shots.json", {}) or {}
        ids = [str(x.get("id")).zfill(3) for x in (doc.get("shots") or [])
               if isinstance(x, dict) and x.get("id")]
    return m, ids


def _segment_media(root, uid, seg_id, segdir):
    out = {}
    dirs = [segdir / "renders", segdir / "stills", segdir / "keyframes"]
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
                continue
            out[_rel(root, p)] = {
                "path": _rel(root, p), "name": p.name,
                "kind": "video" if ext in VIDEO_EXT else
                        ("keyframe" if "keyframe" in p.parent.name else "image"),
                "mtime": _mtime(p), "size": _size(p),
            }
    rdir = root / "units" / uid / "renders"
    if rdir.exists():
        for p in rdir.rglob("*"):
            if p.is_file() and seg_id.lower() in p.name.lower() and \
                    p.suffix.lower() in (IMAGE_EXT | VIDEO_EXT):
                out[_rel(root, p)] = {
                    "path": _rel(root, p), "name": p.name,
                    "kind": "video" if p.suffix.lower() in VIDEO_EXT else "image",
                    "mtime": _mtime(p), "size": _size(p),
                }
    rows = sorted(out.values(), key=lambda x: x["mtime"], reverse=True)
    for i, row in enumerate(rows):
        row["is_latest"] = i == 0
    return rows


def _scene_of_shots(ids, scenes):
    counts = []
    for sc in scenes:
        n = len(set(ids) & set(sc["shots"]))
        if n:
            counts.append((n, sc))
    if not counts:
        return None
    counts.sort(key=lambda x: -x[0])
    return counts[0][1]


def _pack(shot_rows, hi=MAX_SEG_SEC, lo=5.0):
    """把镜头按 ≤hi 秒均分；返回镜头对象列表的列表。"""
    if not shot_rows:
        return []
    n = len(shot_rows)
    prefix = [0.0]
    for s in shot_rows:
        prefix.append(prefix[-1] + _num(s.get("sec")))
    total = prefix[-1]
    k = max(1, int(math.ceil(total / hi)))
    INF = 1e18
    while k <= n:
        dp = [[INF] * (k + 1) for _ in range(n + 1)]
        cut = [[0] * (k + 1) for _ in range(n + 1)]
        dp[0][0] = 0.0
        for j in range(1, k + 1):
            for i in range(1, n + 1):
                for p in range(j - 1, i):
                    seg = prefix[i] - prefix[p]
                    if seg > hi + 1e-6 and i - p > 1:
                        continue
                    val = max(dp[p][j - 1], seg)
                    if val < dp[i][j]:
                        dp[i][j] = val
                        cut[i][j] = p
        if dp[n][k] < INF:
            groups, i = [], n
            for j in range(k, 0, -1):
                p = cut[i][j]
                groups.append(shot_rows[p:i])
                i = p
            groups.reverse()
            return groups
        k += 1
    return [shot_rows]


def _libtv_prompt(segdir, ids):
    lib = _j(segdir / "libtv.json", {}) or {}
    nodes = lib.get("nodes") or []
    node = next((n for n in nodes if set(n.get("shots") or []) & set(ids)), None)
    if node and node.get("prompt"):
        return node["prompt"], "libtv.json"
    node = next((n for n in nodes if n.get("prompt")), None)
    if node:
        return node["prompt"], "libtv.json"
    if lib.get("prompt"):
        return lib["prompt"], "libtv.json"
    m = _j(segdir / "manifest.json", {}) or {}
    if m.get("prompt"):
        return m["prompt"], "manifest"
    return "", ""


def _production_scene_dir(root, uid, scene_id):
    base = root / "units" / uid / "production"
    if not base.is_dir():
        return None
    idx = _j(base / "index.json", {}) or {}
    for row in idx.get("scenes") or []:
        if str(row.get("id")) == str(scene_id):
            d = base / str(row.get("dir") or "")
            if d.is_dir():
                return d
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.startswith(str(scene_id) + "-"):
            return d
    return None


def _storyboards(root, uid, shots, scenes, cfg):
    by_id = {s["id"]: s for s in shots}
    scene_order = {sc["id"]: sc["order"] for sc in scenes}
    rows, covered = [], set()

    # 1) production/<场>/<分镜>/：新架构，分镜 = LibTV 工作流
    prod = root / "units" / uid / "production"
    if prod.is_dir():
        for sdir in sorted(prod.iterdir()):
            if not sdir.is_dir() or sdir.name.startswith((".", "_")):
                continue
            scdoc = _j(sdir / "scene.json", {}) or {}
            scene_id = str(scdoc.get("id") or "")
            if not scene_id:
                hit = re.match(r"^(SC\d+)", sdir.name)
                scene_id = hit.group(1) if hit else ""
            for sbdir in sorted(sdir.iterdir()):
                if not sbdir.is_dir() or sbdir.name.startswith((".", "_")):
                    continue
                sj = _j(sbdir / "storyboard.json", {})
                if not isinstance(sj, dict):
                    sj = {}
                sh = _j(sbdir / "shots.json", {})
                if not (sj or sh or (sbdir / "libtv.json").exists()
                        or (sbdir / "storyboard.json").exists()):
                    continue
                ids = [str(x).zfill(3) for x in (sj.get("shots") or
                                                  (sh.get("shots") if isinstance(sh, dict) else []) or [])]
                if not ids and isinstance(sh, dict):
                    ids = [str(x.get("id")).zfill(3) for x in (sh.get("shots") or [])
                           if isinstance(x, dict) and x.get("id")]
                ids = [x for x in ids if x in by_id]
                if not ids:
                    continue
                sid = str(sj.get("id") or sbdir.name)
                scene = _scene_of_shots(ids, scenes)
                scid = str(sj.get("scene") or scene_id or (scene or {}).get("id") or "")
                sc = next((x for x in scenes if x["id"] == scid), None)
                sec = round(_num(sj.get("sec")) or
                            sum(_num(by_id[x].get("sec")) for x in ids), 1)
                media = _segment_media(root, uid, sid, sbdir)
                videos = [x for x in media if x["kind"] == "video"]
                images = [x for x in media if x["kind"] != "video"]
                named = next((x for x in videos if sid.lower() in x["name"].lower()), None)
                prompt = str(sj.get("prompt") or "").strip()
                source = "storyboard.json" if prompt else ""
                if not prompt:
                    prompt, source = _libtv_prompt(sbdir, ids)
                libmeta = _libtv_meta(sbdir / "libtv.json", cfg)
                rows.append({
                    "id": sid, "seg_id": "", "name": sj.get("name") or "分镜",
                    "scene": scid, "shots": ids,
                    "sec": sec, "start": min(_num(by_id[x].get("start")) for x in ids),
                    "suggested": False, "over_limit": sec > MAX_SEG_SEC + 0.01,
                    "cross_scene": bool(sc) and any(x not in set(sc["shots"]) for x in ids),
                    "prompt": prompt, "prompt_source": source, "media": media,
                    "video": (named or (videos[0] if videos else {})).get("path", ""),
                    "image": (images[0]["path"] if images else ""),
                    "dir": _img_rel(root, sbdir), "source": "production",
                    "status": str(sj.get("status") or ""),
                    "sb_json": _img_rel(root, sbdir / "storyboard.json")
                               if (sbdir / "storyboard.json").exists() else "",
                    "sb_mtime": _mtime_ns(sbdir / "storyboard.json"),
                    "libtv": libmeta,
                    "libtv_path": _img_rel(root, sbdir / "libtv.json")
                                  if (sbdir / "libtv.json").exists() else "",
                    "libtv_mtime": _mtime_ns(sbdir / "libtv.json"),
                    "raw_cast": sj.get("cast") or [], "raw_assets": sj.get("assets") or [],
                    "raw_props": sj.get("props") or [],
                    "raw_has_refs": any(k in sj for k in ("cast", "assets", "props")),
                    "lib_refs": _libtv_ref_rows(libmeta),
                })
                covered.update(ids)

    # 2) segments/SEG-*：老生产层，只补 production 没覆盖的镜头
    base = root / "units" / uid / "segments"
    if base.exists():
        for d in sorted(base.iterdir()):
            if not d.is_dir() or not re.match(r"^SEG-\d+", d.name):
                continue
            m, ids = _segment_shots(d)
            ids = [x for x in ids if x in by_id]
            if not ids or set(ids) <= covered:
                continue
            sec = round(_num(m.get("sec")) or
                        sum(_num(by_id[x].get("sec")) for x in ids), 1)
            scene = _scene_of_shots(ids, scenes)
            media = _segment_media(root, uid, d.name, d)
            videos = [x for x in media if x["kind"] == "video"]
            images = [x for x in media if x["kind"] != "video"]
            named = next((x for x in videos if d.name.lower() in x["name"].lower()), None)
            prompt, source = _libtv_prompt(d, ids)
            libmeta = _libtv_meta(d / "libtv.json", cfg)
            rows.append({
                "id": "", "seg_id": d.name, "name": m.get("title") or "分镜",
                "scene": scene["id"] if scene else "",
                "shots": ids, "sec": sec,
                "start": min(_num(by_id[x].get("start")) for x in ids),
                "suggested": False, "over_limit": sec > MAX_SEG_SEC + 0.01,
                "cross_scene": bool(scene) and any(
                    x not in set(scene["shots"]) for x in ids),
                "prompt": prompt.strip(), "prompt_source": source,
                "media": media,
                "video": (named or (videos[0] if videos else {})).get("path", ""),
                "image": (images[0]["path"] if images else ""),
                "dir": _img_rel(root, d), "source": "segment", "status": "",
                "sb_json": "", "sb_mtime": 0,
                "libtv": libmeta,
                "libtv_path": _img_rel(root, d / "libtv.json")
                              if (d / "libtv.json").exists() else "",
                "libtv_mtime": _mtime_ns(d / "libtv.json"),
                "raw_cast": [], "raw_assets": [], "raw_props": [],
                "lib_refs": _libtv_ref_rows(libmeta),
            })
            covered.update(ids)

    # 3) 未覆盖的镜头给只读建议
    for sc in scenes:
        miss = [s for s in shots if s["id"] in sc["shots"] and s["id"] not in covered]
        for group in _pack(miss):
            ids = [s["id"] for s in group]
            rows.append({
                "id": "", "seg_id": "", "name": "建议分镜",
                "scene": sc["id"], "shots": ids,
                "sec": round(sum(_num(s.get("sec")) for s in group), 1),
                "start": min(_num(s.get("start")) for s in group),
                "suggested": True, "over_limit": False, "cross_scene": False,
                "prompt": "", "prompt_source": "", "media": [], "video": "", "image": "",
                "dir": "", "source": "suggested", "status": "",
                "sb_json": "", "sb_mtime": 0, "libtv": _libtv_meta(None, cfg),
                "libtv_path": "", "libtv_mtime": 0,
                "raw_cast": [], "raw_assets": [], "raw_props": [], "lib_refs": [],
            })
            covered.update(ids)

    rows.sort(key=lambda r: (scene_order.get(r["scene"], 99), r["start"]))
    counters = {sc["id"]: 0 for sc in scenes}
    for r in rows:
        m = re.match(r"^(SC\d+)-(\d+)$", r["id"])
        if m:
            counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))
    for r in rows:
        sc = r["scene"] or (scenes[0]["id"] if scenes else "SC01")
        if not r["id"]:
            counters[sc] = counters.get(sc, 0) + 1
            r["id"] = "%s-%02d" % (sc, counters[sc])
        if r["suggested"]:
            r["name"] = "建议分镜 %d" % counters.get(sc, 0)
        elif not r["name"] or r["name"] == "分镜":
            r["name"] = "分镜 %d" % counters.get(sc, 0)
    for r in rows:
        base_t = r["start"]
        for i, sid in enumerate(r["shots"], 1):
            s = by_id.get(sid)
            if not s:
                continue
            s["storyboard"] = r["id"]
            s["scene"] = r["scene"]
            s["h"] = "%s-%02d" % (r["id"], i)
            s["local_start"] = round(max(0.0, _num(s.get("start")) - base_t), 2)
            s["local_end"] = round(max(0.0, _num(s.get("end")) - base_t), 2)
        r["shot_count"] = len(r["shots"])
    return rows


# ---------------------------------------------------------------- 生成物

def _shot_media(root, uid, storyboards):
    idx = {}
    bases = [(root / "units" / uid / "keyframes", "keyframe"),
             (root / "units" / uid / "renders", "render")]
    for sb in storyboards:
        if sb.get("suggested"):
            continue
        if sb.get("dir"):
            d = root / sb["dir"]
            bases += [(d / "keyframes", "keyframe"), (d / "renders", "render"),
                      (d / "stills", "image")]
        elif sb.get("seg_id"):
            d = root / "units" / uid / "segments" / sb["seg_id"]
            bases += [(d / "keyframes", "keyframe"), (d / "renders", "render"),
                      (d / "stills", "image")]
    for base, kind in bases:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
                continue
            # 两种命名都要认：
            #   001-r1.png（手放 / 旧版）
            #   frame_001-r1.png / video_001-r1.mp4（图谱同步按节点 id 命名）
            m = re.match(r"^(?:frame|video)[_-]?(\d{1,3})", p.stem, re.I)
            sid = m.group(1).zfill(3) if m else p.stem[:3]
            if not sid.isdigit():
                continue
            idx.setdefault(sid, []).append({
                "path": _rel(root, p), "name": p.name,
                "kind": "video" if ext in VIDEO_EXT else kind,
                "mtime": _mtime(p), "size": _size(p),
            })
    for rows in idx.values():
        rows.sort(key=lambda x: x["mtime"], reverse=True)
        for i, row in enumerate(rows):
            row["is_latest"] = i == 0
    return idx


# ---------------------------------------------------------------- 审片

def _review(root, uid):
    d = _j(root / "units" / uid / "review" / "shot-review.json", None)
    out = {}
    if isinstance(d, dict) and isinstance(d.get("rows"), list):
        for r in d["rows"]:
            sid = str(r.get("shot") or "").zfill(3)
            if sid:
                out[sid] = {"verdict": r.get("verdict") or "",
                            "note": r.get("note") or ""}
    return out


def _review_counts(ids, review):
    ok = sum(1 for x in ids if review.get(x, {}).get("verdict") == "ok")
    no = sum(1 for x in ids if review.get(x, {}).get("verdict") == "no")
    return {"ok": ok, "no": no, "pending": len(ids) - ok - no, "total": len(ids)}


# ---------------------------------------------------------------- 设计信息

def _scene_design(root, uid, sc, shots, spaces, man):
    sel = [s for s in shots if s["id"] in set(sc["shots"])]
    space_ids = []
    for s in sel:
        if s.get("space") and s["space"] not in space_ids:
            space_ids.append(s["space"])
    aid = space_ids[0] if space_ids else ""
    sp = spaces.get(aid) or {}
    used_cams, used_lights = [], []
    for s in sel:
        cid = s.get("camera_id") or s.get("camera")
        if cid and cid not in used_cams:
            used_cams.append(cid)
    for l in (sp.get("lights") or []):
        used_lights.append("%s %sK" % (l.get("id", ""), l.get("kelvin", "")))
    styles = [r["id"] for r in (sc.get("asset_refs") or []) if r["role"] == "style"]
    memory = []
    for s in sel:
        if s.get("memory") and s["memory"] not in memory:
            memory.append(s["memory"])
    props = [r["name"] for r in (sc.get("asset_refs") or []) if r["role"] == "prop"]
    return {
        "space_id": aid,
        "space": ("%s %s" % (aid, sp.get("name") or "")).strip() or "—",
        "cameras": " / ".join(used_cams) or "—",
        "lights": " · ".join(used_lights) or "—",
        "style": " / ".join(styles) or "—",
        "props": " / ".join(props) or "—",
        "memory": " / ".join(memory) or "—",
    }


# ---------------------------------------------------------------- 资产
#
# 一个资产 = assets/<type>/<ID-Name>/ 一个目录。最终就是一张图，但允许很多张：
#   variants/<key>/variant.json + image.*   工作台固化的变体（装造 / 机位 / 状态）
#   refs/*.jpg                              散图，自动当候选
#   manifest.files.anchor_ref               锚点图，自动当候选（兼容老资产）
#   build/<canvas>/U*/unit.json             生成单元，产物存在就当候选
# 以上都能被场引用；场在 scenes.json 里写 cast / assets，没写就按名字和内容推断。

_MISS = object()


def _norm(s):
    return re.sub(r"[\s·｜|/\\,，。:：;；()（）\[\]【】\"'`]+", "", str(s or "")).lower()


def _short_name(s):
    return re.split(r"[（(·｜|/]", str(s or ""))[0].strip()


def _slug(s):
    t = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", str(s or "")).strip("-").lower()
    return t[:40] or "img"


def _img_rel(root, p):
    try:
        p = Path(p).resolve()
        return p.relative_to(Path(root).resolve()).as_posix()
    except Exception:
        return ""


def _resolve_img(root, base, value):
    """把 manifest / unit.json 里的图片路径解析成工程相对路径；不存在返回空。"""
    s = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not s:
        return ""
    cands = [Path(base) / s, Path(root) / s, Path(s)]
    for c in cands:
        try:
            c = c.resolve()
            if c.is_relative_to(Path(root).resolve()) and c.is_file() \
                    and c.suffix.lower() in IMAGE_EXT:
                return c.relative_to(Path(root).resolve()).as_posix()
        except Exception:
            continue
    return ""


def _first_image(d, recursive=True):
    d = Path(d)
    if not d.exists():
        return None
    it = d.rglob("*") if recursive else d.iterdir()
    for p in sorted(it):
        try:
            if p.is_file() and p.suffix.lower() in IMAGE_EXT and p.name != ".gitkeep":
                return p
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- LibTV 链接
#
# 工程约定：一场 = LibTV 一个 project（画布），一个分镜 = 画布里的一组节点（工作流），
# 一个资产的生成 = 资产自己的 build/<画布>/。链接只存 projectId / 节点 id / 页面 URL，
# 不猜平台路径；没配 url 时可由 project.json 的 adapters.libtv 模板拼。

def _safe_url(u):
    s = str(u or "").strip()
    return s if s.lower().startswith(("http://", "https://")) else ""


def _libtv_cfg(st):
    cfg = ((st.get("adapters") or {}).get("libtv") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "web_base": str(cfg.get("web_base") or "https://www.liblib.tv"),
        "project_url": str(cfg.get("project_url")
                           or "https://www.liblib.tv/canvas?sourceProjectUuid={project_id}"),
        "node_url": str(cfg.get("node_url") or ""),
    }


def _fmt_url(tpl, project_id="", node_id="", base=""):
    if not tpl:
        return ""
    try:
        return tpl.format(project_id=project_id, node_id=node_id,
                          base=str(base or "").rstrip("/"))
    except Exception:
        return ""


def _editor_url(project_id, space=0, focus=""):
    """LibTV 画布深链：projectId + spaceId + focusNodeId（分镜 group）。"""
    pid = str(project_id or "").strip()
    if not pid:
        return ""
    q = ["projectId=" + pid]
    try:
        sid = int(space or 0)
    except (TypeError, ValueError):
        sid = 0
    if sid:
        q.append("spaceId=%d" % sid)
    if focus:
        q.append("focusNodeId=" + str(focus).strip())
    return "https://www.liblib.tv/canvas?" + "&".join(q)


def _libtv_meta(path, cfg):
    lib = (_j(path, {}) if path else {}) or {}
    if not isinstance(lib, dict):
        lib = {}
    canvas = lib.get("canvas") if isinstance(lib.get("canvas"), dict) else {}
    pid = str(lib.get("project_id") or lib.get("projectId") or
              canvas.get("projectId") or canvas.get("project_id") or "").strip()
    node_id = str(lib.get("node_id") or lib.get("nodeId") or "").strip()
    if not node_id:
        node_id = next((str(n.get("node_id") or n.get("nodeId") or "").strip()
                        for n in (lib.get("nodes") or [])
                        if n.get("node_id") or n.get("nodeId")), "")
    url = _safe_url(lib.get("url") or lib.get("web_url")
                    or canvas.get("url") or canvas.get("web_url"))
    if not url and pid:
        if node_id and cfg.get("node_url"):
            url = _fmt_url(cfg["node_url"], pid, node_id, cfg.get("web_base") or "")
        if not url and cfg.get("project_url"):
            url = _fmt_url(cfg["project_url"], pid, node_id, cfg.get("web_base") or "")
    refs = [r for r in (lib.get("refs") or []) if isinstance(r, dict)]
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    space = _int(lib.get("project_space_id") or lib.get("projectSpaceId")
                 or canvas.get("projectSpaceId") or lib.get("folder_id")
                 or canvas.get("folderId"))
    group_id = str(lib.get("group_id") or lib.get("groupId") or "")
    video = lib.get("video") if isinstance(lib.get("video"), dict) else {}
    editor = _editor_url(pid, space, group_id or node_id)
    open_url = editor if (pid and space) else _safe_url(url)
    return {
        "project_id": pid, "node_id": node_id, "url": _safe_url(open_url),
        "editor_url": _safe_url(editor),
        "name": str(lib.get("name") or canvas.get("name") or lib.get("title") or ""),
        "model": str(lib.get("model") or canvas.get("model") or ""),
        "status": str(lib.get("status") or canvas.get("status") or ""),
        "project_space_id": space, "folder_id": space,
        "asset_folder_id": _int(lib.get("asset_folder_id") or lib.get("assetFolderId")),
        "group_id": group_id, "group_name": str(lib.get("group_name") or ""),
        "group_label": str(lib.get("group_label") or ""),
        "group_stale": bool(lib.get("group_stale")),
        "lib_key": str(lib.get("lib_key") or ""),
        "bound": bool(pid or url),
        "refs": refs,
        "video": dict(video),
        "stills": dict(lib.get("stills") or {})
        if isinstance(lib.get("stills"), dict) else {},
    }


def _libtv_ref_rows(meta):
    out = []
    for r in meta.get("refs") or []:
        out.append({
            "key": str(r.get("key") or r.get("id") or ""),
            "label": str(r.get("label") or r.get("name") or ""),
            "file": str(r.get("file") or r.get("image") or ""),
            "kind": str(r.get("kind") or ""),
            "asset_id": str(r.get("asset_id") or r.get("asset") or ""),
            "node_id": str(r.get("node_id") or ""),
        })
    return out


def _ref_prompt(asset, variant_id=""):
    v = next((x for x in asset.get("variants") or [] if x["id"] == variant_id), None) \
        if variant_id else None
    if v is None:
        v = next((x for x in asset.get("variants") or [] if x.get("primary")), None)
    if v and v.get("prompt"):
        return v["prompt"]
    for g in asset.get("generations") or []:
        for u in g.get("units") or []:
            match = False
            if v and v.get("unit") and u.get("id") == v["unit"] and \
                    (not v.get("canvas") or g.get("id") == v.get("canvas")):
                match = True
            if v and v.get("image") and u.get("output") == v["image"]:
                match = True
            if match and u.get("prompt"):
                return u["prompt"]
    for g in asset.get("generations") or []:
        for u in g.get("units") or []:
            if u.get("prompt"):
                return u["prompt"]
    return _prompt_excerpt(asset.get("prompts_text") or "")


def _prompt_excerpt(text, n=280):
    """从 prompts.md 抽一段能读的预览；优先抽真正写画面/提示词的那段。"""
    t = re.sub(r"^---.*?---", " ", text or "", flags=re.S)
    t = re.sub(r"^\s*#+.*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*>.*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*\|.*$", " ", t, flags=re.M)
    paras = [_plain(p) for p in re.split(r"\n\s*\n", t)]
    paras = [p for p in paras if len(p) >= 30]
    if not paras:
        return _plain(t)[:n]
    def score(p):
        s = 0.0
        if p.startswith("写实"):
            s += 100
        for kw, w in (("提示词", 40), ("母图", 30), ("三视图", 30),
                      ("摄影", 20), ("镜头", 15), ("画面", 10)):
            if kw in p:
                s += w
        return s + min(len(p), 600) / 100.0
    pick = max(paras, key=score)
    return pick[:n] + ("…" if len(pick) > n else "")


def _variants(root, adir, man):
    rows, ids, images = [], set(), set()

    def add(v):
        vid = str(v.get("id") or "").strip()
        image = v.get("image") or ""
        if not vid or vid in ids:
            return
        if image and image in images and v.get("source") != "variant":
            return
        ids.add(vid)
        if image:
            images.add(image)
        rows.append({
            "id": vid,
            "label": str(v.get("label") or vid),
            "status": str(v.get("status") or "未标状态"),
            "image": image,
            "prompt": str(v.get("prompt") or ""),
            "note": str(v.get("note") or ""),
            "source": v.get("source") or "manifest",
            "canvas": str(v.get("canvas") or ""),
            "unit": str(v.get("unit") or ""),
            "updated": v.get("updated") or 0,
            "primary": bool(v.get("primary")),
        })

    # 1. 工作台固化的变体
    vroot = adir / "variants"
    if vroot.is_dir():
        for d in sorted(vroot.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            meta = _j(d / "variant.json", {}) or {}
            if not isinstance(meta, dict):
                meta = {}
            img = _resolve_img(root, d, meta.get("image")) or \
                _img_rel(root, _first_image(d))
            add({"id": meta.get("id") or d.name,
                 "label": meta.get("label") or meta.get("name") or d.name,
                 "status": meta.get("status") or "未标状态",
                 "image": img, "prompt": meta.get("prompt") or "",
                 "note": meta.get("note") or "", "primary": meta.get("primary"),
                 "canvas": meta.get("canvas") or "", "unit": meta.get("unit") or "",
                 "updated": _mtime(d / "variant.json"), "source": "variant"})

    # 2. manifest 里内联的变体
    for i, v in enumerate(man.get("variants") or []):
        if isinstance(v, str):
            v = {"id": v}
        if not isinstance(v, dict):
            continue
        img = _resolve_img(root, adir, v.get("image") or v.get("ref"))
        add({"id": v.get("id") or "m%02d" % (i + 1),
             "label": v.get("label") or v.get("name") or v.get("id") or "变体",
             "status": v.get("status"), "image": img,
             "prompt": v.get("prompt") or "", "note": v.get("note") or "",
             "primary": v.get("primary"), "source": "manifest"})

    # 3. manifest 主图 / 锚点图（老资产靠这两项）
    img = _resolve_img(root, adir, man.get("image") or man.get("poster"))
    if img:
        add({"id": "main", "label": "主图", "status": man.get("status"),
             "image": img, "primary": man.get("primary"), "source": "manifest"})
    files = man.get("files") if isinstance(man.get("files"), dict) else {}
    for key in ("anchor_ref", "anchor", "poster", "cover", "swatch"):
        img = _resolve_img(root, adir, files.get(key))
        if img:
            label = {"anchor_ref": "锚点图", "anchor": "锚点图"}.get(key) or key
            add({"id": key if key != "anchor" else "anchor_ref", "label": label,
                 "status": "参考", "image": img, "source": "ref"})

    # 4. 生成单元的产物
    build = adir / "build"
    if build.is_dir():
        for cdir in sorted(build.iterdir()):
            if not cdir.is_dir() or cdir.name.startswith((".", "_")):
                continue
            for udir in sorted(cdir.iterdir()):
                if not udir.is_dir() or not re.match(r"^U\d+", udir.name):
                    continue
                um = _j(udir / "unit.json", {}) or {}
                if not isinstance(um, dict):
                    um = {}
                img = _resolve_img(root, udir, um.get("output")) or \
                    _img_rel(root, _first_image(udir / "renders"))
                if not img:
                    continue
                add({"id": "%s-%s" % (cdir.name.lower(), udir.name.lower()),
                     "label": um.get("name") or udir.name,
                     "status": um.get("status") or "生成单元",
                     "image": img, "prompt": um.get("prompt") or "",
                     "canvas": cdir.name, "unit": udir.name, "source": "unit"})

    # 5. refs/ 散图
    refs = adir / "refs"
    if refs.is_dir():
        for p in sorted(refs.rglob("*")):
            try:
                if not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
                    continue
            except OSError:
                continue
            rel = _img_rel(root, p)
            if not rel or rel in images:
                continue
            add({"id": "ref-" + _slug(p.stem), "label": p.stem,
                 "status": "参考图", "image": rel, "source": "ref"})

    # 主图只能有一张：显式 primary > 锚点 > 第一张有图的
    flagged = [v for v in rows if v["primary"] and v["image"]]
    if flagged:
        keep = flagged[0]
    else:
        keep = next((v for v in rows if v["id"] in ("anchor_ref", "main") and v["image"]), None) \
            or next((v for v in rows if v["image"]), None)
    for v in rows:
        v["primary"] = bool(keep and v is keep)

    order = {"variant": 0, "manifest": 1, "ref": 2, "unit": 3}
    rows.sort(key=lambda v: (0 if v["primary"] else 1, order.get(v["source"], 9),
                             v["label"]))
    return rows


def asset_prompt_hash(prompt, negative="", size=""):
    """资产节点提示词指纹：用于判断本地改过、但还没推到 LibTV。"""
    raw = json.dumps({
        "prompt": str(prompt or ""),
        "negative": str(negative or ""),
        "size": str(size or ""),
    }, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generations(root, adir, cfg):
    out = []
    build = adir / "build"
    if not build.is_dir():
        return out
    for cdir in sorted(build.iterdir()):
        if not cdir.is_dir() or cdir.name.startswith((".", "_")):
            continue
        cm = _j(cdir / "canvas.json", {}) or {}
        if not isinstance(cm, dict):
            cm = {}
        libmeta = _libtv_meta(cdir / "libtv.json", cfg)
        units = []
        for udir in sorted(cdir.iterdir()):
            if not udir.is_dir() or not re.match(r"^U\d+", udir.name):
                continue
            um = _j(udir / "unit.json", {}) or {}
            if not isinstance(um, dict):
                um = {}
            images = []
            for sub in ("renders", "refs"):
                d = udir / sub
                if not d.exists():
                    continue
                for p in sorted(d.rglob("*")):
                    try:
                        if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                            images.append(_img_rel(root, p))
                    except OSError:
                        continue
            output = _resolve_img(root, udir, um.get("output")) or \
                (images[0] if images else "")
            lib = um.get("libtv") if isinstance(um.get("libtv"), dict) else {}
            cur_hash = asset_prompt_hash(
                um.get("prompt"), um.get("negative"), um.get("size"))
            lstate = lib.get("state") if isinstance(lib.get("state"), dict) else {}
            remote_prompt = str(lstate.get("prompt") or "")
            remote_changed = bool(
                remote_prompt.strip()
                and remote_prompt.strip() != str(um.get("prompt") or "").strip())
            units.append({
                "id": udir.name, "name": um.get("name") or udir.name,
                "status": um.get("status") or "未写",
                "prompt": um.get("prompt") or "",
                "negative": um.get("negative") or "",
                "size": um.get("size") or "",
                "model": um.get("model") or cm.get("model") or "",
                "style": um.get("style") if isinstance(um.get("style"), dict)
                         else {"mode": "libtv"},
                "palette": um.get("palette")
                           if isinstance(um.get("palette"), list) else [],
                "output": output, "images": images,
                "has_output": bool(output),
                "wired": bool(lib.get("wired") or lib.get("node_id")),
                # 老工程没有 prompt_hash：先把已布线单元标成「待推」，
                # 让用户能看到「更新到 LibTV」并主动同步一次，而不是永远不知道。
                "prompt_dirty": bool(lib.get("node_id") and (
                    not lib.get("prompt_hash") or lib["prompt_hash"] != cur_hash)),
                "libtv": lib,
                # LibTV 上直接改的方案：只展示，用户点「采用」才写回本地。
                "libtv_state": lstate,
                "libtv_state_status": str(lstate.get("status") or ""),
                "libtv_prompt": remote_prompt,
                "libtv_model": str(lstate.get("model") or ""),
                "libtv_params": lstate.get("params") or {},
                "libtv_style": lstate.get("style") or [],
                "libtv_urls": lstate.get("urls") or [],
                "libtv_at": str(lstate.get("at") or ""),
                "remote_changed": remote_changed,
            })
        out.append({
            "id": cdir.name, "title": cm.get("title") or cdir.name,
            "purpose": cm.get("purpose") or "", "status": cm.get("status") or "未建",
            "model": cm.get("model") or "", "project_id": cm.get("project_id") or "",
            "libtv": libmeta,
            "libtv_mtime": _mtime_ns(cdir / "libtv.json"),
            "units": units,
            "with_image": len([u for u in units if u["has_output"]]),
        })
    return out


def _asset_dirs(root):
    base = root / "assets"
    if not base.is_dir():
        return
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        tkey = folder.name
        if tkey not in ASSET_TYPE_LABEL:
            continue
        for d in sorted(folder.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            m = _j(d / "manifest.json", None)
            if not isinstance(m, dict):
                m = {}
            aid = str(m.get("id") or "").strip()
            if not aid:
                hit = re.match(r"^([A-Z]{2}-[A-Z0-9]+-\d+)", d.name)
                aid = hit.group(1) if hit else ""
            if not aid:
                continue
            yield tkey, d, m, aid


def _assets(root, uid, man, cfg):
    rows = []
    for tkey, d, m, aid in _asset_dirs(root):
        raw_name = str(m.get("name") or d.name)
        if raw_name.startswith(aid):
            raw_name = raw_name[len(aid):].lstrip("- ")
        variants = _variants(root, d, m)
        primary = next((v for v in variants if v["primary"]), None)
        images = []
        for v in variants:
            if v["image"] and v["image"] not in images:
                images.append(v["image"])
        files = m.get("files") if isinstance(m.get("files"), dict) else {}
        used_by = [str(x) for x in (m.get("used_by") or []) if str(x).strip()]
        ppath = next((p for p in (d / "prompts.md", d / "prompts" / "prompts.md")
                      if p.exists()), None)
        bpath = d / "style_bible.md"
        bible = _text(bpath) if bpath.exists() else ""
        mt = _mtime(d / "manifest.json")
        for v in variants:
            mt = max(mt, float(v.get("updated") or 0))
        row = {
            "id": aid, "name": raw_name, "type": tkey,
            "type_label": ASSET_TYPE_LABEL.get(tkey, tkey),
            "dir": _img_rel(root, d),
            "status": str(m.get("status") or "未标状态"),
            "level": str(m.get("level") or ""),
            "kind": str(m.get("kind") or ""),
            "role": str(m.get("role") or ""),
            "notes": str(m.get("notes") or ""),
            "used_by": used_by,
            "variants": variants,
            "primary": primary["id"] if primary else "",
            "primary_image": primary["image"] if primary else "",
            "images": images,
            "generations": _generations(root, d, cfg),
            "manifest": _img_rel(root, d / "manifest.json"),
            "space": _img_rel(root, d / "space.json") if (d / "space.json").exists() else "",
            "prompts": _img_rel(root, ppath) if ppath else "",
            "prompts_text": _text(ppath) if ppath else "",
            "style_bible": bible,
            "style_bible_path": _img_rel(root, bpath) if bpath.exists() else "",
            "palette": m.get("palette") if isinstance(m.get("palette"), list) else [],
            "light": m.get("light") if isinstance(m.get("light"), dict) else {},
            "lens": m.get("lens") if isinstance(m.get("lens"), dict) else {},
            "dynasty": str(m.get("dynasty") or ""),
            "files": [{"key": k, "path": str(v)} for k, v in sorted(files.items())],
            "updated": mt,
            "scenes": [], "shots": [], "scene_count": 0, "shot_count": 0,
            "in_unit": False,
        }
        row["build_state"], row["build_state_label"] = _build_state(row)
        rows.append(row)
    rows.sort(key=lambda a: (ASSET_ORDER.get(a["type"], 99), _norm(a["name"]), a["id"]))
    return rows


def _name_match(text, asset):
    text = _norm(text)
    if not text:
        return 0
    best = 0
    for cand in (asset["name"], _short_name(asset["name"])):
        n = _norm(cand)
        if len(n) >= 2 and n in text:
            best = max(best, len(n))
    return best


def _scene_asset_refs(scenes, shots, assets, man):
    by_id = {a["id"]: a for a in assets}
    unit_ids = [str(x) for x in (man.get("assets") or []) if str(x).strip()]
    unit_set = set(unit_ids) if unit_ids else set(by_id)
    by_shot = {s["id"]: s for s in shots}

    for sc in scenes:
        refs, seen = [], set()

        def add(aid, variant="", role="", source="explicit"):
            aid = str(aid or "").strip()
            variant = str(variant or "").strip()
            if not aid or (aid, variant, role) in seen:
                return
            seen.add((aid, variant, role))
            a = by_id.get(aid)
            if not a:
                refs.append({"id": aid, "name": aid, "type": "", "type_label": "",
                             "role": role or "asset", "variant": variant,
                             "variant_label": "", "image": "", "status": "未登记",
                             "source": source, "missing": True,
                             "build_state": "missing",
                             "build_state_label": "未登记"})
                return
            v = next((x for x in a["variants"] if x["id"] == variant), None) if variant else \
                next((x for x in a["variants"] if x["primary"]), None)
            refs.append({
                "id": aid, "name": a["name"], "type": a["type"],
                "type_label": a["type_label"], "role": role or "asset",
                "variant": variant,
                "variant_label": (v or {}).get("label", ""),
                "image": a["primary_image"] if not variant else (v or {}).get("image", ""),
                "status": a["status"], "source": source, "missing": False,
                "build_state": a.get("build_state") or "empty",
                "build_state_label": a.get("build_state_label") or "未构建",
                "prompt": _ref_prompt(a, variant),
            })

        def role_of(a):
            return {"characters": "cast", "scenes": "scene", "props": "prop",
                    "style": "style", "brand": "brand"}.get(a["type"], "asset")

        explicit_cast = list(sc.get("cast") or [])
        explicit_assets = list(sc.get("assets") or [])
        explicit_props = list(sc.get("props") or [])
        had_explicit = bool(explicit_cast or explicit_assets or explicit_props)

        for item in explicit_cast:
            add(item["id"], item.get("variant"), "cast")
        for item in explicit_assets:
            a = by_id.get(item["id"])
            add(item["id"], item.get("variant"), role_of(a) if a else "asset")
        for item in explicit_props:
            add(item["id"], item.get("variant"), "prop")

        # 没写 cast：按 character_knows 的键去对角色资产名
        if not explicit_cast:
            for nm in (sc.get("character_knows") or {}):
                best = max((( _name_match(nm, a), a) for a in assets
                            if a["type"] == "characters" and a["id"] in unit_set),
                           key=lambda x: x[0], default=(0, None))
                if best[0] >= 2:
                    add(best[1]["id"], "", "cast", "inferred")

        have = {r["role"] for r in refs}
        text = " ".join([
            sc.get("name", ""), sc.get("chapter", ""), sc.get("goal", ""),
            sc.get("obstacle", ""), sc.get("state_change", ""),
            sc.get("official_text", ""),
            (sc.get("script") or {}).get("text", ""),
        ] + [" ".join(str(x) for x in (sc.get("reveal") or [])),
             " ".join(str(x) for x in (sc.get("hold") or []))])
        shot_text = " ".join(
            "%s %s %s" % (by_shot[x].get("visual", ""), by_shot[x].get("action", ""),
                          by_shot[x].get("line", ""))
            for x in sc.get("shots", []) if x in by_shot)

        # 没写 cast、也没有 character_knows：按场文本里出现的角色名猜
        # （剧本已切场、还没分镜时没有 character_knows，只能靠文本）。
        if not explicit_cast and "cast" not in have:
            hay = text + " " + shot_text
            for a in assets:
                if a["type"] != "characters" or a["id"] not in unit_set:
                    continue
                if _name_match(hay, a):
                    add(a["id"], "", "cast", "inferred")

        # 没写场景：先按 chapter / 场名对场景资产名，再退回镜头所在空间
        if "scene" not in have:
            best = max(((_name_match(sc.get("chapter", ""), a) * 2 +
                         _name_match(sc.get("name", ""), a), a)
                        for a in assets if a["type"] == "scenes" and a["id"] in unit_set),
                       key=lambda x: x[0], default=(0, None))
            if best[0] > 0:
                add(best[1]["id"], "", "scene", "inferred")
            else:
                sid = next((by_shot[x].get("space") for x in sc.get("shots", [])
                            if x in by_shot and by_shot[x].get("space")), "")
                if sid:
                    add(sid, "", "scene", "inferred")

        # 没写道具：按场文本 + 镜头描述里出现的道具名猜
        if "prop" not in have:
            hay = text + " " + shot_text
            for a in assets:
                if a["type"] != "props" or a["id"] not in unit_set:
                    continue
                if _name_match(hay, a):
                    add(a["id"], "", "prop", "inferred")

        # 风格：镜头 look 始终算，写了就是显式
        for x in sc.get("shots", []):
            look = (by_shot.get(x) or {}).get("look")
            if look:
                add(look, "", "style", "explicit" if look in
                    [i["id"] for i in explicit_assets] else "inferred")

        sc["asset_refs"] = refs
        sc["refs_explicit"] = had_explicit
        sc["cast_ids"] = [r["id"] for r in refs if r["role"] == "cast"]
        sc["asset_ids"] = [r["id"] for r in refs if r["role"] != "cast"]


def _storyboard_asset_refs(storyboards, scenes, shots, assets):
    """分镜引用：分镜自己写了就用；否则读 libtv.json.refs；再否则继承场。"""
    by_id = {a["id"]: a for a in assets}
    scene_by = {sc["id"]: sc for sc in scenes}

    for sb in storyboards:
        refs, seen = [], set()

        def add(aid, variant="", role="", source="explicit", image="",
                label="", prompt="", missing=False):
            aid = str(aid or "").strip()
            variant = str(variant or "").strip()
            if not aid:
                return
            key = (aid, variant, role, image)
            if key in seen:
                return
            seen.add(key)
            a = by_id.get(aid)
            if not a:
                refs.append({
                    "id": aid, "name": label or aid, "type": "", "type_label": "",
                    "role": role or "asset", "variant": variant,
                    "variant_label": "", "image": image, "status": "未登记",
                    "source": source, "missing": missing, "prompt": prompt,
                    "build_state": "missing", "build_state_label": "未登记",
                })
                return
            v = next((x for x in a["variants"] if x["id"] == variant), None) if variant else \
                next((x for x in a["variants"] if x.get("primary")), None)
            refs.append({
                "id": aid, "name": a["name"], "type": a["type"],
                "type_label": a["type_label"], "role": role or "asset",
                "variant": variant, "variant_label": (v or {}).get("label", ""),
                "image": image or (v["image"] if v and variant else a["primary_image"]),
                "status": a["status"], "source": source, "missing": False,
                "build_state": a.get("build_state") or "empty",
                "build_state_label": a.get("build_state_label") or "未构建",
                "prompt": prompt or _ref_prompt(a, variant),
            })

        def role_of(a):
            return {"characters": "cast", "scenes": "scene", "props": "prop",
                    "style": "style", "brand": "brand"}.get(a["type"], "asset")

        cast = _ref_items(sb.get("raw_cast") or [])
        aref = _ref_items(sb.get("raw_assets") or [])
        props = _ref_items(sb.get("raw_props") or [])
        had = bool(sb.get("raw_has_refs") or cast or aref or props)
        for item in cast:
            add(item["id"], item.get("variant"), "cast")
        for item in aref:
            a = by_id.get(item["id"])
            add(item["id"], item.get("variant"), role_of(a) if a else "asset")
        for item in props:
            add(item["id"], item.get("variant"), "prop")

        # libtv.json 里已经连好的参考图
        for lr in sb.get("lib_refs") or []:
            a = by_id.get(lr.get("asset_id") or "") or by_id.get(lr.get("key") or "")
            if not a and lr.get("file"):
                a = next((x for x in assets if lr["file"] in
                          ([x["primary_image"]] + [v["image"] for v in x["variants"]])), None)
            if not a and lr.get("key"):
                a = next((x for x in assets if _norm(x["name"]) == _norm(lr["key"])), None)
            if a:
                vid = next((v["id"] for v in a["variants"]
                            if lr.get("file") and v["image"] == lr["file"]), "")
                if any(x["id"] == a["id"] and
                       (not x["variant"] or x["variant"] == a.get("primary"))
                       for x in refs):
                    continue
                add(a["id"], vid, role_of(a), "libtv",
                    image=lr.get("file") or "", label=lr.get("label") or "")
            elif lr.get("file"):
                add(lr.get("key") or lr.get("label") or "ref", "", "ref", "libtv",
                    image=lr["file"], label=lr.get("label") or lr.get("key") or "参考图")

        if not had:
            sc = scene_by.get(sb.get("scene"))
            have_ids = {x.get("id") for x in refs}
            for r in (sc or {}).get("asset_refs") or []:
                if r.get("id") in have_ids:
                    continue
                src = "scene" if (sc or {}).get("refs_explicit") else "scene-inferred"
                add(r["id"], r.get("variant"), r.get("role"), src,
                    image=r.get("image") or "", label=r.get("name") or "",
                    prompt=r.get("prompt") or "")
        sb["asset_refs"] = refs
        sb["refs_explicit"] = had
        sb["refs_source"] = "explicit" if had else (
            "libtv" if any(r["source"] == "libtv" for r in refs) else
            ("scene" if (scene_by.get(sb.get("scene")) or {}).get("refs_explicit")
             else "scene-inferred"))
        video = dict((sb.get("libtv") or {}).get("video") or {})
        # 提示词里自动补 @Image 素材引用（布线时实际发给 LibTV 的版本），
        # 哈希也按这一版算，避免每次打开都误报“提示词改过”。
        sb["wired_refs"] = _wired_refs(refs)
        sb["prompt_ref_block"] = material_ref_block(refs)
        sb["prompt_final"] = apply_ref_block(
            sb.get("prompt") or "", sb["prompt_ref_block"])
        if video:
            ph = prompt_hash(sb.get("prompt_final") or "")
            rh = refs_hash(refs)
            video["prompt_changed"] = bool(
                video.get("prompt_hash") and video["prompt_hash"] != ph)
            video["refs_changed"] = bool(
                video.get("refs_hash") and video["refs_hash"] != rh)
            video["stale"] = bool(video.get("node_id") and
                                  (video["prompt_changed"] or video["refs_changed"]))
            video["output_stale"] = bool(
                (video.get("output_prompt_hash") and
                 video["output_prompt_hash"] != ph) or
                (video.get("output_refs_hash") and
                 video["output_refs_hash"] != rh) or
                (video.get("output_method") and video.get("method") and
                 video["output_method"] != video["method"]))
            sb.setdefault("libtv", {})["video"] = video


def _fill_asset_usage(assets, scenes, storyboards, unit_set):
    usage, sb_usage = {}, {}
    for sc in scenes:
        for r in sc.get("asset_refs") or []:
            if r.get("missing"):
                continue
            usage.setdefault(r["id"], []).append({
                "scene": sc["id"], "scene_name": sc["name"],
                "variant": r["variant"], "variant_label": r["variant_label"],
                "role": r["role"], "source": r["source"],
            })
    for sb in storyboards:
        for r in sb.get("asset_refs") or []:
            if r.get("missing"):
                continue
            sb_usage.setdefault(r["id"], []).append({
                "storyboard": sb["id"], "scene": sb["scene"],
                "variant": r["variant"], "variant_label": r["variant_label"],
                "role": r["role"], "source": r["source"],
            })
    for a in assets:
        rows = usage.get(a["id"], [])
        seen, dedup = set(), []
        for r in rows:
            key = (r["scene"], r["variant"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(r)
        a["scenes"] = dedup
        a["scene_count"] = len({r["scene"] for r in dedup})
        sb_rows = sb_usage.get(a["id"], [])
        seen, sb_dedup = set(), []
        for r in sb_rows:
            key = (r["storyboard"], r["variant"])
            if key in seen:
                continue
            seen.add(key)
            sb_dedup.append(r)
        a["storyboards"] = sb_dedup
        a["storyboard_count"] = len({r["storyboard"] for r in sb_dedup})
        ids = set()
        for sc in scenes:
            if any(r["id"] == a["id"] for r in (sc.get("asset_refs") or [])):
                ids |= set(sc["shots"])
        for sb in storyboards:
            if any(r["id"] == a["id"] for r in (sb.get("asset_refs") or [])):
                ids |= set(sb["shots"])
        a["shots"] = sorted(ids)
        a["shot_count"] = len(ids)
        a["in_unit"] = a["id"] in unit_set


# ---------------------------------------------------------------- 主入口

def build(root, unit=None):
    root = Path(root).resolve()
    st = _j(root / "project.json", {}) or {}
    cfg = _libtv_cfg(st)
    meta = st.get("meta") or {}
    units = st.get("units") or {}
    uid = unit or meta.get("active_unit") or ""
    if uid not in units and units:
        uid = next(iter(units))
    u = units.get(uid) or {}
    man = _j(root / "units" / uid / "manifest.json", {}) or {}

    # 空间
    spaces = {}
    sdir = root / "assets" / "scenes"
    if sdir.exists():
        for d in sorted(sdir.iterdir()):
            if not d.is_dir():
                continue
            sp = _j(d / "space.json", None)
            if sp:
                m = _j(d / "manifest.json", {}) or {}
                spaces[m.get("id") or d.name] = sp

    # 镜头
    shots_doc = _j(root / "units" / uid / "storyboard" / "shots.json", {}) or {}
    shots = []
    for s in shots_doc.get("shots") or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").zfill(3)
        if not sid:
            continue
        shots.append({
            "id": sid,
            "type": str(s.get("type") or "A").upper(),
            "chapter": str(s.get("chapter") or ""),
            "sec": _num(s.get("sec")),
            "start": _num(s.get("start")),
            "end": _num(s.get("end"), _num(s.get("start")) + _num(s.get("sec"))),
            "shot": str(s.get("shot") or ""),
            "camera": str(s.get("camera") or ""),
            "camera_id": str(s.get("camera_id") or s.get("camera") or ""),
            "camera_name": str(s.get("camera_name") or ""),
            "move": str(s.get("move") or ""),
            "framing": str(s.get("framing") or ""),
            "cut_at": str(s.get("cut_at") or ""),
            "sound": str(s.get("sound") or ""),
            "func": str(s.get("func") or ""),
            "line": str(s.get("line") or ""),
            "visual": str(s.get("visual") or ""),
            "action": str(s.get("action") or ""),
            "emotion": str(s.get("emotion") or ""),
            "memory": str(s.get("memory") or ""),
            "key": bool(s.get("key")),
            "language": str(s.get("language") or ""),
            "look": str(s.get("look") or ""),
            "beat": s.get("beat"),
            "role": str(s.get("role") or ""),
            "rate": _num(s.get("rate")),
            "space": "", "scene": "", "storyboard": "", "h": "",
            "local_start": 0.0, "local_end": 0.0,
        })

    scenes = _scenes(root, uid, shots)
    script = _official_script(root, uid)
    # 剧本已经切了场（有场标题）、但还没生成 scenes.json：审片台先按剧本的场展示，
    # 不写文件；有了 scenes.json / shots.json 以后自动换成正式场。
    if not scenes and script.get("sections"):
        scenes = _scenes_from_script(script)
    _map_script(scenes, script)
    ideas = _ideas(root, uid)
    after_final = [x for x in ideas if script.get("mtime") and x["mtime"] > script["mtime"]]

    for s in shots:
        sc = next((x for x in scenes if s["id"] in set(x["shots"])), None)
        s["scene"] = sc["id"] if sc else ""

    # 机位 → 空间
    scene_assets = [a for a in (man.get("assets") or []) if a in spaces] or list(spaces)
    alias = {}
    for aid, sp in spaces.items():
        amap = {}
        for c in sp.get("cameras") or []:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            amap[cid] = cid
            if c.get("name"):
                amap.setdefault(str(c["name"]), cid)
            for a in c.get("alias") or []:
                amap.setdefault(str(a), cid)
        alias[aid] = amap

    def space_for(shot, sc):
        # camera_id 是 space.json 的机位；camera 是运镜（固定 / 推 / 跟），
        # 只在老工程没有 camera_id 时兜底。
        cam = str(shot.get("camera_id") or shot.get("camera") or "")
        for aid in scene_assets:
            hit = alias.get(aid, {}).get(cam)
            if hit:
                return aid, hit
        name = (sc or {}).get("name") or (sc or {}).get("chapter") or ""
        for aid in scene_assets:
            sp_name = str((spaces.get(aid) or {}).get("name") or "")
            if name and (name in sp_name or sp_name in name):
                return aid, alias.get(aid, {}).get(cam, "")
        return (scene_assets[0] if scene_assets else ""), ""

    for s in shots:
        sc = next((x for x in scenes if s["id"] in set(x["shots"])), None)
        aid, cid = space_for(s, sc)
        s["space"] = aid
        if cid:
            s["camera_id"] = cid

    # 资产：扫描 → 场引用（显式优先，缺失按内容推断）→ 分镜引用 → 反向索引
    assets = _assets(root, uid, man, cfg)
    unit_set = {str(x) for x in (man.get("assets") or []) if str(x).strip()} or \
        {a["id"] for a in assets}
    _scene_asset_refs(scenes, shots, assets, man)
    storyboards = _storyboards(root, uid, shots, scenes, cfg)
    _storyboard_asset_refs(storyboards, scenes, shots, assets)

    # 布线门禁 + 素材引用：一场的引用资产全部出图后，分镜才允许布线；
    # 布线时按引用顺序生成 @Image 锚定段，走 LibTV 智能参考（多图）模式。
    scene_by = {sc["id"]: sc for sc in scenes}
    for sc in scenes:
        sc["wire_gate"] = _wire_gate(sc, None)
    for sb in storyboards:
        sb["wire_gate"] = _wire_gate(scene_by.get(sb.get("scene")), sb)

    # 场 = LibTV 项目画布；分镜 = 画布里的工作流节点
    scene_libtv = {}
    for sc in scenes:
        sdir = _production_scene_dir(root, uid, sc["id"])
        sc["dir"] = _img_rel(root, sdir) if sdir else ""
        sc["libtv"] = _libtv_meta((sdir / "libtv.json") if sdir else None, cfg)
        sc["libtv_path"] = _img_rel(root, sdir / "libtv.json") \
            if sdir and (sdir / "libtv.json").exists() else ""
        sc["libtv_mtime"] = _mtime_ns(sdir / "libtv.json") if sdir else 0
        scene_libtv[sc["id"]] = sc["libtv"]
    for sb in storyboards:
        parent = scene_libtv.get(sb["scene"]) or {}
        if not sb["libtv"].get("project_id") and parent.get("project_id"):
            sb["libtv"]["project_id"] = parent["project_id"]
            sb["libtv"]["name"] = sb["libtv"].get("name") or parent.get("name") or ""
        if not sb["libtv"].get("project_space_id") and parent.get("project_space_id"):
            sb["libtv"]["project_space_id"] = parent["project_space_id"]
            sb["libtv"]["folder_id"] = parent.get("folder_id") or \
                parent["project_space_id"]
        if sb["libtv"].get("project_id") and sb["libtv"].get("project_space_id"):
            focus = _editor_url(sb["libtv"]["project_id"],
                                sb["libtv"]["project_space_id"],
                                sb["libtv"].get("group_id") or
                                sb["libtv"].get("node_id"))
            if focus:
                sb["libtv"]["url"] = focus
                sb["libtv"]["editor_url"] = focus
        elif not sb["libtv"].get("url") and parent.get("url"):
            sb["libtv"]["url"] = parent["url"]
        sb["libtv"]["bound"] = bool(sb["libtv"].get("project_id") or sb["libtv"].get("url"))

    _fill_asset_usage(assets, scenes, storyboards, unit_set)
    asset_stats = {
        "total": len(assets),
        "with_image": len([a for a in assets if a["primary_image"]]),
        "missing_image": len([a for a in assets if not a["primary_image"]]),
        "types": {t: len([a for a in assets if a["type"] == t])
                  for t, _l in ASSET_TYPES},
    }

    review = _review(root, uid)
    media = _shot_media(root, uid, storyboards)

    for s in shots:
        sid = s["id"]
        s["media"] = media.get(sid, [])
        s["keyframe"] = next((x["path"] for x in s["media"] if x["kind"] == "keyframe"), "")
        s["render"] = next((x["path"] for x in s["media"] if x["kind"] != "keyframe"), "")
        s["verdict"] = review.get(sid, {}).get("verdict", "")
        s["note"] = review.get(sid, {}).get("note", "")

    for sb in storyboards:
        sb["review"] = _review_counts(sb["shots"], review)
        sb["shot_rows"] = [s for s in shots if s["storyboard"] == sb["id"]]

    for sc in scenes:
        sc["storyboards"] = [x["id"] for x in storyboards if x["scene"] == sc["id"]]
        sc["review"] = _review_counts(sc["shots"], review)
        sc["design"] = _scene_design(root, uid, sc, shots, spaces, man)
        sec = sc.get("script_section") or {}
        sc["script"] = {
            "title": sec.get("name", ""),
            "start": sec.get("start", 0),
            "end": sec.get("end", 0),
            "text": sc.get("official_text") or "",
        }

    counts = _review_counts([s["id"] for s in shots], review)
    shots_path = root / "units" / uid / "storyboard" / "shots.json"
    prompts_path = root / "units" / uid / "prompts" / "prompts.json"
    review_path = root / "units" / uid / "review" / "shot-review.json"

    # 每镜的静帧 / 运动提示词：页面标关键帧、算缺提示词都靠它。
    pdoc = _j(prompts_path, {}) or {}
    stills = {str(x.get("shot") or "").zfill(3): x
              for x in (pdoc.get("stills") or []) if isinstance(x, dict)}
    motions = {str(x.get("shot") or "").zfill(3): x
               for x in (pdoc.get("motions") or []) if isinstance(x, dict)}
    for s in shots:
        st_ = stills.get(s["id"], {})
        mo_ = motions.get(s["id"], {})
        s["still_prompt"] = str(st_.get("prompt") or "")
        s["motion_prompt"] = str(mo_.get("prompt") or "")
        s["negative"] = str(st_.get("negative") or "")

    return {
        "meta": {
            "name": meta.get("name", ""), "season": meta.get("season", "1"),
            "unit": uid, "root": str(root), "station": _station(u),
            "mode": str(meta.get("mode") or "general"),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "episode": {"id": uid, "type": u.get("type", ""),
                    "entry": man.get("entry", ""), "duration": man.get("duration", "")},
        "script": {"official": script, "ideas": ideas, "after_final": after_final},
        "scenes": scenes,
        "storyboards": storyboards,
        "shots": shots,
        "review": counts,
        "spaces": spaces,
        "assets": assets,
        "asset_stats": asset_stats,
        "libtv_unit": _libtv_meta(root / "units" / uid / "libtv.json", cfg),
        "libtv_unit_mtime": _mtime_ns(root / "units" / uid / "libtv.json"),
        "stats": {
            "scenes": len(scenes),
            "storyboards": len([x for x in storyboards if not x["suggested"]]),
            "suggested": len([x for x in storyboards if x["suggested"]]),
            "shots": len(shots),
            "with_media": len([x for x in shots if x["media"]]),
            "seconds": round(sum(x["sec"] for x in shots), 1),
            "assets": len(assets),
        },
        "mtime": {
            "shots": shots_path.stat().st_mtime_ns if shots_path.exists() else 0,
            "prompts": prompts_path.stat().st_mtime_ns if prompts_path.exists() else 0,
            "review": review_path.stat().st_mtime_ns if review_path.exists() else 0,
            "scenes": (root / "units" / uid / "storyboard" / "scenes.json").stat().st_mtime_ns
                      if (root / "units" / uid / "storyboard" / "scenes.json").exists() else 0,
        },
    }
