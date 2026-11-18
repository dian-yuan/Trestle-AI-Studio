"""LibTV Remote MCP 最小客户端（标准库）。

只做工作台需要的几件事：读 token、过期刷新、调工具、建项目 / 资产文件夹。
token 搜索顺序与 专业内容包的授权脚本 一致：
  1. LIBTV_STATE
  2. ~/.workbuddy/libtv_oauth_state.json
  3. <工程>/.workbuddy/tmp/libtv_oauth_state.json
没有 token 就返回不可用，不弹授权、不联网重试。
"""
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MCP = "https://mcp.liblib.tv/mcp"
TOKEN = "https://mcp.liblib.tv/token"
RESOURCE = "https://mcp.liblib.tv/mcp"
ID_SAFE = re.compile(r"[^A-Za-z0-9._:-]")


def safe_key(text, min_len=8):
    """LibTV idempotencyKey 只接受 [A-Za-z0-9._:-]，且至少 8 位。"""
    k = ID_SAFE.sub("-", str(text or ""))[:240]
    return (k + "-" + "0" * min_len)[:255] if len(k) < min_len else k


NAME_KEYS = ("name", "displayName", "title", "nodeName")


def node_name(node):
    """节点的显示名。read_project 的不同视图字段名不完全一样。"""
    if not isinstance(node, dict):
        return ""
    for k in NAME_KEYS:
        v = node.get(k)
        if v:
            return str(v).strip()
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    for k in NAME_KEYS:
        v = data.get(k)
        if v:
            return str(v).strip()
    return ""


def node_id_from(resp):
    """从写工具（create_node / generate_video / upload_media）的返回里取节点 ID。"""
    r = resp or {}
    plan = r.get("plan") or {}
    if plan.get("nodeId"):
        return str(plan["nodeId"])
    op = r.get("operationResult") or {}
    if op.get("subjectNodeId"):
        return str(op["subjectNodeId"])
    created = (op.get("affectedNodes") or {}).get("created") or []
    if created:
        first = created[0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("nodeId") or "")
        return str(first)
    for k in ("nodeId", "id"):
        if r.get(k):
            return str(r[k])
    return ""


def _aux(root):
    """机器级状态目录。桌面版（AIV_STATE_DIR）放在工程外。"""
    env = os.environ.get("AIV_STATE_DIR")
    if env:
        return Path(env)
    return Path(root) / ".workbuddy" / "tmp"


def state_path(root=None):
    env = os.environ.get("LIBTV_STATE")
    if env:
        return Path(env)
    cands = []
    cands.append(Path.home() / ".workbuddy" / "libtv_oauth_state.json")
    if root:
        cands.append(_aux(root) / "libtv_oauth_state.json")
    for p in cands:
        if p.exists():
            return p
    return cands[-1]


def read_state(root=None):
    p = state_path(root)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 会话态
CLAUDE_CRED = Path.home() / ".claude" / ".credentials.json"


def _read_json(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _norm_exp(v):
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return n / 1000.0 if n > 1e11 else n      # 毫秒与秒都收


def _claude_state():
    """Claude Code 自己的 libtv OAuth 条目。只读——绝不回写别人的凭据文件。"""
    d = _read_json(CLAUDE_CRED)
    for k, v in (d.get("mcpOAuth") or {}).items():
        if str(k).startswith("libtv|") and isinstance(v, dict) and v.get("accessToken"):
            return {
                "access_token": str(v.get("accessToken") or ""),
                "refresh_token": str(v.get("refreshToken") or ""),
                "expires_at": _norm_exp(v.get("expiresAt")),
                "client_id": str(v.get("clientId") or ""),
                "saved_at": "",
            }
    return {}


def sources(root=None):
    """token 来源，按优先级。返回 [(kind, path, state, writable)]。

    前三个是工作台自己的、可写可续期；第四个是 Claude Code 的，只读复用——
    用户在 Claude Code 里连过一次 LibTV，工作台就不该再逼他连第二次。
    """
    out = []
    env = os.environ.get("LIBTV_STATE")
    if env:
        # 显式指定就是不许去别处找——测试与"我只用这一份"都靠这条
        p = Path(env)
        return [("env", p, _read_json(p), True)]
    if root:
        p = _aux(root) / "libtv_oauth_state.json"
        out.append(("project", p, _read_json(p), True))
    p = Path.home() / ".workbuddy" / "libtv_oauth_state.json"
    out.append(("user", p, _read_json(p), True))
    out.append(("claude", CLAUDE_CRED, _claude_state(), False))
    return out


def _alive(st, now=None):
    now = time.time() if now is None else now
    if not str(st.get("access_token") or ""):
        return False
    exp = _norm_exp(st.get("expires_at"))
    return (not exp) or exp > now


def active_state(root=None):
    """挑一份来用：先找没到期的，再找能续期的，最后用有 token 的。"""
    now = time.time()
    cands = [x for x in sources(root)
             if str(x[2].get("access_token") or "") or str(x[2].get("refresh_token") or "")]
    for kind, path, st, writable in cands:
        if _alive(st, now):
            return kind, path, st, writable
    for kind, path, st, writable in cands:
        if writable and str(st.get("refresh_token") or ""):
            return kind, path, st, writable
    if cands:
        kind, path, st, writable = cands[0]
        return kind, path, st, writable
    return "", state_path(root), {}, True


def local_session(root=None):
    """不联网的会话态。只回答：有没有 token、还能不能用、能不能自己续。

    状态：signed_out（一份都没有）/ expired（都过期了）/ ready（有没到期的）。
    ready 只代表本地看着能用，真正在线与否要 probe()。
    """
    now = time.time()
    cands = [x for x in sources(root)
             if str(x[2].get("access_token") or "") or str(x[2].get("refresh_token") or "")]
    if not cands:
        return {"state": "signed_out", "source": "", "path": str(state_path(root)),
                "can_refresh": False, "expires_in": None, "saved_at": ""}
    pick = None
    for kind, path, st, writable in cands:
        if _alive(st, now):
            pick = (kind, path, st, writable, True)
            break
    if pick is None:
        for kind, path, st, writable in cands:
            if writable and str(st.get("refresh_token") or ""):
                pick = (kind, path, st, writable, False)
                break
    if pick is None:
        kind, path, st, writable = cands[0]
        pick = (kind, path, st, writable, False)
    kind, path, st, writable, alive = pick
    exp = _norm_exp(st.get("expires_at"))
    return {
        "state": "ready" if alive else "expired",
        "source": kind,
        "path": str(path),
        "can_refresh": bool(writable and str(st.get("refresh_token") or "")),
        "expires_in": (max(0, int(exp - now)) if exp else None),
        "saved_at": str(st.get("saved_at") or ""),
    }


_KINDS = ("no_token", "expired", "unauthorized", "network", "account", "unknown")


def classify(msg):
    """把异常字符串收敛成六种，前端据此给不同的下一步。

    最要紧的一条：网络问题不许引导用户重新授权——那是白折腾。
    """
    m = str(msg or "")
    low = m.lower()
    if "没有 libtv token" in low:
        return "no_token"
    if "可续期" in m or "续期失败" in m:
        return "expired"
    for t in ("invalid_token", "unauthorized", "http 401", "401 "):
        if t in low:
            return "unauthorized"
    for t in ("timed out", "timeout", "getaddrinfo", "name or service",
              "connection", "ssl", "urlopen", "network", "unreachable", "errno"):
        if t in low:
            return "network"
    if "未授权" in m:
        return "no_token"
    if "返回空" in m or "account_user" in low:
        return "account"
    return "unknown"


FIX = {
    "no_token": {"title": "还没连过 LibTV", "action": "login", "text": "去连接"},
    "expired": {"title": "授权过期了", "action": "login", "text": "重新授权"},
    "unauthorized": {"title": "授权已失效", "action": "login", "text": "重新授权"},
    "network": {"title": "连不上 LibTV", "action": "retry", "text": "重试"},
    "account": {"title": "LibTV 账号状态异常", "action": "open", "text": "去网页核对"},
    "unknown": {"title": "LibTV 调用失败", "action": "retry", "text": "重试"},
}


def fix_for(kind):
    return dict(FIX.get(kind) or FIX["unknown"])



def _write_state(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _client_id(st):
    cid = str(st.get("client_id") or "").strip()
    if cid:
        return cid
    try:
        d = json.loads((Path.home() / ".claude" / ".credentials.json")
                       .read_text(encoding="utf-8"))
        for k, v in (d.get("mcpOAuth") or {}).items():
            if k.startswith("libtv|"):
                return str(v.get("clientId") or "").strip()
    except Exception:
        pass
    return ""


def _http_detail(body):
    """从 HTTP 错误 body 里尽量掏出人能看懂的一句。"""
    text = str(body or "").strip()
    if not text:
        return ""

    def dig(v):
        if isinstance(v, str):
            return v.strip()
        if isinstance(v, dict):
            code = v.get("code") or v.get("errorCode") or ""
            msg = (v.get("message") or v.get("detail")
                   or v.get("error_description") or "")
            if code or msg:
                return ("%s：%s" % (code, msg)).strip("：") \
                    if code and msg else str(code or msg).strip()
            for k in ("error", "data", "details"):
                if k in v:
                    got = dig(v[k])
                    if got:
                        return got
            return ""
        if isinstance(v, list):
            for x in v:
                got = dig(x)
                if got:
                    return got
        return ""

    try:
        got = dig(json.loads(text))
    except Exception:
        got = ""
    return (got or text)[:800]


class LibTVHTTPError(RuntimeError):
    """LibTV 返回 4xx/5xx：保留状态码和 body，别只剩 HTTP Error 400。"""

    def __init__(self, status, reason="", body=""):
        self.status = int(status or 0)
        self.reason = str(reason or "")
        self.body = str(body or "")
        detail = _http_detail(self.body)
        msg = "LibTV HTTP %s" % self.status
        if detail:
            msg += "：" + detail
        elif self.reason:
            msg += "：" + self.reason
        if self.status in (400, 401, 403) and not detail:
            msg += "（可能是连接 / 授权问题，请到设置 → LibTV 确认）"
        super().__init__(msg)


class LibTV:
    def __init__(self, root=None):
        self.root = root
        kind, path, st, writable = active_state(root)
        self.source = kind
        self.path = Path(path)
        self.st = st
        self.writable = writable
        self.tok = str(self.st.get("access_token") or "")
        self.rid = 0
        self.inited = False

    def _expired(self):
        try:
            return float(self.st.get("expires_at") or 0) <= time.time() + 30
        except (TypeError, ValueError):
            return True

    def refresh(self):
        cid = _client_id(self.st)
        rt = str(self.st.get("refresh_token") or "")
        if not cid or not rt:
            raise RuntimeError("LibTV 没有可续期的 token")
        if not self.writable:
            raise RuntimeError("这份 token 来自 %s，只读；请重新授权" % (self.source or "?"))
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": rt,
            "client_id": cid, "resource": RESOURCE,
        }).encode()
        req = urllib.request.Request(
            TOKEN, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise LibTVHTTPError(e.code, str(e.reason or ""), raw) from e
        if not tok.get("access_token"):
            raise RuntimeError("LibTV token 续期失败")
        self.st["access_token"] = tok["access_token"]
        self.st["refresh_token"] = tok.get("refresh_token") or rt
        self.st["expires_at"] = time.time() + int(tok.get("expires_in") or 7200) - 300
        self.st["client_id"] = cid
        if tok.get("scope"):
            self.st["scope"] = tok["scope"]
        self.st["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_state(self.path, self.st)
        self.tok = tok["access_token"]

    def ensure_token(self):
        if not self.tok or self._expired():
            self.refresh()
        if not self.tok:
            raise RuntimeError("LibTV 未授权")
        return self.tok

    def _post(self, payload, timeout=30):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.ensure_token(),
            "MCP-Protocol-Version": "2025-06-18",
        }
        req = urllib.request.Request(
            MCP, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            # urlopen 把 4xx 直接抛成 HTTPError；不读 body 就只剩
            # “HTTP Error 400: Bad Request”，上层看不到 NODE_NAME_CONFLICT /
            # IDEMPOTENCY_CONFLICT 这类业务码，也就没法接管或重试。
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise LibTVHTTPError(e.code, str(e.reason or ""), raw) from e

    @staticmethod
    def _parse(text):
        out = []
        for chunk in (text or "").split("\n\n"):
            ds = [ln[5:] for ln in chunk.split("\n") if ln.startswith("data: ")]
            if ds:
                try:
                    out.append(json.loads("\n".join(ds)))
                except Exception:
                    pass
        if not out:
            try:
                out = [json.loads(text)]
            except Exception:
                pass
        return out

    @staticmethod
    def _authish(e):
        """401/403，或 body 明说 token 失效；这类 400 才值得续期重试。"""
        text = (str(e) + " " + getattr(e, "body", "")).lower()
        return (getattr(e, "status", 0) in (401, 403)
                or "invalid_token" in text
                or "unauthorized" in text
                or "token expired" in text)

    def call(self, name, args=None, timeout=30, _retry=True):
        try:
            if not self.inited:
                self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18",
                                       "capabilities": {},
                                       "clientInfo": {"name": "workbench",
                                                      "version": "1.0"}}}, timeout)
                self.inited = True
            self.rid += 1
            s, body = self._post({"jsonrpc": "2.0", "id": self.rid,
                                  "method": "tools/call",
                                  "params": {"name": name,
                                             "arguments": args or {}}}, timeout)
        except LibTVHTTPError as e:
            if _retry and self._authish(e):
                self.refresh()
                self.inited = False
                return self.call(name, args, timeout, _retry=False)
            raise
        if s == 401 and _retry:
            self.refresh()
            self.inited = False
            return self.call(name, args, timeout, _retry=False)
        for m in self._parse(body):
            res = m.get("result") or {}
            if res.get("isError"):
                txt = "".join(c.get("text") or "" for c in (res.get("content") or []))
                if ("invalid_token" in txt or "unauthorized" in txt.lower()) and _retry:
                    self.refresh()
                    self.inited = False
                    return self.call(name, args, timeout, _retry=False)
                raise RuntimeError(txt[:400] or "LibTV MCP error")
            if m.get("error"):
                err = json.dumps(m["error"], ensure_ascii=False)
                if ("invalid_token" in err or "unauthorized" in err.lower()) and _retry:
                    self.refresh()
                    self.inited = False
                    return self.call(name, args, timeout, _retry=False)
                raise RuntimeError(err[:400])
            sc = res.get("structuredContent")
            if sc is not None:
                return sc
        raise RuntimeError("LibTV MCP 没有 structuredContent")

    # ---------- 画布素材 ----------
    def nodes(self, project_id):
        r = self.call("read_project", {
            "projectId": project_id, "view": "nodes"}, timeout=30)
        rows = (r or {}).get("nodes")
        if not isinstance(rows, list):
            rows = ((r or {}).get("project") or {}).get("nodes") or []
        return [n for n in rows if isinstance(n, dict)]

    def node_ids(self, project_id):
        return {str(n.get("id") or n.get("nodeId") or "").strip()
                for n in self.nodes(project_id)} - {""}

    def find_node_id(self, project_id, name, node_type=""):
        """按显示名找回画布上已有的节点。

        LibTV 在同一 project 内按显示名唯一。create_node 撞名时用它接管旧节点，
        改走 update_node，避免一直卡在 NODE_NAME_CONFLICT。
        读不到画布或没有同名节点时返回空串，调用方保留原错误。
        """
        want = str(name or "").strip()
        if not want:
            return ""
        try:
            rows = self.nodes(project_id)
        except Exception:
            return ""
        for n in rows:
            nid = str(n.get("id") or n.get("nodeId") or "").strip()
            if not nid or node_name(n) != want:
                continue
            data = n.get("data") if isinstance(n.get("data"), dict) else {}
            ntype = str(n.get("nodeType") or n.get("type")
                        or data.get("nodeType") or "").strip()
            if ntype.lower() in ("group", "folder", "workflow"):
                continue  # 组/文件夹同名，不是可接管的生成节点
            # 读不到类型时按名字认（同 project 内显示名唯一）；类型明确不同就不接管，
            # 把原错误抛回，免得把图节点当视频节点改坏。
            if not ntype or not node_type or ntype.lower() == node_type.lower():
                return nid
        return ""

    def upload_image(self, project_id, path, name="", x=0, y=0, group=None):
        """上传一张本地图片为画布 image 节点，返回 node_id。"""
        p = Path(path)
        data = p.read_bytes()
        before = self.node_ids(project_id)
        label = str(name or p.stem)[:180]
        # 每次上传都用新 key：同一个 key 配不同的 fileRef / 坐标会被 LibTV 判成
        # 「另一个操作」（IDEMPOTENCY_CONFLICT）。重复上传由图节点按名接管复用。
        key = "wb-up-%s-%s" % (
            hashlib.sha1(data).hexdigest()[:12], uuid.uuid4().hex[:12])
        tk = self.call("upload_ticket", {
            "file_name": p.name, "file_size": len(data),
            "sha1_hex": hashlib.sha1(data).hexdigest(),
            "projectId": project_id,
        }, timeout=60)
        parts = tk.get("parts") or []
        if not parts:
            raise RuntimeError("upload_ticket 没返回分片")
        for part in parts:
            u = urllib.parse.urlparse(part["url"])
            conn = http.client.HTTPSConnection(u.hostname, timeout=300)
            try:
                conn.request("PUT", u.path + ("?" + u.query if u.query else ""),
                             body=data, headers={"Content-Length": str(len(data))})
                resp = conn.getresponse()
                raw = resp.read()
                if resp.status not in (200, 201):
                    raise RuntimeError("上传分片 HTTP %s %s" % (
                        resp.status, raw[:200].decode(errors="replace")))
            finally:
                conn.close()
        done = self.call("upload_complete", {
            "path": tk.get("path"), "upload_id": tk.get("uploadId")}, timeout=60)
        ref = (done.get("fileRef") or done.get("file_ref")
               or (done.get("file") or {}).get("ref"))
        if not ref:
            raise RuntimeError("upload_complete 没给 fileRef")
        media_args = {
            "name": label, "idempotencyKey": safe_key(key),
            "projectId": project_id, "fileRef": ref,
            "mediaType": "image", "x": x, "y": y,
            **({"group": group} if group else {}),
        }
        try:
            media_resp = self.call("upload_media", media_args, timeout=60)
        except Exception as e:
            msg = str(e)
            if "IDEMPOTENCY_CONFLICT" in msg:
                # 这个 key 属于早先的一次上传：同内容的图已在画布上，直接复用。
                hit = self.find_node_id(project_id, label, "image")
                if hit:
                    return hit
                media_args["idempotencyKey"] = safe_key(
                    key + "-" + uuid.uuid4().hex[:8])
                media_resp = self.call("upload_media", media_args, timeout=60)
            elif "NODE_NAME_CONFLICT" in msg:
                # 同名旧图占着名字（常见于换了主图）：先给旧节点让名，再传新图。
                old = self.find_node_id(project_id, label, "image")
                if not old:
                    raise
                try:
                    self.call("update_node", {
                        "node": old, "projectId": project_id,
                        "idempotencyKey": safe_key("wb-up-old-" + label),
                        "newName": (label + " · 旧")[:180],
                    }, timeout=60)
                except Exception:
                    raise e
                media_args["idempotencyKey"] = safe_key(
                    key + "-" + uuid.uuid4().hex[:8])
                media_resp = self.call("upload_media", media_args, timeout=60)
            else:
                raise
        new = self.node_ids(project_id) - before
        if len(new) == 1:
            return new.pop()
        # 幂等重传：图早就在画布上（上一次布线传过，只是本地没记 ID），
        # upload_media 直接返回旧节点，差集为 0。按显示名接管，别误报上传失败。
        nid = node_id_from(media_resp)
        if nid:
            return nid
        hit = self.find_node_id(project_id, label, "image")
        if hit:
            return hit
        raise RuntimeError("预期新增 1 个图片节点，实际 %d 个：%s" % (
            len(new), sorted(new)))


def probe(root=None):
    """返回 (ok, info)。只做只读 doctor，失败不抛。

    info.kind 是六种之一（no_token / expired / unauthorized / network / account / unknown），
    前端拿它决定下一步——尤其 network 不该引导去重新授权。

    2026-11 起远程 MCP 不再提供 account_user；doctor 返回 identity，
    里面有 nickname / activeAccountId，正好用来做连接与账号探针。
    """
    sess = local_session(root)
    c = None
    try:
        if sess["state"] == "signed_out":
            return False, {"reason": "没有 LibTV token", "kind": "no_token",
                           "source": "", "path": sess["path"],
                           "fix": fix_for("no_token")}
        c = LibTV(root)
        r = c.call("doctor", {}, timeout=10)
        ident = r.get("identity") or {}
        user = r.get("user") or ident
        active = (r.get("activeAccountId") or ident.get("activeAccountId")
                  or user.get("activeAccountId") or "")
        nick = user.get("nickname") or ident.get("nickname") or ""
        if not (active or nick or user.get("userId") or ident.get("userId")):
            return False, {"reason": "doctor 没返回账号信息", "kind": "account",
                           "source": getattr(c, "source", ""),
                           "fix": fix_for("account")}
        return True, {
            "kind": "ok",
            "state": str(c.path),
            "source": getattr(c, "source", ""),
            "account_id": active,
            "nickname": nick,
        }
    except Exception as e:
        msg = str(e)[:300]
        kind = classify(msg)
        return False, {"reason": msg, "kind": kind,
                       "source": getattr(c, "source", "") if c else "",
                       "fix": fix_for(kind)}


def project_meta(resp):
    """从 create_project / read_project 的返回里取 uuid / space。"""
    r = resp or {}
    p = r.get("project") or r.get("projectMeta") or {}
    pid = (r.get("projectId") or p.get("uuid") or p.get("id") or r.get("uuid")
           or r.get("id") or "")
    space = (r.get("projectSpaceId") or p.get("projectSpaceId") or p.get("folderId")
             or r.get("folderId") or "")
    return str(pid or ""), int(space) if str(space).isdigit() else 0


def folder_meta(resp):
    r = resp or {}
    f = r.get("folder") or r.get("result") or {}
    fid = (r.get("folderId") or f.get("folderId") or f.get("id") or r.get("id") or "")
    return int(fid) if str(fid).isdigit() else 0


def group_meta(resp):
    r = resp or {}
    plan = r.get("plan") or {}
    op = r.get("operationResult") or {}
    created = (op.get("affectedNodes") or {}).get("created") or []
    return str(plan.get("groupNodeKey") or op.get("subjectNodeId")
               or (created[0] if created else "") or "")
