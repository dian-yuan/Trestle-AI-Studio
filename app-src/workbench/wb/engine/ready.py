"""生成准入：只看硬条件，不打分。

旧 `d ready` 依赖 score / look 两套打分；这里改成确定性检查：

1. `check` 填充体检通过；
2. 本集引用的资产都存在、状态可用、有参考图；场景还要有 space.json 的机位 / 光位；
3. `gates.json` 要求关键帧时，关键帧齐。

不判断「好不好看」——那是用户审片的事。
"""

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from . import checks as checks_mod
from . import core as d


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _unit(root, uid=None):
    st = d.load_state(root)
    return st, d.unit_id(st, uid)


def _referenced_assets(root, uid):
    """本集引用到的资产 ID：unit manifest + scenes + shots + production。"""
    ids = set()

    man = d.unit_manifest(root, uid)
    for x in man.get("assets") or []:
        if isinstance(x, str) and x.strip():
            ids.add(x.strip())

    sc = _load_json(Path(root) / "units" / uid / "storyboard" / "scenes.json", {})
    for s in (sc.get("scenes") or []):
        for key in ("cast", "assets", "props"):
            for x in s.get(key) or []:
                if isinstance(x, str) and x.strip():
                    ids.add(x.strip())

    sh = _load_json(Path(root) / "units" / uid / "storyboard" / "shots.json", {})
    for s in (sh.get("shots") or []):
        for x in s.get("assets") or []:
            if isinstance(x, str) and x.strip():
                ids.add(x.strip())

    prod = Path(root) / "units" / uid / "production"
    if prod.is_dir():
        for p in prod.glob("*/*/storyboard.json"):
            sb = _load_json(p, {})
            for key in ("cast", "assets", "props"):
                for x in sb.get(key) or []:
                    if isinstance(x, str) and x.strip():
                        ids.add(x.strip())

    return sorted(ids)


def _asset_rows(root):
    return {r["id"]: r for r in d.list_assets(root)}


def _has_ref(row):
    """参考图只看资产自己的 refs/；目录名在说好版里是纯 ID。"""
    rf = Path(row["dir"]) / "refs"
    if not rf.is_dir():
        return False
    for x in rf.iterdir():
        try:
            if x.is_file() and not x.name.startswith(".") and x.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def _required_keyframes(root, uid):
    need = set()
    segs = Path(root) / "units" / uid / "segments"
    if not segs.is_dir():
        return need
    for sm in segs.glob("*/manifest.json"):
        m = _load_json(sm, {})
        need |= {str(x).zfill(3) for x in (m.get("keyframe_shots") or [])}
    return need


def check(root, uid=None):
    root = Path(root).resolve()
    st, uid = _unit(root, uid)
    checks = []

    # 1. 填充体检（结构、时长、提示词、六栏、场/段归属）
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = checks_mod.check(argparse.Namespace(cwd=str(root), unit=uid or None))
    checks.append({
        "name": "填充体检",
        "ok": code == 0,
        "detail": "check 通过" if code == 0 else "check 有问题",
        "output": buf.getvalue().strip(),
    })

    # 2. 引用资产：存在、状态可用、有参考图；场景还要有机位 / 光位
    rows = _asset_rows(root)
    ids = _referenced_assets(root, uid)
    bad = []
    for aid in ids:
        r = rows.get(aid)
        if not r:
            bad.append("%s：资产不存在" % aid)
            continue
        if not d._status_ok(r.get("status")):
            bad.append("%s：状态不可用（%s）" % (aid, r.get("status")))
            continue
        if not _has_ref(r):
            bad.append("%s：没有参考图" % aid)
            continue
        if r.get("type") == "scenes":
            sp = _load_json(Path(r["dir"]) / "space.json", {})
            if not (sp.get("cameras") and sp.get("lights")):
                bad.append("%s：space.json 缺机位或光位" % aid)
    checks.append({
        "name": "资产可用",
        "ok": not bad,
        "detail": "；".join(bad) if bad else "%d 个引用资产全部可用" % len(ids),
    })

    # 2b. 生成顺序：有变体 / 装造，就必须先有母图 / 核心形象
    order_bad = []
    for aid in ids:
        r = rows.get(aid)
        if not r:
            continue
        vroot = Path(r["dir"]) / "variants"
        has_variant = False
        if vroot.is_dir():
            for v in vroot.iterdir():
                if v.is_dir() and not v.name.startswith((".", "_")):
                    has_variant = True
                    break
        if has_variant and not _has_ref(r):
            order_bad.append("%s：先出母图 / 核心形象，再出变体" % aid)
    if order_bad:
        checks.append({
            "name": "生成顺序",
            "ok": False,
            "detail": "；".join(order_bad),
        })

    # 3. 关键帧（可选；gates.json 里要求才查）
    gates = _load_json(root / "gates.json", {})
    cfg = gates.get("ready") if isinstance(gates.get("ready"), dict) else {}
    if cfg.get("keyframes"):
        need = _required_keyframes(root, uid)
        have = d.keyframe_ids(Path(root) / "units" / uid)
        missing = sorted(need - have)
        checks.append({
            "name": "关键帧",
            "ok": not missing,
            "detail": ("缺 " + "、".join(missing)) if missing else "齐",
        })

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "unit": uid, "checks": checks}
