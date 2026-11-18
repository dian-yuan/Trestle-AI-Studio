import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { get, post } from '../api'
import { useApp } from '../App'
import { useOnboard } from '../onboarding/context'
import ConfirmDialog from '../components/ConfirmDialog'
import BindNodeDialog from '../components/BindNodeDialog'
import FirstLesson from '../components/FirstLesson'
import Checklist from '../components/Checklist'
import Coach from '../components/Coach'
import Markdown from '../components/Markdown'
import { deferredMessage, nextAction, nodeUrl, shotBadges } from '../libtvState'
import { assetTaskSpec } from '../lib/wbSpecs'

const img = (rel) => rel ? '/img/' + String(rel).replace(/\\/g, '/')
  .split('/').map(encodeURIComponent).join('/') : ''
const IMG_RX = /\.(png|jpe?g|webp|gif|bmp|avif)$/i
const shotImg = (s) => {
  const f = (s && s.render) || ''
  if (f && IMG_RX.test(f)) return img(f)
  return (s && s.render_url) || ''
}

const NODE_W = 128
const NODE_H = 52

// 创意面板：优先用剧本里的第一段「创作思路 / 创作打包」，
// 没有就退回 blockquote / 开头一段。
function ideaMarkdown(text) {
  const s = String(text || '')
  if (!s.trim()) return ''
  const sec = s.match(/^##\s+[\s\S]*?(?=^##\s|$)/m)
  if (sec) {
    const head = (sec[0].match(/^##\s+(.+)/) || [])[1] || ''
    if (!/剧本|场|分镜|镜头/.test(head)) return sec[0].trim()
  }
  const quote = s.split('\n').filter((l) => /^\s*>/.test(l))
    .map((l) => l.replace(/^\s*>\s?/, '')).join('\n').trim()
  return quote || s.slice(0, 800)
}

function short(text, n = 600) {
  const s = String(text || '').replace(/\s+/g, ' ').trim()
  return s.length > n ? s.slice(0, n) + '…' : s
}

function layout(data) {
  const nodes = []
  const edges = []
  const add = (n) => { nodes.push(n); return n }
  add({ id: 'idea', kind: 'idea', label: '创意', sub: 'WorkBuddy 提炼', x: 40, y: 70 })
  add({ id: 'script', kind: 'script', label: '剧本', sub: data.script.title || '未写', x: 210, y: 70 })
  edges.push(['idea', 'script'])

  let sceneY = 60
  for (const sc of data.scenes || []) {
    const sceneId = 'scene:' + sc.id
    add({ id: sceneId, kind: 'scene', label: '场 ' + sc.id, sub: sc.name, x: 400, y: sceneY, ref: sc })
    edges.push(['script', sceneId])
    let segY = sceneY - 20
    for (const sg of sc.segments || []) {
      const segId = 'seg:' + sg.id
      add({ id: segId, kind: 'segment', label: sg.id,
        sub: sg.name + (sg.sec ? ' ' + sg.sec + 's' : ''),
        x: 630, y: segY, ref: sg, scene: sc.id })
      edges.push([sceneId, segId])
      let shotY = segY - 20
      for (const sh of sg.shots || []) {
        const shotId = 'shot:' + sg.id + ':' + sh.id
        add({ id: shotId, kind: 'shot', label: '镜 ' + sh.id,
          sub: [sh.framing, sh.camera].filter(Boolean).join(' '),
          x: 860, y: shotY, ref: sh, seg: sg.id, scene: sc.id })
        edges.push([segId, shotId])
        shotY += 58
      }
      segY = Math.max(segY + 96, shotY + 16)
    }
    sceneY = Math.max(sceneY + 140, segY + 30)
  }

  const assetY = Math.max(sceneY + 48, 220)
  const assetRows = data.assets || []
  assetRows.forEach((a, i) => {
    const id = 'asset:' + a.id
    add({ id, kind: 'asset', label: a.type_label, sub: a.name,
      x: 120 + i * 180, y: assetY, ref: a })
    const used = new Set(a.shots || [])
    for (const n of nodes.filter((x) => x.kind === 'shot' && used.has(x.ref.id))) {
      edges.push([id, n.id])
    }
  })

  const w = Math.max(1040, ...nodes.map((n) => n.x + NODE_W + 40))
  // 没有资产时把资产区画成一条空lane：画布下半部分不会看起来空了一块。
  const assetLane = assetRows.length ? null : {
    x: 24, y: assetY, w: w - 48, h: NODE_H + 16,
  }
  const h = Math.max(420, ...nodes.map((n) => n.y + NODE_H + 60),
    assetLane ? assetLane.y + assetLane.h + 56 : 0)
  return { nodes, edges, w, h, assetLane }
}

// 总览里的镜卡：排版照原型（首帧缩略 + 镜号 + 景别/运镜 + 画面 + 状态），
// 状态后面直接跟下一步按钮——「待生成」不再只是文字。
function ShotCard({ shot, seg, sceneId, busy, onOpen, onAction }) {
  const badges = shotBadges(shot)
  const action = nextAction(shot)
  return (
    <article className="card" onClick={onOpen}>
      <div className="frame">
        {shotImg(shot)
          ? <img src={shotImg(shot)} alt={shot.id} />
          : <span>{shot.framing || '镜'}</span>}
      </div>
      <div className="card-main">
        <div className="row">
          <b>{shot.id}</b>
          {shot.key && <span className="kmark">K</span>}
          <span className="tag">{shot.framing || '—'} / {shot.camera || '—'} · {shot.sec}s</span>
        </div>
        <div className="sub">{short(shot.visual, 64)}</div>
        <div className="status">
          {badges.map((b) => <span key={b.key} className={b.tone}>{b.text}</span>)}
        </div>
        {action && (
          <div className="acts">
            <button
              className={'btn small' + (action.kind === 'open'
                || action.kind === 'bind' ? ' primary' : '')}
              disabled={!!busy || (action.kind === 'open' && !action.nodeKey)}
              title={action.hint || ''}
              onClick={(e) => { e.stopPropagation(); onAction(sceneId, seg, shot, action) }}>
              {busy ? '处理中…' : action.label}
            </button>
          </div>
        )}
      </div>
    </article>
  )
}

export default function Home() {
  const { snap, wb, wbAsk, notify, refresh, unit: appUnit } = useApp()
  const { firstLessonSeen, state: onboardState,
    ready: onboardReady } = useOnboard()
  const nav = useNavigate()
  const unit = (snap && snap.meta && snap.meta.unit) || appUnit || ''
  const [data, setData] = useState(null)
  const [sel, setSel] = useState({ kind: 'idea' })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [confirm, setConfirm] = useState(null)
  const [busy, setBusy] = useState('')
  const [bindTarget, setBindTarget] = useState(null)
  const [loadError, setLoadError] = useState('')
  const wrapRef = useRef(null)
  const seenUnit = useRef('')

  const keepSel = useCallback((r, cur) => {
    const scenes = (r.scenes) || []
    const alive = cur.kind === 'idea' || cur.kind === 'script'
      || (cur.kind === 'scene' && scenes.some((x) => x.id === cur.id))
      || (cur.kind === 'segment' && scenes.some((x) =>
        (x.segments || []).some((y) => y.id === cur.id)))
      || (cur.kind === 'shot' && scenes.some((x) =>
        (x.segments || []).some((y) => y.id === cur.seg
          && (y.shots || []).some((z) => z.id === cur.shot))))
      || (cur.kind === 'asset' && (r.assets || []).some((x) => x.id === cur.id))
    if (alive) return cur
    return scenes.length ? { kind: 'scene', id: scenes[0].id } : { kind: 'idea' }
  }, [])

  const applyData = useCallback((r) => {
    setData(r)
    // 同一个单元刷新（布线 / 生成 / 同步之后）保留当前选中的场 / 段 / 镜；
    // 换单元或第一次加载才回到第一场。
    const sameUnit = seenUnit.current && seenUnit.current === r.unit
    seenUnit.current = r.unit
    setSel((cur) => (sameUnit ? keepSel(r, cur)
      : ((r.scenes || []).length ? { kind: 'scene', id: r.scenes[0].id } : { kind: 'idea' })))
  }, [keepSel])

  const load = useCallback(async () => {
    try {
      const r = await get('/api/structure?' + new URLSearchParams({ unit }))
      if (r && r.ok) { setLoadError(''); applyData(r) }
      else {
        setLoadError((r && r.error) || '读不到项目结构')
        notify((r && r.error) || '读不到项目结构', 'error')
      }
    } catch (e) {
      setLoadError(String(e.message || e))
      notify(String(e), 'error')
    }
  }, [unit, notify, applyData])

  useEffect(() => {
    let alive = true
    setLoadError('')
    get('/api/structure?' + new URLSearchParams({ unit }))
      .then((r) => {
        if (!alive) return
        if (r && r.ok) { setLoadError(''); applyData(r) }
        else {
          setLoadError((r && r.error) || '读不到项目结构')
          notify((r && r.error) || '读不到项目结构', 'error')
        }
      })
      .catch((e) => {
        if (!alive) return
        setLoadError(String(e.message || e))
        notify(String(e), 'error')
      })
    return () => { alive = false }
  }, [unit, snap && snap.versions && snap.versions.storyboards, notify, applyData])

  const ask = (opts) => new Promise((resolve) => setConfirm((prev) => {
    // 上一个确认框还开着就取消它，避免它的 Promise 永远不结束。
    if (prev) prev.resolve(false)
    return { ...opts, resolve }
  }))
  const closeAsk = (ok) => {
    const c = confirm
    setConfirm(null)
    if (c) c.resolve(ok)
  }

  const graph = useMemo(() => (data ? layout(data) : { nodes: [], edges: [], w: 1040, h: 420 }),
    [data])
  const byId = useMemo(() => {
    const m = {}
    graph.nodes.forEach((n) => { m[n.id] = n })
    return m
  }, [graph])

  useEffect(() => {
    if (!data) return
    const t = setTimeout(() => fit(), 60)
    return () => clearTimeout(t)
  }, [data && data.unit, graph.w, graph.h])

  function fit() {
    const w = wrapRef.current ? wrapRef.current.clientWidth - 40 : 1000
    setZoom(Math.max(0.4, Math.min(1, w / graph.w)))
  }

  function zoomBy(d) {
    setZoom((z) => Math.max(0.3, Math.min(1.6, z + d)))
  }

  function clickNode(n) {
    if (n.kind === 'school') return
    if (n.kind === 'scene') setSel({ kind: 'scene', id: n.ref.id })
    else if (n.kind === 'segment') setSel({ kind: 'segment', id: n.ref.id, scene: n.scene })
    else if (n.kind === 'shot') setSel({ kind: 'shot', seg: n.seg, shot: n.ref.id, scene: n.scene })
    else if (n.kind === 'asset') setSel({ kind: 'asset', id: n.ref.id })
    else setSel({ kind: n.kind })
    setDrawerOpen(true)
  }

  function isSel(n) {
    if (sel.kind === 'scene') return n.kind === 'scene' && n.ref.id === sel.id
    if (sel.kind === 'segment') return n.kind === 'segment' && n.ref.id === sel.id
    if (sel.kind === 'shot') return n.kind === 'shot' && n.ref.id === sel.shot && n.seg === sel.seg
    if (sel.kind === 'asset') return n.kind === 'asset' && n.ref.id === sel.id
    return n.kind === sel.kind
  }

  // 抽屉展开 / 收起后画布宽度变了，重新按新宽度适应一次。
  useEffect(() => {
    if (!data) return undefined
    const t = setTimeout(() => fit(), 320)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawerOpen, data && data.unit])

  if (!data) {
    return (
      <section className="page home-page">
        {loadError ? (
          <div className="glass-card side-card">
            <p className="graph-warn">{loadError}</p>
            <button className="btn small" onClick={load}>重试</button>
          </div>
        ) : <p className="empty">正在读项目结构…</p>}
      </section>
    )
  }

  const scene = sel.kind === 'scene'
    ? (data.scenes || []).find((x) => x.id === sel.id) : null
  const segment = sel.kind === 'segment' ? (() => {
    for (const sc of data.scenes || []) {
      const sg = (sc.segments || []).find((x) => x.id === sel.id)
      if (sg) return { sg, sc }
    }
    return null
  })() : null
  const shot = sel.kind === 'shot' ? (() => {
    for (const sc of data.scenes || []) {
      for (const sg of sc.segments || []) {
        if (sg.id !== sel.seg) continue
        const sh = (sg.shots || []).find((x) => x.id === sel.shot)
        if (sh) return { sh, sg, sc }
      }
    }
    return null
  })() : null
  const asset = sel.kind === 'asset'
    ? (data.assets || []).find((x) => x.id === sel.id) : null
  const drawerTitle = sel.kind === 'idea' ? '创意'
    : sel.kind === 'script' ? '剧本'
      : scene ? '场 ' + scene.id + ' · ' + scene.name
        : segment ? '段 ' + segment.sg.id + ' · ' + segment.sg.name
          : shot ? '镜 ' + shot.sh.id
            : asset ? asset.type_label + ' · ' + asset.name : '内容'

  const openScene = (sceneId) =>
    nav('/review?unit=' + encodeURIComponent(unit) + '&scene=' + encodeURIComponent(sceneId))

  function assetAction(a) {
    wbAsk(assetTaskSpec(a))
  }

  // 镜卡上的下一步：出图谱 / 布线 / 在 LibTV 生成 / 同步结果。
  // 生成在 LibTV 里做：这里只负责把节点接好、把对应节点打开。
  async function runShot(sceneId, seg, shotRow, action) {
    if (!shotRow || busy || confirm) return
    const key = action.kind + '-' + shotRow.id

    if (action.kind === 'graph') {
      wb('生成布线图谱', '剧本《' + (data.script.title || '') + '》· 场 '
        + sceneId + ' · 段 ' + seg.id)
      return
    }

    if (action.kind === 'bind') {
      const ref = (shotRow.external_refs || []).find((x) => !x.bound)
      if (!ref) { notify('这一镜没有待绑定的素材', 'info'); return }
      setBindTarget({
        id: ref.id, type: ref.kind, segment: seg.id,
        external: { kind: ref.kind, label: ref.label },
      })
      return
    }

    if (action.kind === 'open') {
      const base = (seg.libtv && seg.libtv.editor_url) || ''
      const url = nodeUrl(base, action.nodeKey)
      if (!url) {
        notify('这一镜还没连到 LibTV，先布线再打开', 'warn')
        return
      }
      window.open(url, '_blank', 'noreferrer')
      return
    }

    if (action.kind === 'sync') {
      setBusy(key)
      try {
        const r = await post('/api/storyboard/graph/sync', { unit, segment: seg.id })
        if (!r.ok) {
          notify('同步失败：' + ((((r.errors || [])[0] || {}).error) || r.error || ''), 'error')
        } else {
          const rows = r.rows || []
          const fresh = rows.filter((x) => x.downloaded).length
          const running = rows.filter((x) => x.status === 'generating').length
          const unbound = rows.filter((x) => x.needs_binding
            || x.status === 'unbound').length
          const missing = rows.filter((x) => x.missing).length
          if (fresh) notify('已下载 ' + fresh + ' 个结果（新版本）', 'ok')
          else if (running) notify(running + ' 个节点还在生成，稍后再同步', 'info')
          else if (unbound) notify(unbound + ' 个素材待绑定', 'warn')
          else if (missing) notify(missing + ' 个 LibTV 节点已删除', 'warn')
          else notify('线上结果已经是最新', 'info')
        }
        // 部分节点可能已经下载成功：失败也刷新一次。
        await load()
        refresh()
      } finally {
        setBusy('')
      }
      return
    }

    if (action.kind === 'wire') {
      let dry
      try {
        dry = await post('/api/storyboard/graph/wire', {
          unit, segment: seg.id, shot: shotRow.id, dry_run: true,
        })
      } catch (e) {
        notify('检查失败：' + (e.message || e), 'error')
        return
      }
      // 只认这一镜的硬 blocker；视频等首帧这类 defer 是合法中间态，不拦布线。
      if (dry.hard_ok === false) {
        const blocker = ((dry.hard_blockers || [])[0]) || {}
        notify(blocker.message || '这一镜还不能布线', 'warn')
        return
      }
      if (dry.hard_ok === undefined && !dry.ok) {
        notify(dry.error || '这一镜还不能布线', 'warn')
        return
      }
      const plan = dry.plan || {}
      const cost = plan.cost || {}
      const ok = await ask({
        title: '布线 · 镜 ' + shotRow.id,
        desc: '把这一镜的参考图 / 首帧 / 视频节点一起接到 LibTV 画布。只布线，不生成。',
        details: [
          '参考图 ' + (cost.uploads || 0) + ' 张 · 出图 ' + (cost.images || 0)
            + ' 个 · 出片 ' + (cost.videos || 0) + ' 条',
          '布完线，卡片上会出现「首帧 ↗ / 视频 ↗」，点开就在 LibTV 对应节点上生成。',
        ],
        confirmText: '布线',
      })
      if (!ok) return
      setBusy(key)
      try {
        const r = await post('/api/storyboard/graph/wire', {
          unit, segment: seg.id, shot: shotRow.id, dry_run: false, confirm: true,
        })
        if (!r.ok) {
          notify('布线失败：' + ((r.errors || [{}])[0].error || r.error || ''), 'error')
          await load()
          refresh()
          return
        }
        const msg = deferredMessage(r)
        if (msg) notify('已布线；' + msg, 'warn')
        else notify('镜 ' + shotRow.id + ' 已布线，去 LibTV 生成', 'ok')
        await load()
        refresh()
      } finally {
        setBusy('')
      }
      return
    }
  }

  return (
    <section className={'page home-page' + (drawerOpen ? ' drawer-open' : '')}>
      <div className="page-head">
        <div>
          <h1>{data.title || '当前项目'}</h1>
          <p>{data.one_line || '项目结构 · 点节点看下面'}</p>
          <div className="meta-row">
            {data.meta.type && <span className="chip">{data.meta.type}</span>}
            {data.meta.duration && <span className="chip">{data.meta.duration}</span>}
            <span className="chip">{data.meta.scenes} 场 · {data.meta.segments} 段 · {data.meta.shots} 镜</span>
            <span className="chip">{data.meta.assets} 个资产</span>
          </div>
        </div>
        <span className="grow" />
        {data.scenes[0] && data.scenes[0].libtv && data.scenes[0].libtv.editor_url && (
          <a className="btn" href={data.scenes[0].libtv.editor_url}
            target="_blank" rel="noreferrer">打开 LibTV 项目 ↗</a>
        )}
        <button className="btn"
          onClick={() => wb('接手当前页面', '项目总览', null, { skipAsk: true })}>
          在 WorkBuddy 继续聊
        </button>
      </div>

      {onboardReady && <FirstLesson structure={data} unit={unit} />}
      <Checklist structure={data} unit={unit} />

      <div className={'ov-split' + (drawerOpen ? ' open' : '')}>
        <div className="ov-main">
      <div className="glass-card ov-card" data-onb="home-canvas">
        <div className="ov-head">
          <b>结构画布</b>
          <span className="faint">点节点看下面的内容；资产连线由镜头的 assets 自动推得</span>
          <span className="grow" />
          <button className="btn small" onClick={() => zoomBy(-0.1)}>－</button>
          <span className="ov-zoom">{Math.round(zoom * 100)}%</span>
          <button className="btn small" onClick={() => zoomBy(0.1)}>＋</button>
          <button className="btn small" onClick={fit}>适应</button>
          <button className="btn small"
            onClick={() => setDrawerOpen((v) => !v)}>
            {drawerOpen ? '收起内容' : '内容面板'}
          </button>
        </div>
        <div className="ov-wrap" ref={wrapRef}>
          <div className="ov-canvas" style={{ width: graph.w, height: graph.h, zoom }}>
            <svg className="ov-edges" viewBox={'0 0 ' + graph.w + ' ' + graph.h}>
              {graph.edges.map(([a, b], i) => {
                const s = byId[a]
                const t = byId[b]
                if (!s || !t) return null
                const assetEdge = s.kind === 'asset'
                const x1 = assetEdge ? s.x + NODE_W / 2 : s.x + NODE_W
                const y1 = assetEdge ? s.y : s.y + NODE_H / 2
                const x2 = assetEdge ? t.x + NODE_W / 2 : t.x
                const y2 = assetEdge ? t.y + NODE_H : t.y + NODE_H / 2
                const d = assetEdge
                  ? `M${x1} ${y1} C${x1} ${y1 + 50} ${x2} ${y2 + 50} ${x2} ${y2}`
                  : `M${x1} ${y1} C${x1 + 40} ${y1} ${x2 - 40} ${y2} ${x2} ${y2}`
                return <path key={i} d={d} />
              })}
            </svg>
            {graph.assetLane && (
              <div className="ov-lane"
                style={{
                  left: graph.assetLane.x, top: graph.assetLane.y,
                  width: graph.assetLane.w, height: graph.assetLane.h,
                }}>
                <b>资产</b>
                <span>这一集还没有资产 · 添加后会自动连到用它的镜</span>
                <span className="grow" />
                <button className="btn small"
                  onClick={() => nav('/assets' + (unit ? '?unit=' + encodeURIComponent(unit) : ''))}>
                  打开资产库 →
                </button>
              </div>
            )}
            {graph.nodes.map((n) => (
              <div key={n.id}
                className={'ov-node ' + n.kind + (isSel(n) ? ' on' : '')}
                style={{ left: n.x, top: n.y, width: NODE_W }}
                onClick={() => clickNode(n)}>
                <b>{n.label}</b>
                <span>{n.sub}</span>
              </div>
            ))}
            <span className="ov-label">结构画布 · 节点固定 · 资产连到用它的镜</span>
          </div>
        </div>
      </div>
        </div>
        {createPortal((
        <aside className={'ov-drawer' + (drawerOpen ? ' open' : '')}>
          <div className="ov-drawer-inner">
            <div className="ov-drawer-head">
              <b>{drawerTitle}</b>
              <span className="grow" />
              <button className="icon-btn" aria-label="收起"
                onClick={() => setDrawerOpen(false)}>×</button>
            </div>
      <div className="ov-panel">
        {sel.kind === 'idea' && (
          <div className="ov-content">
            <div className="ov-content-head">
              <b>创意</b><span className="faint">WorkBuddy 提炼</span>
              <span className="grow" />
              <button className="btn small"
                onClick={() => wb('延申思路', '项目 ' + data.title)}>
                打开 WorkBuddy 修改创意
              </button>
            </div>
            <div className="ov-content-body">
              <p className="ov-big">{data.one_line || '还没有一句话；去 WorkBuddy 聊出来。'}</p>
              {!!ideaMarkdown(data.script.text) && (
                <Markdown text={ideaMarkdown(data.script.text)} className="ov-md" />
              )}
              <p className="note">创意来自 WorkBuddy 对话；平台只读，不在平台新建。</p>
            </div>
          </div>
        )}

        {sel.kind === 'script' && (
          <div className="ov-content">
            <div className="ov-content-head">
              <b>剧本{data.script.title ? ' · ' + data.script.title : ''}</b>
              <span className="faint">{data.script.status || ''}</span>
              <span className="grow" />
              <button className="btn small"
                onClick={() => wb('完善剧本', '剧本《' + (data.script.title || '') + '》')}>
                打开 WorkBuddy 修改剧本
              </button>
            </div>
            <div className="ov-content-body">
              {data.script.text
                ? <Markdown text={data.script.text} className="ov-md" />
                : <p className="empty">还没有剧本。</p>}
            </div>
          </div>
        )}

        {scene && (
          <>
            <div className="seg-head">
              <b>场 {scene.id} · {scene.name}</b>
              <span>{scene.seconds || (scene.segments || []).reduce(
                (n, x) => n + Number(x.sec || 0), 0)} 秒 · {(scene.segments || []).length} 段
                · {(scene.segments || []).reduce((n, x) => n + (x.shots || []).length, 0)} 镜</span>
              <span className="grow" />
              {scene.libtv && scene.libtv.editor_url && (
                <a className="btn small" href={scene.libtv.editor_url}
                  target="_blank" rel="noreferrer">打开整场 ↗</a>
              )}
              <button className="btn small" onClick={() => openScene(scene.id)}>进入这一场的详情 →</button>
            </div>
            {(scene.segments || []).map((sg) => (
              <div className="seg-block" key={sg.id}>
                <div className="seg-head">
                  <b>{sg.id} · {sg.name}</b>
                  <span>{sg.sec} 秒 · {(sg.shots || []).length} 镜</span>
                  <span className="grow" />
                  <button className="btn small"
                    onClick={() => setSel({ kind: 'segment', id: sg.id, scene: scene.id })}>
                    只看这一段
                  </button>
                </div>
                <div className="cards">
                  {(sg.shots || []).map((sh) => (
                    <ShotCard key={sh.id} shot={sh} seg={sg} sceneId={scene.id}
                      busy={busy} onOpen={() => openScene(scene.id)}
                      onAction={runShot} />
                  ))}
                </div>
              </div>
            ))}
          </>
        )}

        {segment && (
          <>
            <div className="seg-head">
              <b>{segment.sg.id} · {segment.sg.name}</b>
              <span>{segment.sg.sec} 秒 · {(segment.sg.shots || []).length} 镜</span>
              <span className="grow" />
              <button className="btn small"
                onClick={() => openScene(segment.sc.id)}>进入这一场的详情 →</button>
            </div>
            <div className="cards">
              {(segment.sg.shots || []).map((sh) => (
                <ShotCard key={sh.id} shot={sh} seg={segment.sg} sceneId={segment.sc.id}
                  busy={busy} onOpen={() => openScene(segment.sc.id)}
                  onAction={runShot} />
              ))}
            </div>
          </>
        )}

        {shot && (
          <>
            <div className="ov-panel-head"><b>镜 {shot.sh.id}</b>
              <span className="faint">{shot.sh.framing} / {shot.sh.camera} · {shot.sh.sec}s</span>
              <span className="grow" />
              <button className="btn small primary" onClick={() => openScene(shot.sc.id)}>去编排 →</button>
            </div>
            <p className="ov-text">{shot.sh.visual || '还没有画面描述。'}</p>
            {shot.sh.line && <p className="ov-line">台词：{shot.sh.line}</p>}
            {!!(shot.sh.assets || []).length && (
              <p className="note">用到资产：{shot.sh.assets.join('、')}</p>
            )}
          </>
        )}

        {asset && (
          <>
            <div className="ov-panel-head"><b>{asset.type_label} · {asset.name}</b>
              <span className="faint">{asset.id} · {asset.status}</span>
              <span className="grow" />
              <button className="btn small" onClick={() => assetAction(asset)}>打开 WorkBuddy 修改资产</button>
              <button className="btn small" onClick={() => nav('/assets?asset=' + encodeURIComponent(asset.id))}>
                打开资产页
              </button>
            </div>
            <p className="note">连到：{(asset.shots || []).length ? asset.shots.join(' / ') : '还没有镜引用'}（由 shots.json 自动推得）</p>
            <div className="ov-asset">
              <div className="ov-asset-img">
                {asset.image ? <img src={img(asset.image)} alt={asset.name} /> : <span>暂无母图</span>}
              </div>
              <div>
                <div className="ov-variants">
                  {(asset.variants || []).slice(0, 6).map((v) => (
                    <div className="ov-variant" key={v.id}>
                      {v.image ? <img src={img(v.image)} alt={v.label} /> : <span />}
                      <em>{v.label}</em>
                    </div>
                  ))}
                  {!(asset.variants || []).length && <span className="faint">还没有变体</span>}
                </div>
                <p className="ov-text">{short(asset.prompts, 700) || '还没有提示词。'}</p>
                <div className="ov-plan">
                  <div className="ov-plan-head"><b>{asset.plan.recipe}</b><span>{asset.plan.title}</span></div>
                  <p className="note">{asset.plan.why}</p>
                  <div className="ov-steps">
                    {(asset.plan.steps || []).map((s, i) => (
                      <React.Fragment key={s}>
                        {i > 0 && <em>→</em>}
                        <span>{s}</span>
                      </React.Fragment>
                    ))}
                  </div>
                  <p className="note">{(asset.plan.actions || []).join(' · ')} · {asset.plan.cost}</p>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
          </div>
        </aside>
        ), document.body)}
      </div>

      <ConfirmDialog
        open={!!confirm}
        title={confirm ? confirm.title : ''}
        desc={confirm ? confirm.desc : ''}
        details={confirm ? confirm.details : []}
        confirmText={(confirm && confirm.confirmText) || '继续'}
        cancelText="取消"
        onConfirm={() => closeAsk(true)}
        onCancel={() => closeAsk(false)}
      />
      <BindNodeDialog
        open={!!bindTarget}
        unit={unit}
        segment={bindTarget ? bindTarget.segment : ''}
        node={bindTarget}
        onClose={() => setBindTarget(null)}
        onBound={() => { notify('已绑定，去 LibTV 打开节点生成', 'ok'); load(); refresh() }}
      />

      <Coach id="home.canvas" anchor='[data-onb="home-canvas"]'
        enabled={firstLessonSeen && !onboardState.checklistOpen} />
    </section>
  )
}
