"""图谱：读写、规范化、校验（plan）、指纹。

纯逻辑，不联网、不写磁盘。服务端负责落盘和调 LibTV。
字段契约见 专业内容包。
"""
import hashlib
import json
import re
from pathlib import Path

RECIPES = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")
ROLES = ("reference", "reference_video", "image2image",
         "first_frame", "last_frame", "video")
NODE_TYPES = ("image", "video")
INTENTS = ("generate", "interpolate")
STAGES = ("reference", "greybox", "final", "video")
ROLE_STAGE = {
    "reference": "reference",
    "reference_video": "reference",
    "image2image": "final",
    "first_frame": "final",
    "last_frame": "final",
    "video": "video",
}
DONE = ("generated", "reviewed", "approved")


def rel_path(unit):
    return "units/%s/storyboard/graph.json" % unit


def path(root, unit):
    return Path(root) / "units" / str(unit) / "storyboard" / "graph.json"


def load(root, unit):
    p = path(root, unit)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(root, unit, data):
    p = path(root, unit)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)
    return p


# ---------------------------------------------------------------- 规范化
def _s(v):
    return str(v if v is not None else "").strip()


def normalize(data, unit=""):
    """补齐缺字段，保留未知字段。不抛错——错误留给 plan 报。"""
    d = dict(data) if isinstance(data, dict) else {}
    d["version"] = int(d.get("version") or 1)
    d["unit"] = _s(d.get("unit")) or _s(unit)
    segs = []
    for seg in (d.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        s = dict(seg)
        s["id"] = _s(s.get("id"))
        s["scene"] = _s(s.get("scene")) or (
            s["id"].split("-")[0] if "-" in s["id"] else "")
        s["recipe"] = _s(s.get("recipe")).upper()
        s["stage"] = _s(s.get("stage")) or "planned"
        nodes = []
        for n in (s.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            m = dict(n)
            m["id"] = _s(m.get("id"))
            m["shot"] = _s(m.get("shot")) or shot_of(m)
            m["type"] = _s(m.get("type")) or "image"
            m["role"] = _s(m.get("role")) or "reference"
            m["name"] = _s(m.get("name"))
            m["stage"] = _s(m.get("stage")) or ROLE_STAGE.get(m["role"], "")
            m["refs"] = [_s(x) for x in (m.get("refs") or []) if _s(x)]
            m["model"] = _s(m.get("model"))
            m["intent"] = _s(m.get("intent"))
            m["prompt"] = str(m.get("prompt") or "")
            m["source"] = _s(m.get("source")).replace("\\", "/")
            m["params"] = dict(m.get("params") or {})
            m["status"] = _s(m.get("status")) or "planned"
            m["node_id"] = _s(m.get("node_id"))
            m["input_hash"] = _s(m.get("input_hash"))
            # 外部素材：素材在 LibTV，Trestle 只记引用。只对 external 节点补字段，
            # 避免给所有旧节点塞空对象、写盘时膨胀。
            if m["source"] == "external" or isinstance(m.get("external"), dict):
                ext = m.get("external") if isinstance(m.get("external"), dict) else {}
                m["external"] = {
                    "kind": _s(ext.get("kind")) or m["type"],
                    "label": _s(ext.get("label")),
                    "node_id": _s(ext.get("node_id")),
                    "name": _s(ext.get("name")),
                    "bound_at": _s(ext.get("bound_at")),
                    "missing": bool(ext.get("missing")),
                }
                if not m["node_id"]:
                    m["node_id"] = m["external"]["node_id"]
            # 多版本：history 只追加，current_rev 是唯一指针；旧节点没 history
            # 但有 url/file 时在内存里合成一条，不写盘。
            hist = []
            for h in (m.get("history") or []):
                if not isinstance(h, dict):
                    continue
                try:
                    hrev = int(h.get("rev") or 0)
                except (TypeError, ValueError):
                    hrev = 0
                hist.append({
                    "rev": hrev,
                    "node_id": _s(h.get("node_id")),
                    "url": _s(h.get("url")),
                    "file": _s(h.get("file")),
                    "input_hash": _s(h.get("input_hash")),
                    "at": _s(h.get("at")),
                    "status": _s(h.get("status")),
                })
            m["history"] = hist
            try:
                cur = int(m.get("current_rev") or 0)
            except (TypeError, ValueError):
                cur = 0
            if not cur:
                if hist:
                    cur = max(x["rev"] for x in hist)
                elif m.get("url") or m.get("file"):
                    cur = 1
            m["current_rev"] = cur
            rev = m.get("review") if isinstance(m.get("review"), dict) else {}
            m["review"] = {"verdict": _s(rev.get("verdict")),
                           "note": str(rev.get("note") or "")}
            nodes.append(m)
        s["nodes"] = nodes
        src = [str(x).zfill(3) for x in (s.get("source_shots") or [])
               if str(x).strip()]
        if not src:
            for n in nodes:
                sh = shot_of(n)
                if sh and sh not in src:
                    src.append(sh)
        s["source_shots"] = src
        edges = []
        for e in (s.get("edges") or []):
            if not isinstance(e, dict):
                continue
            edges.append({"from": _s(e.get("from")), "to": _s(e.get("to"))})
        s["edges"] = edges
        segs.append(s)
    d["segments"] = segs
    return d


# ---------------------------------------------------------------- 校验
_SHOT_RX = re.compile(r"^(?:frame|video|ref[_-]?video)[_-]?(\d{1,3})", re.I)


def shot_of(node):
    """节点属于哪一镜：优先 node.shot，其次 frame_001 / video_001 命名。"""
    s = str(node.get("shot") or "").strip()
    if s:
        return s.zfill(3) if s.isdigit() else s
    m = _SHOT_RX.match(str(node.get("id") or ""))
    return m.group(1).zfill(3) if m else ""


def _block(code, message, segment="", node="", **extra):
    out = {"code": code, "segment": segment, "node": node, "message": message}
    out.update(extra)
    return out


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def plan(data, exists=None, only=""):
    """返回 {ok, blockers, warnings, cost, segments}。

    exists(rel_path) -> bool：判断 reference 节点的 source 在不在磁盘上。
    """
    d = normalize(data, data.get("unit") if isinstance(data, dict) else "")
    exists = exists or (lambda _p: True)
    blockers, warnings = [], []
    cost = {"uploads": 0, "images": 0, "videos": 0}
    out_segments = []

    seen_seg = set()
    for seg in d["segments"]:
        sid = seg["id"]
        if only and sid != only:
            continue
        if not sid:
            blockers.append(_block("no_segment_id", "段没有 id"))
            continue
        if sid in seen_seg:
            blockers.append(_block("duplicate_segment", "段 id 重复：%s" % sid, sid))
            continue
        seen_seg.add(sid)

        seg_block_start = len(blockers)
        if seg["recipe"] not in RECIPES:
            blockers.append(_block(
                "no_recipe",
                "段 %s 没有配方（只能是 %s）" % (sid, " / ".join(RECIPES)),
                sid))

        by_id = {}
        for n in seg["nodes"]:
            nid = n["id"]
            if not nid:
                blockers.append(_block("no_node_id", "段 %s 有节点没 id" % sid, sid))
                continue
            if nid in by_id:
                blockers.append(_block(
                    "duplicate_node", "节点 id 重复：%s" % nid, sid, nid))
                continue
            by_id[nid] = n

        for e in seg["edges"]:
            for k in ("from", "to"):
                if e.get(k) and e[k] not in by_id:
                    blockers.append(_block(
                        "missing_edge_node",
                        "边引用了不存在的节点：%s" % e[k], sid, e[k]))

        def refs_of(n):
            return [by_id[x] for x in (n.get("refs") or []) if x in by_id]

        for n in seg["nodes"]:
            nid = n["id"]
            if not nid:
                continue
            if n["type"] not in NODE_TYPES:
                blockers.append(_block(
                    "bad_type", "节点 %s 的 type 只能是 image / video" % nid,
                    sid, nid))
            if n["role"] not in ROLES:
                blockers.append(_block(
                    "bad_role", "节点 %s 的 role 不认识：%s" % (nid, n["role"]),
                    sid, nid))

            if n["role"] == "reference":
                if n["type"] != "image":
                    blockers.append(_block(
                        "ref_not_image", "参考节点 %s 必须是 image" % nid, sid, nid))
                if n["source"] == "external":
                    # 素材在 LibTV：未绑定是合法中间态，只 defer，不拦整段。
                    if not (n.get("external") or {}).get("node_id") and not n["node_id"]:
                        blockers.append(_block(
                            "external_unbound",
                            "参考节点 %s 还没绑定 LibTV 节点" % nid,
                            sid, nid, defer=True))
                    continue
                if not n["node_id"]:
                    if not n["source"]:
                        blockers.append(_block(
                            "no_source",
                            "参考节点 %s 既没有 node_id，也没有 source 图片" % nid,
                            sid, nid))
                    elif not exists(n["source"]):
                        blockers.append(_block(
                            "missing_source",
                            "参考节点 %s 的图不存在：%s" % (nid, n["source"]),
                            sid, nid))
                    else:
                        cost["uploads"] += 1
                continue

            if n["role"] == "reference_video":
                if n["type"] != "video":
                    blockers.append(_block(
                        "ref_video_not_video",
                        "参考视频节点 %s 必须是 video" % nid, sid, nid))
                if n["source"] != "external":
                    blockers.append(_block(
                        "ref_video_not_external",
                        "参考视频节点 %s 需要客户在 LibTV 上传后绑定" % nid,
                        sid, nid, defer=True))
                elif not (n.get("external") or {}).get("node_id") and not n["node_id"]:
                    blockers.append(_block(
                        "external_unbound",
                        "参考视频节点 %s 还没绑定 LibTV 节点" % nid,
                        sid, nid, defer=True))
                continue

            # ---- 生成节点 ----
            if not n["model"]:
                blockers.append(_block(
                    "no_model", "节点 %s 没有模型" % nid, sid, nid))
            if not n["prompt"].strip():
                blockers.append(_block(
                    "no_prompt", "节点 %s 没有提示词" % nid, sid, nid))

            ups = refs_of(n)
            if n["type"] == "video":
                cost["videos"] += 1
                _plan_video(seg, n, ups, blockers, warnings)
            else:
                cost["images"] += 1
                bad = [r["id"] for r in ups if r["type"] != "image"]
                if bad:
                    blockers.append(_block(
                        "image_ref_not_image",
                        "图节点 %s 的输入里有非图片节点：%s" % (
                            nid, "、".join(bad)), sid, nid))
                if n["role"] not in ("image2image", "first_frame", "last_frame"):
                    warnings.append(_block(
                        "image_role_unusual",
                        "图节点 %s 的 role 是 %s，通常应为 image2image / "
                        "first_frame / last_frame" % (nid, n["role"]), sid, nid))

        out_segments.append({
            "id": sid, "recipe": seg["recipe"], "stage": seg["stage"],
            "nodes": len(seg["nodes"]),
            "blockers": len(blockers) - seg_block_start,
        })

    return {
        "ok": not blockers,
        "unit": d["unit"],
        "blockers": blockers,
        "warnings": warnings,
        "cost": cost,
        "segments": out_segments,
    }


def _plan_video(seg, n, ups, blockers, warnings):
    sid, nid = seg["id"], n["id"]
    if n["intent"] not in INTENTS:
        blockers.append(_block(
            "bad_intent",
            "视频节点 %s 的 intent 只能是 generate / interpolate" % nid,
            sid, nid))
        return
    ids = [r["id"] for r in ups]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        blockers.append(_block(
            "duplicate_ref",
            "视频节点 %s 重复引用了：%s" % (nid, "、".join(dup)), sid, nid))

    refs = [r for r in ups if r["role"] == "reference"]
    ref_videos = [r for r in ups if r["role"] == "reference_video"]
    first = [r for r in ups if r["role"] == "first_frame"]
    last = [r for r in ups if r["role"] == "last_frame"]
    if ref_videos and (refs or first or last):
        warnings.append(_block(
            "video_ref_mixed",
            "视频节点 %s 同时挂了参考视频和图片参考；LibTV 可能不接受混用" % nid,
            sid, nid))
    if refs and (first or last):
        blockers.append(_block(
            "frames_and_refs",
            "视频节点 %s 同时挂了首尾帧和参考图；LibTV 不接受，"
            "参考只能进首帧图" % nid, sid, nid))
        return
    if n["intent"] == "interpolate":
        if len(first) != 1 or len(last) != 1:
            blockers.append(_block(
                "interpolate_needs_frames",
                "视频节点 %s 是 interpolate，必须正好一个 first_frame "
                "和一个 last_frame" % nid, sid, nid))
    elif first or last:
        if len(first) != 1:
            blockers.append(_block(
                "generate_needs_first",
                "视频节点 %s 用了首帧模式，必须正好一个 first_frame" % nid,
                sid, nid))
        if last:
            blockers.append(_block(
                "generate_has_last",
                "视频节点 %s 是 generate，不该有 last_frame；"
                "要锁尾帧就用 interpolate" % nid, sid, nid))
    if not ups:
        warnings.append(_block(
            "no_reference", "视频节点 %s 没有参考，会走纯文本" % nid, sid, nid))

    # 上游必须先出图（LibTV 硬校验：图片引用要有 url）
    for r in first + last:
        if r["status"] not in DONE:
            blockers.append(_block(
                "upstream_not_generated",
                "视频节点 %s 的首帧 / 尾帧 %s 还没生成（status=%s）" % (
                    nid, r["id"], r["status"]), sid, nid, defer=True))
    for r in refs:
        if r["role"] == "reference" and not r["node_id"] and r["status"] not in DONE:
            # 参考图在 wire 时上传，不要求先生成
            continue

    sec = _num((n.get("params") or {}).get("duration"), 0)
    if sec and not (4 <= sec <= 30):
        blockers.append(_block(
            "bad_duration",
            "视频节点 %s 的时长 %.0f 秒超出 4–30 秒" % (nid, sec), sid, nid))


# ---------------------------------------------------------------- 指纹
def input_hash(node, upstream=None):
    """输入指纹：提示词 + 参数 + 模型 + 上游节点。用于本地判 stale。"""
    upstream = upstream or []
    payload = {
        "prompt": str(node.get("prompt") or ""),
        "params": node.get("params") or {},
        "model": str(node.get("model") or ""),
        "intent": str(node.get("intent") or ""),
        "source": str(node.get("source") or ""),
        "refs": [str(x) for x in upstream],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
