"""工作台（Workbench Console）。

层次：
    snapshot  工程 → 快照（唯一数据模型）
    watch     文件变化 → 分区版本号
    views     快照 → HTML（纯函数）
    server    HTTP / SSE / 写接口 / 安全
    md        轻量 Markdown 渲染
"""

__all__ = ["snapshot", "watch", "views", "server", "md"]
