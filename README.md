# Trestle AI Studio

> 本地优先的 AI 视频工程工作台。把剧本、分场、分镜、提示词和资产放进一个工程目录，
> 用本地工作台读写，最后交给 LibTV 出片。

![License](https://img.shields.io/badge/license-FSL--1.1--MIT-blue)
![Status](https://img.shields.io/badge/status-source--available-lightgrey)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

---

## ⚠️ 这是源码展示，不是可运行发行版

本仓库用于展示 Trestle 的工程结构、工作台实现和出片引擎设计。

- **不包含**导演内容包 / 专业 skill；
- **不包含**安装包构建脚本、运行时和第三方二进制；
- **不保证**克隆后可以直接跑通完整流程；
- 完整产品是 **Trestle 专业版**；
- 本仓库的 **Releases** 提供官方安装包和热更新，源码分支是展示快照。

如果你要找的是可以安装使用的版本，请到 Releases 下载官方安装包，而不是从源码构建。

---

## 这个仓库展示什么

| 模块 | 说明 |
|---|---|
| 启动器 | 选择工程、启动工作台、环境检查、窗口管理 |
| 工作台服务 | 本地 HTTP 服务，只绑定 `127.0.0.1`；读写工程、快照、导出、LibTV 客户端 |
| 出片引擎 | 工程准入检查、素材检查、交付前校验 |
| React 前端 | 总览、剧本、资产、审片、交付等页面 |
| 工程模板 | `templates/empty/` 展示一个空工程的文件结构 |
| 文档 | 架构、产品说明、代码导读 |

## 不包含什么

- 导演内容包 / 专业 skill（本项目的核心方法论）
- 专业工艺库、案例库和提示词规则
- 安装包、热更新、运行时、FFmpeg、CPython
- 内部计划、设计稿、测试工程和发布脚本

本仓库的源码可以阅读、学习和商用（受许可证限制），但它不是完整产品。

---

## 架构

```text
┌─────────────────┐         ┌──────────────────────────┐
│ 启动器           │────────▶│ 工作台 HTTP 127.0.0.1     │
│ launcher.py     │         │ app-src/workbench        │
└─────────────────┘         └───────────┬──────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              工程目录              LibTV MCP            ffmpeg
              project/              (外部服务)           (外部程序)
                    ▲
                    │ 读写
          ┌─────────┴─────────┐
          │ Agent             │
          │ WorkBuddy / Claude│
          └─────────┬─────────┘
                    │ 读取
                    ▼
          导演内容包（不在本仓库）
```

设计取舍：

- **本地优先**：工程文件在本地，工作台只绑 `127.0.0.1`；
- **文件即事实源**：工程状态以目录里的 JSON / Markdown 为准，不依赖聊天记录；
- **工作台只布线，不生成**：生成由用户在 LibTV 里点击，避免误花积分；
- **Agent 只写内容**：导演内容包负责创作规则，工作台负责工程和同步。

---

## 代码导读

| 想了解 | 看哪里 |
|---|---|
| 启动器怎么找 Agent / ffmpeg | [`launcher.py`](launcher.py) |
| 工作台 HTTP API | [`app-src/workbench/wb/server.py`](app-src/workbench/wb/server.py) |
| 工程快照与页面数据 | [`app-src/workbench/wb/snapshot.py`](app-src/workbench/wb/snapshot.py) |
| 图谱与节点 | [`app-src/workbench/wb/graph.py`](app-src/workbench/wb/graph.py) |
| 出片引擎与准入检查 | [`app-src/workbench/wb/engine/`](app-src/workbench/wb/engine/) |
| React 前端 | [`app-src/workbench/web/src/`](app-src/workbench/web/src/) |
| 空工程结构 | [`templates/empty/`](templates/empty/) |

---

## 本地构建

本仓库不保证完整运行，但可以检查源码和构建前端。

前端：

```bash
cd app-src/workbench/web
npm ci
npm run build
```

Python 语法检查：

```bash
python -m compileall launcher.py app-src/workbench
```

完整运行还需要：

- 导演内容包（不在本仓库）；
- WorkBuddy 或 Claude Code；
- LibTV 账号与授权；
- FFmpeg。

这些依赖由官方安装包提供。

---

## 专业版

官方安装包提供完整能力：

- 导演内容包：从创意、剧本、分场、分镜到提示词的完整流程；
- 专业工艺库与检查规则；
- 工作台布线、LibTV 同步、出片与交付包；
- 安装包与自动更新；
- 商业支持。

官方安装包在本仓库的 **Releases** 中提供。

商业合作 / 授权咨询：**yuandgz@163.com**

---

## 许可证

本仓库使用 **FSL-1.1-MIT**（Functional Source License 1.1, MIT Future License）。

你可以：

- 阅读、复制、修改源码；
- 在商业项目中使用；
- 再分发修改后的版本。

你不可以：

- 提供与本产品竞争的产品或服务（包括直接转售本产品）。

每个版本发布满两年后，自动转为 MIT 许可。

FSL 不是 OSI 定义的开源许可，本仓库应称为 **源码公开 / source-available**。
完整条款见 [LICENSE](LICENSE)。

第三方组件不适用本许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 商标

`LibTV`、`LiblibAI`、`Seedance`、`WorkBuddy` 等名称是其各自权利人的商标。
本项目与这些公司没有隶属关系，也不代表其官方立场。

---

## English summary

Trestle AI Studio is a local-first AI video production workbench for Windows. It keeps a
project's script, scenes, storyboards, prompts and assets in a local folder, and provides a
local workbench for editing and review before handing the project to LibTV for generation.

**This repository is a source-available showcase, not a runnable distribution.** It contains
the launcher, workbench server, rendering engine, React UI and project skeleton. The director
content pack and the complete skill are not included. Official installers are available under
Releases.

Licensed under **FSL-1.1-MIT**: commercial use is permitted, competing use is not. Each release
becomes MIT after two years. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Commercial licensing / partnership: **yuandgz@163.com**
