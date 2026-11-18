# 架构

> 本文描述本仓库展示的核心代码如何组成一个本地 AI 视频工作台。
> 导演内容包、LibTV 账号和 FFmpeg 属于外部依赖，不在本仓库内。

---

## 1. 分层

```text
┌──────────────────────────────────────────────────────────┐
│ 启动器  launcher.py / ui/picker.html                      │
│ 选择工程 · 环境检查 · 启动工作台 · 窗口 · 更新              │
└───────────────────────────┬──────────────────────────────┘
                            │ 启动本地 HTTP 服务
                            ▼
┌──────────────────────────────────────────────────────────┐
│ 工作台服务  app-src/workbench/wb/                         │
│ server · snapshot · watch · graph · export · derive · md  │
│ LibTV 客户端 · 槽位 · 索引                                 │
└───────────────┬──────────────────────────┬───────────────┘
                │ API / 静态文件            │ 读取
                ▼                          ▼
┌──────────────────────────┐      ┌───────────────────────┐
│ React 工作台              │      │ 工程目录              │
│ app-src/workbench/web/   │      │ project.json / units/ │
│ 总览 · 剧本 · 资产 · 审片 │      │ assets/ · design/     │
│ · 交付                    │      └───────────────────────┘
└──────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────┐
│ 出片引擎  app-src/workbench/wb/engine/                    │
│ 确定性检查 · 生成准入 · CLI                               │
└──────────────────────────────────────────────────────────┘

外部依赖：
  Agent（WorkBuddy / Claude） → 读导演内容包 → 写工程文件
  LibTV MCP                   → 建节点 / 分组 / 同步结果
  ffmpeg                      → 合并视频 / 抽封面
```

---

## 2. 启动器

`launcher.py` 负责工作台之外的一切：

- 选择页：最近工程、新建、打开文件夹；
- 环境检查：Agent、导演内容包、LibTV、ffmpeg、磁盘、Edge/WebView2；
- 启动本地服务，并把浏览器或 WebView 指过去；
- 管理窗口和进程；
- 检查更新、下载热更新或安装包。

启动器不读工程内容，只把工程路径和运行参数交给工作台。

## 3. 工作台服务

`app-src/workbench/workbench.py` 是入口，实际实现都在 `wb/`：

| 模块 | 职责 |
|---|---|
| `server.py` | HTTP 服务、API 路由、静态文件、写操作鉴权、备份与冲突处理 |
| `snapshot.py` | 把工程目录读成统一快照：场、分镜、镜头、剧本、生成物 |
| `watch.py` | 监听文件 mtime/size，按分区递增版本号，通知前端刷新 |
| `graph.py` | 图谱读写、规范化、校验、指纹；纯逻辑，不联网 |
| `export.py` | 把一集或一场导出成 Markdown；只读，不改工程 |
| `derive.py` | 从工程文件推导"思路 → 资产 / 剧本"关系 |
| `md.py` | 轻量 Markdown → HTML，不引第三方库，不执行原始 HTML |
| `libtv_mcp.py` | LibTV Remote MCP 最小客户端：读 token、刷新、调工具 |
| `libtv_slots.py` | 场 / 段 / 节点与 LibTV 项目、组的幂等映射 |
| `libtv_index.py` | 本地对象到 LibTV 项目 / 组 / 节点的索引 |
| `engine/` | 确定性检查和生成准入，见下一节 |

设计原则：

- **工程文件是唯一事实源。** 快照每次都从磁盘读，不维护第二份状态。
- **只绑 `127.0.0.1`。** 写操作必须带进程级 token；写前备份，冲突返回 409。
- **只读和写入分离。** 导出、预览、推导都是只读；写操作集中处理。
- **不依赖第三方 Python 包。** 工作台和启动器只使用标准库。

## 4. 出片引擎

`wb/engine/` 只做确定性检查，不打分、不猜：

| 模块 | 职责 |
|---|---|
| `checks.py` | 工程、资产、场、段的体检 |
| `ready.py` | 生成准入：资产、参考图、机位 / 光位、关键帧等硬条件 |
| `cli.py` | 命令行入口，便于脚本和 Agent 调用 |
| `core.py` | 检查项和数据模型的公共实现 |

引擎不生成图片或视频。它回答的是"现在能不能生成 / 布线"，生成动作由用户在 LibTV 点击。

## 5. React 工作台

`app-src/workbench/web/src/`：

| 页面 | 用途 |
|---|---|
| `Home.jsx` | 总览、进度、下一步 |
| `Ideas.jsx` | 创意与导演简报 |
| `Outline.jsx` | 内容大纲 / 剧本 |
| `Script.jsx` | 分场与台词 |
| `Assets.jsx` | 角色、场景、道具、风格资产 |
| `Review.jsx` | 审片台：分镜、镜头、图谱、布线 |
| `Deliver.jsx` | 交付包、合并、导出 |
| `Lineage.jsx` | 资产与镜头的引用关系 |

组件层负责对话框、图谱、媒体预览、设置抽屉、首次引导等。
前端通过 `api.js` 调本地 HTTP API，不直接读写文件。

## 6. 数据流

### 打开一个工程

```text
launcher 选择工程
  → 启动 workbench.py --cwd <project>
  → server 绑定 127.0.0.1:<port>
  → 前端请求 /api/...
  → snapshot.build(root, unit) 读取工程
  → 返回页面数据
```

### Agent 写内容

```text
用户和 Agent 对话
  → Agent 读导演内容包（不在本仓库）
  → Agent 写 script/、storyboard/、prompts/ 等文件
  → watch.py 发现 mtime 变化
  → 前端分区版本号 +1，触发刷新
  → snapshot 重新读取
```

### LibTV 布线

```text
用户在审片台点「布线」
  → server 调 libtv_slots 计算槽位
  → libtv_mcp 调 LibTV MCP 建项目 / 组 / 节点
  → libtv_index 记录本地对象与 LibTV 的对应
  → 结果写回工程的 libtv.json
```

工作台不发起生成，只建节点、挂参考、记录映射。生成由用户在 LibTV 点击。

### 交付

```text
engine 检查
  → export.py 生成 Markdown
  → ffmpeg 合并视频 / 抽封面
  → deliver/交付包
```

## 7. 写入安全

- 服务只绑 `127.0.0.1`；
- 写请求必须带 `X-WB-Token`；
- 写前备份，冲突返回 409；
- 静态文件和工程文件都做路径边界检查；
- 不执行工程里的脚本，不渲染原始 HTML。

## 8. 扩展点

- **新页面**：在 `web/src/pages/` 增加页面，在 `api.js` 增加请求；
- **新 API**：在 `server.py` 增加路由，复用 snapshot / 写入工具；
- **新检查**：在 `engine/checks.py` 或 `engine/ready.py` 增加确定性检查；
- **新适配器**：工程里的 `project.json.adapters` 预留了外部服务配置；
- **新内容包**：Agent 通过外部内容包工作，工作台不感知具体创作规则。

## 9. 不在本仓库的部分

- 导演内容包与专业 skill；
- LibTV 账号、token 和平台接口细节；
- FFmpeg、CPython 运行时、安装包构建；
- 内部计划、设计稿和测试工程。

这些由官方安装包或外部服务提供。
