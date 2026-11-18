# Third-Party Notices

本仓库包含下列第三方组件。它们**不适用**本项目的 FSL-1.1-MIT 许可，
各自遵循其原有许可；本项目的许可只覆盖原创代码和文档。

---

## 1. LeiBox 组件

本仓库的 React 前端使用了两个 LeiBox 组件：

| 组件 | 目录 | 上游 |
|---|---|---|
| Dot Grid Loader（点阵加载） | `app-src/workbench/web/src/leibox/ai-generating/` | <https://leibox.pantoria.cn/ai/components/ai-generating.json> |
| Transfer Glow Confirmation（转账光雾确认） | `app-src/workbench/web/src/leibox/transfer-glow/` | <https://leibox.pantoria.cn/ai/components/transfer-glow.json> |

LeiBox 目录对这两个组件的说明是：允许再分发源码，但要求**保留源码声明与设计参考署名**；目录没有为组件指定统一许可证。源码中保留的声明和署名即为准。

- Transfer Glow 的设计参考：Gabriel，<https://x.com/gabriell_lab/status/2117982465415389633/video/1>
- 实现为独立的 DOM / Canvas2D 代码，不包含原视频或原图。

## 2. Lucide 图标

`transfer-glow` 组件内嵌 Lucide 图标。Lucide 使用 ISC 许可：

```
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

部分 Lucide 图标源自 Feather，Copyright (c) 2013-present Cole Bemis，同样使用 ISC 许可。
完整声明见 `app-src/workbench/web/src/leibox/transfer-glow/TransferGlowEngine.js` 内嵌文本。

## 3. Inter 字体

`transfer-glow` 组件内嵌 Inter 字体子集。Inter 使用 SIL Open Font License 1.1：

- Copyright 2020 The Inter Project Authors (<https://github.com/rsms/inter>)
- 许可证全文见 <https://scripts.sil.org/OFL>
- 完整 OFL 文本已内嵌在 `TransferGlowEngine.js` 中

## 4. JavaScript 依赖

直接依赖：

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| react | 19.3.1 | MIT | UI |
| react-dom | 19.3.1 | MIT | UI |
| react-router-dom | 7.18.5 | MIT | 路由 |
| marked | 18.1.3 | MIT | Markdown 渲染 |
| vite | 8.3.6 | MIT | 构建（开发依赖） |
| @vitejs/plugin-react | 6.2.1 | MIT | 构建（开发依赖） |
| playwright-core | 1.65.0 | Apache-2.0 | 本地自动化（开发依赖） |

传递依赖的许可证以 `app-src/workbench/web/package-lock.json` 为准，主要包括：

- MIT
- Apache-2.0（如 `detect-libc`）
- MPL-2.0（`lightningcss`，由 Vite 使用）
- BSD-3-Clause（`source-map-js`）
- ISC（`picocolors`）

MPL-2.0 组件的源码可从其上游仓库或 npm 包获取，许可证全文见
<https://www.mozilla.org/MPL/2.0/>。

## 5. Python 代码

本仓库展示的 Python 代码只使用 Python 标准库。`build/` 目录不在本仓库中；
官方构建使用的 Pillow、PyInstaller 等工具不计入本仓库。

## 6. 官方安装包中的其他组件

官方安装包是独立构建，包含本仓库之外的组件，例如：

- Apache-2.0 许可的第三方 skill
- LGPL 构建的 FFmpeg
- CPython 运行时（PSF License）
- Inno Setup（安装器）

这些组件的许可证文本随官方安装包提供，不在本仓库范围内。

## 7. 商标

`LibTV`、`LiblibAI`、`Seedance`、`WorkBuddy` 等名称是其各自权利人的商标。
本项目与这些公司没有隶属关系，也不代表其官方立场。
