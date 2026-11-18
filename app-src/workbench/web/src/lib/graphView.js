// 图谱节点的统一视图：来源 / 阶段 / 状态 / 版本。
// Home、Review、GraphPanel、Deliver 都从这里取，不再各写一套 role/type 判断。

export const STAGE_ORDER = ['reference', 'greybox', 'final', 'video', 'other']

export const STAGE_LABEL = {
  reference: '参考',
  greybox: '白膜',
  final: '定稿',
  video: '成片',
  other: '其它',
}

export const ROLE_LABEL = {
  reference: '参考图',
  reference_video: '参考视频',
  image2image: '图生图',
  first_frame: '首帧',
  last_frame: '尾帧',
  video: '成片',
}

export const STATE_LABEL = {
  planned: '待布线',
  wired: '已布线',
  generating: '生成中',
  generated: '已生成',
  reviewed: '已审',
  approved: '已通过',
  failed: '失败',
  stale: '待返修',
  missing: '节点已删',
  unbound: '待绑定',
}

const ROLE_STAGE = {
  reference: 'reference',
  reference_video: 'reference',
  image2image: 'final',
  first_frame: 'final',
  last_frame: 'final',
  video: 'video',
}

export const isRef = (n) => n && (n.role === 'reference' || n.role === 'reference_video')
export const isExternal = (n) => n && n.source === 'external'
export const nodeKey = (n) => String((n && (n.external?.node_id || n.node_id)) || '')
export const nodeStage = (n) => (n && n.stage) || ROLE_STAGE[n && n.role] || 'other'

export function stateTone(status) {
  if (status === 'missing' || status === 'failed') return 'err'
  if (status === 'generating') return 'info'
  if (status === 'generated' || status === 'reviewed' || status === 'approved') return 'ok'
  if (status === 'stale' || status === 'planned' || status === 'wired') return 'warn'
  return 'faint'
}

export function nodeView(n) {
  const stage = nodeStage(n)
  const key = nodeKey(n)
  const ext = n.external || {}
  const external = isExternal(n)
  return {
    raw: n,
    id: n.id || '',
    name: n.name || ext.label || ext.name || n.id || '',
    type: n.type || 'image',
    role: n.role || '',
    roleLabel: ROLE_LABEL[n.role] || n.role || '',
    stage,
    stageLabel: STAGE_LABEL[stage] || stage,
    source: n.source || (external ? 'external' : 'generated'),
    external,
    kind: ext.kind || n.type || 'image',
    bound: external ? !!key : true,
    missing: !!(ext.missing || n.status === 'missing'),
    nodeKey: key,
    hasResult: !!(n.file || n.url),
    status: n.status || 'planned',
    stateLabel: STATE_LABEL[n.status] || n.status || '',
    tone: stateTone(n.status),
    needsPush: !!n.needs_push,
    model: n.model || '',
    rev: Number(n.current_rev || n.rev || 0),
    historyCount: Array.isArray(n.history) ? n.history.length : 0,
    prompt: n.prompt || '',
  }
}

// 一段 / 一镜的节点分组。materials 含参考图与参考视频，永不参与成片。
export function groupNodes(nodes = []) {
  const out = {
    materials: [], greybox: [], frames: [],
    video: null, videos: [], refVideo: null, other: [],
  }
  for (const raw of nodes) {
    const v = nodeView(raw)
    if (isRef(raw)) {
      out.materials.push(v)
      if (raw.role === 'reference_video') out.refVideo = v
      continue
    }
    if (v.stage === 'greybox') { out.greybox.push(v); continue }
    if (raw.role === 'video') {
      out.videos.push(v)
      if (!out.video) out.video = v
      continue
    }
    if (v.type === 'image') { out.frames.push(v); continue }
    out.other.push(v)
  }
  return out
}

// 首帧：first_frame > image2image(final) > last_frame；白膜不参与。
export function pickFrame(nodes = []) {
  const rank = { first_frame: 0, image2image: 1, last_frame: 2 }
  return nodes
    .filter((n) => n && n.type === 'image' && !isRef(n))
    .filter((n) => (n.stage || 'final') !== 'greybox')
    .filter((n) => n.role in rank)
    .sort((a, b) => rank[a.role] - rank[b.role])[0] || null
}

// 一镜的节点视图（Review 用 graph 节点；Home 可直接用后端字段）。
export function shotView(shot, nodes = []) {
  const g = groupNodes(nodes)
  const grouped = nodes.length > 0
  return {
    ...g,
    frame: grouped ? nodeView(pickFrame(nodes) || {}) : null,
    externalRefs: grouped
      ? g.materials.map((v) => ({
          id: v.id, label: v.name, kind: v.kind, node_id: v.nodeKey,
          bound: v.bound, stage: v.stage, role: v.role,
          status: v.status, missing: v.missing,
        }))
      : (shot?.external_refs || []),
    needsBinding: grouped
      ? g.materials.filter((v) => !v.bound || v.missing).length
      : Number(shot?.needs_binding || 0),
    frameRev: grouped
      ? (g.frame ? (g.frame.rev || 1) : 0)
      : Number(shot?.frame_rev || 0),
    videoRev: grouped
      ? (g.video ? (g.video.rev || 1) : 0)
      : Number(shot?.video_rev || 0),
    frameHistory: grouped
      ? (g.frame ? g.frame.historyCount : 0)
      : Number(shot?.frame_history || 0),
    videoHistory: grouped
      ? (g.video ? g.video.historyCount : 0)
      : Number(shot?.video_history || 0),
  }
}
