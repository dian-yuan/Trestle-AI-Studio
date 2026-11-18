<p align="center">
  <b>Trestle AI Studio</b>
</p>
<p align="center">把 AI 视频做成一个工程。</p>
<p align="center">
  本地优先的 AI 视频工程工作台：剧本、分场、分镜、提示词、资产和交付，<br>
  全部放在一个文件夹里，由工作台和 Agent 一起推进。
</p>

<p align="center">
  <a href="https://github.com/dian-yuan/Trestle-AI-Studio/releases/latest">
    <img alt="Download" src="https://img.shields.io/badge/download-latest-blue?style=flat-square&logo=github">
  </a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square">
  <img alt="Status" src="https://img.shields.io/badge/status-beta-orange?style=flat-square">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-FSL--1.1--MIT-green?style=flat-square"></a>
</p>

<p align="center">
  <a href="#它是什么">它是什么</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#工作流">工作流</a> ·
  <a href="#下载与安装">下载</a> ·
  <a href="#工程结构">工程结构</a> ·
  <a href="#源码与专业版">源码与专业版</a>
</p>

---

> **仓库说明**：本仓库是 Trestle 的源码展示，不包含导演内容包；
> 完整产品请下载 [最新 Release](https://github.com/dian-yuan/Trestle-AI-Studio/releases/latest)。

---

## 它是什么

Trestle 是一个 Windows 桌面应用，用来把一部 AI 视频从想法做到交付。

它不把工作留在聊天窗口里。创意、剧本、场、分镜、镜头、提示词、角色和场景资产、
审片记录、交付包，全部落在一个本地工程文件夹里。工作台负责读写和检查，Agent 负责写内容，
LibTV 负责生成。

**工程文件就是唯一事实源。** 你可以随时把工程拷走、换电脑打开，也可以不用 Trestle 继续编辑。

## 为什么需要它

AI 视频真正难的地方，通常不是"生成一个镜头"：

- 一个片子里几十个镜头，角色、场景、道具要一致；
- 剧本、分镜、提示词、参考图散落在聊天记录和不同文件夹里；
- 生成要花积分，不能反复试错；
- 成片、字幕、封面和交付包要能追溯。

Trestle 把这些收进一个工程，让每一层都有输入、输出和检查点。

## 核心能力

- **工程化项目**：一个项目一个文件夹，`project.json` + `units/` + `assets/`，不依赖数据库。
- **从创意到剧本**：导演简报、内容大纲、分场剧本、台词，一步步落到文件。
- **资产库**：角色、场景、道具、风格、品牌；每项资产有状态、参考图和变体。
- **分镜与镜头**：场、段、镜、关键帧；镜头表可以直接对照剧本和资产。
- **提示词管理**：静帧 / 运动提示词、负向词、节点级编辑；改完再同步到 LibTV。
- **LibTV 布线**：节点、分组、参考图的映射和同步；工作台只布线，不替你点生成。
- **审片与返修**：按镜头标记状态、记录审阅结果、回到对应节点修改。
- **交付包**：成片、字幕、封面、音轨和交付说明，一次导出。
- **Agent 协作**：WorkBuddy 或 Claude Code 读写同一个工程；你改文件，Agent 也能看到。
- **本地优先**：工作台只绑定 `127.0.0.1`，不上传剧本和素材，不带遥测。

## 工作流

```text
创意 / 导演简报
      ↓
剧本 / 内容大纲
      ↓
场 · 分镜 · 镜头
      ↓
提示词
      ↓
资产与生成单元
      ↓
LibTV 布线 / 生成
      ↓
审片 / 返修
      ↓
交付包
```

工作台负责工程、检查、布线、同步和交付；生成动作由你在 LibTV 点击，避免误花积分。

## 下载与安装

**Windows 10/11。** 从 [Releases](https://github.com/dian-yuan/Trestle-AI-Studio/releases/latest)
下载安装包，安装包自带运行时、FFmpeg 和导演内容包。

使用前准备：

- 一个 Agent：WorkBuddy 或 Claude Code；
- 一个 LibTV 账号（出片前连接）；
- 足够的磁盘空间。

安装包包含完整产品；本仓库源码不包含导演内容包，不能直接构建出完整产品。

## 工程结构

```text
<项目>/
├─ project.json              工程元信息、集列表、外部适配器
├─ design/                   创意、导演简报、审阅记录
├─ assets/                   角色 / 场景 / 道具 / 风格 / 品牌
├─ units/<集>/
│  ├─ script/                剧本 / 内容大纲
│  ├─ storyboard/            场、镜头、图谱
│  ├─ prompts/               静帧 / 运动提示词
│  ├─ production/            生成单元
│  ├─ review/                审片记录
│  └─ deliver/               交付包
└─ .workbuddy/               运行态（备份 / 临时文件，不提交）
```

完整说明见 [docs/project-format.md](docs/project-format.md)；
最小工程示例见 [`templates/empty/`](templates/empty/)。

## 源码与专业版

Trestle 的核心思路是"工作台开源、内容包专业"。

| | 本仓库源码 | 官方安装包 |
|---|:---:|:---:|
| 启动器、工作台、出片引擎 | ✅ | ✅ |
| React 工作台 | ✅ | ✅ |
| 工程结构模板 | ✅ | ✅ |
| 导演内容包 / 专业 skill | ❌ | ✅ |
| 安装包、运行时、自动更新 | ❌ | ✅ |
| 商业支持 | ❌ | ✅ |

本仓库是**源码展示**，不是可运行发行版：

- 不包含导演内容包；
- 不保证克隆后可以直接跑通完整流程；
- 不包含安装包构建脚本和第三方运行时。

完整产品请使用官方安装包。

## 架构

```text
启动器 → 本地工作台（127.0.0.1）→ 工程目录
                    ├→ LibTV MCP（建节点 / 同步）
                    ├→ FFmpeg（合并 / 抽帧）
                    └→ Agent（WorkBuddy / Claude）
```

四层：

- **启动器**：选择工程、环境检查、启动工作台、窗口和更新；
- **工作台**：本地 HTTP 服务、工程快照、API、文件监听、导出；
- **出片引擎**：确定性的工程检查、资产检查、生成准入；
- **React 前端**：总览、创意、大纲、剧本、资产、审片、交付、关系图。

详细设计见 [docs/architecture.md](docs/architecture.md)。

### 代码导读

| 想了解 | 看哪里 |
|---|---|
| 启动器怎么找 Agent / ffmpeg | [`launcher.py`](launcher.py) |
| 工作台 API | [`app-src/workbench/wb/server.py`](app-src/workbench/wb/server.py) |
| 工程快照 | [`app-src/workbench/wb/snapshot.py`](app-src/workbench/wb/snapshot.py) |
| 图谱与节点 | [`app-src/workbench/wb/graph.py`](app-src/workbench/wb/graph.py) |
| 出片引擎 | [`app-src/workbench/wb/engine/`](app-src/workbench/wb/engine/) |
| React 前端 | [`app-src/workbench/web/src/`](app-src/workbench/web/src/) |

## 开发与构建

本仓库不保证完整运行，但可以检查源码和构建前端。

```bash
cd app-src/workbench/web
npm ci
npm run build
```

```bash
python -m compileall launcher.py app-src/workbench
```

完整运行还需要导演内容包、Agent、LibTV 和 FFmpeg；这些由官方安装包或外部服务提供。
参与贡献前请先看 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

源码公开，许可证为 **FSL-1.1-MIT**（Functional Source License 1.1, MIT Future License）。

你可以：

- 阅读、复制、修改源码；
- 在商业项目中使用；
- 再分发修改后的版本。

你不可以：

- 提供与本产品竞争的产品或服务（包括直接转售本产品）。

每个版本发布满两年后，自动转为 MIT 许可。FSL 不是 OSI 定义的开源许可，
本仓库应称为 **源码公开 / source-available**。

商业合作 / 授权咨询：**yuandgz@163.com**

第三方组件不适用本许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
完整条款见 [LICENSE](LICENSE)。

## 商标

`LibTV`、`LiblibAI`、`Seedance`、`WorkBuddy` 等名称是其各自权利人的商标。
本项目与这些公司没有隶属关系，也不代表其官方立场。

---

## English summary

Trestle AI Studio is a local-first AI video production workbench for Windows. It keeps a
project's ideas, script, scenes, storyboards, shots, prompts, assets and delivery package in a
local folder, and provides a workbench for editing, review and LibTV wiring. A coding agent
(WorkBuddy or Claude Code) writes the content; LibTV generates the images and videos; FFmpeg
assembles the final cut.

This repository is a **source-available showcase**, not a runnable distribution. The director
content pack, installer and runtime are part of the official product, available under
[Releases](https://github.com/dian-yuan/Trestle-AI-Studio/releases/latest).

Licensed under **FSL-1.1-MIT**: commercial use is permitted, competing use is not. Each release
becomes MIT after two years. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Commercial licensing / partnership: **yuandgz@163.com**
