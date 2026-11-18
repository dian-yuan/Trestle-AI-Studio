"""本地对象 → LibTV 工程 / 组 / 节点 的索引。

只读工程文件，不联网。`build()` 产出内存索引；服务端决定要不要落盘。
字段契约见 专业内容包。
"""
import json
import time
from pathlib import Path


def _j(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _s(v):
    return str(v if v is not None else "").strip()


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _space(lib, fallback=0):
    return _int(lib.get("project_space_id") or lib.get("projectSpaceId")
                or lib.get("folder_id") or fallback)


def _pid(lib):
    canvas = lib.get("canvas") if isinstance(lib.get("canvas"), dict) else {}
    return _s(lib.get("project_id") or lib.get("projectId")
              or canvas.get("projectId") or canvas.get("project_id"))


def _node_id(lib):
    nid = _s(lib.get("node_id") or lib.get("nodeId"))
    if nid:
        return nid
    for n in (lib.get("nodes") or []):
        if isinstance(n, dict):
            x = _s(n.get("node_id") or n.get("nodeId"))
            if x:
                return x
    return ""


def _entry(kind, fid, name, pid, space, editor, group="", node="",
           status="", extra=None):
    # 段 / 场聚焦到组；镜 / 节点 / 资产聚焦到具体节点。
    focus = node if kind in ("node", "shot", "asset") else (group or node)
    e = {
        "local_kind": kind,
        "local_id": fid,
        "name": name,
        "project_id": pid,
        "space_id": space,
        "group_id": group,
        "node_id": node,
        "url": editor(pid, space, focus) if pid else "",
        "status": status,
    }
    if extra:
        e.update(extra)
    return e


def build(root, unit, snap, cfg):
    """返回 {"version", "unit", "built_at", "entries": [...]}。"""
    root = Path(root)
    editor = _editor
    entries = []
    seen = set()

    def add(e):
        key = (e["local_kind"], e["local_id"])
        if not e["local_id"] or key in seen:
            return
        seen.add(key)
        entries.append(e)

    # 1) 集
    ulib = _j(root / "units" / unit / "libtv.json")
    upid = _pid(ulib)
    uspace = _space(ulib)
    if upid:
        add(_entry("unit", unit, unit, upid, uspace, editor,
                   status=_s(ulib.get("status")) or "已连线"))

    # 2) 场
    scene_space = {}
    for sc in (snap.get("scenes") or []):
        sid = _s(sc.get("id"))
        sdir = _production_scene_dir(root, unit, sid)
        if not sdir:
            continue
        lib = _j(sdir / "libtv.json")
        pid = _pid(lib) or upid
        space = _space(lib, uspace)
        scene_space[sid] = (pid, space)
        add(_entry("scene", sid, _s(sc.get("name")) or sid, pid, space, editor,
                   group=_s(lib.get("group_id")),
                   status=_s(lib.get("status")) or ("已连线" if pid else "未连线")))

    # 3) 段 + 4) 镜
    for sb in (snap.get("storyboards") or []):
        sid = _s(sb.get("id"))
        scene = _s(sb.get("scene"))
        pid, space = scene_space.get(scene, (upid, uspace))
        libm = sb.get("libtv") or {}
        pid = _s(libm.get("project_id")) or pid
        space = _int(libm.get("project_space_id"), space)
        group = _s(libm.get("group_id"))
        node = _s(libm.get("node_id"))
        add(_entry("segment", sid, _s(sb.get("name")) or sid, pid, space, editor,
                   group=group, node=node,
                   status=_s(libm.get("status")) or ("已连线" if pid else "未连线")))
        lib = _j(root / sb["libtv_path"]) if sb.get("libtv_path") else {}
        for shot, meta in (lib.get("stills") or {}).items():
            if not isinstance(meta, dict):
                continue
            nid = _s(meta.get("node_id") or meta.get("nodeId"))
            if nid:
                add(_entry("shot", _s(shot), _s(meta.get("label")) or _s(shot),
                           pid, space, editor, group=group, node=nid,
                           status=_s(meta.get("status")) or "已连线"))

    # 5) 图谱节点
    g = _j(root / "units" / unit / "storyboard" / "graph.json")
    for seg in (g.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        seg_id = _s(seg.get("id"))
        sb = next((x for x in (snap.get("storyboards") or [])
                   if _s(x.get("id")) == seg_id), {})
        libm = sb.get("libtv") or {}
        pid = _s(libm.get("project_id")) or upid
        space = _int(libm.get("project_space_id"), uspace)
        group = _s(libm.get("group_id"))
        for n in (seg.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            nid = _s(n.get("node_id"))
            if not nid:
                continue
            add(_entry("node", "%s:%s" % (seg_id, _s(n.get("id"))),
                       _s(n.get("name")) or _s(n.get("id")), pid, space, editor,
                       group=group, node=nid, status=_s(n.get("status")),
                       extra={"segment": seg_id, "role": _s(n.get("role"))}))

    # 6) 资产画布
    for kind_dir in ("characters", "scenes", "props", "style", "brand"):
        base = root / "assets" / kind_dir
        if not base.is_dir():
            continue
        for adir in sorted(base.iterdir()):
            if not adir.is_dir() or adir.name.startswith(("_", ".")):
                continue
            man = _j(adir / "manifest.json")
            aid = _s(man.get("id")) or adir.name.split("-")[0]
            bdir = adir / "build"
            if not bdir.is_dir():
                continue
            for cdir in sorted(bdir.iterdir()):
                if not cdir.is_dir():
                    continue
                lib = _j(cdir / "libtv.json")
                pid = _pid(lib)
                if not pid:
                    continue
                space = _space(lib)
                add(_entry("asset", "%s:%s" % (aid, cdir.name),
                           "%s · %s" % (aid, cdir.name), pid, space, editor,
                           node=_node_id(lib),
                           status=_s(lib.get("status")) or "已连线"))

    return {
        "version": 1,
        "unit": unit,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entries": entries,
    }


def _editor(pid, space, focus=""):
    if not pid:
        return ""
    q = ["projectId=" + _s(pid)]
    if space:
        q.append("spaceId=%d" % _int(space))
    if focus:
        q.append("focusNodeId=" + _s(focus))
    return "https://www.liblib.tv/canvas?" + "&".join(q)


def _production_scene_dir(root, uid, scene_id):
    base = Path(root) / "units" / uid / "production"
    if not base.is_dir():
        return None
    idx = _j(base / "index.json")
    for row in (idx.get("scenes") or []):
        if _s(row.get("id")) == scene_id:
            d = base / _s(row.get("dir"))
            if d.is_dir():
                return d
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.startswith(scene_id + "-"):
            return d
    return None
