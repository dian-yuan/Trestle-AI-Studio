# Trestle 出片引擎（wb/engine）

> 这是从旧 `ai-video-director/bin/director.py` 抽出来的确定性引擎。
> 旧 skill 已删除；引擎独立在 `wb/engine/`，随工作台打包。

---

## 只保留什么

| 命令 | 干什么 |
| :--- | :--- |
| `changji check` | 填充体检：资产引用 / 机位 / 时长 / 提示词 / 六栏 / 场段归属 |
| `changji asset check` | 资产体检：字段 / 三层 / 文件 / level / ID 唯一 |
| `changji scene check` | 场分析体检：目标 / 阻力 / 信息增量 / 揭示与按住 |
| `changji segment check <段号>` | 单段体检：时长 / 镜号归属 / 提示词占位 |
| `changji unique` | 唯一性：镜号 / 场号 / 分镜号 / 资产 ID / 单元号 / 生成物 |
| `changji ready` | 生成准入：`check` + 资产有参考图 + 场景有机位/光位 + **有变体必须先有母图** + 可选关键帧；**不打分** |

---

## 不保留什么

这些命令**不提供**：

```
score / look        # 打分、影像评估：质量由用户审片
gate / done / status / next / note / save / log / audit
pack                # 工作台 / LibTV 不读 pack.json
adapter / upgrade / tree / space
idea / script / split
unit / asset new|list|level / segment new|list
board / build
```

原因：

- 打分不是门禁，替代不了用户看片；
- 站号状态机是旧六环骨架，说好版用自己的五段；
- CRUD / 画布 / 工程运维归工作台；
- `pack.json` 没有消费方。

---

## 结构

```
wb/engine/
├─ cli.py          # 命令入口
├─ checks.py       # 保留的命令：check / asset / scene / segment / unique
├─ ready.py        # 硬条件准入（不依赖 score/look）
└─ core.py         # 确定性实现（从旧 director.py 搬来）
```

`core.py` 只保留这五个命令和它们依赖的确定性实现；
`checks.py` / `cli.py` 是入口，不再暴露旧命令。

---

## 用法

```bash
cd app-src/workbench
python -m wb.engine.cli check --cwd <工程根>
python -m wb.engine.cli ready --cwd <工程根> -u S1-E01
```

或在启动器 / 工作台里由程序自动调用；Agent 只在 headless 或对话里查结果。
