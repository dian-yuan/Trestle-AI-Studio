# Contributing

感谢关注 Trestle。

## 先读这个

本仓库是**源码展示（source-available showcase）**，不是完整产品：

- 不包含导演内容包 / 专业 skill；
- 不保证克隆后可以跑通完整流程；
- 官方产品在私有仓开发和构建。

因此，功能类 PR 不一定能合并；文档、代码可读性、明显的 bug 修复更容易被接受。

## 可以贡献什么

- 文档修正、错别字、示例补充；
- 核心代码的可读性改进；
- 与专业内容包无关的 bug 修复；
- 构建、开发体验改进；
- 安全问题请走私密渠道，见 [SECURITY.md](SECURITY.md)。

## 不可以贡献什么

- 不要提交导演内容包、专业 skill、提示词库或案例库；
- 不要提交客户项目、真实画布 ID、token、密钥；
- 不要提交从第三方资料整理的内容，除非你有明确授权。

## 开发环境

- Windows 10/11；
- Python 3.10+；
- Node.js 20+。

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

完整运行还需要导演内容包、Agent、LibTV 和 FFmpeg，这些不在本仓库。

## 代码风格

- Python 只使用标准库；保持现有的模块边界；
- React 使用函数组件和现有样式体系；
- 注释可以使用中文；
- 不引入新的运行时依赖，除非在 PR 里说明理由。

## 提交 PR

1. 先开 Issue 说明问题和范围；
2. 保持改动聚焦，不要顺手重构无关文件；
3. 说明你验证了什么（构建、语法检查、手动路径）；
4. 确认没有提交敏感信息。

## 许可证

贡献的代码按本仓库的 **FSL-1.1-MIT** 许可提供。
如果你不同意，请不要提交 PR。
