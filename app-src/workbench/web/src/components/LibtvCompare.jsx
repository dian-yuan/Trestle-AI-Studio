import React, { useEffect, useMemo, useState } from 'react'

// 页面主体：两个画布。
// 左边本地方案（画布 → 生成单元），右边 LibTV 线上画布（节点 + 连线）。
// 两边都按层级重新排整齐、缩进页面内；点节点看差异，右键本地图开操作菜单。

const LIB_STATUS = {
  done: '已生成',
  generating: '生成中',
  failed: '生成失败',
  idle: '还没结果',
  missing: '节点读不到',
  unwired: '未布线',
}

const NODE_TYPE = {
  image: '图片',
  video: '视频',
  'material-style': '风格',
  material: '素材',
  text: '文本',
}

const STATUS_TONE = {
  done: 'ok',
  generating: 'info',
  failed: 'err',
  missing: 'err',
  unwired: 'warn',
}

function localLabel(u) {
  if (!u) return '—'
  if (!u.prompt) return '缺提示词'
  if (!u.wired) return '待布线'
  if (u.prompt_dirty) return '待推 LibTV'
  if (u.output) return '已生成'
  if (u.libtv_state_status === 'generating') return '生成中'
  return '待生成'
}

function styleLabel(u) {
  const mode = (u && u.style && u.style.mode) || 'libtv'
  if (mode === 'asset') return (u.style || {}).asset || '项目风格'
  if (mode === 'none') return '纯形'
  if (mode === 'custom') return '自定义风格'
  return '在 LibTV 里选'
}

function planOf(row) {
  const parts = []
  if (row.model) parts.push(row.model)
  const p = row.params || {}
  if (p.ratio) parts.push(p.ratio)
  if (p.resolution) parts.push(p.resolution)
  if (p.duration) parts.push(p.duration + 's')
  return parts.join(' · ')
}

function stylesOf(row) {
  return (row.style || []).map((x) => x.name).filter(Boolean)
}

const excerpt = (text, n = 46) => {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

const PANEL_W = 560
const PAD = 18
const GAP_X = 36
const NODE_H = 96

// 按连线层级排成列：同一列上下排，列从左到右。
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
  const maxRows = Math.max(...keys.map((k) => cols[k].length), 1)
  const nodeW = Math.max(110, Math.min(190,
    (PANEL_W - PAD * 2 - GAP_X * (keys.length - 1)) / keys.length))
  const placed = []
  keys.forEach((k, ci) => {
    cols[k].sort((a, b) => (a.y || 0) - (b.y || 0))
      .forEach((n, ri) => {
        placed.push({
          ...n,
          left: PAD + ci * (nodeW + GAP_X),
          top: PAD + ri * (NODE_H + 8),
          sw: nodeW,
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
      // 同列 / 回流：走一条小弧线，避免穿过节点。
      paths.push({
        id: e.id || e.source + '-' + e.target,
        d: 'M ' + sx + ' ' + sy + ' C ' + (sx + 20) + ' ' + (sy - 26)
          + ', ' + (tx + 20) + ' ' + (ty - 26) + ', ' + tx + ' ' + ty,
      })
      continue
    }
    const dx = Math.max(20, (tx - sx) / 2)
    paths.push({
      id: e.id || e.source + '-' + e.target,
      d: 'M ' + sx + ' ' + sy + ' C ' + (sx + dx) + ' ' + sy + ', '
        + (tx - dx) + ' ' + ty + ', ' + tx + ' ' + ty,
    })
  }
  return {
    placed, paths,
    w: PAD * 2 + keys.length * nodeW + (keys.length - 1) * GAP_X,
    h: Math.max(260, PAD * 2 + maxRows * NODE_H + (maxRows - 1) * 8),
  }
}

function findUnitByNode(units, nodeId) {
  return units.find((u) => (u.libtv || {}).node_id === nodeId) || null
}

export default function LibtvCompare({
  asset, canvases, rows, counts, canvasGraph, errors, loading, lastRead, auto,
  onToggleAuto, onRefresh, onOpen, onPull, onPush, onPushAll,
  onEditUnit, onWireUnit, onPromote, onCanvasAction, onSyncState,
  onRevealCanvas, onRevealImage, onCanvasInit, onAskAsset,
  onWbUnit, onWbCanvas, busy,
}) {
  const canvasesArr = canvases || []
  const [active, setActive] = useState('')
  const [picked, setPicked] = useState(null)
  const [ctx, setCtx] = useState(null)

  const g = canvasesArr.find((x) => x.id === active) || canvasesArr[0] || null
  const units = (g && g.units) || []
  const graph = (g && canvasGraph[g.id]) || {}
  const hasProject = canvasesArr.some((x) => x.project_id)
  const anyBusy = !!busy

  useEffect(() => {
    if (!ctx) return undefined
    const close = () => setCtx(null)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [ctx])

  const summary = useMemo(() => {
    const c = (counts || {})[g && g.id] || {}
    const nodes = Number(c.nodes || 0) - Number(c.groups || 0)
    const edges = Number(c.edges || 0)
    const diff = rows.filter((r) => r.canvas === (g && g.id)
      && r.remote_changed).length
    return { nodes: Math.max(nodes, 0), edges, diff }
  }, [counts, rows, g && g.id])

  if (!canvasesArr.length) {
    return (
      <div className="glass-card libtv-compare">
        <div className="sec-head cmp-head">
          <b>生成画布 · LibTV ↔ 本地</b>
          <span className="faint small">还没连线 LibTV</span>
          <span className="grow" />
        </div>
        <p className="hint">
          这个资产还没有生成画布。建画布 → 写提示词 → 布线并生成；
          之后这里会同时显示本地方案和 LibTV 线上的画布结构。
        </p>
        <div className="trunk-acts">
          <button className="btn primary" disabled={anyBusy} onClick={onCanvasInit}>
            {busy === 'canvas-init' ? '建立中…' : '＋ 建生成画布'}
          </button>
          <button className="btn" onClick={onAskAsset}>让 WorkBuddy 写提示词</button>
        </div>
      </div>
    )
  }

  const unitRows = {}
  for (const u of units) {
    unitRows[u.id] = rows.find((r) => r.canvas === g.id && r.unit === u.id) || null
  }

  // 本地方案：画布根节点 → 生成单元。
  const localNodes = [{
    id: 'root', kind: 'root', label: g.id,
    name: g.title || '生成画布', y: 0,
    meta: units.length + ' 单元 · '
      + (asset && asset.primary_image ? '参考主图 · ' : '')
      + (units[0] ? styleLabel(units[0]) : '—'),
  }]
  const localEdges = []
  units.forEach((u, i) => {
    localNodes.push({
      id: 'u:' + u.id, kind: 'unit', unit: u, label: u.id,
      name: u.name || '', y: i,
      status: localLabel(u), diff: !!(unitRows[u.id] && unitRows[u.id].remote_changed),
    })
    localEdges.push({ source: 'root', target: 'u:' + u.id })
  })
  const localLay = rankedLayout(localNodes, localEdges)

  // 线上方案：LibTV 节点（组只是容器，不参与排版）→ 按连线分列。
  const rawNodes = (graph.nodes || []).filter((n) => n.type !== 'group')
  const remoteNodes = rawNodes.map((n, i) => {
    const u = findUnitByNode(units, n.id)
    const row = u ? unitRows[u.id] : null
    return {
      id: n.id, kind: 'remote', node: n, unit: u, y: n.y || i,
      label: n.name || n.id,
      status: n.status,
      diff: !!(row && row.remote_changed),
    }
  })
  const remoteIds = new Set(remoteNodes.map((n) => n.id))
  const remoteEdges = (graph.edges || []).filter(
    (e) => remoteIds.has(e.source) && remoteIds.has(e.target))
  const remoteLay = rankedLayout(remoteNodes, remoteEdges)

  const pickedRow = picked && picked.unitId ? unitRows[picked.unitId] : null
  const pickedUnit = picked && picked.unitId
    ? units.find((u) => u.id === picked.unitId) : null
  const pickedNode = picked && picked.nodeId
    ? rawNodes.find((n) => n.id === picked.nodeId) : null

  const openCtx = (e, unitId) => {
    e.preventDefault()
    e.stopPropagation()
    setCtx({ x: e.clientX, y: e.clientY, unitId: unitId || '' })
  }

  const nodeCard = (n) => {
    const on = n.kind === 'root'
      ? !!(picked && picked.kind === 'root')
      : n.kind === 'unit'
        ? !!(picked && picked.kind === 'local'
          && picked.unitId === n.unit.id)
        : !!(picked && picked.kind === 'remote'
          && picked.nodeId === n.id)
    if (n.kind === 'root') {
      return (
        <button key={n.id} type="button"
          className={'lvc-node root' + (on ? ' sel' : '')}
          style={{ left: n.left, top: n.top, width: n.sw, height: n.sh }}
          onClick={() => setPicked({ kind: 'root', unitId: '', nodeId: '' })}>
          <span className="lvc-node-top"><i className="cmp-dot info" />
            <b>{n.label} · {n.name}</b></span>
          <span className="lvc-node-meta">{n.meta}</span>
          <span className="lvc-node-prompt">本地方案设计</span>
        </button>
      )
    }
    if (n.kind === 'unit') {
      const u = n.unit
      return (
        <button key={n.id} type="button" data-unit={u.id}
          className={'lvc-node' + (n.diff ? ' diff' : '') + (on ? ' sel' : '')}
          style={{ left: n.left, top: n.top, width: n.sw, height: n.sh }}
          onClick={() => setPicked({ kind: 'local', unitId: u.id, nodeId: '' })}
          onContextMenu={(e) => openCtx(e, u.id)}>
          <span className="lvc-node-top">
            <i className={'cmp-dot ' + (u.output ? 'ok'
              : u.wired ? 'info' : 'warn')} />
            <b>{u.id} · {u.name}</b>
          </span>
          <span className="lvc-node-meta">{n.status}{u.size ? ' · ' + u.size : ''}</span>
          <span className="lvc-node-prompt">{excerpt(u.prompt) || '（还没有提示词）'}</span>
          {n.diff && <em className="lvc-badge">有差异</em>}
        </button>
      )
    }
    const u = n.unit
    return (
      <button key={n.id} type="button"
        className={'lvc-node remote' + (n.diff ? ' diff' : '') + (on ? ' sel' : '')}
        style={{ left: n.left, top: n.top, width: n.sw, height: n.sh }}
        onClick={() => setPicked(u
          ? { kind: 'remote', unitId: u.id, nodeId: n.id }
          : { kind: 'remote', unitId: '', nodeId: n.id })}>
        <span className="lvc-node-top">
          <i className={'cmp-dot ' + (STATUS_TONE[n.status] || '')} />
          <b>{u ? u.id + ' · ' + (u.name || '') : n.label}</b>
        </span>
        <span className="lvc-node-meta">
          {NODE_TYPE[n.node.type] || n.node.type}
          {n.node.style_name ? ' · ' + n.node.style_name : ''}
          {n.status === 'done' ? ' · 已生成' : n.status === 'generating' ? ' · 生成中' : ''}
        </span>
        <span className="lvc-node-prompt">{excerpt(n.node.prompt) || ' '}</span>
        {n.diff && <em className="lvc-badge">有差异</em>}
      </button>
    )
  }

  const board = (lay, nodes) => {
    if (!lay) return <p className="empty" style={{ padding: '10px 4px' }}>还没有节点。</p>
    return (
      <div className="lvc-board" style={{ width: lay.w, height: lay.h }}>
        <svg className="lvc-edges" width={lay.w} height={lay.h} aria-hidden="true">
          <defs>
            <marker id="lvc-arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(160,180,210,.55)" />
            </marker>
          </defs>
          {lay.paths.map((p) => (
            <path key={p.id} d={p.d} fill="none"
              stroke="rgba(160,180,210,.42)" strokeWidth="1.2"
              markerEnd="url(#lvc-arrow)" />
          ))}
        </svg>
        {nodes.map((n) => nodeCard(n))}
      </div>
    )
  }

  return (
    <div className="glass-card libtv-compare">
      <div className="sec-head cmp-head">
        <b>生成画布 · 本地 ↔ LibTV</b>
        <span className={'cdot ' + (errors && errors.length ? 'warn' : 'ok')} />
        <span className="faint small">
          {lastRead ? '读取于 ' + lastRead
            : (hasProject ? '正在读 LibTV…' : '还没连线 LibTV')}
          {auto ? ' · 每 15 秒自动读' : ''}
          {loading ? ' · 刷新中' : ''}
        </span>
        <span className="grow" />
        {canvasesArr.length > 1 && (
          <select className="input cmp-select" value={g.id}
            onChange={(e) => { setActive(e.target.value); setPicked(null) }}>
            {canvasesArr.map((c) => (
              <option key={c.id} value={c.id}>{c.id} · {c.title || ''}</option>
            ))}
          </select>
        )}
        <label className="cmp-auto">
          <input type="checkbox" checked={!!auto}
            onChange={(e) => onToggleAuto(e.target.checked)} />
          实时
        </label>
        <button className="btn small" disabled={!!loading || anyBusy}
          onClick={onRefresh}>
          {loading ? '读取中…' : '立即读取'}
        </button>
      </div>

      <div className="cmp-summary">
        <span>{g.id} · {units.length} 单元 · 已布线 {units.filter((u) => u.wired).length}</span>
        <i>↔</i>
        <span>LibTV {summary.nodes} 节点{summary.edges ? ' · ' + summary.edges + ' 连线' : ''}</span>
        {!!summary.diff && (
          <span className="cmp-chip warn">{summary.diff} 个节点有差异</span>
        )}
        {!!(g.units || []).filter((u) => u.wired && u.prompt_dirty).length && (
          <span className="cmp-chip warn">
            {g.units.filter((u) => u.wired && u.prompt_dirty).length} 个待推
          </span>
        )}
      </div>

      {!!(errors || []).length && (
        <p className="graph-warn" style={{ margin: '0 0 6px' }}>{errors[0]}</p>
      )}

      <div className="lvc-duo">
        <section className="lvc-pane local"
          onContextMenu={(e) => openCtx(e, '')}>
          <div className="lvc-pane-head">
            <b>本地方案设计</b>
            <span className="faint small">{g.status || '未建'}</span>
            <span className="grow" />
            <button className="btn small" disabled={anyBusy}
              onClick={onRefresh}>刷新</button>
            <button className="btn small" disabled={anyBusy}
              onClick={() => onWbCanvas(g, 'canvas')}>修改画布</button>
          </div>
          <div className="lvc-board-wrap">{board(localLay, localLay ? localLay.placed : [])}</div>
          <div className="lvc-pane-foot">
            <button className="btn small primary" disabled={anyBusy}
              onClick={() => onCanvasAction(g, 'wiregen')}>
              {busy === 'wiregen-' + g.id ? '检查中…' : '布线并生成'}
            </button>
            <button className="btn small" disabled={anyBusy}
              onClick={() => onCanvasAction(g, 'wire')}>仅布线</button>
            <button className="btn small" disabled={anyBusy}
              onClick={() => onCanvasAction(g, 'ensure')}>补齐画布</button>
            <button className="btn small" disabled={anyBusy}
              onClick={() => onPushAll(g)}>推送全部</button>
            <button className="btn small" disabled={anyBusy}
              onClick={() => onSyncState(g)}>同步线上状态</button>
            <button className="btn small"
              onClick={() => onRevealCanvas(g)}>文件夹</button>
            <span className="faint small">右键画布 / 节点有更多操作</span>
          </div>
        </section>

        <section className="lvc-pane remote">
          <div className="lvc-pane-head">
            <b>LibTV 线上方案</b>
            <span className="faint small">只读</span>
            <span className="grow" />
            {g.libtv && g.libtv.editor_url && (
              <a className="btn small" href={g.libtv.editor_url}
                target="_blank" rel="noreferrer">去 LibTV ↗</a>
            )}
            <button className="btn small" disabled={anyBusy}
              onClick={() => onCanvasAction(g, 'sync')}>
              {busy === 'sync-' + g.id ? '同步中…' : '同步结果'}
            </button>
          </div>
          <div className="lvc-board-wrap">
            {remoteLay ? board(remoteLay, remoteLay.placed)
              : (
                <p className="empty" style={{ padding: '10px 4px' }}>
                  还没读到 LibTV 画布；点「同步线上状态」或「立即读取」。
                </p>
              )}
          </div>
          <div className="lvc-pane-foot">
            <span className="faint small">
              线上是只读视图；改提示词请在中间选节点后推送，或去 LibTV 改。
            </span>
          </div>
        </section>
      </div>

      {!!(pickedUnit || pickedNode) && (
        <div className="lvc-detail">
          {pickedUnit ? (
            <>
              <div className="cmp-detail-head">
                <b>{g.id} · {pickedUnit.id} {pickedUnit.name}</b>
                <span className="faint small">
                  本地 {localLabel(pickedUnit)} · LibTV {pickedRow
                    ? (LIB_STATUS[pickedRow.status] || pickedRow.status) : '未布线'}
                  {pickedRow && pickedRow.at ? ' · 线上读于 ' + pickedRow.at : ''}
                </span>
                <span className="grow" />
                <button className="btn small primary" disabled={anyBusy}
                  onClick={() => onWbUnit(g, pickedUnit)}>
                  只改这个节点的提示词
                </button>
                <button className="btn small" disabled={anyBusy}
                  onClick={() => onEditUnit(g, pickedUnit)}>
                  提示词 / 风格
                </button>
                {!pickedUnit.wired && (
                  <button className="btn small" disabled={anyBusy}
                    onClick={() => onWireUnit(g, pickedUnit)}>布此单元</button>
                )}
                {!!pickedUnit.wired && (
                  <button className="btn small" disabled={anyBusy}
                    onClick={() => onPush(g, pickedUnit)}>推送本地方案 → LibTV</button>
                )}
                {!!(pickedRow && pickedRow.remote_changed) && (
                  <button className="btn small" disabled={anyBusy}
                    onClick={() => onPull(g, pickedUnit)}>采用线上方案</button>
                )}
                {!!pickedUnit.wired && (
                  <button className="btn small"
                    onClick={() => onOpen({
                      canvas: g.id, unit: pickedUnit.id,
                      node_id: (pickedUnit.libtv || {}).node_id || '',
                    })}>在 LibTV 改这个节点 ↗</button>
                )}
                {!!pickedUnit.output && (
                  <button className="btn small" disabled={anyBusy}
                    onClick={() => onPromote(g, pickedUnit)}>收为变体</button>
                )}
                {!!pickedUnit.output && (
                  <button className="btn small"
                    onClick={() => onRevealImage(pickedUnit.output)}>定位成图</button>
                )}
              </div>
              <p className="faint small cmp-plan">
                {(pickedRow && planOf(pickedRow)) || '还没连到 LibTV'}
                {pickedRow && stylesOf(pickedRow).length
                  ? ' · 风格 ' + stylesOf(pickedRow).join(' / ') : ''}
                {' · 本地画幅 ' + (pickedUnit.size || '—') + ' · ' + styleLabel(pickedUnit)}
                {pickedRow && pickedRow.remote_changed ? ' · 提示词不同' : ''}
              </p>
              <div className="cmp-cols">
                <div className="cmp-col">
                  <h5>本地方案设计{pickedUnit.prompt_dirty ? ' · 待推' : ''}</h5>
                  <p>{pickedUnit.prompt || '（空）'}</p>
                </div>
                <div className="cmp-col remote">
                  <h5>LibTV 线上方案
                    {pickedRow && pickedRow.remote_changed ? ' · 不同' : ''}</h5>
                  <p>{(pickedRow && pickedRow.prompt) || '（还没读到线上提示词）'}</p>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="cmp-detail-head">
                <b>{pickedNode.name || pickedNode.id}</b>
                <span className="faint small">
                  {NODE_TYPE[pickedNode.type] || pickedNode.type}
                  {pickedNode.style_name ? ' · ' + pickedNode.style_name : ''}
                  {pickedNode.status === 'done' ? ' · 已生成' : ''}
                </span>
                <span className="grow" />
                <button className="btn small primary"
                  onClick={() => onOpen({
                    canvas: g.id, unit: '', node_id: pickedNode.id,
                  })}>在 LibTV 打开这个节点 ↗</button>
              </div>
              {!!pickedNode.prompt && (
                <div className="gp-prompt">{pickedNode.prompt}</div>
              )}
            </>
          )}
        </div>
      )}

      {!!ctx && (
        <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }}
          onMouseDown={(e) => e.stopPropagation()}>
          {!!ctx.unitId && (
            <>
              <button onClick={() => {
                const u = units.find((x) => x.id === ctx.unitId)
                setCtx(null)
                setPicked({ kind: 'local', unitId: ctx.unitId, nodeId: '' })
                if (u) onWbUnit(g, u)
              }}>只改这个节点的提示词（WorkBuddy）</button>
              <button onClick={() => {
                const u = units.find((x) => x.id === ctx.unitId)
                setCtx(null)
                if (u && u.wired) onPush(g, u)
              }}>推送这个节点 → LibTV</button>
              <button onClick={() => {
                const u = units.find((x) => x.id === ctx.unitId)
                setCtx(null)
                if (u) onPull(g, u)
              }}>采用线上这个节点</button>
              <hr />
            </>
          )}
          <button onClick={() => { setCtx(null); onWbCanvas(g, 'canvas') }}>
            修改画布（WorkBuddy）
          </button>
          <button onClick={() => { setCtx(null); onWbCanvas(g, 'wire') }}>
            修改布线方案（WorkBuddy）
          </button>
          <button onClick={() => { setCtx(null); onPushAll(g) }}>
            推送本地全部改动
          </button>
          <button onClick={() => { setCtx(null); onSyncState(g) }}>
            同步 LibTV 状态
          </button>
          <hr />
          <button onClick={() => { setCtx(null); onRevealCanvas(g) }}>
            打开画布文件夹
          </button>
        </div>
      )}
    </div>
  )
}
