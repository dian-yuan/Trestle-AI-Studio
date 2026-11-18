# 工程文件结构

> 本文只描述目录和文件职责，不展开完整字段契约。
> 字段定义、创作规则和生成规则属于专业内容包，不在本仓库。

---

## 1. 顶层

```text
<项目>/
├─ project.json              工程元信息、集列表、外部适配器
├─ design/                   创意、导演简报、审阅记录
├─ assets/                   角色 / 场景 / 道具 / 风格 / 品牌资产
├─ units/                    一集一个目录
└─ .workbuddy/               运行态：备份、临时文件、回收站（不提交）
```

工程目录本身就是数据库。工作台、Agent 和用户都通过读写这些文件协作，不依赖聊天记录或外部数据库。

## 2. 一集

```text
units/<集号>/
├─ manifest.json             集号、片型、平台、时长、资产引用
├─ script/
│  ├─ copy.md                内容大纲 / 分场剧本
│  ├─ draft/                 草稿
│  └─ derived/               从剧本派生的内容
├─ storyboard/
│  ├─ scenes.json            场
│  ├─ shots.json             镜头
│  └─ graph.json             节点、连线、阶段（存在时）
├─ prompts/
│  └─ prompts.json           静帧 / 运动提示词
├─ production/
│  └─ <场>/<段>/             生成单元：unit.json、参考、renders
├─ renders/
│  └─ ...                    本集渲染产物
├─ review/
│  └─ shot-review.json       审片状态
└─ deliver/
   ├─ delivery.json          交付元信息
   ├─ final/                 成片、字幕、封面、音轨
   ├─ preview/               预览
   └─ publish/               发布副本
```

集号使用 `S<季>-E<集>`，例如 `S1-E01`。

## 3. 资产

```text
assets/
├─ characters/<ID>/          角色
│  ├─ manifest.json          身份、装造、音色
│  ├─ prompts.md             资产说明与提示词
│  ├─ refs/                  母图、四视图
│  ├─ variants/<key>/        装造变体
│  └─ voice/                 音色卡
├─ scenes/<ID>/              场景
│  ├─ manifest.json
│  ├─ space.json             房间、区域、机位、光位
│  ├─ prompts.md
│  ├─ refs/
│  └─ variants/<key>/
├─ props/<ID>/               道具
├─ style/<ID>/               风格
│  ├─ manifest.json
│  └─ style_bible.md         媒介、画幅、光、色、质感、禁止项
└─ brand/<ID>/               字标、图标、片头尾
```

资产 ID 建议使用可读前缀：

- 角色：`CH-...`
- 场景：`SC-...`
- 道具：`PR-...`
- 风格：`ST-...`
- 品牌：`BR-...`

具体命名规则由专业内容包定义；工作台只要求 ID 稳定、可引用。

## 4. 命名约定

| 对象 | 示例 | 说明 |
|---|---|---|
| 集 | `S1-E01` | 季 + 集 |
| 场 | `SC01` | 稳定编号 |
| 段 | `E01-01` | 一集内的生成段 |
| 镜头 | `001` | 三位数字，连续 |
| 资产 | `CH-HOST-01` | 类型前缀 + 名称 + 序号 |
| 变体 | `variants/<key>/` | key 由内容包或用户定义 |
| 提示词节点 | `frame_001` / `video_001` | 与镜头号对应 |

## 5. 谁写什么

| 角色 | 写 | 不写 |
|---|---|---|
| Agent / 导演 | `design/`、`script/`、`storyboard/`、`prompts/`、资产说明 | 程序文件、LibTV 映射、审片状态 |
| 工作台 | `project.json`、`libtv.json`、`review/`、运行态 | 剧本、分镜内容、提示词 |
| 用户 | 审片、确认、在 LibTV 点生成 | 程序文件和账本 |

工作台写文件前会备份；Agent 只写内容文件，避免覆盖程序状态。

## 6. 生命周期

```text
创意 / 导演简报
      ↓
剧本 / 内容大纲
      ↓
场 · 分镜 · 镜头
      ↓
提示词
      ↓
生成单元（production/）
      ↓
审片（review/）
      ↓
交付包（deliver/）
```

每一层的输入和门禁由专业内容包定义；工作台负责读取、展示、检查和布线。

## 7. 运行态

`.workbuddy/` 是程序内部目录：

- `trash/`：回收站；
- `backup/`：写入前备份；
- `tmp/`：临时文件、启动状态。

它不进入版本控制，也不应该被 Agent 修改。

## 8. 版本控制建议

- 工程目录可以整体放进 Git；
- 大文件（渲染、成片、参考图）建议使用 Git LFS 或单独存放；
- `.workbuddy/`、缓存和临时文件加入 `.gitignore`；
- 交付包可以提交，也可以单独归档。

## 9. 结构示例

本仓库的 [`templates/empty/`](../templates/empty/) 是一个最小工程，包含：

- `project.json`
- 五类资产目录及其 `_TEMPLATE`
- 一集的完整目录
- 交付元信息

完整字段契约、创作规则和生成规则不在本仓库，由专业内容包提供。
