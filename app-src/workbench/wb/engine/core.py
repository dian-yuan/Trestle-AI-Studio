#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
director.py - Trestle 专用 AI 视频导演引擎（资产 → 单元 两级结构）

工程由 Trestle 创建，本 skill 只往里填内容。工程模型：
    资产 assets/    项目级、公用、可被任何一集引用（角色 / 场景 / 道具 / 品牌 / 风格）
    单元 units/     一集一个，只引用资产 ID，不复制资产
    设计 design/    人读的立项 / 复盘 / 规矩
    场景里有 space.json：三维空间结构（房间尺寸 / 机位坐标 / 光位 / 分区）

Trestle 工程不装 shared/ adapters/ tools/ legacy/ 根 storyboard/ 和单元 segments/；
doctor/upgrade 也不会补。

用法：
    d upgrade                                      补单元内容目录 / 适配器
    d doctor                                       结构体检（内容层）
    d tree                                         打印工程结构（含每格文件数）

    d asset new <type> <名字> [--id]               新建资产（ID/LOOK/STATE 三层 schema）
    d asset list [type]                            资产清单
    d asset level <ID> L3                          设置资产等级（L0-L4；没写按 L1 算）
    d asset check                                  资产体检（必填字段 / 三层 / 文件在位 / level / ID 唯一；有问题退出码 1）
    d space <SC-ID>                                打印场景三维空间（机位表 + 俯视图）

    d unit new <S1-E02> [--type 讲解|剧情|打斗|广告|纪录|品牌]
                                                   新建一集（按片型登记站号路线）
    d unit list

    d segment new SC01-01 [--scene SC01] [--title] [--sec]
                                                   新建分镜（落 production/<场>/<分镜>/）
    d segment list|check [SC01-01]                 分镜列表 / 分镜体检
    d board sync|check [-u S1-E01] [--ensure] [--keep-orphans]  生产层：重写场索引与镜头快照 / 查漂移
    d build sync|check                             资产生成层：重写 canvases.json / 查缺词与输出槽
    d unique                                       唯一性：镜号/场号/分镜号/资产 ID/单元号/生成物

    d pack [<unit>] [-o 路径]                      导出生成包 pack.json（引擎适配层的输入契约）
    d check [<unit>]                               填充体检（资产引用 / 机位 / 时长 / 提示词 / 六栏；有问题退出码 1）
    d adapter list                                 引擎适配器登记表
    d adapter add <id> --kind canvas|image|audio  登记一个引擎
    d adapter set <id> [--endpoint] [--status]     改适配器状态

    d score [-u S1-E01]                            单元评分（六维加权，写 review/score.json）
    d look  [-u S1-E01] [--json]                   影像评估（美学 / 情绪曲线 / 特效调度）
    d ready [-u S1-E01]                            生成准入（不过退出码 1）
    d script [-u S1-E01]                           分镜剧本：出 md + html
    d script check [-u S1-E01]                     剧本体检：角色与场景块 / 口头禅 / AI 味（不过退出码 1）
    d scene list|check [-u S1-E01]                 场分析：目标/阻力/信息增量/揭示与按住
    d split <镜号> <N> [-u S1-E01]                 拆镜：一镜拆 N 个，自动重编号并同步提示词
    d idea check [关键词]                          创意标准体检（碎片 / 缺板块 / L1-L3；有碎片退出码 1）
    d idea list                                    创意清单
    d idea template                                打印创意标准模板
    d score idea "一个想法"                        想法评分（钩子 / 具体性 / 可拍性 / 风险 / 落点）

    d status [-u S1-E01]                           当前单元进度 + 资产清单
    d gate <站号> [-u] [--yes 1,3|--all|--reset]
    d done <站号> [-u] [--artifact 文件] [--skip|--force]
    d next / d note "下一动作"
    d save -m "改了什么" [--tag v1]                提交（自动加 [站N] 前缀）+ 打标签
    d log / d audit

分工：工程、工作台、LibTV 操作归 Trestle；内容、监理、评分、打包归本 skill。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 站点

STATIONS = [
    (0, "立项", "这支片子进不进第一季", "台账里一行", "人工"),
    (1, "找料", "别人怎么讲的，哪些能搬", "拆解文档", "benchmark-script-migration"),
    (2, "文案", "这件事怎么讲得像人话", "口播定稿", "kepu-video-copywriter"),
    (3, "规制", "多长、几镜、合不合规", "分镜表 + 校验报告", "short-video-script-desk"),
    (4, "编剧", "剧情线的人物和戏怎么搭", "剧本", "shanyin-screenwriting-master"),
    (5, "音色", "谁在说、嗓子什么样、多快", "音色卡", "项目标准（无 skill）"),
    (5.5, "立意", "观众该记住什么", "导演阐述 + 记忆点表", "人工"),
    (5.6, "切场", "这场戏立不立得住", "场分析（scenes.json）", "d scene check"),
    (5.7, "资产", "谁出场 / 在哪儿 / 用什么，定没定稿", "资产清单 + 各资产 prompts.md", "d asset check"),
    (6, "分镜", "每个镜头拍什么", "施工图（逐镜表）", "script-to-storyboard"),
    (7, "提示词", "这一帧长什么样 / 怎么动", "提示词清单", "tapnow-prompt-polish"),
    (8, "生成", "图怎么变成片", "画布 + 成片 mp4", "TapNow 出图 / LibTV 编排"),
    (9, "交付", "片头片尾、导出、提案", "成片 / 提案页", "libtv-to-treatment"),
]

# 站点路线：站号必须递增；不在路线上的站不要求过闸。
# 广告跳过环 2（5.6 切场）——15-60 秒没有「场」这个单位（R41）。
# 纪录不写解说文案（站 2），事实核实走站 1；打斗是剧情里的场，沿用剧情线。
# 品牌是品牌片/品牌资产片的路线，保留旧口径；广告与品牌不是同一条线，但都以「段」为基本单位。
ROUTES = {
    "讲解": [0, 1, 2, 3, 5, 5.5, 5.6, 5.7, 6, 7, 8, 9],
    "剧情": [0, 4, 5, 5.5, 5.6, 5.7, 6, 7, 8, 9],
    "打斗": [0, 4, 5, 5.5, 5.6, 5.7, 6, 7, 8, 9],
    "广告": [0, 2, 5, 5.5, 5.7, 6, 7, 8, 9],
    "纪录": [0, 1, 5, 5.5, 5.6, 5.7, 6, 7, 8, 9],
    "品牌": [0, 1, 4, 5, 5.5, 5.6, 5.7, 7, 8, 9],
}

GATES = {
    0: [
        "集号未复用（台账里没有同号）",
        "与本季其他集不撞主旨词",
        "单片只有一个物证，且物证已在 assets/props 登记",
        "用到的角色 / 场景资产已登记，且状态不是「草稿」",
    ],
    1: [
        "拆解表里每一招都标了「能搬 / 改着搬 / 不能搬」",
        "有红线对照（哪些一搬就出事）",
        "拆解文档已入库，不是聊天记录",
    ],
    2: [
        "创意提案阶段用户点过头（确认闸没跳）",
        "事实核验表齐全，未核实信息标了［核查］",
        "净字数统计排除斜杠标记",
        "开篇自报 <=3 秒，钩子是一个具体的事",
    ],
    3: [
        "F03 绝对化 / F04 无出处功效 / F05 无出处评价 / F08 个人信息 四条清零",
        "每条口播都有一张画面，对位表无空行",
        "结尾落回「人」或反命题闭环，不停在口号",
        "时长加得回去（分镜合计 = 字数 / 语速）",
    ],
    4: [
        "每场戏有明确戏剧动作",
        "无括号暗示、无解释性台词、无说教",
        "不写心理描写（全部用行为外化）",
    ],
    5: [
        "六必填齐（职业身份/性别/年龄/音高/声线质地/发声特点）",
        "工程两项齐（语速 字/秒、口音底色）",
        "试听句是定稿原句，一字不改",
        "音色与情绪分离（情绪只动音量/停顿/气口）",
        "卡定稿前不 bind 角色（绑错等于全片重录）",
    ],
    5.5: [
        "导演阐述在位：一句话主旨 + 2-4 个记忆点，每个写清「观众记住什么」",
        "记忆点有镜号落点（shots.json 的 memory 字段），不是只写在文档里",
        "每个记忆点都是唯一可复现的画面，不是形容词",
    ],
    5.6: [
        "场文件在位，d scene check 全过",
        "每场都有目标 / 阻力 / 观众带走什么（audience_out）",
        "每场揭示 <=3 条、按住 >=1 条",
        "每个镜头都能归到某一场，chapter 与 scenes 无孤儿",
    ],
    5.7: [
        "三张清单齐：角色 / 场景 / 道具，每行写清出现在哪几场",
        "每个「建」的资产都有 manifest.json + prompts.md（提示词跟着资产走）",
        "角色到 L2（有定妆图）、场景到 L2（有母图）",
        "状态门干净：status 不以「待 / 草稿 / 未 / 部分」开头",
    ],
    6: [
        "每镜时长由字数实测算出，逐句 diff 与口播零差异",
        "逐镜式单镜默认 6s、硬上限 8s（证据读镜 ≤8s，追逐 ≤9s）；节拍式按整段算",
        "镜号连续、不重编",
        "每镜机位能在 space.json 里找到对应 ID",
        "全片 BGM 一栏写「无」",
        "每镜的 func / emotion 已写（要审，先要说清这一镜干什么、要什么情绪）",
        "难点镜的构图与光线在六栏里用文字说清了（需要看图时在 Trestle 审片台生成白模；不生图也能过）",
        "逐镜审片结论已落 review/shot-review.json（没有白模时按文字描述审），返工镜已改完或明确不做",
    ],
    7: [
        "静帧提示词只写「长什么样」",
        "运动提示词只写「什么在动」，不提场景与服装",
        "多视图参考加了负面词：不要分格 / 不要多视图并排 / 不要文字",
        "视频提示词里写了台词原话",
    ],
    8: [
        "视频节点带台词原话 + 时长预算（字数 ÷ 该段语速，基准 4.4，分段语速见项目规格）",
        "显式写了「不要背景音乐 / 音效 / 字幕」",
        "一章一个项目（画布不超一章）",
        "先 3 镜试跑验脸验色验质感，过了才批量",
    ],
    9: [
        "字标用 A2「一条线」，<=64px 换 C1 单色",
        "暖黄只落冷底，面积 <=2%",
        "全片无破折号",
        "粒子语言 / 震屏 / 摔字没有进正片",
    ],
}

CROSS_GATES = [
    "全片无破折号",
    "无套话（随着…时代到来）",
    "无黑话（赋能/闭环/抓手/打法）",
    "无恐吓词（细思极恐/不寒而栗）",
    "排比 <=2 句，反问 <=1 次",
    "不承诺、不吓人、不催单",
    "参考外部作品先核再写",
]

ASSET_TYPES = [
    ("characters", "CH", "角色", "谁出现在画面里：长相、服装、声音、禁则"),
    ("scenes", "SC", "场景", "在哪拍：空间结构、机位、光位、材质"),
    ("props", "PR", "道具", "手里拿的物证：尺寸、材质、使用痕迹"),
    ("brand", "BR", "品牌", "字标、图标、片头片尾、slogan"),
    ("style", "ST", "风格", "色板、滤镜、光比、镜头语言"),
]

PUNCT = "，。？！、；：“”‘’（）《》〈〉—…·,.?!;:\"'()[]{}~～"

REQ = {
    "characters": [("name", "角色名"), ("role", "身份"), ("voice.rate", "语速 字/秒")],
    "scenes": [("name", "场景名"), ("materials.wall", "主墙材质")],
    "props": [("name", "道具名"), ("dims.w", "宽度"), ("dims.h", "高度"),
              ("material", "材质"), ("wear", "使用痕迹")],
    "brand": [("name", "名称")],
    "style": [("name", "名称")],
}



STATE_FILE = "project.json"
DIRECTOR_MD = "DIRECTOR.md"
VERSIONING_MD = "VERSIONING.md"
MARK_BEGIN = "<!-- director:status:begin -->"
MARK_END = "<!-- director:status:end -->"
ICON = {"done": "OK", "current": ">>", "pending": "..", "skipped": "--"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "tmp"}


# ---------------------------------------------------------------- 基础

def out(msg=""):
    sys.stdout.write(str(msg) + "\n")


def die(msg, code=1):
    sys.stderr.write("[director] " + str(msg) + "\n")
    sys.exit(code)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def dwidth(s):
    import unicodedata
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def dpad(s, width, align="left"):
    gap = max(0, width - dwidth(s))
    return (" " * gap + str(s)) if align == "right" else (str(s) + " " * gap)


def sn(n):
    """站号显示：5.5 / 5.6 / 5.7 保留小数，整数站不带 .0"""
    try:
        return ("%g" % float(n))
    except (TypeError, ValueError):
        return str(n)


def station(n):
    for s in STATIONS:
        if s[0] == n:
            return s
    die("没有站 %s" % n)


def git(cwd, *args, check=False):
    cmd = ["git", "-c", "core.quotepath=false"] + list(args)
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        die("找不到 git，请先安装 Git for Windows 并加入 PATH")
    if check and p.returncode != 0:
        die("git %s 失败：\n%s" % (" ".join(args), (p.stderr or p.stdout).strip()))
    return p


def is_git_repo(path):
    return (Path(path) / ".git").exists()


def jload(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die("读不动 %s：%s" % (p, e))


def jdump(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- 状态

def state_path(root):
    return Path(root) / STATE_FILE


def sync_gates(st, gates):
    """门禁定义变了就同步：同文本保留勾选，新条目补成未勾选，已有站状态不动。"""
    changed = False
    for uid, u in (st.get("units") or {}).items():
        stations = u.get("stations") or {}
        for n, _, _, _, _ in STATIONS:
            key = str(n)
            want = gates.get(n) or []
            s = stations.get(key)
            if not want:
                if not s:
                    stations[key] = make_station(n, [])
                    changed = True
                continue
            if not s:
                stations[key] = make_station(n, want)
                changed = True
                continue
            have = s.get("gate") or []
            if [g.get("text") for g in have] == list(want):
                continue
            by = {g.get("text"): g for g in have if isinstance(g, dict)}
            s["gate"] = [by.get(t) or {"text": t, "ok": False} for t in want]
            changed = True
        u["stations"] = stations
    return changed


def load_state(root):
    p = state_path(root)
    if not p.exists():
        die("这里不是 Trestle 工程（缺 %s）。请在 Trestle 里打开或新建工程。" % STATE_FILE)
    st = jload(p)
    if st.get("meta", {}).get("schema", 1) < 2:
        die("这是旧版流程节点式工程，Trestle 不支持；请新建工程")
    if "adapters" not in st:          # 老工程自动补适配器位，不动原有状态
        st["adapters"] = {a["id"]: a for a in default_adapters()}
        save_state(root, st)
    # Trestle 只写单元登记，不写站点状态；这里补齐一次
    # 门禁条目改过也要同步（例如站 6 新增了验镜三条），否则老工程永远看不到新闸
    gates, _ = load_gates(root)
    if sync_gates(st, gates):
        save_state(root, st)
    return st


def load_gates(root):
    """项目级门禁覆盖：根目录 gates.json 存在时按它走（键＝站号，"cross" 为跨站）"""
    g = jload(Path(root) / "gates.json", None)
    if not isinstance(g, dict):
        return dict(GATES), list(CROSS_GATES)
    merged = dict(GATES)
    for k, v in g.items():
        if k == "cross":
            continue
        try:
            fk = float(k)
            merged[int(fk) if fk.is_integer() else fk] = [str(x) for x in v]
        except (TypeError, ValueError):
            pass
    return merged, [str(x) for x in (g.get("cross") or CROSS_GATES)]


def save_state(root, st):
    st["meta"]["updated"] = today()
    jdump(state_path(root), st)


CHANGJI_PLATFORM_DIRS = ("shared", "adapters", "tools", "legacy", "storyboard")


def stamp_changji(root, st):
    """本 skill 只服务 Trestle 工程，统一盖 meta.app 章。"""
    if not st:
        return
    meta = st.setdefault("meta", {})
    if str(meta.get("app") or "") != "changji":
        meta["app"] = "changji"
        save_state(root, st)


def ensure_changji_unit_dirs(root, uid):
    """Trestle 单元只补内容层目录；不补平台层，也不建 segments/。"""
    subs = ("script/draft", "script/derived", "storyboard", "prompts",
            "renders", "edit", "review", "production",
            "deliver/final", "deliver/preview",
            "deliver/publish/douyin", "deliver/publish/bilibili")
    made = []
    for sub in subs:
        p = Path(root) / "units" / uid / sub
        if not p.is_dir():
            p.mkdir(parents=True, exist_ok=True)
            made.append("units/%s/%s/" % (uid, sub))
    return made


def route_of(u):
    return ROUTES.get(u.get("type", "讲解"), ROUTES["讲解"])


def unit_id(st, arg=None):
    u = arg or st["meta"].get("active_unit")
    if not u:
        die("没有活动单元。用 -u S1-E01 指定，或先 d unit new S1-E01")
    if u not in st.get("units", {}):
        die("没有单元 %s。现有：%s" % (u, "、".join(st.get("units", {})) or "无"))
    return u


def unit(st, arg=None):
    return st["units"][unit_id(st, arg)]


def cur_station(u):
    for n in route_of(u):
        if u["stations"].get(str(n), {}).get("status", "pending") == "pending":
            return n
    return route_of(u)[-1]


def gate_ready(u, n):
    items = u["stations"].get(str(n), {}).get("gate", [])
    return bool(items) and all(i.get("ok") for i in items)


def make_station(n, items=None):
    return {"name": station(n)[1], "status": "pending", "artifact": "",
            "gate": [{"text": t, "ok": False} for t in (items if items is not None else GATES[n])]}


def asset_dirs(root):
    """[(类型, 目录)]。判定标准：带 manifest.json 的目录才是资产（_TEMPLATE 除外）"""
    res = []
    for typ, _, _, _ in ASSET_TYPES:
        d = Path(root) / "assets" / typ
        if not d.exists():
            continue
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and not sub.name.startswith("_") and (sub / "manifest.json").exists():
                res.append((typ, sub))
    return res


def id_of(folder_name):
    parts = folder_name.split("-")
    return "-".join(parts[:3]) if len(parts) >= 3 else folder_name


def read_manifest(adir):
    return jload(Path(adir) / "manifest.json", {}) or {}


def list_assets(root):
    rows = []
    for typ, sub in asset_dirs(root):
        m = read_manifest(sub)
        rows.append({"type": typ, "dir": sub, "id": m.get("id") or id_of(sub.name),
                     "name": m.get("name") or sub.name, "status": m.get("status", "?"),
                     "level": m.get("level", ""), "manifest": m})
    return rows


def asset_index(root):
    idx = {}
    for typ, sub in asset_dirs(root):
        idx[read_manifest(sub).get("id") or id_of(sub.name)] = (typ, sub)
    return idx


def unit_dir(root, uid):
    return Path(root) / "units" / uid


# ---------------------------------------------------------------- 模板

GITIGNORE = """\
# 只忽略「可再生成的」与「过程垃圾」；资产与工程文件一律入库
.workbuddy/tmp/
__pycache__/
*.pyc
node_modules/
.venv/
.DS_Store
Thumbs.db
*.tmp
*.log
*-导出-临时.mp4

# 生成包与节点回写可以随时重算，不进仓库（200KB/集，且随分镜变动）
units/*/pack.json

# 单个超过 50MB 的工程/视频文件不进仓库（d audit 会提示）
"""

MANIFEST_CHAR = {
    "id": "CH-XX-01",
    "name": "（角色名）",
    "type": "characters",
    "role": "（在片中的身份，例：主讲人 / 产品负责人）",
    "status": "草稿",
    "level": "L1",
    # 三层资产：ID 身份（永不变）/ LOOK 妆造（换装时加）/ STATE 剧情状态（受伤/淋雨/病中）
    "id_layer": {
        "age": "（年龄）",
        "height_cm": 0,
        "face": "（脸型、轮廓）",
        "marks": ["（识别特征 1，近景必出）", "（识别特征 2）", "（识别特征 3）"],
        "build": "（体型）",
        "temper": "（气质，例：温和、不锋利）",
        "files": {"master": "", "four_view": ""}
    },
    "looks": [
        {"id": "LK-01", "name": "日常",
         "outfit": "（上装 + 下装 + 主色，例：炭灰机能外套 #3A3C3E + 深灰圆领）",
         "constraints": ["（禁穿/禁用色，例：不得换卡其、驼色、米黄）"],
         "files": {"four_view": ""},
         "scenes": []}
    ],
    "states": [],
    "voice": {
        "pitch": "（音高）",
        "texture": "（声线质地）",
        "rate": "（语速 字/秒）",
        "accent": "（口音底色）",
        "avoid": ["（可听辨的避免特征）"]
    },
    "expression_rule": "（表情基准，例：平静浅笑不露齿）",
    "files": {},
    "used_by": [],
    "notes": ""
}

MANIFEST_SCENE = {
    "id": "SC-XX-01",
    "name": "（场景名）",
    "type": "scenes",
    "kind": "室内",
    "status": "草稿",
    "level": "L1",
    "space": "space.json",
    "materials": {"wall": "（主墙材质与颜色）", "floor": "（地面）", "ceiling": "（顶面）"},
    "style": "（风格语言，例：暖调复古 / 低饱和 / 有生活痕迹）",
    "time_of_day": "（时间设定，例：日落前一小时）",
    "depth_rule": "（人物与背景的距离与虚化要求）",
    "files": {},
    "used_by": [],
    "notes": ""
}

MANIFEST_PROP = {
    "id": "PR-XX-01",
    "name": "（道具名）",
    "type": "props",
    "status": "草稿",
    "level": "L1",
    "dims": {"w": 0.0, "h": 0.0, "d": 0.0, "unit": "m"},
    "material": "（材质）",
    "wear": "（使用痕迹，越具体越可信）",
    "source": "（实物来源 / 采购 / 参考图路径）",
    "files": {},
    "used_by": [],
    "notes": ""
}

MANIFEST_BRAND = {
    "id": "BR-XX-01",
    "name": "（品牌资产名）",
    "type": "brand",
    "status": "草稿",
    "level": "L1",
    "slogan": "",
    "files": {},
    "used_by": [],
    "notes": ""
}

MANIFEST_STYLE = {
    "id": "ST-XX-01",
    "name": "（风格资产名）",
    "type": "style",
    "status": "草稿",
    "level": "L1",
    "palette": [{"name": "（色名）", "hex": "#000000", "usage": "（用在哪、面积上限）"}],
    "light": {"ratio": "", "kelvin": "", "notes": ""},
    "lens": {"filters": [], "notes": ""},
    "files": {},
    "used_by": [],
    "notes": ""
}

MANIFESTS = {"characters": MANIFEST_CHAR, "scenes": MANIFEST_SCENE,
             "props": MANIFEST_PROP, "brand": MANIFEST_BRAND, "style": MANIFEST_STYLE}

SPACE_TPL = {
    "id": "SC-XX-01",
    "name": "（场景名）",
    "coord": {
        "system": "右手系；原点＝房间中心地面；+X 画面右；+Y 上；+Z 朝背景墙（远离机位）",
        "unit": "m"
    },
    "room": {"w": 6.0, "d": 4.0, "h": 2.8,
             "walls": {"back": "背景墙（+Z）", "left": "左墙（-X）", "right": "右墙（+X）",
                       "front": "机位侧（-Z），无实体墙，摄影机可退到房外"}},
    "zones": [
        {"id": "A", "name": "主讲区", "pos": [0.0, 0.2], "size": [2.4, 1.2],
         "note": "人物站位区，人后留 1.5-2m"}
    ],
    "cameras": [
        {"id": "C1", "name": "主镜正面", "pos": [0.0, 1.45, -2.3], "target": [0.0, 1.45, 0.2],
         "focal": 50, "shot": "MS", "note": "开场自报与正题口播主机位"}
    ],
    "lights": [
        {"id": "L1", "name": "窗光主光", "pos": [-2.6, 2.0, 0.5], "kelvin": 4000,
         "ratio": 0.7, "note": "左入，日落前一小时"}
    ],
    "sightlines": []
}

UNIT_MANIFEST = {
    "id": "S1-E01",
    "entry": "台账里的一行（集号 / 选题 / 核心一句话 / 时长档 / 平台）",
    "type": "讲解",
    "platform": "",
    "duration": "",
    "evidence": "",
    "assets": [],
    "files": {},
    "notes": ""
}

# ---------------------------------------------------------------- 填充层契约
# 站 6 交 shots.json（逐镜表，机器可读），站 7 交 prompts.json（静帧/运动分开）
# 站 8 由引擎适配层消费 units/<uid>/pack.json，产物回 renders/

SHOTS_TPL = {
    "unit": "（集号）",
    "fps": 25,
    "aspect": "16:9",
    "audio_rate": 4.4,
    "shots": [],
    "total_sec": 0.0,
    "_字段说明": {
        "id": "镜号，三位数字，连续不重编",
        "type": "素材类：A 人物 / B 实物 / MG 图解 / T 字卡。只有 A 必须绑三维机位",
        "chapter": "章号或章名，预览按章分组并算节奏曲线",
        "sec": "时长（秒），= 净字数 / audio_rate，向上取到 0.5",
        "camera": "机位 ID，必须在引用场景的 space.json 里存在",
        "camera_name": "机位中文名（可省，预览显示用）",
        "shot": "景别 WS/MS/MCU/CU/ECU",
        "angle": "角度：eye / low / high / dutch / overhead（可省，默认 eye）",
        "move": "② 运镜及主体：固定 / 推 / 拉 / 摇 / 移 / 跟 / 手持 / 升降 / 急推 / 甩",
        "framing": "③ 构图：这一镜的构图落点（前景/中景/背景各放什么）",
        "func": "叙事功能，例：开场自报 / 主位讲解 / 物证特写",
        "role": "视觉职责：show / prove / orient / reset（归不进四类的镜删掉）",
        "emotion": "这一镜要观众感到什么（具体；不写「感动/震撼」这类空词）",
        "beat": "情绪节拍 1-5（d look 用它画情绪曲线）",
        "look": "风格资产 ID（ST-xx），没有写空串",
        "line": "台词原话，一字不改；无台词写空串",
        "vo": "台词是画外音时写 true（B/MG 镜不扛台词，台词一律 vo）",
        "rate": "本镜语速（字/秒），可省；默认用 chapter_rates 或 audio_rate",
        "visual": "④ 画面内容：这一镜看见什么（承接 + 动作 + 末态）",
        "action": "起点动作 → 终点动作（四层动力学：主动件/稳定件/补偿件/不自主）",
        "cut_at": "⑤ 切点：在哪个节点切（动作闭合 / 台词落点 / 视线转移）",
        "sound": "⑥ 声效：动作音 / 环境声 / 静默（不接受「无」当没想过）",
        "memory": "记忆点编号 M1，没有写空串",
        "read": "B 镜是证据阅读镜时写 true（时长上限放宽到 8s）",
        "over_ok": "显式声明超长理由（写了才放行；不要拿它当万能钥匙）",
        "hold_camera": "形式要求锁机位时写 true（横向滚动剖面 / 一镜到底拆拍）",
        "note": "备注：假设、返工原因、超长理由等",
    },
}

PROMPTS_TPL = {
    "unit": "（集号）",
    "stills": [],
    "motions": [],
    "_字段说明": {
        "stills[].prompt": "只写这一帧长什么样：主体/服装/光线/色温/构图/质感",
        "stills[].negative": "负面词：不要分格 / 不要多视图并排 / 不要文字",
        "motions[].prompt": "只写什么在动 + 台词原话 + 时长预算 + 不要音乐/音效/字幕",
        "motions[].sec": "本镜要生成的秒数",
    },
}

SCENES_TPL = {
    "unit": "（集号）",
    "scenes": [],
    "_字段说明": {
        "id": "场号 SC01，连续不重编",
        "name": "场名（给人看）",
        "chapter": "章名，必须与 shots.json 的 chapter 对上",
        "goal": "这一场要完成什么",
        "obstacle": "阻力是什么",
        "audience_in": "观众进来时知道什么",
        "audience_out": "观众出去时知道什么",
        "reveal": "揭示（最多 3 条）",
        "hold": "留白 / 按住观众的问题（末场可空）",
        "state_change": "这一场之后什么变了",
        "shots": "本场镜号列表",
    },
}

PRODUCTION_LIBTV_TPL = {
    "model": "seedance-2.5",
    "duration_sec": 0,
    "status": "未连线",
    "nodes": [],
    "refs": [],
    "_字段说明": {
        "模型": "seedance-2.5（单次 30s）｜ minimax-h3（5-15s）",
        "连线纪律": "填好 nodes / refs，status 改成「已连线」后停下，等人确认才生成",
    },
}

# 引擎适配器：只登记「怎么对接」，不绑定实现。空 endpoint = 预留未接
ENGINE_ADAPTERS = [
    {"id": "libtv", "kind": "canvas", "status": "预留", "endpoint": "",
     "consumes": "units/<unit>/pack.json",
     "produces": "units/<unit>/renders/",
     "note": "节点级编排：建节点 / 连线 / 分组，一章一个画布。消费 pack.json 的 shots + assets。锚点图取自 assets/*/refs"},
    {"id": "tapnow", "kind": "image", "status": "预留", "endpoint": "",
     "consumes": "units/<unit>/pack.json",
     "produces": "assets/*/refs/",
     "note": "出图与定妆：角色定妆图 / 场景母图。产物回写 assets/*/refs 并登记到 manifest.files"},
    {"id": "tts", "kind": "audio", "status": "预留", "endpoint": "",
     "consumes": "units/<unit>/pack.json",
     "produces": "assets/characters/*/voice/",
     "note": "音色合成：音色挂在角色上，不挂镜头。换音色只改一处"},
]

ADAPTER_KINDS = ("canvas", "image", "audio", "edit", "deliver")


def make_unit_state(utype, gates=None):
    g = gates or GATES
    return {"type": utype,
            "stations": {str(n): make_station(n, g.get(n)) for n, _, _, _, _ in STATIONS},
            "next_action": ""}


def default_adapters():
    return json.loads(json.dumps(ENGINE_ADAPTERS))



# ---------------------------------------------------------------- 资产命令

def slug_type(typ):
    for t, pre, cn, _ in ASSET_TYPES:
        if typ in (t, cn):
            return t, pre, cn
    die("资产类型只能是：%s" % "、".join("%s(%s)" % (t, cn) for t, _, cn, _ in ASSET_TYPES))


def char_marks(m):
    """识别特征：新口径 id_layer.marks；兼容旧的 identity_marks / look.features。"""
    idl = m.get("id_layer") or {}
    marks = idl.get("marks")
    if not marks:
        marks = m.get("identity_marks")
    if not marks:
        marks = (m.get("look") or {}).get("features")
    return [x for x in (marks or []) if x and not str(x).strip().startswith("（")]


def char_outfit(m):
    """服装/妆造锚点：新口径 looks[0].outfit；兼容旧的 wardrobe.anchor。"""
    looks = m.get("looks") or []
    if looks and not is_blank(looks[0].get("outfit")):
        return str(looks[0]["outfit"]).strip()
    anchor = (m.get("wardrobe") or {}).get("anchor")
    return "" if is_blank(anchor) else str(anchor).strip()


def asset_files(m):
    """把 manifest 里各层的文件路径摊平，键名带层级，值必须是非空字符串。"""
    out = {}

    def add(prefix, files):
        if not isinstance(files, dict):
            return
        for k, v in files.items():
            if isinstance(v, str) and v.strip():
                out["%s%s" % (prefix, k)] = v.strip()

    add("", m.get("files") or {})
    add("id_layer.", (m.get("id_layer") or {}).get("files") or {})
    for lk in m.get("looks") or []:
        lid = str(lk.get("id") or "?")
        add("looks.%s." % lid, lk.get("files") or {})
        if str(lk.get("four_view") or "").strip():
            out["looks.%s.four_view" % lid] = str(lk["four_view"]).strip()
    for st in m.get("states") or []:
        sid = str(st.get("id") or "?")
        add("states.%s." % sid, st.get("files") or {})
        if str(st.get("ref") or "").strip():
            out["states.%s.ref" % sid] = str(st["ref"]).strip()
    return out


def get_dotted(m, path):
    cur = m
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def is_blank(v):
    if isinstance(v, str):
        s = v.strip()
        return (not s) or s.startswith("（")
    return v in (None, [], {}, 0, 0.0)


def cmd_asset_check(args):
    root = Path(args.cwd or ".").resolve()
    rows = list_assets(root)
    out("")
    out("资产体检 · %s" % root.name)
    out("=" * 56)
    if not rows:
        out("  还没有资产。先 d asset new scenes 演播室")
        out("")
        return
    seen, fails = {}, 0
    for r in rows:
        typ, adir, m = r["type"], r["dir"], r["manifest"]
        probs = []
        if not m:
            probs.append("缺 manifest.json")
        mid = m.get("id", "")
        if "XX" in mid:
            probs.append("ID 还是模板值（%s）" % mid)
        if mid in seen:
            probs.append("ID 与 %s 重复" % seen[mid])
        seen[mid] = r["id"]
        if not str(m.get("level") or "").strip():
            probs.append("缺 level（按 L1 算；站 6 前到 L2，生成前到 L3）")
        for path, label in REQ[typ]:
            v = get_dotted(m, path)
            if is_blank(v):
                probs.append("缺 %s" % label)
        if typ == "characters":
            if len(char_marks(m)) < 3:
                probs.append("识别特征不足 3 条（id_layer.marks，近景必出）")
            if not char_outfit(m):
                probs.append("缺服装/妆造锚点（looks[].outfit 或 wardrobe.anchor）")
            looks = m.get("looks") or []
            look_ids = {str(lk.get("id") or "") for lk in looks if isinstance(lk, dict)}
            for lk in looks:
                if not isinstance(lk, dict):
                    continue
                fv = lk.get("four_view") or (lk.get("files") or {}).get("four_view")
                if not str(fv or "").strip():
                    probs.append("LOOK %s 缺四视图" % (lk.get("id") or "?"))
            for st in m.get("states") or []:
                if not isinstance(st, dict):
                    continue
                base = str(st.get("base") or "")
                if base and base not in look_ids:
                    probs.append("STATE %s 的 base=%s 不存在" % (st.get("id") or "?", base))
        if typ == "style":
            pal = [p for p in (m.get("palette") or []) if isinstance(p, dict) and p.get("hex")]
            if len(pal) < 3:
                probs.append("色板不足 3 色")
        files = asset_files(m)
        if typ in ("characters", "brand", "style") and not files:
            probs.append("没有挂任何文件（files）")
        if typ == "characters":
            keys = {k.split(".")[-1] for k in files}
            if "master" not in keys:
                probs.append("缺身份母图（files.master 或 id_layer.files.master）")
            if "four_view" not in keys:
                probs.append("缺四视图（身份或 LOOK 至少一张）")
        for k, f in files.items():
            fp = Path(f)
            # manifest 里的路径允许两种写法：相对资产目录（refs/xxx.png，R37/R15 的写法）
            # 或相对工程根（assets/.../refs/xxx.png）。两种都认。
            cand = fp if fp.is_absolute() else None
            if cand is None:
                cand = (adir / fp) if (adir / fp).exists() else (Path(root) / fp)
            if not cand.exists():
                probs.append("文件不存在：%s（%s）" % (f, k))
        if typ == "scenes":
            sp = jload(adir / "space.json", None)
            if not sp:
                probs.append("缺 space.json（三维空间）")
            else:
                room = sp.get("room") or {}
                if not all(room.get(k) for k in ("w", "d", "h")):
                    probs.append("空间缺房间尺寸 w/d/h")
                cams = sp.get("cameras") or []
                if not cams:
                    probs.append("空间缺机位 cameras")
                for c in cams:
                    if not c.get("pos") or c.get("focal") in (None, ""):
                        probs.append("机位 %s 缺位置或焦段" % c.get("id"))
                lights = sp.get("lights") or []
                if not lights:
                    probs.append("空间缺光位 lights")
                for l in lights:
                    if l.get("kelvin") in (None, ""):
                        probs.append("光位 %s 缺色温" % l.get("id"))
        if probs:
            fails += 1
            out("  [!!] %-9s %s" % (r["id"], r["name"]))
            for p in probs:
                out("        - " + p)
        else:
            out("  [OK] %-9s %-12s %s %s" % (r["id"], r["name"],
                                             ("[%s]" % m["level"]) if m.get("level") else "[--]",
                                             m.get("status", "")))
    out("=" * 56)
    lv = [_level(r) for r in rows if _level(r) is not None]
    out("合计 %d 个资产，%d 个待补。%s" % (
        len(rows), fails,
        ("　等级 L2+ %d/%d。" % (sum(1 for x in lv if x >= 2), len(lv))) if lv else ""))
    if fails:
        out("资产不齐开不了机：角色缺识别特征会换脸，场景缺机位会是「站着的人」。")
    out("")
    return 1 if fails else 0


# ---------------------------------------------------------------- 空间

def topdown(sp, cols=56, rows=20):
    room = sp.get("room") or {}
    w = float(room.get("w") or 6)
    d = float(room.get("d") or 4)
    cams = sp.get("cameras") or []
    xs = [float((c.get("pos") or [0])[0]) for c in cams] + [-w / 2, w / 2]
    zs = [float((c.get("pos") or [0, 0, 0])[2]) for c in cams] + [-d / 2, d / 2]
    xmin, xmax = min(xs) - 0.3, max(xs) + 0.3
    zmin, zmax = min(zs) - 0.3, max(zs) + 0.3
    if xmax - xmin < 0.1:
        xmax = xmin + 1
    if zmax - zmin < 0.1:
        zmax = zmin + 1
    g = [[" "] * cols for _ in range(rows)]

    def cx(x):
        return 1 + int(round((float(x) - xmin) / (xmax - xmin) * (cols - 3)))

    def rz(z):
        return rows - 2 - int(round((float(z) - zmin) / (zmax - zmin) * (rows - 3)))

    for c in range(cols):
        g[0][c] = "-"
        g[rows - 1][c] = "-"
    for r in range(rows):
        g[r][0] = "|"
        g[r][cols - 1] = "|"
    g[0][0] = g[0][cols - 1] = g[rows - 1][0] = g[rows - 1][cols - 1] = "+"

    def put(r, c, ch):
        if 0 <= r < rows and 0 <= c < cols:
            g[r][c] = ch

    # 房间矩形（真实位置，不贴边框）
    r0, r1 = rz(d / 2), rz(-d / 2)
    c0, c1 = cx(-w / 2), cx(w / 2)
    for c in range(c0, c1 + 1):
        put(r0, c, "=")
        put(r1, c, "=")
    for r in range(r0, r1 + 1):
        put(r, c0, "!")
        put(r, c1, "!")
    for r, c in ((r0, c0), (r0, c1), (r1, c0), (r1, c1)):
        put(r, c, "+")

    # 先画分区（整块填自己的字母），再画房间边框压在上面
    for z in sp.get("zones") or []:
        pos = z.get("pos") or [0, 0]
        size = z.get("size") or [0.8, 0.8]
        x0, x1 = cx(pos[0] - size[0] / 2), cx(pos[0] + size[0] / 2)
        zr0, zr1 = rz(pos[1] + size[1] / 2), rz(pos[1] - size[1] / 2)
        ch = (z.get("id") or "?")[0]
        for r in range(min(zr0, zr1), max(zr0, zr1) + 1):
            for c in range(min(x0, x1), max(x0, x1) + 1):
                put(r, c, ch)
    for l in sp.get("lights") or []:
        p = l.get("pos") or [0, 0, 0]
        put(rz(p[2]), cx(p[0]), "L")
    for i, c in enumerate(cams):
        p = c.get("pos") or [0, 0, -2]
        label = c.get("id", "")[-1]
        put(rz(p[2]), cx(p[0]), label if label.isdigit() else "C")

    # 房间边界（真实位置）
    r0, r1 = rz(d / 2), rz(-d / 2)
    c0, c1 = cx(-w / 2), cx(w / 2)
    for c in range(c0, c1 + 1):
        put(r0, c, "=")
        put(r1, c, "=")
    for r in range(r0, r1 + 1):
        put(r, c0, "!")
        put(r, c1, "!")
    for r, c in ((r0, c0), (r0, c1), (r1, c0), (r1, c1)):
        put(r, c, "+")
    lines = ["".join(row) for row in g]
    lines.append("  图例：A/B/C/D=分区  L=灯  0-9=机位（编号同 C0-C9）  =!=房间边界")
    lines.append("        上边＝背景墙（+Z）　下边＝机位侧（-Z）")
    return lines


# ---------------------------------------------------------------- 单元命令

def nettext(s):
    """净字数：去掉标点、空白、// 标记"""
    return "".join(ch for ch in str(s or "") if ch not in PUNCT and not ch.isspace())


def unit_manifest(root, uid):
    return jload(unit_dir(root, uid) / "manifest.json", {}) or {}


def is_filled(v):
    if isinstance(v, str):
        s = v.strip()
        return bool(s) and not s.startswith("（")
    return bool(v)


def build_pack(root, uid, st):
    """把某一集解析成生成包：资产（展开设定）+ 逐镜表 + 提示词 + 适配器"""
    root = Path(root)
    m = unit_manifest(root, uid)
    idx = asset_index(root)
    warns = []

    refs = [x for x in (m.get("assets") or []) if x]
    # evidence 是「物证说明」自由文本，只有当它长得像资产 ID 时才参与校验
    ev = str(m.get("evidence") or "").strip()
    if ev and re.match(r"^[A-Z]{2}-[A-Z0-9]{2,}-\d{2,}$", ev) and ev not in refs:
        refs.append(ev)
    if not refs:
        warns.append("单元没登记任何资产引用（manifest.assets 或 evidence 为空）")

    assets = {t: [] for t, _, _, _ in ASSET_TYPES}
    cams, scenes_of = {}, []
    for aid in refs:
        if aid not in idx:
            warns.append("引用的资产不存在：%s（先 d asset new 或改正 ID）" % aid)
            continue
        typ, adir = idx[aid]
        man = read_manifest(adir)
        refs_found = {p.relative_to(root).as_posix() for p in adir.rglob("*")
                      if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")}
        for f in asset_files(man).values():
            fp = Path(f)
            cand = fp if fp.is_absolute() else (
                (adir / fp) if (adir / fp).exists() else (root / fp))
            if cand.exists() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                try:
                    refs_found.add(cand.relative_to(root).as_posix())
                except ValueError:
                    refs_found.add(str(cand))
        row = {"id": aid, "name": man.get("name", ""), "status": man.get("status", ""),
               "dir": adir.relative_to(root).as_posix(), "refs": sorted(refs_found)}
        if typ == "scenes":
            sp = jload(adir / "space.json", {}) or {}
            row["space"] = {k: sp.get(k) for k in
                            ("coord", "room", "zones", "cameras", "lights", "sightlines")}
            scenes_of.append(aid)
            for c in sp.get("cameras") or []:
                cid = c.get("id")
                if not cid:
                    continue
                cams[cid] = aid
                for al in [c.get("name")] + list(c.get("alias") or []):
                    if al:
                        cams.setdefault(str(al).strip(), cid)
        else:
            for k in ("role", "look", "wardrobe", "voice", "expression_rule", "dims",
                      "level", "id_layer", "looks", "states",
                      "material", "wear", "materials", "palette", "light", "lens",
                      "slogan", "style", "time_of_day", "depth_rule"):
                if k in man:
                    row[k] = man[k]
        assets[typ].append(row)
        stt = str(row["status"]).strip()
        if stt in ("", "草稿") or stt.startswith("待"):
            warns.append("资产未定稿：%s %s（%s）" % (aid, row["name"], row["status"]))
        if typ == "characters" and not row["refs"]:
            warns.append("角色 %s 没有参考图（%s/refs 空），生成会换脸" % (aid, row["dir"]))
        if typ == "scenes" and not row["refs"]:
            warns.append("场景 %s 没有母图（%s/refs 空），带环境的镜头会飘" % (aid, row["dir"]))

    sb = jload(unit_dir(root, uid) / "storyboard" / "shots.json", {}) or {}
    shots = [s for s in (sb.get("shots") or []) if s.get("id")]
    pr = jload(unit_dir(root, uid) / "prompts" / "prompts.json", {}) or {}
    stills = {x.get("shot"): x for x in (pr.get("stills") or []) if x.get("shot")}
    motions = {x.get("shot"): x for x in (pr.get("motions") or []) if x.get("shot")}
    rate = float(sb.get("audio_rate") or 4.4) or 4.4
    # 分段语速：镜头自带 rate > chapter_rates[本镜 chapter] > 全片基准
    ch_rates = {str(k): float(v) for k, v in (sb.get("chapter_rates") or {}).items() if v}

    def shot_rate(s):
        if s.get("rate"):
            try:
                return float(s["rate"])
            except (TypeError, ValueError):
                pass
        c = str(s.get("chapter") or "").strip()
        return ch_rates.get(c, rate)

    if not shots:
        warns.append("还没有镜头（storyboard/shots.json 里 shots 为空）——站 6 没做")
    total = 0.0
    over_lim = []
    over_ok = []          # 显式声明了超长理由的镜（不报错，但要列出来）
    pending_prompts = []
    LIMITS = {"B": 2.5, "MG": 4.0, "T": 4.0}      # 动作/图解/字卡的时长上限
    utype = str(m.get("type") or "").strip()
    # B 镜有两种：动作插入镜（一个动作完成即切，2.5s）
    #            证据阅读镜（观众要读完票面/图表/文件，按 Extraction Rule，可到 8s）
    READ_RE = re.compile(r"文字|字符|字样|小字|序号|字母|数字|票根|票据|票面|合同|账单|说明书|"
                         r"表格|图表|示意图|座位图|文件|证件|屏幕|界面|读数|字迹|名牌|牌面")
    for s in shots:
        sid = s["id"]
        sec = float(s.get("sec") or 0)
        total += sec
        if sec <= 0:
            warns.append("镜头 %s 缺时长（sec）" % sid)
        cam = str(s.get("camera") or "").strip()
        shtype = str(s.get("type") or "A").strip().upper()
        if cam and cams.get(cam):
            s["camera_id"] = cams[cam]
        if shtype == "A" and cam and cams and cam not in cams:
            warns.append("镜头 %s（人物镜）机位 %s 在 space.json 里找不到，也没有别名" % (sid, cam))
        if shtype == "A" and not cam:
            warns.append("镜头 %s（人物镜）没写机位" % sid)
        _raw_line = str(s.get("line") or "")
        line = _raw_line
        if shtype in ("MG", "T", "片头", "字卡"):
            line = ""                   # 图解与字卡的时长由版式定，不跟语速比
        if line and sec > 0 and nettext(line):
            r = shot_rate(s)
            want = speech_sec(line, r)
            # 台词是下限不是等号：说完可以留白，但不能说不完
            if sec < want - 0.35:
                warns.append("镜头 %s 说不完：%d 字 + 停顿需要 %.1fs，只给了 %.1fs"
                             % (sid, len(nettext(line)), want, sec))
            elif sec > want + 3.0:
                warns.append("镜头 %s 可能拖：台词只要 %.1fs，给了 %.1fs（超过 3 秒的空白要有设计，写进 note）"
                             % (sid, want, sec))
        if not is_filled(s.get("visual")):
            warns.append("镜头 %s 的 visual 还是空模板" % sid)
        _lim = LIMITS.get(shtype)
        if shtype == "B":
            # 首选显式声明：这一镜要不要「读」里面的内容
            # 其次才靠 role + 关键词兜底（启发式永远不全，所以给了显式字段）
            _role = str(s.get("role") or "").lower()
            _read = s.get("read")
            if (_read is True
                    or (_read is None and _role in ("prove", "orient")
                        and READ_RE.search(str(s.get("visual") or "")))):
                _lim = 8.0                     # 证据阅读镜：留到观众看完为止
        if shtype == "T" and nettext(_raw_line):
            # 字卡按「读一遍」算：字数 ÷ 语速 + 1 秒落地；短语卡仍是 4 秒
            _lim = max(4.0, len(nettext(_raw_line)) / max(1.0, shot_rate(s)) + 1.0)
        # 时长一律取到 0.5 秒，判定必须留同量级容差，否则永远差 0.5 秒
        if _lim and sec > _lim + 0.5:
            if str(s.get("over_ok") or "").strip():
                over_ok.append((sid, shtype, sec, _lim))
            else:
                over_lim.append((sid, shtype, sec, _lim))
        # 单镜硬上限：逐镜式 8s（默认目标 6s）；打斗追逐 9s；字卡按读完算。
        # 单独报，避免和上面的 B/MG 上限重复报同一镜。
        _cap = 9.0 if utype == "打斗" else 8.0
        if shtype == "T" and _lim:
            _cap = max(_cap, _lim)
        if sec > _cap + 0.5 and not (_lim and sec > _lim + 0.5):
            if str(s.get("over_ok") or "").strip():
                over_ok.append((sid, shtype, sec, _cap))
            else:
                warns.append("镜头 %s 超过单镜硬上限 %.0fs（%.1fs）：默认目标是 6s，"
                             "超了先想是不是该拆；确有必要写 over_ok 说明理由"
                             % (sid, _cap, sec))
    # 连用三个同景别（视觉疲劳第一成因）
    # 连用三个同景别：要区分「同一机位的 PPT」和「不同机位的正反打」
    # 正反打必然是对切同景别（CU 对 CU），这是剧情片的合法语法，不该报
    _seq = [(str(x.get("id")), str(x.get("shot") or "").strip().upper(),
             str(x.get("camera") or "").strip(),
             len(nettext(str(x.get("line") or ""))),
             bool(x.get("hold_camera"))) for x in shots]
    _triple = []
    for i in range(2, len(_seq)):
        a, b, c = _seq[i - 2], _seq[i - 1], _seq[i]
        if not (a[1] and a[1] == b[1] == c[1]):
            continue
        # 形式要求锁机位的（横向滚动剖面、一镜到底拆拍），显式声明后豁免
        if all(x[4] for x in (a, b, c)):
            continue
        cams3 = {a[2], b[2], c[2]}
        # 机位有变化就是 coverage（正反打、动作三机位、越肩对切都算），放行
        # 只有「同一机位 + 同一景别」连续三次，才是真的 PPT
        if len(cams3) >= 2:
            continue
        _triple.append(_seq[i][0])
    if _triple:
        warns.append("%d 处连用三个同景别（%s）：视觉疲劳的第一成因，第三个必须换"
                     % (len(_triple), "、".join(_triple[:4])))

    # 每场的机位覆盖
    _by_ch = {}
    for x in shots:
        _c = str(x.get("chapter") or "").strip() or "（未分场）"
        _by_ch.setdefault(_c, set()).add(str(x.get("camera") or "").strip())
    # 按场分级：一场里的镜数少，机位自然少，不该一刀切要 3 个
    _need = {}
    for x in shots:
        c = str(x.get("chapter") or "").strip() or "（未分场）"
        _need[c] = _need.get(c, 0) + 1
    _thin = [c for c, v in _by_ch.items()
             if len(v - {""}) < 3 and _need.get(c, 0) >= 5]
    if _thin and len(shots) > 3:
        warns.append("这些场（≥5 镜）机位少于 3 个（%s）：没有 coverage，剪辑时没得选"
                     % "、".join(_thin[:3]))

    # 有台词镜占比：硬上限 70%（R02 / SKILL 环 3）；>85% 就是「一句一镜」
    _nl = sum(1 for x in shots
              if str(x.get("line") or "").strip() and not x.get("vo"))
    _ratio = _nl / max(1, len(shots))
    if _ratio > 0.70:
        if _ratio > 0.85:
            _why = "每镜都在扛一句台词，这就是「一句一镜」"
        else:
            _why = "超过 70% 上限：画面在给台词让路，B/MG 镜没有承担足够的视觉职责"
        warns.append("口播镜占 %.0f%%（%d/%d）：%s。健康值 60-70%% —— "
                     "台词按段算，画面按需要切，B/MG 镜的台词写成画外音（加 vo:true）"
                     % (_ratio * 100, _nl, len(shots), _why))

    # 视觉职责：RESET 占比过高 = 旁白讲事实、画面给氛围
    _roles = [str(x.get("role") or "").strip().lower() for x in shots]
    _marked = [r for r in _roles if r]
    if _marked:
        _n_reset = sum(1 for r in _marked if r == "reset")
        _rr = _n_reset / max(1, len(_marked))
        if _rr > 0.20 and _n_reset > 2:
            warns.append("RESET 镜占 %.0f%%（上限 20%%）：旁白在讲事实时画面给的是氛围，"
                         "观众听到的和看到的对不上。静音测试：这画面能验证那句话吗"
                         % (_rr * 100))
        _bad = [x.get("id") for x, r in zip(shots, _roles) if not r]
        if len(_bad) > len(shots) * 0.5:
            warns.append("%d 镜没标 role（SHOW/PROVE/ORIENT/RESET）：画面职责不明，"
                         "就无法判断「该给证据的地方有没有给证据」" % len(_bad))

    # 六栏覆盖率：R07 说六栏缺一栏就是没写完。工具不做逐镜硬报（老工程会全红），
    # 但任何一栏在 >30% 的 AI 镜里缺席，就说明这一栏没有被当成必填。
    _ai = [x for x in shots if str(x.get("type") or "A").strip().upper()
           not in ("MG", "T", "片头", "字卡")]
    if _ai:
        _missing = {}
        for col, label in (("move", "② 运镜及主体"), ("framing", "③ 构图"),
                           ("cut_at", "⑤ 切点"), ("sound", "⑥ 声效")):
            n = sum(1 for x in _ai if not str(x.get(col) or "").strip()
                    and not (col == "cut_at" and x.get("hold_camera")))
            if n > len(_ai) * 0.3:
                _missing[label] = n
        if _missing:
            warns.append("六栏覆盖不足（AI 镜 %d）：%s。缺的栏不是可选项，"
                         "R07 的六栏缺一栏就是没写完"
                         % (len(_ai), "、".join("%s 缺 %d" % (k, v)
                                               for k, v in _missing.items())))

    if over_ok:
        warns.append("%d 镜声明了超长理由（%s）：默认上限已放行，"
                     "请再确认一次理由成立，不是因为「台词长就给它长」"
                     % (len(over_ok), "、".join(x[0] for x in over_ok[:6])))
    if over_lim:
        extra = sum(x[2] - x[3] for x in over_lim)
        by_t = {}
        for _sid, _t, _sec, _lim in over_lim:
            a = by_t.setdefault(_t, [0, 0.0])
            a[0] += 1
            a[1] += _sec - _lim
        desc = "、".join("%s %d 镜多出 %.0f 秒" % (t, v[0], v[1]) for t, v in by_t.items())
        warns.append("动作/图解镜撑太长：%s（合计多出 %.0f 秒）。B 镜 1-2.5s、MG 1.5-4s；"
                     "超了多半是拿它撑台词 —— 台词回 A 镜，动作镜做 1-2 秒插入"
                     % (desc, extra))

    ai_shots = manual_shots = 0
    for s in shots:
        sid = s["id"]
        shtype = str(s.get("type") or "A").strip().upper()
        if shtype in ("MG", "T", "片头", "字卡"):
            manual_shots += 1          # 图解与字卡是后期手工，不该有 AI 提示词
            continue
        ai_shots += 1
        _sp = str((stills.get(sid) or {}).get("prompt") or "").strip()
        if "待重写" in _sp:
            if sid not in pending_prompts:
                pending_prompts.append(sid)   # 拆镜占位：单独报，不和「没写」混为一谈
        elif not is_filled(_sp):
            warns.append("镜头 %s 没有静帧提示词" % sid)
        _mo = motions.get(sid) or {}
        _mp = str(_mo.get("prompt") or "").strip()
        _ref = str(_mo.get("motion_ref") or "").strip()
        # 节拍式：一份整段提示词装多拍，后面几拍用 motion_ref 指过去
        _via = bool(_ref) and is_filled((motions.get(_ref) or {}).get("prompt"))
        if "待重写" in _mp:
            if sid not in pending_prompts:
                pending_prompts.append(sid)
        elif float(s.get("sec") or 0) >= 2 and not is_filled(_mp) and not _via:
            warns.append("镜头 %s 没有运动提示词（>=2s 应当生成视频）" % sid)
        elif float(s.get("sec") or 0) >= 3:
            _sec = float(s.get("sec") or 0)
            _min = 380 if _sec >= 5 else 250          # R45：5-6s 380-500，3-4s ≥250
            if len(_mp) < _min:
                warns.append("镜头 %s 运动提示词只有 %d 字（%.0fs 镜标准 ≥%d 字）："
                             "2.5 不推理，漏写就是没有；按 R45 补动作四层/环境动态/光照六项"
                             % (sid, len(_mp), _sec, _min))

    if pending_prompts:
        warns.append("%d 镜的提示词是拆镜占位（%s）：拆完必须重写，不能直接生成"
                     % (len(pending_prompts), "、".join(pending_prompts[:8])))

    dur = str(m.get("duration") or "")
    dur_s = None
    if dur:
        hits = re.findall(r"(\d+)\s*分(?:钟)?\s*(\d+)?\s*秒", dur)
        if hits:
            a, b = hits[-1]
            dur_s = int(a) * 60 + int(b or 0)
        else:
            hits = re.findall(r"(\d+(?:\.\d+)?)\s*(?:秒|s)", dur)
            if hits:
                dur_s = float(hits[-1])
    if dur_s and total > 0 and abs(dur_s - total) > max(3.0, total * 0.1):
        warns.append("单元登记的时长 %s（约 %.0fs）与分镜合计 %.1fs 差得多" % (dur, dur_s, total))

    pack = {
        "schema": 1,
        "generated": now(),
        "project": {"name": st["meta"].get("name", ""), "season": st["meta"].get("season", "")},
        "unit": {"id": uid, "type": m.get("type", ""), "entry": m.get("entry", ""),
                 "platform": m.get("platform", ""), "duration": dur, "evidence": ev},
        "assets": assets,
        "shots": shots,
        "prompts": {"stills": pr.get("stills") or [], "motions": pr.get("motions") or []},
        "totals": {"shots": len(shots), "sec": round(total, 2),
                   "ai_shots": ai_shots, "manual_shots": manual_shots,
                   "stills": len(stills), "motions": len(motions),
                   "stills_done": sum(1 for s in shots if is_filled((stills.get(s["id"]) or {}).get("prompt"))),
                   "rate": rate},
        "adapters": st.get("adapters") or {},
        "warnings": warns,
    }
    return pack, warns


def keyframe_ids(udir):
    """已有白模的镜号集合。集级 keyframes/、production 分镜、旧 segments/ 都算。"""
    udir = Path(udir)
    got = set()
    kf = udir / "keyframes"
    if kf.is_dir():
        got |= {p.stem for p in kf.glob("*.png")}
    prod = udir / "production"
    if prod.is_dir():
        for k in prod.glob("*/*/keyframes"):
            if k.is_dir():
                got |= {p.stem for p in k.glob("*.png")}
    segs = udir / "segments"
    if segs.is_dir():
        for d in sorted(segs.iterdir()):
            k = d / "keyframes"
            if d.is_dir() and k.is_dir():
                got |= {p.stem for p in k.glob("*.png")}
    return got


def segment_drift(root, uid):
    """段内 shots.json 是建段时的副本，不参与生成。与总表不一致必须报出来，防止静默分叉。"""
    udir = Path(root) / "units" / uid
    total = {str(s.get("id")).zfill(3): s
             for s in (jload(udir / "storyboard" / "shots.json", {}) or {}).get("shots") or []}
    segs = udir / "segments"
    bad = []
    if not segs.is_dir():
        return bad
    for d in sorted(segs.iterdir()):
        f = d / "shots.json"
        if not d.is_dir() or not f.exists():
            continue
        for s in (jload(f, {}) or {}).get("shots") or []:
            sid = str(s.get("id")).zfill(3)
            t = total.get(sid)
            if not t:
                bad.append("%s/%s 镜号在总表里不存在" % (d.name, sid))
                continue
            diff = [k for k in ("sec", "line", "type", "camera")
                    if str(t.get(k) or "") != str(s.get(k) or "")]
            if diff:
                bad.append("%s 的 %s 与总表不一致（%s），改总表后重跑该段" %
                           (d.name, sid, "、".join(diff)))
    return bad[:6]


# ---------------------------------------------------------------- 生产层（production）
# 新结构：units/<集>/production/<场>/<分镜>/
#   场索引 scene.json、分镜提示词 storyboard.json 是手写来源；
#   分镜镜头快照 shots.json 由 d board sync 生成，不要手改。

def _safe_name(s, limit=60):
    s = "".join(ch for ch in str(s or "")
                if ch not in '\\/:*?"<>|' and ord(ch) >= 32).strip()
    return s[:limit].strip(" .") or ""


def _prod_base(root, uid):
    return Path(root) / "units" / uid / "production"


def _prod_index(root, uid):
    p = _prod_base(root, uid) / "index.json"
    d = jload(p, {}) or {}
    rows = d.get("scenes") if isinstance(d.get("scenes"), list) else []
    return p, [r for r in rows if isinstance(r, dict)]


def _scene_dir_name(scene):
    sid = str(scene.get("id") or "")
    name = _safe_name(scene.get("name") or scene.get("chapter") or "")
    return (sid + "-" + name) if name and name != sid else sid


def _find_prod_scene(root, uid, scene_id):
    """找 production/<场>/。索引优先，其次 SCxx-名字，最后裸 SCxx。"""
    base = _prod_base(root, uid)
    if not base.is_dir():
        return None
    _p, idx = _prod_index(root, uid)
    for row in idx:
        if str(row.get("id")) == str(scene_id):
            d = base / str(row.get("dir") or "")
            if d.is_dir():
                return d
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.startswith(str(scene_id) + "-"):
            return d
    d = base / str(scene_id)
    return d if d.is_dir() else None


def _write_prod_index(root, uid, scenes, dirs):
    """production/index.json：场号 → 目录。工作台靠它找场目录。"""
    rows = []
    for sc in scenes:
        sid = str(sc.get("id") or "")
        d = dirs.get(sid)
        if not d:
            continue
        rows.append({"id": sid, "name": sc.get("name") or "",
                     "dir": d.name})
    p = _prod_base(root, uid) / "index.json"
    jdump(p, {"unit": uid, "_generated": True,
              "_source": "storyboard/scenes.json",
              "_note": "场索引，由 d board sync 重写，不要手改。",
              "scenes": rows})
    return p


def _scene_index_doc(uid, scene, storyboard_ids, order):
    return {
        "id": str(scene.get("id") or ""),
        "name": str(scene.get("name") or ""),
        "chapter": str(scene.get("chapter") or ""),
        "order": order,
        "shots": [str(x).zfill(3) for x in (scene.get("shots") or [])],
        "storyboards": list(storyboard_ids),
        "status": "未生成",
        "_generated": True,
        "_source": "storyboard/scenes.json",
        "_note": "场索引，由 d board sync 重写，不要手改。",
    }


def _storyboard_ids(root, uid, force_total=None):
    """production 下所有分镜：{seg: (dir, scene_id)}。"""
    base = _prod_base(root, uid)
    out = {}
    if not base.is_dir():
        return out
    for sdir in sorted(base.iterdir()):
        if not sdir.is_dir() or sdir.name.startswith((".", "_")):
            continue
        for sbdir in sorted(sdir.iterdir()):
            if not sbdir.is_dir() or sbdir.name.startswith((".", "_")):
                continue
            sj = jload(sbdir / "storyboard.json", {}) or {}
            if not sj and not (sbdir / "libtv.json").exists():
                continue
            sid = str(sj.get("id") or sbdir.name)
            scene = str(sj.get("scene") or "")
            if not scene:
                m = re.match(r"^(SC\d+)", sdir.name)
                scene = m.group(1) if m else ""
            out[sid] = (sbdir, scene)
    return out


def _sync_storyboard_shots(root, uid, sbdir, total, prune=False, dry_run=False):
    sj = jload(sbdir / "storyboard.json", {}) or {}
    ids = [str(x).zfill(3) for x in (sj.get("shots") or [])]
    missing = [x for x in ids if x not in total]
    removed = []
    sj_changed = False
    if prune and missing:
        keep = [x for x in ids if x in total]
        if keep != ids:
            removed = [x for x in ids if x not in total]
            sj_changed = True
            if not dry_run:
                try:
                    shutil.copy2(str(sbdir / "storyboard.json"),
                                 str(sbdir / "storyboard.json.bak"))
                except Exception:
                    pass
                sj["shots"] = keep
                jdump(sbdir / "storyboard.json", sj)
            ids = keep
    rows = [total[x] for x in ids if x in total]
    snap = {
        "storyboard": str(sj.get("id") or sbdir.name),
        "unit": uid,
        "shots": rows,
        "total_sec": round(sum(float(s.get("sec") or 0) for s in rows), 1),
        "_generated": True,
        "_source": "storyboard/shots.json",
        "_note": "镜头快照，由 d board sync 重写，不要手改。",
    }
    p = sbdir / "shots.json"
    old = jload(p, None) or {}
    old_rows = {str(x.get("id")).zfill(3): x
                for x in (old.get("shots") or [])
                if isinstance(x, dict) and x.get("id")}
    stale = [x for x in ids
             if x in total and x in old_rows
             and _shot_sig(old_rows[x]) != _shot_sig(total[x])]
    changed = old != snap
    if changed and not dry_run:
        jdump(p, snap)
    return changed, missing, stale, removed, sj_changed


SHOT_SIG_FIELDS = (
    "type", "sec", "shot", "angle", "camera", "move", "framing",
    "visual", "action", "line", "vo", "read", "fx", "transition",
    "interp", "end_frame", "morph",
)


def _shot_sig(row):
    return tuple(str((row or {}).get(k) or "") for k in SHOT_SIG_FIELDS)


def _shot_id_of_file(name):
    m = re.match(r"^(\d{3})", str(name or ""))
    return m.group(1) if m else ""


def _archive_shot_media(root, base, shot_ids, tag, dry_run=False):
    """把属于这些镜号的旧生成物移到 base/_archive/<tag>/，不直接删除。"""
    base = Path(base)
    shot_ids = {str(x).zfill(3) for x in shot_ids if x}
    if not shot_ids or not base.is_dir():
        return []
    moved = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if "_archive" in p.parts:
            continue
        sid = _shot_id_of_file(p.name)
        if sid not in shot_ids:
            continue
        rel = p.relative_to(base)
        dst = base / "_archive" / tag / rel
        moved.append(p)
        if dry_run:
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = dst.with_name(dst.stem + "-%d" % int(time.time()) + dst.suffix)
            try:
                shutil.move(str(p), str(dst))
            except Exception:
                shutil.copy2(str(p), str(dst))
                p.unlink()
        except Exception:
            pass
    return moved


def _prune_prompts(udir, drop_ids, dry_run=False):
    """删掉总表已不存在/内容已变镜号的静帧与运动提示词。"""
    drop_ids = {str(x).zfill(3) for x in drop_ids if x}
    if not drop_ids:
        return 0
    p = udir / "prompts" / "prompts.json"
    doc = jload(p, None)
    if not isinstance(doc, dict):
        return 0
    n = 0
    for key in ("stills", "motions"):
        rows = [x for x in (doc.get(key) or [])
                if isinstance(x, dict) and str(x.get("shot")).zfill(3) not in drop_ids]
        n += len(doc.get(key) or []) - len(rows)
        doc[key] = rows
    if n and not dry_run:
        jdump(p, doc)
    return n


def _prune_review(udir, drop_ids, dry_run=False):
    drop_ids = {str(x).zfill(3) for x in drop_ids if x}
    if not drop_ids:
        return 0
    p = udir / "review" / "shot-review.json"
    doc = jload(p, None)
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return 0
    rows = [x for x in doc["rows"]
            if not (isinstance(x, dict) and
                    str(x.get("shot")).zfill(3) in drop_ids)]
    n = len(doc["rows"]) - len(rows)
    if n and not dry_run:
        doc["rows"] = rows
        jdump(p, doc)
    return n


def production_drift(root, uid):
    """production 分镜的快照 / 场景索引 / 镜号归属漂移。"""
    udir = Path(root) / "units" / uid
    total = {str(s.get("id")).zfill(3): s
             for s in (jload(udir / "storyboard" / "shots.json", {}) or {}).get("shots") or []}
    scenes = (jload(udir / "storyboard" / "scenes.json", {}) or {}).get("scenes") or []
    bad = []
    ids = _storyboard_ids(root, uid)
    seen = {}
    for seg, (sbdir, scene_id) in ids.items():
        sj = jload(sbdir / "storyboard.json", {}) or {}
        shots = [str(x).zfill(3) for x in (sj.get("shots") or [])]
        if not shots:
            bad.append("%s 还没分配镜号（storyboard.json.shots 为空）" % seg)
        for sid in shots:
            if sid not in total:
                bad.append("%s 的 %s 在总表里不存在（跑 d board sync 清理）" % (seg, sid))
            if sid in seen and seen[sid] != seg:
                bad.append("镜 %s 同时属于 %s 和 %s：一镜只能归一个分镜" %
                           (sid, seen[sid], seg))
            seen[sid] = seg
        snap = jload(sbdir / "shots.json", None)
        if not isinstance(snap, dict) or snap.get("_generated") is not True:
            bad.append("%s/shots.json 不是生成物（缺 _generated）" % seg)
        else:
            for s in snap.get("shots") or []:
                sid = str(s.get("id")).zfill(3)
                t = total.get(sid)
                if not t:
                    continue
                diff = [k for k in ("sec", "line", "type", "camera")
                        if str(t.get(k) or "") != str(s.get(k) or "")]
                if diff:
                    bad.append("%s/%s 快照与总表不一致（%s）" % (seg, sid, "、".join(diff)))
                break          # 每个分镜报一条就够，避免刷屏
        if scene_id:
            sc = next((x for x in scenes if str(x.get("id")) == scene_id), None)
            if sc is None:
                bad.append("%s 的 scene=%s 在 scenes.json 里不存在" % (seg, scene_id))
    # 场索引是否落后
    base = _prod_base(root, uid)
    if base.is_dir():
        for sdir in sorted(base.iterdir()):
            if not sdir.is_dir() or sdir.name.startswith((".", "_")):
                continue
            sj = jload(sdir / "scene.json", {}) or {}
            sid = str(sj.get("id") or "")
            if not sid:
                m = re.match(r"^(SC\d+)", sdir.name)
                sid = m.group(1) if m else ""
            sc = next((x for x in scenes if str(x.get("id")) == sid), None)
            if sc is None:
                continue
            want_shots = [str(x).zfill(3) for x in (sc.get("shots") or [])]
            want_sbs = sorted(seg for seg, (d, _s) in ids.items() if d.parent == sdir)
            diff = []
            if str(sj.get("name") or "") != str(sc.get("name") or ""):
                diff.append("name")
            if str(sj.get("chapter") or "") != str(sc.get("chapter") or ""):
                diff.append("chapter")
            if sorted(str(x).zfill(3) for x in (sj.get("shots") or [])) != sorted(want_shots):
                diff.append("shots")
            if sorted(sj.get("storyboards") or []) != want_sbs:
                diff.append("storyboards")
            if diff:
                bad.append("production/%s/scene.json 与 scenes.json 不一致（%s），跑 d board sync" %
                           (sdir.name, "、".join(diff)))
    return bad[:10]


def _ensure_unit_dirs(udir):
    for rel in ("script/draft", "script/derived", "storyboard", "prompts",
                "production", "renders", "edit", "review", "keyframes",
                "deliver/final", "deliver/preview",
                "deliver/publish/douyin", "deliver/publish/bilibili"):
        (udir / rel).mkdir(parents=True, exist_ok=True)


def cmd_unique(args):
    """唯一性：镜号 / 场号 / 分镜号 / 资产 ID / 单元号 / 输出槽 / 生成物。"""
    root = Path(args.cwd or ".").resolve()
    st = load_state(root)
    errs = []
    # 单元号
    for uid in st.get("units") or {}:
        if not (root / "units" / uid).is_dir():
            errs.append("单元 %s 已登记但目录不存在" % uid)
    # 资产 ID
    seen_asset = {}
    for typ, adir in asset_dirs(root):
        m = jload(adir / "manifest.json", {}) or {}
        aid = str(m.get("id") or id_of(adir.name))
        if aid in seen_asset:
            errs.append("资产 ID 重复：%s（%s / %s）" % (aid, seen_asset[aid], adir.name))
        seen_asset[aid] = adir.name
        pre = {"characters": "CH", "scenes": "SC", "props": "PR",
               "brand": "BR", "style": "ST"}.get(typ, "")
        if pre and not aid.startswith(pre + "-"):
            errs.append("资产 %s 的前缀不是 %s" % (aid, pre))
    for uid in st.get("units") or {}:
        udir = root / "units" / uid
        scenes = (jload(udir / "storyboard" / "scenes.json", {}) or {}).get("scenes") or []
        shots = (jload(udir / "storyboard" / "shots.json", {}) or {}).get("shots") or []
        sids, shot_ids = set(), set()
        for sc in scenes:
            sid = str(sc.get("id") or "")
            if sid in sids:
                errs.append("%s 场号重复：%s" % (uid, sid))
            sids.add(sid)
        for s in shots:
            sid = str(s.get("id") or "").zfill(3)
            if sid in shot_ids:
                errs.append("%s 镜号重复：%s" % (uid, sid))
            shot_ids.add(sid)
        # 分镜号唯一 + 镜头只归属一个分镜
        owner = {}
        for seg, (sbdir, _scene) in sorted(_storyboard_ids(root, uid).items()):
            sj = jload(sbdir / "storyboard.json", {}) or {}
            ids = [str(x).zfill(3) for x in (sj.get("shots") or [])]
            for sid in ids:
                if sid in owner and owner[sid] != seg:
                    errs.append("%s 镜 %s 同时归 %s 和 %s" % (uid, sid, owner[sid], seg))
                owner[sid] = seg
            snap = jload(sbdir / "shots.json", None)
            if isinstance(snap, dict) and snap.get("_generated") is not True:
                errs.append("%s/shots.json 缺少 _generated，可能被手改" % seg)
        # 只有一份定稿剧本
        finals = []
        for p in (udir / "script").rglob("*.md"):
            try:
                head = p.read_text(encoding="utf-8")[:600]
            except Exception:
                continue
            if re.search(r"^status:\s*final\s*$", head, re.M):
                finals.append(p.relative_to(udir).as_posix())
        if len(finals) > 1:
            errs.append("%s 有多份 status: final 剧本：%s" % (uid, "、".join(finals)))
    if errs:
        out("")
        out("唯一性体检：%d 条" % len(errs))
        for e in errs:
            out("  - " + e)
        out("")
        return 1
    out("")
    out("唯一性体检：全部通过")
    out("")
    return 0

def cmd_segment(args):
    """分镜（生成单元）：新架构落 production/<场>/<分镜>/，SEG-* 保留旧 segments/。"""
    root = Path(args.cwd or ".").resolve()
    st = load_state(root)
    uid = unit_id(st, args.unit)
    udir = root / "units" / uid
    if args.s_cmd == "list":
        rows = []
        for seg, (sbdir, scene) in sorted(_storyboard_ids(root, uid).items()):
            sj = jload(sbdir / "storyboard.json", {}) or {}
            rows.append((seg, scene, float(sj.get("sec") or 0),
                         len(sj.get("shots") or []),
                         bool(str(sj.get("prompt") or "").strip()),
                         sbdir))
        segs = udir / "segments"
        if segs.is_dir():
            for d in sorted(segs.iterdir()):
                if d.is_dir() and re.match(r"^SEG-\d+", d.name):
                    m = jload(d / "manifest.json", {}) or {}
                    rows.append((d.name, "", float(m.get("sec") or 0),
                                 len(m.get("shots") or []), False, d))
        if not rows:
            out("还没有分镜。d segment new SC01-01 --scene SC01")
            return 0
        out("")
        out("分镜 · %s" % uid)
        for seg, scene, sec, n, has_prompt, d in rows:
            out("  %-10s %-5s %5.1fs  %2d 镜  %s  %s" % (
                seg, scene or "-", sec, n,
                "有词" if has_prompt else "  - ", d.relative_to(root).as_posix()))
        out("")
        return 0

    seg = str(args.seg or "").strip().upper()
    if not seg:
        die("用法：d segment new SC01-01 --scene SC01")
    prod = bool(re.match(r"^SC\d+-\d+$", seg))
    if not prod and not re.match(r"^SEG-\d+", seg):
        die("分镜号形如 SC01-01（新架构）或 SEG-01（旧段）")

    if args.s_cmd == "check":
        sbdir = None
        if prod:
            hit = _storyboard_ids(root, uid).get(seg)
            sbdir = hit[0] if hit else None
        else:
            p = udir / "segments" / seg
            sbdir = p if p.is_dir() else None
        if not sbdir:
            die("没有这个分镜：%s" % seg)
        sj = (jload(sbdir / "storyboard.json", {}) or
              jload(sbdir / "manifest.json", {}) or {})
        total = {str(s.get("id")).zfill(3): s
                 for s in (jload(udir / "storyboard" / "shots.json", {}) or {}).get("shots") or []}
        shot_ids = [str(x).zfill(3) for x in (sj.get("shots") or [])]
        shot_sec = sum(float((total.get(x) or {}).get("sec") or 0) for x in shot_ids)
        sec = float(sj.get("sec") or 0) or shot_sec
        lib = jload(sbdir / "libtv.json", {}) or {}
        errs, warns = [], []
        if not sec:
            errs.append("秒数是 0：storyboard.sec 和镜号都没填")
        if sec > 15.5:
            errs.append("分镜 %.1fs 超过单次生成上限 15s，拆成两个" % sec)
        if sec and sec < 5.0:
            warns.append("只有 %.1fs，和相邻分镜合并更划算" % sec)
        if not str(sj.get("purpose") or "").strip():
            warns.append("purpose 没写：这一分镜要完成什么")
        if not str(sj.get("prompt") or "").strip():
            warns.append("分镜提示词是空的")
        for x in shot_ids:
            if x not in total:
                errs.append("镜 %s 在总表里不存在" % x)
        kf = sbdir / "keyframes"
        kfn = len(list(kf.glob("*.png"))) if kf.is_dir() else 0
        refs = len(lib.get("refs") or [])
        out("")
        out("分镜体检 · %s / %s" % (uid, seg))
        out("  时长 %.1fs ｜ %d 镜 ｜ 白模 %d 张 ｜ 参考图 %d 张 ｜ LibTV %s" % (
            sec, len(shot_ids), kfn, refs, lib.get("status") or "未连线"))
        for e in errs:
            out("  X " + e)
        for w in warns:
            out("  ! " + w)
        if not errs and not warns:
            out("  全部通过")
        out("")
        return 1 if errs else 0

    # new
    if prod:
        m = re.match(r"^(SC\d+)-(\d+)$", seg)
        scene_id = str(args.scene or m.group(1)).upper()
        if not re.match(r"^SC\d+$", scene_id):
            die("场号形如 SC01")
        doc = jload(udir / "storyboard" / "scenes.json", {}) or {}
        scenes = [x for x in (doc.get("scenes") or []) if isinstance(x, dict)]
        scene = next((x for x in scenes if str(x.get("id")) == scene_id), None)
        sdir = _find_prod_scene(root, uid, scene_id)
        if sdir is None:
            name = (scene or {}).get("name") or (scene or {}).get("chapter") or ""
            sdir = _prod_base(root, uid) / _scene_dir_name({"id": scene_id, "name": name})
            sdir.mkdir(parents=True, exist_ok=True)
        if scene is None:
            scene = {"id": scene_id, "name": "", "chapter": "", "shots": []}
        sbdir = sdir / seg
        if sbdir.exists():
            die("分镜已存在：%s" % sbdir.relative_to(root).as_posix())
        for sub in ("refs", "renders", "keyframes"):
            (sbdir / sub).mkdir(parents=True, exist_ok=True)
        sj = {
            "id": seg, "scene": scene_id,
            "name": args.title or "分镜", "title": args.title or "",
            "sec": float(args.sec or 0), "shots": [], "prompt": "",
            "status": "未生成", "purpose": "", "emotion": "",
            "cast": scene.get("cast") or [], "assets": scene.get("assets") or [],
            "_note": "提示词写 prompt；shots 填镜号后跑 d board sync。",
        }
        jdump(sbdir / "storyboard.json", sj)
        jdump(sbdir / "shots.json", {
            "storyboard": seg, "unit": uid, "shots": [], "total_sec": 0.0,
            "_generated": True, "_source": "storyboard/shots.json",
            "_note": "镜头快照，由 d board sync 重写，不要手改。",
        })
        lib = json.loads(json.dumps(PRODUCTION_LIBTV_TPL))
        lib["duration_sec"] = float(args.sec or 0)
        jdump(sbdir / "libtv.json", lib)
        # 场索引 / index.json
        sbs = sorted({x for x in (jload(sdir / "scene.json", {}) or {}).get("storyboards") or []}
                     | {seg})
        jdump(sdir / "scene.json",
              _scene_index_doc(uid, scene, sbs, scenes.index(scene) if scene in scenes else 0))
        _p, idx = _prod_index(root, uid)
        dirs = {str(r.get("id")): (_prod_base(root, uid) / str(r.get("dir")))
                for r in idx if r.get("dir")}
        dirs[scene_id] = sdir
        index_scenes = list(scenes)
        if scene not in index_scenes:
            index_scenes.append(scene)
        _write_prod_index(root, uid, index_scenes, dirs)
        out("+ %s/（storyboard / shots / libtv / refs / renders / keyframes）" %
            sbdir.relative_to(root).as_posix())
        out("  场：production/%s" % sdir.name)
        out("")
        out("下一步：填 storyboard.json 的 shots 和 prompt，然后 d board sync")
        return 0

    # 旧段 SEG-*
    sdir = udir / "segments" / seg
    if sdir.exists():
        die("段已存在：%s" % sdir.relative_to(root).as_posix())
    (sdir / "keyframes").mkdir(parents=True, exist_ok=True)
    jdump(sdir / "manifest.json", {
        "id": seg, "title": args.title or "", "purpose": "", "emotion": "",
        "sec": float(args.sec or 0), "hard": False, "shots": [], "assets": [],
        "keyframe_shots": [], "refs_budget": {"characters": 2, "scene": 1,
                                              "first_frame": 1, "keyframes": 3,
                                              "props": 1},
        "next_from": "",
    })
    jdump(sdir / "shots.json", {"segment": seg, "shots": []})
    lib = json.loads(json.dumps(PRODUCTION_LIBTV_TPL))
    lib["duration_sec"] = float(args.sec or 0)
    jdump(sdir / "libtv.json", lib)
    full = jload(udir / "segments" / "full.json", {}) or {}
    full.setdefault("unit", uid)
    rows = [x for x in (full.get("segments") or []) if x.get("id") != seg]
    rows.append({"id": seg, "title": args.title or "", "sec": float(args.sec or 0)})
    full["segments"] = sorted(rows, key=lambda x: str(x.get("id")))
    jdump(udir / "segments" / "full.json", full)
    out("+ units/%s/segments/%s/（旧段结构；新工程请用 SC01-01）" % (uid, seg))
    out("")
    return 0



def cmd_check(args):
    root = Path(args.cwd or ".").resolve()
    st = load_state(root)
    uids = [unit_id(st, args.unit)] if args.unit else list(st["units"])
    if not uids:
        out("还没有单元。d unit new S1-E01")
        return 0
    bad = 0
    for uid in uids:
        pack, warns = build_pack(root, uid, st)
        t = pack["totals"]
        out("")
        out("填充体检 · %s（%s）" % (uid, pack["unit"]["type"]))
        out("=" * 56)
        out("  镜头 %-3d（AI %d / 手工 %d）合计 %6.1fs ｜ 静帧 %d/%d ｜ 运动 %d ｜ 资产引用 %d"
            % (t["shots"], t["ai_shots"], t["manual_shots"], t["sec"],
               t["stills_done"], t["ai_shots"], t["motions"],
               sum(len(v) for v in pack["assets"].values())))
        cur = cur_station(st["units"][uid])
        done = sum(1 for n in route_of(st["units"][uid])
                   if st["units"][uid]["stations"].get(str(n), {}).get("status") == "done")
        out("  进度：当前站 %s %s ｜ 已完成 %d/%d 站" % (sn(cur), station(cur)[1], done,
                                                     len(route_of(st["units"][uid]))))
        kfn = len(keyframe_ids(root / "units" / uid))
        rvfile = root / "units" / uid / "review" / "shot-review.json"
        out("  验镜：深度白模 %d 张 ｜ 审片结论 %s" % (kfn, "已导入" if rvfile.exists() else "未导入"))
        warns = list(warns) + segment_drift(root, uid)
        warns = list(warns) + production_drift(root, uid)
        _scenes, _sbad = load_scenes(root, uid)
        warns = list(warns) + _sbad
        if not warns:
            out("  [OK] 没有发现问题")
        else:
            bad += 1
            out("  [!!] %d 条：" % len(warns))
            for w in warns:
                out("       - " + w)
    out("")
    out("结论：%d 个单元里 %d 个还有问题。全部干净才能进站 8 批量生成。" %
        (len(uids), bad))
    out("  （有问题时退出码 1；脚本/Agent 可以直接用 d check 做门禁）" if bad else "")
    out("")
    return 1 if bad else 0


# ---------------------------------------------------------------- 引擎适配层

def status_block(root, st):
    lines = []
    uid = st["meta"].get("active_unit")
    if uid and uid in st["units"]:
        u = st["units"][uid]
        cur = cur_station(u)
        r = route_of(u)
        lines.append("[%s · %s线 · 当前站 %s %s]" % (uid, u.get("type", ""), sn(cur), station(cur)[1]))
        for n, name, _, _, _ in STATIONS:
            s = u["stations"].get(str(n), {})
            if n not in r:
                icon, tail = ICON["skipped"], "跳过（不在本片型路线）"
            else:
                stt = s.get("status", "pending")
                icon = ICON["current"] if n == cur and stt == "pending" else ICON.get(stt, "..")
                if stt == "done":
                    tail = s.get("artifact") or "已完成"
                    gm = s.get("gate") or []
                    ok = sum(1 for i in gm if i.get("ok"))
                    if not gm:
                        tail += "　!! 门禁未逐条勾（force / skip 过的，等于没验）"
                    elif ok < len(gm):
                        tail += "　!! 门禁只勾了 %d/%d" % (ok, len(gm))
                elif n == cur:
                    gm = s.get("gate", [])
                    tail = "门禁 %d/%d" % (sum(1 for i in gm if i.get("ok")), len(gm))
                else:
                    tail = ""
            lines.append("%s %s %s %s" % (icon, dpad(n, 2, "right"), dpad(name, 8), tail))
        lines.append("")
        lines.append("下一步唯一动作：" + (u.get("next_action") or "未设定，用 d note 写"))
    else:
        lines.append("（还没有单元。d unit new S1-E01）")

    rows = list_assets(root)
    lines.append("")
    if rows:
        lines.append("资产（项目级，所有单元共用）")
        for typ, _, cn, _ in ASSET_TYPES:
            for x in [y for y in rows if y["type"] == typ]:
                lines.append("  %-9s %s %s 用于 %s" % (
                    x["id"], dpad(x["name"], 18), dpad(x["status"], 14),
                    "、".join(x["manifest"].get("used_by") or []) or "-"))
    else:
        lines.append("资产：还没有（d asset new scenes 演播室）")
    return "\n".join(lines)


def _station_arg(v):
    """允许 6 / 5.6 两种写法；整数站仍返回 int，保持老状态文件键位不变"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        die("站号要写成 6 或 5.6")
    return int(f) if f.is_integer() else f


def station_rows():
    return "\n".join("| %s | %s | %s | %s |" % (sn(n), name, q, art)
                     for n, name, q, art, _ in STATIONS)


def cross_rows():
    return "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(CROSS_GATES))


def asset_table(root):
    rows = list_assets(root)
    if not rows:
        return "_还没有资产。先 `d asset new scenes 演播室`，再 `d asset new characters 江寻`。_"
    lines = ["| 类型 | ID | 名字 | 状态 | 用于 |", "| :--- | :--- | :--- | :--- | :--- |"]
    for typ, _, cn, _ in ASSET_TYPES:
        for r in [x for x in rows if x["type"] == typ]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                cn, r["id"], r["name"], r["status"],
                "、".join(r["manifest"].get("used_by") or []) or "-"))
    return "\n".join(lines)


def render_director_md(root, st):
    try:
        block = status_block(root, st)
        table = asset_table(root)
    except SystemExit:
        block, table = "（状态读取失败）", "（资产读取失败）"
    return DIRECTOR_MD_TPL.format(
        name=st["meta"]["name"], begin=MARK_BEGIN, end=MARK_END,
        status_block=block, asset_table=table,
        station_rows=station_rows(), cross_rows=cross_rows(), versioning=VERSIONING_MD)


def replace_block(text, begin, end, content):
    i, j = text.find(begin), text.find(end)
    if i < 0 or j < 0:
        return None
    return text[:i + len(begin)] + "\n" + content + "\n" + text[j:]


def refresh_director_md(root, st):
    p = Path(root) / DIRECTOR_MD
    if not p.exists():
        p.write_text(render_director_md(root, st), encoding="utf-8", newline="\n")
        return
    text = p.read_text(encoding="utf-8")
    new = replace_block(text, MARK_BEGIN, MARK_END, status_block(root, st))
    if new is None:
        p.write_text(render_director_md(root, st), encoding="utf-8", newline="\n")
        return
    new2 = replace_block(new, "<!-- director:assets:begin -->", "<!-- director:assets:end -->",
                         asset_table(root))
    p.write_text(new2 if new2 else new, encoding="utf-8", newline="\n")


VERSIONING_MD_TPL = """\
# 版本纪律 · {name}

> 解决一个具体问题：同一个东西改出 v1 / v2 / v8，最后不知道哪个是现行版，也没法回退。
> 规则只有一条：**版本是 git 的事，不是文件名的事。**

## 一、三条硬规矩

1. **文件名不带版本号。** 不写 `口播草案-v8.md`、`江寻-演播室-v3-TapNow提示词.md`、`最终版2.md`。
2. **一改一提交。** 改完立刻 `d save -m "..."`。
3. **旧版不删，多留一手。** 被取代的文件移进 `legacy/`，头部写一行
   `> 已被 <新文件> 取代（{today}），仅作历史参考，不再更新。`

## 二、提交信息

```
[站N] 动词: 改了什么        动词四选一：写 / 改 / 拆 / 并
[资产] 动词: 改了哪个资产   角色 / 场景 / 道具 / 风格 的改动
[工程] 动词: 改脚手架本身
```

`d save` 自动加 `[站N]`。为什么按站记？台词一动，站 3 时长、站 5 试听句、站 6 逐镜时长、
站 7 提示词、站 8 视频时长全部重算。按站看历史，一眼知道动了谁的下游。

**资产改动的影响面比台词更大**：改一次场景，全季所有带环境的镜头都要重跑。
所以资产改动单独用 `[资产]` 前缀，别混在某一集里。

## 三、标签

| 什么时候 | 标签 |
| :--- | :--- |
| 资产定稿（角色定妆 / 场景母图） | `stage00-assets` |
| 口播定稿 | `stage02-v1` |
| 分镜定稿 | `stage06-v1` |
| 成片 | `stage09-v1` |

取回：`git show stage02-v1:units/S1-E01/script/口播.md`

## 四、分支

- `main` 永远可发片。
- 实验走 `exp/一句话`，成了合并，不成 `git branch -D`。
- 不许 force push 已共享分支。

## 五、什么不进仓库

`.workbuddy/tmp/`、`__pycache__`、`node_modules`、`.venv`、单个 >50MB 的工程/视频文件。
其余全进，**包括锚点图、定妆图、色卡、space.json**。素材可复现是产线的一部分。
"""


# ---------------------------------------------------------------- 预览 / 评分

def _bar(v, width=18):
    n = int(round(max(0, min(100, v)) / 100.0 * width))
    return "#" * n + "." * (width - n)

def speech_sec(line, rate):
    """一句话实际需要的秒数：纯口播 + 句读停顿。

    只算「字数÷语速」会系统性偏短 —— 标点和句间停顿没算进去，配音一定对不上。
    停顿参考：逗号/顿号 0.2s，句号/问号/感叹号 0.45s。
    """
    txt = nettext(line)
    n = len(txt)
    if not n or rate <= 0:
        return 0.0
    comma = len(re.findall(r"[，,、；;：:]", line or ""))
    period = len(re.findall(r"[。！？!?]", line or ""))
    return n / rate + comma * 0.2 + period * 0.45


LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def _has_ref(root, aid):
    """资产有没有参考图。角色/场景/道具没有图就不能生成，与 level 字段无关。

    .gitkeep 和 0 字节占位不算图 —— 这是最容易蒙过检查的一处。
    """
    for d in Path(root).glob("assets/*/%s-*" % aid):
        rf = d / "refs"
        if not rf.is_dir():
            continue
        for x in rf.iterdir():
            if x.is_file() and not x.name.startswith(".") and x.stat().st_size > 0:
                return True
    return False


def _level(r):
    """资产等级：L0-L4。没写 level 的返回 None（按老口径只查状态）。"""
    return LEVEL_RANK.get(str((r or {}).get("level") or "").strip().upper()[:2])


def _status_ok(s):
    """资产可用：不以「待 / 草稿 / 未 / 部分」开头。"""
    s = str(s or "").strip()
    return bool(s) and not s.startswith(("待", "草稿", "未", "部分"))


def load_scenes(root, uid):
    """读场分析。返回 (scenes, 问题列表)。"""
    udir = Path(root) / "units" / uid
    doc = jload(udir / "storyboard" / "scenes.json", {}) or {}
    scenes = [x for x in (doc.get("scenes") or []) if isinstance(x, dict)]
    shots = (jload(udir / "storyboard" / "shots.json", {}) or {}).get("shots") or []
    ids = {str(x.get("id")).zfill(3) for x in shots}
    bad = []
    if not scenes:
        if not (udir / "storyboard" / "scenes.json").exists():
            return [], ["还没有 scenes.json —— 场是决策层，没有它分镜只能按台词猜"]
        return [], ["scenes.json 里一场都没有 —— 先切场：台词定稿 → 切场 → 场分析 → 分镜"]

    REQ = ("id", "name", "chapter", "goal", "obstacle",
           "audience_in", "audience_out", "reveal", "hold", "state_change")
    LIST_FIELDS = {"reveal", "hold"}        # 空数组是「明确写了 0 条」，不等于没填
    covered = set()
    for _idx, sc in enumerate(scenes):
        sid = sc.get("id") or "?"
        miss = [k for k in REQ
                if (k not in LIST_FIELDS and not str(sc.get(k) or "").strip())
                or (k in LIST_FIELDS and k not in sc)]
        if miss:
            bad.append("%s 缺字段：%s" % (sid, "、".join(miss)))
        if len(sc.get("reveal") or []) > 3:
            bad.append("%s 揭示 %d 条（上限 3）：塞太多观众一条也记不住"
                       % (sid, len(sc.get("reveal") or [])))
        if not (sc.get("hold") or []):
            # 末场豁免：落点场的职责是把片子收住，不是按住下一个问题
            if _idx < len(scenes) - 1:
                bad.append("%s 没有 hold：不会按住的场，观众没有期待" % sid)
        if str(sc.get("audience_in") or "") == str(sc.get("audience_out") or "") and sc.get("audience_in"):
            bad.append("%s 观众进来和出去知道的一样：这场没有信息增量" % sid)
        for x in sc.get("shots") or []:
            covered.add(str(x).zfill(3))

    # chapter 两边是否对得上
    ch_shots = {}
    for x in shots:
        ch_shots.setdefault(str(x.get("chapter") or "").strip(), set()).add(str(x.get("id")).zfill(3))
    ch_scenes = {str(sc.get("chapter") or "").strip() for sc in scenes}
    for c in ch_scenes:
        if c and c not in ch_shots:
            bad.append("场里写了 chapter「%s」，但 shots.json 里没有这个 chapter" % c)

    orphan = ids - covered
    if orphan:
        bad.append("%d 镜不属于任何场（%s）：场是决策层，镜必须挂在场上"
                   % (len(orphan), "、".join(sorted(orphan)[:6])))
    return scenes, bad


def cmd_scene(args):
    """场分析：list 看每场目标与信息增量，check 体检。"""
    root = Path(args.cwd or ".").resolve()
    st = load_state(root)
    uid = unit_id(st, args.unit)
    scenes, bad = load_scenes(root, uid)

    if args.s_cmd == "list":
        out("")
        out("场分析 · %s" % uid)
        out("=" * 64)
        if not scenes:
            out("  还没有场。先切场：台词定稿 → 切场 → 场分析 → 分镜")
            out("")
            return 0
        for sc in scenes:
            out("  %s  %s（%d 镜）" % (sc.get("id"), sc.get("name"), len(sc.get("shots") or [])))
            out("      目标：%s" % (sc.get("goal") or "—"))
            out("      阻力：%s" % (sc.get("obstacle") or "—"))
            out("      观众：%s → %s" % (sc.get("audience_in") or "?", sc.get("audience_out") or "?"))
            out("      揭示：%s" % ("；".join(sc.get("reveal") or []) or "—"))
            out("      按住：%s" % ("；".join(sc.get("hold") or []) or "—"))
            out("")
        return 0

    out("")
    out("场体检 · %s（%d 场）" % (uid, len(scenes)))
    out("=" * 64)
    if not bad:
        out("  [OK] 场分析完整，镜全部有归属。")
    else:
        out("  [!!] %d 条：" % len(bad))
        for x in bad:
            out("       - " + x)
        out("  （退出码 1：场门禁没过，不要进环 3）")
    out("")
    return 1 if bad else 0

AI_HIGH = [
    "让我们一起", "不仅仅是", "换句话说", "总的来说", "值得注意的是",
    "某种程度上", "某种意义上", "你有没有想过", "难道不是",
    "这一刻，他", "这一刻，她", "人生就是", "你要相信", "一切都是最好的安排",
    "他明白了", "她明白了", "他感到", "她感到", "他终于明白", "她终于明白",
]
AI_LOW = ["其实", "说实话", "你知道吗", "不仅", "而且", "温暖的", "治愈",
          "深刻的", "有意义", "有价值", "成长", "在这个"]
AI_HIGH_RX = re.compile(r"在这个.{0,12}(时代|世界|社会|环境)")


def _script_body(text):
    lines = (text or "").replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:])
    return "\n".join(lines)


def _script_block(text):
    body = _script_body(text)
    m = re.search(r"(?ms)^##\s+角色与场景\s*$(.*?)(?=^##\s+|\Z)", body)
    return m.group(1).strip() if m else ""


def _script_dialogue(text):
    body = _script_body(text)
    m = re.search(r"(?ms)^##\s+[^\n]*剧本[^\n]*$(.*)\Z", body)
    return m.group(1).strip() if m else body


def _dialogue_text(text):
    """只取台词行：`角色：台词` 或引号开头的行。表格/小标题/说明不算。"""
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "-", "*", "|")) or "|" in s:
            continue
        m = re.match(r"^([^：:]{1,10})[：:]\s*(.+)$", s)
        if m:
            lines.append(m.group(2))
        elif s.startswith(("「", "\"", "“")):
            lines.append(s)
    return "\n".join(lines)


def _idea_frontmatter(text):
    lines = (text or "").replace("\r\n", "\n").split("\n")
    meta = {}
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for ln in lines[1:i]:
                    if ":" in ln:
                        k, v = ln.split(":", 1)
                        meta[k.strip()] = v.strip()
                break
    return meta


def _idea_sections(text):
    lines = (text or "").replace("\r\n", "\n").split("\n")
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


def _idea_level(meta, sections):
    present, filled = set(), set()
    for key in IDEA_REQUIRED:
        for title, body in sections.items():
            if key in title:
                present.add(key)
                if _idea_has_content(body):
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


def _idea_deleted(root):
    p = Path(root) / ".workbuddy" / "idea-deleted.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("deleted") if isinstance(doc, dict) else None
    return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


