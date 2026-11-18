#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LibTV 槽位表：一个场一个项目槽，一个分镜一个组槽。

为什么要有这一层：

- LibTV MCP 只能 create/bind 组，**不能删除、也不能改组的名字**；项目能改名和
  移动，但同样不能删除。以前按「分镜名」的哈希做幂等键，名字一变就建新组，
  旧组永远留在画布上，越改越多。
- 这里把「场 / 分镜」当成稳定的槽位：改名、重排、甚至整场重写都尽量复用原来的
  project_id / group_id；名字变了只记录 `group_label`（想要的名字），线上组名
  保持原样。组名不同步是平台限制，不是重复建组。
- 只有槽位数量真的超过历史峰值时才新建。旧项目由归档动作移出工作目录，旧组则
  留给以后的分镜复用。

槽位表落在 `units/<集>/libtv-map.json`，是远程 ID 的长期账本；`production/`
里的 `libtv.json` 仍然保留当前生效的 ID，方便快照和人工查看。
"""
import hashlib
import json
import re
import time
from pathlib import Path

MAP_NAME = "libtv-map.json"
VERSION = 1


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _key(prefix, *parts):
    raw = "\x1f".join(str(x or "") for x in parts)
    return "%s-%s" % (prefix, hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12])


def _norm(text):
    """名字匹配用：去掉空白和常见分隔符，大小写不敏感。"""
    return re.sub(r"[\s·:：\-—_/]+", "", str(text or "")).lower()


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _shots(v):
    if not isinstance(v, (list, tuple, set)):
        return []
    return [str(x).zfill(3) for x in v if str(x).strip()]


def _overlap(a, b):
    """镜头集合的 Jaccard；用来把重命名前后的分镜对上。"""
    aa, bb = set(_shots(a)), set(_shots(b))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / float(len(aa | bb))


def load(root, unit):
    p = Path(root) / "units" / str(unit) / MAP_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["version"] = VERSION
    data["unit"] = str(unit or data.get("unit") or "")
    if not isinstance(data.get("scenes"), list):
        data["scenes"] = []
    for sc in data["scenes"]:
        if not isinstance(sc, dict):
            continue
        if not sc.get("key"):
            sc["key"] = _key("sc", sc.get("id"), sc.get("name"))
        if not isinstance(sc.get("storyboards"), list):
            sc["storyboards"] = []
        for sb in sc["storyboards"]:
            if not isinstance(sb, dict):
                continue
            if not sb.get("key"):
                sb["key"] = _key("sb", sb.get("id"), sb.get("group_id"),
                                 sc.get("key"))
    return data


def save(root, unit, data):
    p = Path(root) / "units" / str(unit) / MAP_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def active_scene(data, scene_id):
    for sc in data.get("scenes") or []:
        if sc.get("active") and str(sc.get("id") or "") == str(scene_id):
            return sc
    return None


def active_sb(scene, sb_id):
    for sb in (scene or {}).get("storyboards") or []:
        if sb.get("active") and str(sb.get("id") or "") == str(sb_id):
            return sb
    return None


def _scene_match(slots, cur, used, weak=False):
    """按 稳定键 → 项目 ID → 场号 → 场名 找槽位；weak 时再按顺序兜底。"""
    free = [s for s in slots if id(s) not in used]
    checks = (
        lambda s: cur.get("key") and s.get("key") == cur["key"],
        lambda s: cur.get("project_id")
        and str(s.get("project_id") or "") == str(cur["project_id"]),
        lambda s: cur.get("id") and str(s.get("id") or "") == str(cur["id"]),
        lambda s: cur.get("name") and _norm(s.get("name")) == _norm(cur["name"]),
    )
    for pred in checks:
        for s in free:
            if pred(s):
                return s
    if not weak:
        return None
    # 场名和场号都变了（大改），按顺序复用；这样不会因为重写就新建一个画布。
    order = cur.get("order")
    if order is not None:
        same = [s for s in free if s.get("order") == order]
        if same:
            return min(same, key=lambda s: 0 if not s.get("active") else 1)
        # 没有同序号时，取序号最接近的空闲槽，避免插入一场就全新建。
        if free:
            return min(free, key=lambda s: abs(_int(s.get("order"), 999) - _int(order, 999)))
    return free[0] if free else None


def apply_scenes(data, current):
    """把当前场对到槽位，返回 [(current, slot)]；未匹配的旧场标记 inactive。"""
    slots = data.setdefault("scenes", [])
    for s in slots:
        s["active"] = False
        for sb in s.get("storyboards") or []:
            sb["active"] = False
    used, matched = set(), set()

    def use(cur, slot):
        used.add(id(slot))
        matched.add(id(cur))
        slot.update({
            "id": str(cur.get("id") or ""),
            "name": str(cur.get("name") or ""),
            "order": cur.get("order"),
            "active": True,
            "last_seen": _now(),
        })
        if cur.get("project_id"):
            slot["project_id"] = str(cur["project_id"])
        if cur.get("asset_folder_id"):
            slot["asset_folder_id"] = _int(cur["asset_folder_id"])
        if cur.get("folder_id"):
            slot["folder_id"] = _int(cur["folder_id"])
        return (cur, slot)

    pairs = []
    # 先做精确匹配，再做弱匹配，避免新场按顺序抢走旧场已经能按名字对上的槽。
    for weak in (False, True):
        for cur in current:
            if id(cur) in matched:
                continue
            slot = _scene_match(slots, cur, used, weak=weak)
            if slot is not None:
                pairs.append(use(cur, slot))
    for cur in current:
        if id(cur) in matched:
            continue
        slot = {"key": cur.get("key") or _key("sc", cur.get("id"),
                                                cur.get("name")),
                "first_seen": _now(), "storyboards": []}
        slots.append(slot)
        pairs.append(use(cur, slot))
    # 保持输入顺序，方便调用方按场取。
    order = {id(c): i for i, c in enumerate(current)}
    pairs.sort(key=lambda x: order.get(id(x[0]), 999))
    return pairs


def _sb_match(slots, cur, used, weak=False):
    free = [s for s in slots if id(s) not in used]
    checks = (
        lambda s: cur.get("key") and s.get("key") == cur["key"],
        lambda s: cur.get("group_id")
        and str(s.get("group_id") or "") == str(cur["group_id"]),
        lambda s: cur.get("id") and str(s.get("id") or "") == str(cur["id"]),
        lambda s: cur.get("name") and _norm(s.get("name")) == _norm(cur["name"]),
    )
    for pred in checks:
        for s in free:
            if pred(s):
                return s
    if not weak:
        return None
    cur_shots = _shots(cur.get("shots"))
    if cur_shots:
        hit = max(free, key=lambda s: _overlap(cur_shots, s.get("shots")),
                  default=None)
        if hit is not None and _overlap(cur_shots, hit.get("shots")) >= 0.5:
            return hit
    idx = cur.get("index")
    if idx is not None:
        same = [s for s in free if s.get("index") == idx]
        if same:
            return min(same, key=lambda s: 0 if not s.get("active") else 1)
        if free:
            return min(free, key=lambda s: abs(
                _int(s.get("index"), 999) - _int(idx, 999)))
    return free[0] if free else None


def apply_storyboards(scene, current):
    """把当前分镜对到本场槽位；返回 [(current, slot)]。"""
    slots = scene.setdefault("storyboards", [])
    used, matched = set(), set()

    def use(cur, slot):
        used.add(id(slot))
        matched.add(id(cur))
        slot.update({
            "id": str(cur.get("id") or ""),
            "name": str(cur.get("name") or ""),
            "shots": _shots(cur.get("shots")),
            "index": cur.get("index"),
            "active": True,
            "last_seen": _now(),
        })
        if cur.get("group_id"):
            slot["group_id"] = str(cur["group_id"])
        if cur.get("group_name"):
            slot["group_name"] = str(cur["group_name"])
        return (cur, slot)

    pairs = []
    for weak in (False, True):
        for cur in current:
            if id(cur) in matched:
                continue
            slot = _sb_match(slots, cur, used, weak=weak)
            if slot is not None:
                pairs.append(use(cur, slot))
    for cur in current:
        if id(cur) in matched:
            continue
        slot = {"key": cur.get("key") or _key(
            "sb", cur.get("id"), cur.get("group_id"), scene.get("key")),
            "first_seen": _now()}
        slots.append(slot)
        pairs.append(use(cur, slot))
    order = {id(c): i for i, c in enumerate(current)}
    pairs.sort(key=lambda x: order.get(id(x[0]), 999))
    return pairs


def merge_remote_groups(scene, groups):
    """把线上有、账本里没有的组收成 inactive 槽位；清掉线上已不存在的 ID。

    返回 {group_id: group}；`groups is None` 表示这次没读到线上，保持原样。
    """
    if groups is None:
        return None
    by_id = {}
    for g in groups:
        gid = str(g.get("nodeId") or g.get("id") or "")
        if gid:
            by_id[gid] = g
    known = set()
    for sb in scene.get("storyboards") or []:
        gid = str(sb.get("group_id") or "")
        if not gid:
            continue
        if gid in by_id:
            known.add(gid)
            sb["group_name"] = str(by_id[gid].get("name")
                                   or sb.get("group_name") or "")
            sb["group_children"] = len(by_id[gid].get("childNodeIds") or [])
        else:
            # 用户可能手动删了；下次补建，不要继续指向不存在的组。
            # epoch 让补建换一个 idempotencyKey，否则同一 key 会一直返回
            # 已被删组的旧回执。
            sb["group_id"] = ""
            sb["group_missing"] = True
            sb["group_epoch"] = _int(sb.get("group_epoch"), 1) + 1
    for gid, g in by_id.items():
        if gid in known:
            continue
        scene.setdefault("storyboards", []).append({
            "key": _key("sb", gid),
            "id": "",
            "name": str(g.get("name") or ""),
            "shots": [],
            "index": len(scene.get("storyboards") or []),
            "group_id": gid,
            "group_name": str(g.get("name") or ""),
            "group_children": len(g.get("childNodeIds") or []),
            "active": False,
            "remote_only": True,
            "first_seen": _now(),
        })
        known.add(gid)
    return by_id


def _desired_name(cur):
    return ("%s %s" % (cur.get("id") or "", cur.get("name") or "")).strip()[:180]


def _orphan_slots(scene, assigned):
    return [sb for sb in (scene.get("storyboards") or [])
            if sb.get("group_id") and sb.get("group_id") not in assigned
            and not sb.get("active")]


def assign_groups(scene, remote_ok=True):
    """给 active 分镜分配组：复用原组 → 复用空旧组 → 才新建。

    返回 {sb_key: {"action": reuse|reuse_orphan|create|missing,
                    "group_id": ..., "group_name": ..., "desired": ...,
                    "children": n}}。
    """
    assigned, out = set(), {}
    for sb in scene.get("storyboards") or []:
        if not sb.get("active"):
            continue
        gid = str(sb.get("group_id") or "")
        if gid:
            assigned.add(gid)
            out[sb["key"]] = {
                "action": "reuse", "group_id": gid,
                "group_name": str(sb.get("group_name") or ""),
                "children": _int(sb.get("group_children")),
            }
        else:
            out[sb["key"]] = {"action": "create", "group_id": "",
                              "group_name": "", "children": 0}
    for sb in scene.get("storyboards") or []:
        if not sb.get("active"):
            continue
        rec = out.get(sb["key"])
        if not rec or rec["action"] != "create":
            continue
        # 同一个场里被换下来的旧组，优先拿来复用；空的先选。
        orphans = _orphan_slots(scene, assigned)
        if not orphans:
            continue
        pick = min(orphans, key=lambda s: (
            0 if _int(s.get("group_children")) == 0 else 1,
            0 if _norm(s.get("name")).startswith(_norm(scene.get("id"))) else 1,
            _int(s.get("index"), 999)))
        gid = str(pick.get("group_id") or "")
        rec.update({"action": "reuse_orphan", "group_id": gid,
                    "group_name": str(pick.get("group_name") or ""),
                    "children": _int(pick.get("group_children"))})
        assigned.add(gid)
        pick["group_id"] = ""
        pick["reused_by"] = sb["key"]
        pick["active"] = False
    for sb in scene.get("storyboards") or []:
        if not sb.get("active"):
            continue
        rec = out.get(sb["key"])
        desired = _desired_name(sb)
        rec["desired"] = desired
        rec["group_stale"] = bool(
            rec.get("group_name") and desired and rec["group_name"] != desired)
        sb["group_action"] = rec["action"]
        sb["group_stale"] = rec["group_stale"]
        sb["desired_name"] = desired
        if rec.get("group_id"):
            sb["group_id"] = str(rec["group_id"])
        if rec.get("group_name"):
            sb["group_name"] = str(rec["group_name"])
        sb["group_children"] = _int(rec.get("children"))
    return out


def orphan_groups(scene):
    """没有被任何 active 分镜用到的旧组。"""
    used = {str(sb.get("group_id") or "")
            for sb in scene.get("storyboards") or [] if sb.get("active")}
    return [sb for sb in (scene.get("storyboards") or [])
            if sb.get("group_id") and sb.get("group_id") not in used]


def note_group_created(scene, sb_key, gid, name):
    for sb in scene.get("storyboards") or []:
        if sb.get("key") == sb_key:
            sb["group_id"] = str(gid or "")
            sb["group_name"] = str(name or "")
            sb["group_stale"] = False
            sb["group_missing"] = False
            return


def note_group_reused(scene, sb_key, gid, name):
    for sb in scene.get("storyboards") or []:
        if sb.get("key") == sb_key:
            sb["group_id"] = str(gid or "")
            sb["group_name"] = str(name or sb.get("group_name") or "")
            sb["group_missing"] = False
            return
