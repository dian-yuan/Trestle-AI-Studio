"""轻量 Markdown → HTML。

只覆盖工程文档实际用到的语法：标题、表格、代码块、列表、引用、分隔线、
粗体、斜体、行内代码、链接、图片。不引第三方库，不执行原始 HTML。

    render(text, base="design/xxx.md") -> HTML
"""

import html
import posixpath
import re


def _link(target, base):
    t = target.strip()
    if re.match(r"^(https?:|mailto:|#|/)", t):
        return t
    if t.endswith((".md", ".html", ".htm")):
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(base), t))
        return "/docs?path=" + rel
    return t


def _img(src, base):
    s = src.strip()
    if re.match(r"^(https?:|/|data:)", s):
        return s
    rel = posixpath.normpath(posixpath.join(posixpath.dirname(base), s))
    return "/img/" + rel


def inline(text, base=""):
    s = html.escape(text, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
               lambda m: '<img alt="%s" src="%s">' % (m.group(1), _img(m.group(2), base)), s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: '<a href="%s">%s</a>' % (html.escape(_link(m.group(2), base)), m.group(1)), s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
    return s


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_sep(line):
    cells = _cells(line)
    return bool(cells) and all(re.match(r"^:?-+:?$", c) for c in cells)


def _aligns(line):
    out = []
    for c in _cells(line):
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        else:
            out.append("")
    return out


def render(text, base=""):
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        fence = re.match(r"^\s*```(.*)$", line)
        if fence:
            lang = fence.group(1).strip()
            buf = []
            i += 1
            while i < n and not re.match(r"^\s*```", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = ' class="lang-%s"' % html.escape(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(buf))))
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            # 文档嵌在页面里，页面的 h1 归页面标题；正文标题整体降一级。
            lv = min(6, len(m.group(1)) + 1)
            out.append("<h%d>%s</h%d>" % (lv, inline(m.group(2), base), lv))
            i += 1
            continue

        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        if line.strip().startswith("|") and i + 1 < n and _is_sep(lines[i + 1]):
            head = _cells(line)
            aligns = _aligns(lines[i + 1])
            i += 2
            # 工程里的表格常在表头后空一行再写正文，兼容这种写法
            while i < n and not lines[i].strip():
                i += 1
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            th = "".join(
                '<th scope="col"%s>%s</th>' % (' style="text-align:%s"' % a if a else "", inline(c, base))
                for c, a in zip(head, aligns))
            body = []
            for r in rows:
                tds = []
                for j, c in enumerate(r):
                    a = aligns[j] if j < len(aligns) else ""
                    num = bool(re.match(r"^[+-]?\d+\.\d+s?$", c.strip()))
                    attrs = ""
                    if a:
                        attrs += ' style="text-align:%s"' % a
                    if num:
                        attrs += ' class="num"'
                    tds.append("<td%s>%s</td>" % (attrs, inline(c, base)))
                body.append("<tr>%s</tr>" % "".join(tds))
            out.append('<div class="table-wrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                       % (th, "".join(body)))
            continue

        if re.match(r"^\s*>", line):
            buf = []
            while i < n and re.match(r"^\s*>", lines[i]):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>"
                       % render("\n".join(buf), base))
            continue

        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            buf = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i])
                i += 1
                buf.append("<li>%s</li>" % inline(item, base))
            out.append("<%s>%s</%s>" % (tag, "".join(buf), tag))
            continue

        if not line.strip():
            i += 1
            continue

        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|\s*```|\s*>|\s*([-*+]|\d+\.)\s+|\s*\|)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % "<br>".join(inline(x, base) for x in buf))

    return "\n".join(out)
