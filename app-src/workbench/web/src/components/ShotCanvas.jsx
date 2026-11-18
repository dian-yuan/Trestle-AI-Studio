import React, { useEffect, useMemo, useState } from 'react'
import { ROLE_LABEL, STATE_LABEL } from '../lib/graphView'

// 分镜页的「两个画布」：本地这一镜的结构 ↔ LibTV 线上同一镜的结构。
// 和资产页同一套逻辑：层级排列、点节点看差异、右键本地图开 WorkBuddy。

const TYPE_LABEL = {
  image: '图片',
  video: '视频',
  'material-style': '风格',
  material: '素材',
  text: '文本',
}

const REMOTE_STATUS = {
  done: '已生成',
  generating: '生成中',
  failed: '生成失败',
  idle: '还没结果',
}

const STATUS_TONE = {
  done: 'ok',
  generating: 'info',
  failed: 'err',
  missing: 'err',
  planned: 'warn',
  wired: 'warn',
  generated: 'ok',
  reviewed: 'ok',
  approved: 'ok',
  stale: 'warn',
}

const excerpt = (t, n = 42) => {
  const s = String(t || '').replace(/\s+/g, ' ').trim()
  return s.length > n ? s.slice(0, n) + '…' : s
}

const pad = (v) => String(v || '').trim().padStart(3, '0')

const PANEL_W = 560
const PAD = 18
const GAP_X = 36
const NODE_H = 100

function rankedLayout(nodes, edges) {
  if (!nodes.length) return null
  const byId = {}
  for (const n of nodes) byId[n.id] = n
  const rank = {}
  for (const n of nodes) rank[n.id] = 0
  for (let pass = 0; pass < nodes.length + 1; pass += 1) {
    let changed = false
    for (const e of edges) {
      if (!byId[e.source] || !byId[e.target]) continue
      const nr = (rank[e.source] || 0) + 1
      if (nr > (rank[e.target] || 0)) {
        rank[e.target] = nr
        changed = true
      }
    }
    if (!changed) break
  }
  const cols = {}
  for (const n of nodes) {
    const r = rank[n.id] || 0
    if (!cols[r]) cols[r] = []
    cols[r].push(n)
  }
  const keys = Object.keys(cols).map(Number).sort((a, b) => a - b)
  const rows = Math.max(...keys.map((k) => cols[k].length), 1)
  const w = Math.max(110, Math.min(190,
    (PANEL_W - PAD * 2 - GAP_X * (keys.length - 1)) / keys.length))
  const placed = []
  keys.forEach((k, ci) => {
    cols[k].sort((a, b) => (a.y || 0) - (b.y || 0)).forEach((n, ri) => {
      placed.push({
        ...n,
        left: PAD + ci * (w + GAP_X),
        top: PAD + ri * (NODE_H + 8),
        sw: w,
        sh: NODE_H,
      })
    })
  })
  const pos = {}
  for (const n of placed) pos[n.id] = n
  const paths = []
  for (const e of edges) {
    const a = pos[e.source]
    const b = pos[e.target]
    if (!a || !b) continue
    const sx = a.left + a.sw
    const sy = a.top + a.sh / 2
    const tx = b.left
    const ty = b.top + b.sh / 2
    if (tx <= sx) {
      paths.push({
        id: e.source + '-' + e.target,
        d: 'M ' + sx + ' ' + sy + ' C ' + (sx + 18) + ' ' + (sy - 24)
          + ', ' + (tx + 18) + ' ' + (ty - 24) + ', ' + tx + ' ' + ty,
      })
      continue
    }
    const dx = Math.max(18, (tx - sx) / 2)
    paths.push({
      id: e.source + '-' + e.target,
      d: 'M ' + sx + ' ' + sy + ' C ' + (sx + dx) + ' ' + sy + ', '
        + (tx - dx) + ' ' + ty + ', ' + tx + ' ' + ty,
    })
  }
  return {
    placed, paths,
    w: PAD * 2 + keys.length * w + (keys.length - 1) * GAP_X,
    h: Math.max(420, PAD * 2 + rows * NODE_H + (rows - 1) * 8),
  }
}

function nodeKey(n) {
  return String((n && (n.external && n.external.node_id || n.node_id)) || '')
}

export default function ShotCanvas({
  unit, segment, shotId, shotLabel, graph, stateRows, canvasGraph,
  stateAt, auto, stateBusy, hasLibtv, onToggleAuto, onRefreshState,
  onWbNode, onPushNode, onPullNode, onOpenNode,
  onWbShot, onWbWire, onPushAll, onSyncResults, onWireShot, onWireSegment,
  onReveal, onOpenLibTV,
}) {
  const [picked, setPicked] = useState('')
  const [ctx, setCtx] = useState(null)

  useEffect(() => {
    if (!ctx) return undefined
    const close = () => setCtx(null)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [ctx])

  useEffect(() => { setPicked(''); setCtx(null) }, [segment, shotId])

  const seg = ((graph && graph.graph && graph.graph.segments) || [])
    .find((x) => x.id === segment)
  const all = (seg && seg.nodes) || []
  const rows = stateRows || []
  const rowOf = (n) => rows.find((r) => r.id === n.id) || null

  const localNodes = useMemo(() => {
    if (!shotId) return []
    const own = all.filter((n) => pad(n.shot) === pad(shotId))
    const keep = new Set(own.map((n) => n.id))
    const byId = {}
    for (const n of all) byId[n.id] = n
    const addRefs = (n) => {
      for (const r of (n.refs || [])) {
        if (keep.has(r) || !byId[r]) continue
        keep.add(r)
        addRefs(byId[r])
      }
    }
    own.forEach(addRefs)
    const out = []
    for (const id of keep) {
      const n = byId[id]
      if (!n) continue
      out.push({
        id: n.id,
        label: n.id,
        name: ROLE_LABEL[n.role] || n.role || '',
        node: n,
        y: out.length,
        status: STATE_LABEL[n.status] || n.status || '',
        prompt: n.prompt || '',
        diff: !!(rowOf(n) && rowOf(n).remote_changed),
      })
    }
    return out
  }, [all, shotId, rows])

  const localEdges = useMemo(() => {
    const ids = new Set(localNodes.map((n) => n.id))
    const out = []
    for (const n of localNodes) {
      for (const r of (n.node.refs || [])) {
        if (ids.has(r)) out.push({ source: r, target: n.id })
      }
    }
    return out
  }, [localNodes])

  const localLay = rankedLayout(localNodes, localEdges)

  const localByNodeId = {}
  for (const n of localNodes) {
    const k = nodeKey(n.node)
    if (k) localByNodeId[k] = n
  }

  const remote = canvasGraph || {}
  const remoteNodes = useMemo(() => {
    const src = (remote.nodes || []).filter((n) => n.type !== 'group')
    const keep = new Set()
    for (const n of src) {
      if (localByNodeId[n.id]) keep.add(n.id)
    }
    for (let pass = 0; pass < 4; pass += 1) {
      let changed = false
      for (const e of (remote.edges || [])) {
        if (keep.has(e.target) && !keep.has(e.source)
          && src.some((n) => n.id === e.source)) {
          keep.add(e.source)
          changed = true
        }
      }
      if (!changed) break
    }
    if (!keep.size) return []
    return src.filter((n) => keep.has(n.id)).map((n, i) => {
      const local = localByNodeId[n.id]
      return {
        id: n.id, label: n.name || n.id, node: n, y: n.y || i,
        local,
        status: n.status,
        diff: !!(local && local.diff),
      }
    })
  }, [remote, localNodes])

  const remoteIds = new Set(remoteNodes.map((n) => n.id))
  const remoteEdges = (remote.edges || []).filter(
    (e) => remoteIds.has(e.source) && remoteIds.has(e.target))
  const remoteLay = rankedLayout(remoteNodes, remoteEdges)

  const selLocal = localNodes.find((n) => picked === 'local:' + n.id) || null
  const selRemote = remoteNodes.find((n) => picked === 'remote:' + n.id) || null
  const selRow = selLocal ? rowOf(selLocal.node) : null
  const diffCount = localNodes.filter((n) => n.diff).length

  const openCtx = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setCtx({ x: e.clientX, y: e.clientY })
  }

  const localCard = (n) => {
    const on = picked === 'local:' + n.id
    return (
      <button type="button" key={n.id}
        className={'lvc-node' + (n.diff ? ' diff' : '') + (on ? ' sel' : '')}
        style={{ left: n.left, top: n.top, width: n.sw, height: n.sh }}
        onClick={() => setPicked(on ? '' : 'local:' + n.id)}>
        <span className="lvc-node-top">
          <i className={'cmp-dot ' + (STATUS_TONE[n.node.status] || '')} />
          <b>{n.label}</b>
          <em className="lvc-role">{n.name}</em>
        </span>
        <span className="lvc-node-meta">{n.status}
          {n.node.model ? ' · ' + n.node.model : ''}</span>
        <span className="lvc-node-prompt">{excerpt(n.prompt) || '（还没有提示词）'}</span>
        {n.diff && <em className="lvc-badge">有差异</em>}
      </button>
    )
  }

  const remoteCard = (n) => {
    const on = picked === 'remote:' + n.id
    return (
      <button type="button" key={n.id}
        className={'lvc-node remote' + (n.diff ? ' diff' : '') + (on ? ' sel' : '')}
        style={{ left: n.left, top: n.top, width: n.sw, height: n.sh }}
        onClick={() => setPicked(on ? '' : 'remote:' + n.id)}>
        <span className="lvc-node-top">
          <i className={'cmp-dot ' + (STATUS_TONE[n.status] || '')} />
          <b>{n.local ? n.local.label : n.label}</b>
          <em className="lvc-role">
            {n.local ? n.local.name : (TYPE_LABEL[n.node.type] || n.node.type)}
          </em>
        </span>
        <span className="lvc-node-meta">
          {REMOTE_STATUS[n.status] || n.status}
          {n.node.style_name ? ' · ' + n.node.style_name : ''}
        </span>
        <span className="lvc-node-prompt">{excerpt(n.node.prompt) || ' '}</span>
        {n.diff && <em className="lvc-badge">有差异</em>}
      </button>
    )
  }

  const renderBoard = (lay, cards) => {
    if (!lay) return <p className="empty" style={{ padding: '10px 4px' }}>还没有节点。</p>
    return (
      <div className="lvc-board" style={{ width: lay.w, height: lay.h }}>
        <svg className="lvc-edges" width={lay.w} height={lay.h} aria-hidden="true">
          <defs>
            <marker id="shot-arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(160,180,210,.55)" />
            </marker>
          </defs>
          {lay.paths.map((p) => (
            <path key={p.id} d={p.d} fill="none"
              stroke="rgba(160,180,210,.42)" strokeWidth="1.2"
              markerEnd="url(#shot-arrow)" />
          ))}
        </svg>
        {cards}
      </div>
    )
  }

  if (!seg || !localNodes.length) {
    return (
      <div className="glass-card libtv-compare">
        <div className="sec-head cmp-head">
          <b>这一镜的画布 · 本地 ↔ LibTV</b>
        </div>
        <p className="hint" style={{ margin: 0 }}>
          这一镜还没有布线方案节点。先在右侧让 WorkBuddy 出图谱，或点「布线到 LibTV」。
        </p>
        <div className="trunk-acts" style={{ marginTop: 8 }}>
          <button className="btn primary" onClick={onWbShot}>让 WorkBuddy 写这一镜</button>
          <button className="btn" onClick={onWbWire}>让 WorkBuddy 出布线方案</button>
        </div>
      </div>
    )
  }

  return (
    <div className="glass-card libtv-compare">
      <div className="sec-head cmp-head">
        <b>这一镜的画布 · 本地 ↔ LibTV</b>
        <span className="faint small">{shotLabel || shotId}</span>
        <span className={'cdot ' + (stateBusy ? 'warn' : 'ok')} />
        <span className="faint small">
          {stateAt ? '读取于 ' + stateAt
            : (hasLibtv ? '还没读线上状态' : '这场还没连 LibTV')}
          {auto ? ' · 每 15 秒自动读' : ''}
        </span>
        <span className="grow" />
        {!!diffCount && <span className="cmp-chip warn">{diffCount} 个节点有差异</span>}
        <label className="cmp-auto">
          <input type="checkbox" checked={!!auto}
            onChange={(e) => onToggleAuto(e.target.checked)} />
          实时
        </label>
        <button className="btn small" disabled={!!stateBusy} onClick={onRefreshState}>
          {stateBusy ? '读取中…' : '立即读取'}
        </button>
      </div>

      <div className="lvc-duo">
        <section className="lvc-pane local" onContextMenu={openCtx}>
          <div className="lvc-pane-head">
            <b>本地这一镜的方案</b>
            <span className="faint small">{localNodes.length} 节点</span>
            <span className="grow" />
            <button className="btn small" onClick={onWbShot}>修改这一镜</button>
            <button className="btn small" onClick={onWbWire}>修改布线方案</button>
          </div>
          <div className="lvc-board-wrap">
            {renderBoard(localLay, localLay.placed.map(localCard))}
          </div>
          <div className="lvc-pane-foot">
            <button className="btn small primary" onClick={onWireShot}>布线这一镜</button>
            <button className="btn small" onClick={onWireSegment}>布线整段</button>
            <button className="btn small" onClick={onPushAll}>推送全部</button>
            <button className="btn small" disabled={!!stateBusy}
              onClick={onRefreshState}>同步线上状态</button>
            <button className="btn small" onClick={onReveal}>工程文件夹</button>
            <span className="faint small">右键画布 / 节点有更多操作</span>
          </div>
        </section>

        <section className="lvc-pane remote">
          <div className="lvc-pane-head">
            <b>LibTV 线上这一镜</b>
            <span className="faint small">只读</span>
            <span className="grow" />
            <button className="btn small" disabled={!hasLibtv}
              onClick={onOpenLibTV}>去 LibTV ↗</button>
            <button className="btn small" disabled={!hasLibtv}
              onClick={onSyncResults}>同步结果</button>
          </div>
          <div className="lvc-board-wrap">
            {remoteLay ? renderBoard(remoteLay, remoteLay.placed.map(remoteCard)) : (
              <p className="empty" style={{ padding: '10px 4px' }}>
                还没读到这一镜的 LibTV 节点；点「同步线上状态」。
              </p>
            )}
          </div>
          <div className="lvc-pane-foot">
            <span className="faint small">
              线上只读；改提示词请在中间选节点后推送，或去 LibTV 改。
            </span>
          </div>
        </section>
      </div>

      {!!(selLocal || selRemote) && (
        <div className="lvc-detail">
          {selLocal ? (
            <>
              <div className="cmp-detail-head">
                <b>{selLocal.label} · {selLocal.name}</b>
                <span className="faint small">
                  本地 {selLocal.status}
                  {selRow ? ' · LibTV ' + (REMOTE_STATUS[selRow.status] || selRow.status) : ' · 未布线'}
                  {selRow && selRow.at ? ' · 线上读于 ' + selRow.at : ''}
                </span>
                <span className="grow" />
                <button className="btn small primary" onClick={() => onWbNode(selLocal.node)}>
                  只改这个节点的提示词
                </button>
                {!!nodeKey(selLocal.node) && (
                  <>
                    <button className="btn small" onClick={() => onPushNode(selLocal.node)}>
                      推送本地方案 → LibTV
                    </button>
                    {!!(selRow && selRow.remote_changed) && (
                      <button className="btn small" onClick={() => onPullNode(selLocal.node)}>
                        采用线上方案
                      </button>
                    )}
                    <button className="btn small" onClick={() => onOpenNode(selLocal.node)}>
                      在 LibTV 改这个节点 ↗
                    </button>
                  </>
                )}
              </div>
              <p className="faint small cmp-plan">
                {selRow ? (selRow.model || '—') : '还没连到 LibTV'}
                {selRow && selRow.params && selRow.params.ratio
                  ? ' · ' + selRow.params.ratio : ''}
                {selRow && (selRow.style || []).length
                  ? ' · 风格 ' + selRow.style.map((x) => x.name).filter(Boolean).join(' / ')
                  : ''}
                {selRow && selRow.remote_changed ? ' · 提示词不同' : ''}
              </p>
              <div className="cmp-cols">
                <div className="cmp-col">
                  <h5>本地方案设计</h5>
                  <p>{selLocal.prompt || '（空）'}</p>
                </div>
                <div className="cmp-col remote">
                  <h5>LibTV 线上方案
                    {selRow && selRow.remote_changed ? ' · 不同' : ''}</h5>
                  <p>{(selRow && selRow.prompt)
                    || (selLocal.node.remote_prompt)
                    || '（还没读到线上提示词）'}</p>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="cmp-detail-head">
                <b>{selRemote.label}</b>
                <span className="faint small">
                  {selRemote.local ? selRemote.local.name
                    : (TYPE_LABEL[selRemote.node.type] || selRemote.node.type)}
                  {selRemote.status === 'done' ? ' · 已生成' : ''}
                </span>
                <span className="grow" />
                <button className="btn small primary"
                  onClick={() => onOpenNode({
                    node_id: selRemote.id,
                    type: selRemote.node.type,
                  })}>在 LibTV 打开这个节点 ↗</button>
              </div>
              {!!selRemote.node.prompt && (
                <div className="gp-prompt">{selRemote.node.prompt}</div>
              )}
            </>
          )}
        </div>
      )}

      {!!ctx && (
        <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }}
          onMouseDown={(e) => e.stopPropagation()}>
          <button onClick={() => { setCtx(null); onWbShot() }}>
            修改这一镜（WorkBuddy）
          </button>
          <button onClick={() => { setCtx(null); onWbWire() }}>
            修改布线方案（WorkBuddy）
          </button>
          <button onClick={() => { setCtx(null); onPushAll() }}>
            推送本地全部改动
          </button>
          <button onClick={() => { setCtx(null); onRefreshState() }}>
            同步 LibTV 状态
          </button>
          <hr />
          <button onClick={() => { setCtx(null); onReveal() }}>
            打开工程文件夹
          </button>
        </div>
      )}
    </div>
  )
}
