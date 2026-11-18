// 磁盘上的状态 / 等级仍是英文原值（draft / final …），这里只负责显示。
// 不要在这里改回写逻辑：写文件时必须存原值，否则脚本和工作台会对不上。

const STATUS_LABELS = {
  draft: '草稿',
  developing: '发展中',
  ready: '可写剧本',
  final: '定稿',
  pending: '待办',
  done: '已完成',
  skipped: '跳过',
  ok: '通过',
  no: '返工',
}

const LEVEL_LABELS = {
  '碎片': '碎片',
  L1: 'L1 骨架',
  L2: 'L2 可发展',
  L3: 'L3 可写剧本',
}

// 资产的 L1–L4 和创意的 L1–L3 不是一回事：
// 资产 L1 设定 / L2 定妆或母图 / L3 四视图 + space / L4 多状态
const ASSET_LEVEL_LABELS = {
  L1: 'L1 设定',
  L2: 'L2 定妆',
  L3: 'L3 四视图',
  L4: 'L4 多状态',
}

export function statusLabel(value, fallback = '—') {
  const raw = String(value == null ? '' : value).trim()
  if (!raw) return fallback
  return STATUS_LABELS[raw.toLowerCase()] || raw
}

export function levelLabel(value, fallback = '—') {
  const raw = String(value == null ? '' : value).trim()
  if (!raw) return fallback
  return LEVEL_LABELS[raw.toUpperCase()] || LEVEL_LABELS[raw] || raw
}

export function assetLevelLabel(value, fallback = '—') {
  const raw = String(value == null ? '' : value).trim()
  if (!raw) return fallback
  return ASSET_LEVEL_LABELS[raw.toUpperCase()] || raw
}
