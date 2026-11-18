import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { get, post, exportHref } from '../api'
import { useApp } from '../App'
import ConfirmDialog from '../components/ConfirmDialog'
import Markdown from '../components/Markdown'
import ShotCanvas from '../components/ShotCanvas'
import FloatingPanel from '../components/FloatingPanel'
import MediaPreview from '../components/MediaPreview'
import BindNodeDialog from '../components/BindNodeDialog'
import Coach from '../components/Coach'
import { LinkPicker, Modal } from '../components/Modal'
import { deferredMessage, MEDIA_DONE, mediaState, nextAction, nodeUrl, shotBadges } from '../libtvState'

const img = (rel) => rel ? '/img/' + String(rel).replace(/\\/g, '/')
  .split('/').map(encodeURIComponent).join('/') : ''

const fmt = (n) => Number(n || 0).toFixed(1)
// snapshot 的 render 可能是 mp4；首帧只认图片，视频文件不能塞进 <img>。
const IMG_RX = /\.(png|jpe?g|webp|gif|bmp|avif)$/i
const frameImg = (s) => {
  const f = (s && s.render) || ''
  if (IMG_RX.test(f)) return f
  return (s && s.render_url) || ''
}
const mediaSrc = (p) => (/^https?:\/\//i.test(p || '') ? p : img(p))

const ROLE_CN = {
  reference: '参考图', reference_video: '参考视频', image2image: '图生图',
  first_frame: '首帧', last_frame: '尾帧', video: '视频',
}

const REWORK_OPTS = [
  ['prompt', '提示词', '只改描述，参考图和参数不动'],
  ['ref', '参考图', '换或补一张参考图，提示词不动'],
  ['param', '参数', '只改时长 / 画幅 / 分辨率'],
  ['recipe', '配方', '换配方（R1–R7）或阶段'],
]

function SceneNav({ scenes, cur, unit }) {
  return (
    <div className="glass-card side-card scene-nav">
      <div className="sec-head"><b>场次 <i>{scenes.length}</i></b></div>
      {scenes.map((sc) => (
        <Link key={sc.id} className={'asset-mini' + (sc.id === cur ? ' on' : '')}
          to={'/review?unit=' + encodeURIComponent(unit) + '&scene=' + encodeURIComponent(sc.id)}>
          <b>{sc.id} · {sc.name}</b>
          <span>{sc.script_only
            ? '剧本场 · 待生成分镜'
            : ((sc.storyboards || []).length + ' 分镜 · ' + (sc.shots || []).length + ' 镜')}</span>
        </Link>
      ))}
    </div>
  )
}

function Player({ sb, shots, onSelect }) {
  const video = useRef(null)
  const box = useRef({ start: 0, end: 0 })
  const [loop, setLoop] = useState(false)
  const [cur, setCur] = useState('')

  useEffect(() => {
    const v = video.current
    if (!v) return
    const onTime = () => {
      if (!loop) return
      if (v.currentTime > box.current.end) v.currentTime = box.current.start
    }
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [loop])

  if (!sb.video) return null
  const total = Math.max(Number(sb.sec || 0), 0.1)
  return (
    <div className="sbplayer">
      <video ref={video} src={img(sb.video)} controls preload="metadata" />
      <div className="timeline">
        {shots.map((s) => {
          const left = Math.max(0, Math.min(100, Number(s.local_start || 0) / total * 100))
          const width = Math.max(1.5, Math.min(100 - left,
            (Number(s.local_end || 0) - Number(s.local_start || 0)) / total * 100))
          return (
            <button key={s.h} className={'mark' + (cur === s.h ? ' on' : '')}
              style={{ left: left + '%', width: width + '%' }}
              title={s.h}
              onClick={() => {
                setCur(s.h)
                box.current = { start: Number(s.local_start || 0), end: Number(s.local_end || 0) }
                if (video.current) video.current.currentTime = Number(s.local_start || 0)
                onSelect(s.h)
              }}>
              {s.h}
            </button>
          )
        })}
      </div>
      <div className="ptools">
        <button className={'btn small' + (loop ? ' primary' : '')}
          onClick={() => { setLoop(!loop); if (!loop && video.current) video.current.currentTime = box.current.start }}>
          {loop ? '循环当前镜' : '循环当前镜'}
        </button>
        <button className="btn small" onClick={() => {
          setLoop(false)
          if (video.current) { video.current.currentTime = 0; video.current.play().catch(() => {}) }
        }}>从头播</button>
      </div>
    </div>
  )
}

function ShotCard({ shot, onVerdict, onNote, onKeyframe, onWb }) {  const [noteOpen, setNoteOpen] = useState(false)
  const [note, setNote] = useState(shot.note || '')
  const v = shot.verdict || ''
  return (
    <article className="shot-card">
      <div className="shot-head">
        <b>{shot.h}</b>
        <span>{fmt(shot.local_start)}–{fmt(shot.local_end)}s · 景别 {shot.shot || '—'} · {shot.func || '—'}</span>
      </div>
      {(shot.render || shot.keyframe) && (
        <img className="shot-frame" src={img(shot.render || shot.keyframe)} alt={shot.h} />
      )}
      <dl className="shot-kv">
        <div><dt>运镜</dt><dd>{shot.move || '—'}</dd></div>
        <div><dt>画面</dt><dd>{shot.visual || '—'}{shot.action ? '　动作：' + shot.action : ''}</dd></div>
        <div><dt>台词</dt><dd>{shot.line || '（无台词）'}{shot.sound ? '　声音：' + shot.sound : ''}{shot.cut_at ? '　剪辑：' + shot.cut_at : ''}</dd></div>
        <div><dt>情绪</dt><dd>{shot.emotion || '—'}{shot.look ? ' · ' + shot.look : ''}{shot.memory ? ' · ' + shot.memory : ''}</dd></div>
      </dl>
      <div className="reviewbar">
        <button className={'btn small' + (v === 'ok' ? ' primary' : '')} onClick={() => onVerdict(shot, 'ok')}>过</button>
        <button className={'btn small' + (v === 'no' ? ' danger' : '')} onClick={() => onVerdict(shot, 'no')}>返工</button>
        <button className={'btn small' + (!v ? ' primary' : '')} onClick={() => onVerdict(shot, '')}>待定</button>
        <button className="btn small" onClick={() => setNoteOpen((x) => !x)}>备注</button>
        <button className="btn small" onClick={() => onKeyframe(shot)}>
          {shot.keyframe ? '重生成线稿' : '生成线稿'}
        </button>
        <button className="btn small" onClick={() => onWb(shot)}>打开 WorkBuddy 接手这一镜</button>
        <span className={'vstate ' + (v === 'ok' ? 'ok' : v === 'no' ? 'warn' : '')}>
          {v === 'ok' ? '已过' : v === 'no' ? '返工' : '未审'}
        </span>
      </div>
      {noteOpen && (
        <div className="notebox">
          <textarea className="input note" value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="审片备注" />
          <button className="btn small primary" onClick={() => onNote(shot, note)}>保存备注</button>
        </div>
      )}
    </article>
  )
}

// 表格「状态」列只显示状态，不放动作；动作统一在选中镜的详情里。
function GenCell({ shot, onRetry }) {
  if (shot._graphLoading) {
    return <span className="faint">图谱读取中…</span>
  }
  if (shot._graphError) {
    return (
      <>
        <span className="err">图谱读取失败</span>
        <button className="btn small"
          onClick={(e) => { e.stopPropagation(); onRetry() }}>重试</button>
      </>
    )
  }
  const badges = shotBadges(shot)
  const localDone = MEDIA_DONE.includes(shot.frame_status || '')
    || MEDIA_DONE.includes(shot.video_status || '')
  const localRunning = shot.frame_status === 'generating'
    || shot.video_status === 'generating'
  return (
    <>
      {badges.map((b) => <span key={b.key} className={b.tone}>{b.text}</span>)}
      {shot.remote_generating && !localRunning && !localDone && (
        <span className="info">LibTV 生成中</span>
      )}
      {shot.remote_done && !localDone && (
        <span className="warn">LibTV 已出图 · 待同步</span>
      )}
      {shot.remote_changed && <span className="info">LibTV 改过方案</span>}
    </>
  )
}

function SceneBrief({ scene }) {
  if (!scene) return null
  const d = scene.design || {}
  const design = [d.space, d.cameras, d.lights, d.style, d.props, d.memory]
    .filter(Boolean).join(' · ')
  const text = scene.official_text || (scene.script_section || {}).text || ''
  const rows = [
    ['目标', scene.goal],
    ['障碍', scene.obstacle],
    ['观众进入', scene.audience_in],
    ['观众离开', scene.audience_out],
    ['状态变化', scene.state_change],
    ['阻力 / 节拍', [scene.resistance, scene.beat_range].filter(Boolean).join(' · ')],
    ['设计', design],
  ].filter((x) => x[1])
  return (
    <div className="brief">
      {rows.map(([k, v]) => (
        <div className="brief-kv" key={k}>
          <span>{k}</span>
          <p>{v}</p>
        </div>
      ))}
      {(scene.asset_refs || []).length > 0 && (
        <div className="brief-acts">
          {(scene.asset_refs || []).map((a) => (
            <Link className="asset-mini" key={a.id + '@' + (a.variant || '')}
              to={'/assets?asset=' + encodeURIComponent(a.id)}>
              <b>{a.name}</b>
              <span>{a.id} · {a.image ? '有图' : '缺图'}</span>
            </Link>
          ))}
        </div>
      )}
      {!!text && (
        <div className="brief-text">
          <h5>剧本 / 构思</h5>
          <pre>{text}</pre>
        </div>
      )}
    </div>
  )
}

function ReviewTree({ scenes, storyboards, shots, sceneId, sbId, shotH, onPick }) {
  return (
    <div className="glass-card side-card review-tree">
      <div className="sec-head">
        <b>场 / 段 / 镜</b>
        <span className="faint small">{scenes.length} 场</span>
      </div>
      {scenes.map((sc) => {
        const sbs = (sc.storyboards || [])
          .map((id) => storyboards.find((x) => x.id === id)).filter(Boolean)
        return (
          <details className="rt-scene" open={sc.id === sceneId} key={sc.id}>
            <summary><b>{sc.id}</b><span>{sc.name}</span></summary>
            {sbs.map((sb) => (
              <div className="rt-seg" key={sb.id}>
                <button className={'rt-sb' + (sb.id === sbId ? ' on' : '')}
                  onClick={() => onPick(sc.id, sb.id, '')}>
                  {sb.id}<em>{sb.name || ''}</em>
                </button>
                {shots.filter((s) => s.storyboard === sb.id).map((s) => (
                  <button key={s.h}
                    className={'rt-shot' + (s.h === shotH ? ' on' : '')}
                    onClick={() => onPick(sc.id, sb.id, s.h)}>
                    {s.id}<em>{s.framing || ''}</em>
                    {s.verdict === 'ok' && <i className="ok">✓</i>}
                    {s.verdict === 'no' && <i className="warn">返</i>}
                  </button>
                ))}
              </div>
            ))}
            {!sbs.length && <p className="empty">还没有分镜。</p>}
          </details>
        )
      })}
    </div>
  )
}

export default function Review() {
  const { snap, wb, wbAsk, notify, unit, refresh } = useApp()
  const [params, setParams] = useSearchParams()
  const uid = params.get('unit') || (snap && snap.meta && snap.meta.unit) || unit
  const scenes = (snap && snap.scenes) || []
  const storyboards = (snap && snap.storyboards) || []
  const shots = (snap && snap.shots) || []
  const sceneId = params.get('scene') || (scenes[0] && scenes[0].id) || ''
  const scene = scenes.find((s) => s.id === sceneId) || scenes[0]
  const sbIds = (scene && scene.storyboards) || []
  const sceneSbs = sbIds.map((id) => storyboards.find((x) => x.id === id)).filter(Boolean)
  const sbId = params.get('sb') || (sceneSbs[0] && sceneSbs[0].id) || ''
  const sb = sceneSbs.find((x) => x.id === sbId) || sceneSbs[0]
  const sbShots = useMemo(
    () => shots.filter((s) => s.storyboard === (sb && sb.id)),
    [shots, sb]
  )
  const [selectedShot, setSelectedShot] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [busy, setBusy] = useState('')
  const [assetPick, setAssetPick] = useState(null)
  const [graphData, setGraphData] = useState(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState([])
  const [rework, setRework] = useState(null)
  const [bindTarget, setBindTarget] = useState(null)
  const [canvasState, setCanvasState] = useState({ rows: [], canvases: {}, at: '' })
  const [canvasAuto, setCanvasAuto] = useState(true)
  const [canvasBusy, setCanvasBusy] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)
  const [briefOpen, setBriefOpen] = useState(false)
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    if (!scene) return
    const q = new URLSearchParams(params)
    q.set('unit', uid)
    q.set('scene', scene.id)
    if (sb && !params.get('sb')) q.set('sb', sb.id)
    if (q.toString() !== params.toString()) setParams(q, { replace: true })
  }, [scene && scene.id, sb && sb.id])

  useEffect(() => { setSelectedShot('') }, [sb && sb.id])

  const [graphState, setGraphState] = useState('loading')

  useEffect(() => {
    if (!sb) { setGraphData(null); setGraphState('empty'); return }
    let alive = true
    setGraphState('loading')
    get('/api/storyboard/graph?' + new URLSearchParams({ unit: uid }))
      .then((r) => {
        if (!alive) return
        if (r && r.ok) { setGraphData(r); setGraphState('ok') }
        else { setGraphData(null); setGraphState('error') }
      })
      .catch(() => { if (alive) { setGraphData(null); setGraphState('error') } })
    return () => { alive = false }
  }, [uid, sb && sb.id, snap && snap.versions && snap.versions.storyboards])

  // 这一镜的 LibTV 结构 / 方案：切段时读一次，之后每 15 秒自动读。
  useEffect(() => {
    setCanvasState({ rows: [], canvases: {}, at: '' })
    if (sb && sb.libtv && sb.libtv.project_id) readCanvasState(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid, sb && sb.id])

  useEffect(() => {
    if (!canvasAuto || !sb) return undefined
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') readCanvasState(true)
    }, 15000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasAuto, sb && sb.id])

  // 图谱节点按「当前这一段 + 镜号」归位：外部素材 / 白膜 / 首帧 / 成片分开。
  // 不跨段合并，避免复制段 / 旧图谱残留时显示别段的状态。
  const graphShots = useMemo(() => {
    const out = {}
    const keyOf = (n) => (n.external && n.external.node_id) || n.node_id || ''
    const segs = (graphData && graphData.graph && graphData.graph.segments) || []
    for (const seg of segs) {
      if (sb && seg.id !== sb.id) continue
      for (const n of seg.nodes || []) {
        const raw = String(n.shot || '').trim()
        const sh = /^\d+$/.test(raw) ? raw.padStart(3, '0') : raw
        if (!sh) continue
        const row = out[sh] || (out[sh] = { nodes: 0, external_refs: [] })
        row.nodes += 1
        // LibTV 上读到的最新状态（「同步 LibTV 状态」之后才有）。
        if (n.remote_status === 'generating') row.remote_generating = true
        if (n.remote_status === 'done') row.remote_done = true
        if (n.remote_changed) row.remote_changed = true
        const stage = n.stage || (n.role === 'reference' || n.role === 'reference_video'
          ? 'reference' : n.role === 'video' ? 'video' : 'final')
        if (n.role === 'reference' || n.role === 'reference_video') {
          if (n.source !== 'external') continue
          const ext = n.external || {}
          row.external_refs.push({
            id: n.id || '',
            label: ext.label || n.name || n.id || '',
            kind: ext.kind || n.type || 'image',
            node_id: keyOf(n),
            bound: !!keyOf(n),
            stage, role: n.role || 'reference',
            status: n.status || 'planned',
            missing: !!(ext.missing || n.status === 'missing'),
            prompt: n.prompt || '',
          })
          continue
        }
        if (n.role === 'video') {
          row.video_status = n.status || ''
          row.video_wired = !!keyOf(n)
          row.video_id = n.id || ''
          row.video_node = keyOf(n)
          row.video_prompt = n.prompt || ''
          row.video_needs_push = !!n.needs_push
          row.video_rev = Number(n.current_rev || n.rev || 0)
          row.video_history = (n.history || []).length
          if (n.file) row.video_file = n.file
          if (n.remote_at) {
            row.video_remote = {
              status: n.remote_status || '', prompt: n.remote_prompt || '',
              params: n.remote_params || {}, model: n.remote_model || '',
              style: n.remote_style || [], at: n.remote_at || '',
              changed: !!n.remote_changed,
            }
          }
          continue
        }
        if (n.type !== 'image') continue
        if (stage === 'greybox') {
          row.greybox_status = n.status || ''
          row.greybox_wired = !!keyOf(n)
          row.greybox_node = keyOf(n)
          if (n.file) row.greybox_file = n.file
          continue
        }
        if (n.role === 'first_frame' || n.role === 'last_frame') {
          row._frames = row._frames || []
          row._frames.push(n)
        }
        const rank = n.role === 'first_frame' ? 0
          : n.role === 'image2image' ? 1
            : n.role === 'last_frame' ? 2 : 3
        if (row._rank === undefined || rank < row._rank) {
          row._rank = rank
          row.frame_status = n.status || ''
          row.frame_wired = !!keyOf(n)
          row.frame_id = n.id || ''
          row.frame_role = n.role || ''
          row.frame_node = keyOf(n)
          row.frame_prompt = n.prompt || ''
          row.frame_needs_push = !!n.needs_push
          row.frame_rev = Number(n.current_rev || n.rev || 0)
          row.frame_history = (n.history || []).length
          if (n.file) row.frame_file = n.file
          if (n.url) row.frame_url = n.url
          row.frame_id = n.id || ''
          row.frame_remote = n.remote_at ? {
            status: n.remote_status || '', prompt: n.remote_prompt || '',
            params: n.remote_params || {}, model: n.remote_model || '',
            style: n.remote_style || [], at: n.remote_at || '',
            changed: !!n.remote_changed,
          } : null
        }
      }
    }
    for (const row of Object.values(out)) {
      const frames = row._frames || []
      row.frames_ready = !frames.some((n) => !MEDIA_DONE.includes(n.status || ''))
      row.frames_missing = frames
        .filter((n) => !MEDIA_DONE.includes(n.status || ''))
        .map((n) => n.id)
      row.needs_binding = row.external_refs.filter((x) => !x.bound || x.missing).length
      row.output_nodes = row.nodes - row.external_refs.length
      delete row._frames
      delete row._rank
    }
    return out
  }, [graphData, sb && sb.id])

  const graphWired = Object.values(graphShots).some((r) =>
    r.frame_wired || r.video_wired || r.greybox_wired)
  const graphHasNodes = Object.values(graphShots).some((r) => (r.nodes || 0) > 0)

  const globalBusy = !!busy

  const rowState = useCallback((s) => {
    const localVideo = ((s.media || []).find((m) => m.kind === 'video') || {}).path || ''
    if (graphState === 'error') {
      // 图谱读失败：不伪造 wired/generated，动作全部锁住，给「重试」。
      return { ...s, nodes: 0, _graphError: true, render: frameImg(s) }
    }
    if (graphState !== 'ok') {
      // 图谱还在加载：只显示本地缩略图，不伪造生成状态。
      return { ...s, nodes: 0, _graphLoading: true, render: frameImg(s) }
    }
    const g = graphShots[s.id] || {}
    return {
      ...s,
      ...g,
      render: g.frame_file || frameImg(s),
      render_url: g.frame_url || '',
      video_file: g.video_file || localVideo,
    }
  }, [graphState, graphShots])

  if (!scene) {
    const official = (snap && snap.script && snap.script.official) || {}
    const ctx = '剧本《' + (official.title || '') + '》· ' + (official.path || uid)
    return (
      <section className="page review-page">
        <div className="backbar">
          <Link className="backbtn" to={'/script?unit=' + encodeURIComponent(uid)}>← 剧本</Link>
        </div>
        <div className="page-head doc-head">
          <div>
            <h1>审片台</h1>
            <p>{uid} ｜ 这一集还没有场次 / 分镜</p>
          </div>
          <div className="acts">
            <button className="btn" onClick={() => wb('从剧本生成场', ctx)}>生成场</button>
            <button className="btn primary" onClick={() => wb('从剧本生成分镜', ctx)}>生成分镜</button>
          </div>
        </div>
        <div className="glass-card sb-card">
          <div className="sec-head"><b>还没有场次</b></div>
          <p className="empty">
            剧本已经在，但还没切场 / 拆镜。点上面「生成场」先把剧本切成场，再「生成分镜」拆出镜头；
            生成后这场会自动出现在左边的场次列表里。
          </p>
        </div>
        {official.text ? (
          <div className="glass-card sb-card">
            <div className="sec-head"><b>剧本原文</b><span className="faint">还没切场，先按原文审</span></div>
            <Markdown text={official.text} />
          </div>
        ) : (
          <div className="glass-card sb-card">
            <div className="sec-head"><b>剧本</b></div>
            <p className="empty">这一集还没有剧本。先回剧本页写正文。</p>
          </div>
        )}
      </section>
    )
  }

  const sceneCtx = '剧本《' + (snap.script.official.title || '') + '》· 场 ' + scene.id
  const lib = (sb && sb.libtv) || {}
  const sceneLib = scene.libtv || {}
  const bound = !!(lib.node_id || lib.bound)
  const project = !!(sceneLib.project_id || lib.project_id)
  const gate = (sb && sb.wire_gate) || scene.wire_gate
    || { ready: true, required: 0, blockers: [] }
  const assetsReady = gate.ready !== false
  const wiredRefs = (sb && sb.wired_refs) || []
  const refNo = (r) => wiredRefs.findIndex((x) => x.id === r.id
    && (x.variant || '') === (r.variant || '')) + 1
  const issues = []
  const bindingTotal = sbShots.reduce((n, s) =>
    n + Number(rowState(s).needs_binding || 0), 0)
  if (bindingTotal) issues.push(bindingTotal + ' 个素材待绑定')
  if (!project) issues.push('本场 LibTV 还没建')
  if (sb && !sb.prompt) issues.push('这一分镜缺提示词')
  if (sb && bound && !sb.video) issues.push('已布线，还没生成')
  if (sb && sb.video && !sbShots.some((s) => s.verdict)) issues.push('已生成，还没审')
  if (!assetsReady) issues.push('引用资产还没全部出图，布线和生成会锁住')
  const hasText = (v) => v && !/^(—|-|–|暂无|未定|待定|无)$/.test(String(v).trim())
  const designRows = [
    ['空间', (scene.design || {}).space], ['机位', (scene.design || {}).cameras],
    ['灯光', (scene.design || {}).lights], ['风格', (scene.design || {}).style],
    ['道具', (scene.design || {}).props], ['记忆点', (scene.design || {}).memory],
  ].filter(([, v]) => hasText(v))

  function ask(title, desc) {
    return new Promise((resolve) => setConfirm((prev) => {
      // 上一个确认框还开着就把它取消掉，避免它的 Promise 永远不结束。
      if (prev) prev.resolve(false)
      return { title, desc, resolve }
    }))
  }
  function closeConfirm(ok) {
    const c = confirm
    setConfirm(null)
    if (c) c.resolve(ok)
  }

  async function run(key, fn) {
    setBusy(key)
    try { await fn() } catch (e) {
      notify('操作失败：' + (e.message || e), 'error')
    } finally { setBusy('') }
  }

  async function verdict(shot, v) {
    await run('verdict-' + shot.id, async () => {
      const r = await post('/api/review', { unit: uid, id: shot.id, verdict: v })
      if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
      notify('镜 ' + shot.id + '：' + (v === 'ok' ? '过' : v === 'no' ? '返工' : '待定'), 'ok')
      refresh()
    })
  }

  async function saveNote(shot, note) {
    await run('note-' + shot.id, async () => {
      const r = await post('/api/review', { unit: uid, id: shot.id, note })
      if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
      notify('备注已保存', 'ok')
      refresh()
    })
  }

  async function keyframe(shot) {
    const ok = await ask('生成线稿关键帧',
      '用 LibTV Z Image Turbo 1K 生成一张黑白线稿，可能消耗积分。只影响这一个镜头。')
    if (!ok) return
    await run('kf-' + shot.id, async () => {
      const r = await post('/api/keyframe/gen', {
        unit: uid, shot: shot.id, force: !!shot.keyframe, confirm: true,
      })
      if (!r.ok) { notify('线稿生成失败：' + (r.error || ''), 'error'); return }
      notify('线稿已生成：' + (r.shot || shot.id), 'ok')
      refresh()
    })
  }

  async function wire() {
    if (!sb) return
    if (!assetsReady) {
      notify('引用资产还没全部出图：先去资产库「布线并生成」并同步', 'warn')
      return
    }
    await run('wire', async () => {
      const p = await post('/api/storyboard/wire', {
        unit: uid, sb: sb.id, method: 'auto', dry_run: true,
      })
      if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
      if (p.method_ready === false) { notify(p.method_error || '这个手法还缺输入', 'warn'); return }
      if (p.assets_ready === false) {
        notify('还有 ' + (p.assets_blockers || []).length + ' 个引用资产没出图', 'warn')
        return
      }
      const modeName = p.method === 'image' ? '智能参考（多图 @Image）'
        : p.method === 'frames' ? '首尾帧' : '纯文本'
      const bits = [
        '模式：' + modeName + (p.mode_type ? '（' + p.mode_type + '）' : ''),
        p.needs_sync ? '会先补齐本场项目 / 工作流组' : '',
        '参考图 ' + ((p.refs || []).length) + ' 张（本次上传 ' + ((p.uploads || []).length) + ' 张）',
        (p.prompt_ref_block ? '提示词已自动补 @Image 素材引用' : ''),
        '视频节点：' + (p.action === 'update' ? '更新已有节点' : '新建节点') +
          ' · ' + (p.model || '') + ' · ' + (p.settings?.duration || '') + 's',
        '只布线，不生成；生成请到 LibTV 里点。',
      ].filter(Boolean)
      const ok = await ask((p.action === 'update' ? '重新布线' : '布线') + ' · ' + modeName, bits.join('；'))
      if (!ok) return
      const r = await post('/api/storyboard/wire', {
        unit: uid, sb: sb.id, method: 'auto', dry_run: false, confirm: true,
      })
      if (!r.ok) { notify('布线失败：' + (r.error || ''), 'error'); return }
      notify('已布线：' + (r.left || 0) + ' 个输入 · 去 LibTV 点生成', 'ok')
      refresh()
    })
  }

  async function sbSync() {
    if (!sb) return
    await run('sb-sync', async () => {
      const r = await post('/api/storyboard/sync', { unit: uid, sb: sb.id })
      if (!r.ok) { notify('同步失败：' + (r.error || ''), 'error'); return }
      notify(r.message || '已同步', r.status === 'error' ? 'error' : 'ok')
      refresh()
    })
  }

  async function sbPull() {
    if (!sb) return
    await run('sb-pull', async () => {
      let r = await post('/api/storyboard/pull', { unit: uid, sb: sb.id })
      if (!r.ok) { notify('同步线上失败：' + (r.error || ''), 'error'); return }
      if (r.changed) {
        const ok = await ask('线上改过这一段的提示词', '要把线上的版本写回本地吗？选「保留本地」就什么都不改。')
        if (!ok) { notify('保留了本地版本', 'warn'); return }
        r = await post('/api/storyboard/pull', { unit: uid, sb: sb.id, apply: true })
      }
      notify(r.message || (r.changed ? '已同步线上' : '线上和本地一致'), r.ok ? 'ok' : 'warn')
      if (r.changed) refresh()
    })
  }

  async function sceneSync() {
    await run('scene-sync', async () => {
      const p = await post('/api/libtv/sync-scene', { unit: uid, scene: scene.id, dry_run: true })
      if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
      const row = (p.scenes || [])[0] || {}
      const ok = await ask('同步本场 LibTV 结构',
        '会补齐场项目 / 资产文件夹 / 工作流组，不改剧本和提示词。' +
        (row.reason ? '原因：' + row.reason : ''))
      if (!ok) return
      const r = await post('/api/libtv/sync-scene', {
        unit: uid, scene: scene.id, dry_run: false, confirm: true,
      })
      if (!r.ok) { notify('同步失败：' + (r.error || ''), 'error'); return }
      const x = (r.scenes || [])[0] || {}
      notify('本场结构已同步：复用组 ' + (x.groups_reused || 0) +
        ' · 新建组 ' + (x.groups_new || 0), (r.errors || []).length ? 'warn' : 'ok')
      refresh()
    })
  }

  async function ensureProject() {
    await run('ensure', async () => {
      const p = await post('/api/libtv/ensure', { unit: uid, scene: scene.id, dry_run: true })
      if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
      const ok = await ask('补齐本场 LibTV 结构',
        '会建 / 修项目、资产文件夹和工作流组；不改剧本和提示词。')
      if (!ok) return
      const r = await post('/api/libtv/ensure', {
        unit: uid, scene: scene.id, dry_run: false, confirm: true,
      })
      if (!r.ok) { notify('补齐失败：' + (r.error || ''), 'error'); return }
      notify('本场结构已补齐', 'ok')
      refresh()
    })
  }

  async function archive() {
    const ok = await ask('归档旧项目', '把不再对应当前场的旧 LibTV 项目移进 [归档] 容器；组不会删。')
    if (!ok) return
    await run('archive', async () => {
      const r = await post('/api/libtv/archive', { unit: uid, dry_run: false, confirm: true })
      if (!r.ok) { notify('归档失败：' + (r.error || ''), 'error'); return }
      notify('旧项目已归档', 'ok')
      refresh()
    })
  }

  // 先把整段的方案图谱一次接好（参考图 / 首帧 / 视频节点），生成留给 LibTV。
  async function wireSegment() {
    if (!sb) return
    let dry
    try {
      dry = await post('/api/storyboard/graph/wire', {
        unit: uid, segment: sb.id, dry_run: true,
      })
    } catch (e) {
      notify('检查失败：' + (e.message || e), 'error')
      return
    }
    if (dry.hard_ok === false) {
      notify(((dry.hard_blockers || [])[0] || {}).message || '这一段还不能布线', 'warn')
      return
    }
    if (dry.hard_ok === undefined && !dry.ok) {
      notify(dry.error || '这一段还不能布线', 'warn')
      return
    }
    const plan = dry.plan || {}
    const cost = plan.cost || {}
    const skip = sbShots.reduce((n, s) =>
      n + Number(rowState(s).needs_binding || 0), 0)
    const ok = await ask('布线整段 · ' + sb.id,
      '把这一段的参考图 / 首帧 / 视频节点一起接到 LibTV 画布。只布线，不生成。' +
      ' 参考图 ' + (cost.uploads || 0) + ' 张 · 出图 ' + (cost.images || 0) +
      ' 个 · 出片 ' + (cost.videos || 0) + ' 条。'
      + (skip ? ' 有 ' + skip + ' 个素材待绑定，本次会跳过。' : ''))
    if (!ok) return
    await run('wire-seg', async () => {
      const r = await post('/api/storyboard/graph/wire', {
        unit: uid, segment: sb.id, dry_run: false, confirm: true,
      })
      if (!r.ok) {
        notify('布线失败：' + ((((r.errors || [])[0] || {}).error) || r.error || ''), 'error')
        await refresh()
        await reloadGraph()
        return
      }
      const msg = deferredMessage(r)
      if (msg) notify('已布线；' + msg, 'warn')
      else notify('方案图谱已接好，去 LibTV 逐个生成', 'ok')
      await refresh()
      await reloadGraph()
    })
  }

  async function reloadGraph() {
    try {
      const g = await get('/api/storyboard/graph?' + new URLSearchParams({ unit: uid }))
      if (g && g.ok) { setGraphData(g); setGraphState('ok') }
      else setGraphState('error')
    } catch (e) {
      setGraphState('error')
    }
  }

  async function wireRow(shot) {
    if (!sb) return
    let dry
    try {
      dry = await post('/api/storyboard/graph/wire', {
        unit: uid, segment: sb.id, shot: shot.id, dry_run: true,
      })
    } catch (e) {
      notify('检查失败：' + (e.message || e), 'error')
      return
    }
    // 视频等首帧属于 defer，是合法中间态，不阻止先布首帧。
    if (dry.hard_ok === false) {
      notify(((dry.hard_blockers || [])[0] || {}).message || '这一镜还不能布线', 'warn')
      return
    }
    if (dry.hard_ok === undefined && !dry.ok) {
      notify(dry.error || '这一镜还不能布线', 'warn')
      return
    }
    const ok = await ask('布线 · 镜 ' + shot.id,
      '先布参考图和首帧节点；视频节点等首帧生成后再补布。只布线，不生成。')
    if (!ok) return
    await run('wire-' + shot.id, async () => {
      const r = await post('/api/storyboard/graph/wire', {
        unit: uid, segment: sb.id, shot: shot.id, dry_run: false, confirm: true,
      })
      if (!r.ok) {
        notify('布线失败：' + ((((r.errors || [])[0] || {}).error) || r.error || ''), 'error')
        await refresh()
        await reloadGraph()
        return
      }
      const msg = deferredMessage(r)
      if (msg) notify('已布线；' + msg, 'warn')
      else notify('镜 ' + shot.id + ' 已布线', 'ok')
      await refresh()
      await reloadGraph()
    })
  }

  async function syncRow() {
    if (!sb) return
    await run('row-sync', async () => {
      const r = await post('/api/storyboard/graph/sync', { unit: uid, segment: sb.id })
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
      await refresh()
      await reloadGraph()
    })
  }

  function rowAction(shot, action) {
    if (action.kind === 'wire') return wireRow(shot)
    if (action.kind === 'sync') return syncRow()
    if (action.kind === 'graph') {
      return askWire()
    }
    if (action.kind === 'bind') {
      const ref = (rowState(shot).external_refs || [])
        .find((x) => !x.bound || x.missing)
      if (!ref) { notify('这一镜没有待绑定的素材', 'info'); return }
      setBindTarget({
        id: ref.id, type: ref.kind, segment: sb.id,
        external: { kind: ref.kind, label: ref.label },
      })
      return
    }
    if (action.kind === 'open') {
      const url = nodeUrl(sb && sb.libtv && sb.libtv.editor_url, action.nodeKey)
      if (!url) { notify('这一镜还没连到 LibTV，先布线', 'warn'); return }
      window.open(url, '_blank', 'noreferrer')
    }
  }

  function pickTree(sceneId, sbId2, h) {
    const q = new URLSearchParams(params)
    q.set('unit', uid)
    q.set('scene', sceneId)
    q.set('sb', sbId2)
    setParams(q, { replace: true })
    setSelectedShot(h || '')
  }

  function nextSegment() {
    const idx = sceneSbs.findIndex((x) => x.id === (sb && sb.id))
    const next = sceneSbs[idx + 1]
    if (!next) { notify('这是本场最后一段', 'info'); return }
    const q = new URLSearchParams(params)
    q.set('sb', next.id)
    setParams(q, { replace: true })
  }

  async function openHistory() {
    try {
      const r = await get('/api/storyboard/history?' + new URLSearchParams({ unit: uid }))
      if (r && r.ok) setHistory(r.entries || [])
    } catch (e) { /* ignore */ }
    setHistoryOpen(true)
  }

  async function graphWireShot(shot) {
    await run('shot-wire-' + shot.id, async () => {
      const r = await post('/api/storyboard/graph/wire', {
        unit: uid, segment: sb.id, shot: shot.id, dry_run: false, confirm: true,
      })
      if (!r.ok) {
        notify('重跑失败：' + ((((r.errors || [])[0] || {}).error) || ''), 'error')
        return
      }
      notify('这一镜已更新', 'ok')
      await refresh()
      const g = await get('/api/storyboard/graph?' + new URLSearchParams({ unit: uid }))
      if (g && g.ok) setGraphData(g)
    })
  }

  function saveAssets(ids) {
    const entries = (ids || []).map((x) => typeof x === 'string'
      ? { id: x, variant: '' } : x)
    const target = assetPick
    setAssetPick(null)
    run('assets', async () => {
      if (target === 'scene') {
        const cast = entries.filter((e) => {
          const a = (snap.assets || []).find((x) => x.id === e.id)
          return a && a.type === 'characters'
        }).map((e) => e.variant ? e : e.id)
        const rest = entries.filter((e) => !cast.some((c) =>
          (typeof c === 'string' ? c : c.id) === e.id))
          .map((e) => e.variant ? e : e.id)
        const r = await post('/api/scene/assets', {
          unit: uid, scene: scene.id, cast, assets: rest,
        })
        if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
      } else {
        const r = await post('/api/storyboard/assets', { unit: uid, sb: sb.id, assets: ids })
        if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
      }
      notify('资产引用已保存', 'ok')
      refresh()
    })
  }

  const currentShot = sbShots.find((s) => s.h === selectedShot) || sbShots[0] || null
  const bindingCount = sbShots.reduce((n, s) =>
    n + Number(rowState(s).needs_binding || 0), 0)
  const anyBinding = bindingCount > 0
  const curState = currentShot ? rowState(currentShot) : null
  const shotFrame = curState
    ? ((IMG_RX.test(curState.frame_file || '') ? curState.frame_file : '')
      || curState.frame_url || '') : ''
  const shotVideo = (curState && curState.video_file) || ''
  const curAction = currentShot ? nextAction(rowState(currentShot)) : null
  const framePrompt = (curState && curState.frame_prompt) || ''
  const videoPrompt = (curState && curState.video_prompt) || ''
  const frameNodeId = (curState && curState.frame_id) || ''
  const videoNodeId = (curState && curState.video_id) || ''
  const promptNodeId = frameNodeId || videoNodeId || ''
  const promptNeedsPush = frameNodeId
    ? !!(curState && curState.frame_needs_push)
    : !!(curState && curState.video_needs_push)
  const frameKind = { first_frame: '首帧', last_frame: '尾帧', image2image: '图生图' }[
    (curState && curState.frame_role) || ''] || '画面'
  const promptLabel = frameNodeId ? frameKind + '节点提示词'
    : (videoNodeId ? '视频节点提示词' : '分镜提示词（旧版 prompts.json）')
  const shownPrompt = frameNodeId ? framePrompt
    : (videoNodeId ? videoPrompt
      : ((currentShot && currentShot.still_prompt)
        || (currentShot && currentShot.visual) || ''))
  const curRemote = (curState && (curState.frame_remote || curState.video_remote)) || null
  const remoteBits = (() => {
    if (!curRemote) return ''
    const parts = []
    if (curRemote.status) parts.push(curRemote.status)
    if (curRemote.model) parts.push(curRemote.model)
    const p = curRemote.params || {}
    if (p.ratio) parts.push(p.ratio)
    if (p.resolution) parts.push(p.resolution)
    if (p.duration) parts.push(p.duration + 's')
    const style = (curRemote.style || []).map((x) => x.name).filter(Boolean)
    if (style.length) parts.push('风格 ' + style.join(' / '))
    return parts.join(' · ')
  })()
  // ---- WorkBuddy 任务卡：镜 / 节点 / 布线都先钉目标，再留一块给用户写意图 ----
  const graphSeg = () => {
    const segs = (graphData && graphData.graph && graphData.graph.segments) || []
    return segs.find((x) => x.id === (sb && sb.id)) || null
  }
  const graphNode = (id) => {
    const seg = graphSeg()
    return ((seg && seg.nodes) || []).find((n) => n.id === id) || null
  }
  const shotNodes = (shot) => {
    const seg = graphSeg()
    const sid = String((shot && shot.id) || '').replace(/^0+/, '')
    return ((seg && seg.nodes) || []).filter((n) => {
      const s = String(n.shot || '').replace(/^0+/, '')
      if (s) return s === sid
      const m = /^[a-z]+_(\d+)/i.exec(String(n.id || ''))
      return !!m && m[1].replace(/^0+/, '') === sid
    })
  }

  const askNodePrompt = (node, initialIntent) => {
    const n = graphNode(node)
    const role = ROLE_CN[(n && n.role) || ''] || (n && n.role) || '节点'
    wbAsk({
      title: '改这个节点的提示词',
      desc: '只改这一个节点的 prompt；画布结构、上下游和其他节点都不动。',
      targets: [
        { label: '文件', value: 'units/' + uid + '/storyboard/graph.json' },
        { label: '字段', value: 'segments[' + (sb ? sb.id : '') + '].nodes[' + node + '].prompt' },
        { label: '节点', value: node + ' · ' + role
          + (n && n.model ? ' · ' + n.model : '')
          + (n && n.node_id ? ' · 已布线' : ' · 未布线') },
      ],
      note: '改完回 Trestle 点「更新到 LibTV」，LibTV 上才会变。',
      presets: ['更贴近这一镜的画面', '主体动作更明确', '光线更明确',
        '构图更简洁', '去掉背景杂物', '我还没想好，先给 2 个改法'],
      placeholder: '例：改成低机位近景，人物在画面左侧，背景压暗，只留一盏顶光',
      initialIntent: String(initialIntent || '').replace(/^[；;、\s]+/, ''),
      action: '写这个节点提示词',
      ctx: sceneCtx + ' · 段 ' + (sb ? sb.id : '') + ' · 节点 ' + node,
      extra: { unit: uid, segment: sb && sb.id, node },
    })
  }

  const askShot = (shot, focus, initialIntent) => {
    if (!shot) return
    const nodes = shotNodes(shot)
    const bits = nodes.map((n) => n.id + '（'
      + (ROLE_CN[n.role] || n.role || '节点') + '）').join('、')
    wbAsk({
      title: '改这一镜',
      desc: '先选改哪一层，再说想改成什么；只处理这一镜，不碰其他镜。',
      targets: [
        { label: '镜', value: shot.h + '（id=' + shot.id + '）'
          + (shot.framing ? ' · ' + shot.framing : '')
          + (shot.camera ? ' / ' + shot.camera : '') },
        { label: '分镜', value: 'units/' + uid + '/storyboard/shots.json → id="' + shot.id + '"' },
        { label: '提示词', value: 'units/' + uid + '/storyboard/graph.json → 段 '
          + (sb ? sb.id : '') + ' 的 ' + (bits || '这一镜还没有节点') },
        { label: '不改', value: '其他镜、其他段、资产、剧本；不布线、不生成' },
      ],
      choices: {
        label: '先改哪一层',
        options: [
          { value: 'all', label: '整镜（先看问题）' },
          { value: 'shot', label: '分镜描述 / 动作' },
          { value: 'frame', label: '首帧提示词' },
          { value: 'video', label: '视频提示词' },
          { value: 'ref', label: '参考图' },
          { value: 'param', label: '参数（时长 / 画幅）' },
          { value: 'recipe', label: '配方 / 节点结构' },
        ],
        defaultValue: focus || 'all',
        // focus 由后端任务书单独写一行「本轮只先改：…」，这里不再重复。
        silent: true,
      },
      presets: ({
        ref: ['换一张更干净的参考图', '补一张背面 / 侧面参考', '参考图别带背景'],
        param: ['时长改成 4 秒', '画幅改成 16:9', '分辨率提高一档'],
        recipe: ['减少节点，流程更短', '加首尾帧让转场更稳', '换一个更稳的配方'],
      }[focus]) || ['动作更明确', '节奏更快、时长缩短', '换成近景', '情绪再强一点',
        '我还没想好，先给 2 个改法'],
      placeholder: '例：动作改成先抬手再转身；首帧换成近景，人物在右三分之一',
      initialIntent: initialIntent || '',
      action: '接手这一镜',
      ctx: sceneCtx + ' · 段 ' + (sb ? sb.id : '') + ' · 镜 ' + shot.id,
      extra: { unit: uid, segment: sb && sb.id, shot: shot.id },
    })
  }

  const askWire = () => {
    const seg = graphSeg()
    const nodes = (seg && seg.nodes) || []
    const edges = (seg && seg.edges) || []
    wbAsk({
      title: '重做这一段的布线方案',
      desc: '只改这一段的节点结构和连线；已有提示词默认不动。',
      targets: [
        { label: '文件', value: 'units/' + uid + '/storyboard/graph.json' },
        { label: '段', value: (sb ? sb.id : '') + ' · ' + ((sb && sb.name) || '') },
        { label: '现在', value: (seg && seg.recipe ? '配方 ' + seg.recipe + ' · ' : '')
          + nodes.length + ' 个节点 · ' + edges.length + ' 条连线' },
        { label: '不改', value: '其他段、剧本、分镜、资产；不建 LibTV 节点' },
      ],
      presets: ['减少节点，流程更短', '加首尾帧让转场更稳', '换一个更稳的配方',
        '先给两个方案让我选'],
      placeholder: '例：把这一镜拆成首帧 + 视频两步，去掉中间参考节点',
      action: '出图谱',
      ctx: sceneCtx + ' · 段 ' + (sb ? sb.id : ''),
      extra: { unit: uid, segment: sb && sb.id, graph: true },
    })
  }

  const askScene = (initialIntent, title) => wbAsk({
    title: title || '改这一场',
    desc: '只改这一场的剧本段落和场分析；其他场、分镜、资产不动。',
    targets: [
      { label: '剧本', value: 'units/' + uid + '/script/copy.md → 场 ' + scene.id },
      { label: '场分析', value: 'units/' + uid + '/storyboard/scenes.json → ' + scene.id },
      { label: '现状', value: (scene.name || scene.id)
        + (scene.goal ? ' · 目标：' + scene.goal : '') },
    ],
    presets: ['这场节奏再紧一点', '补强冲突 / 障碍', '改人物动机',
      '我还没想好，先说说问题'],
    placeholder: '例：把这场改成两人先沉默，再由一句台词打破；目标改成“让观众怀疑主角”',
    initialIntent: initialIntent || '',
    action: '改这场',
    ctx: sceneCtx,
    extra: { unit: uid, scene: scene.id },
  })
  const pushNodePrompt = async (node) => {
    if (!sb || !node) return
    setBusy('push-' + node)
    try {
      const r = await post('/api/storyboard/graph/push', {
        unit: uid, segment: sb.id, node, confirm: true,
      })
      if (!r.ok) { notify('推到 LibTV 失败：' + (r.error || ''), 'error'); return }
      notify('已更新到 LibTV', 'ok')
      await reloadGraph()
      refresh()
    } finally {
      setBusy('')
    }
  }

  // 读这一镜的 LibTV 节点状态 + 画布结构；只读。
  async function readCanvasState(silent = true) {
    if (!sb) return
    if (!(sb.libtv && sb.libtv.project_id)) {
      setCanvasState({ rows: [], canvases: {}, at: '' })
      return
    }
    if (!silent) setCanvasBusy(true)
    try {
      const r = await post('/api/storyboard/graph/state', {
        unit: uid, segment: sb.id,
      })
      if (r && r.ok) {
        setCanvasState({
          rows: r.rows || [], canvases: r.canvases || {},
          at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        })
        await reloadGraph()
        if (!silent) {
          const changed = (r.rows || []).filter((x) => x.remote_changed).length
          notify(changed
            ? 'LibTV 上有 ' + changed + ' 个节点改过方案，选节点可「采用线上方案」'
            : 'LibTV 节点状态已更新', changed ? 'warn' : 'ok')
        }
      } else if (!silent) {
        notify('同步 LibTV 状态失败：' + ((((r.errors || [])[0] || {}).error)
          || r.error || ''), 'error')
      }
    } finally {
      if (!silent) setCanvasBusy(false)
    }
  }

  async function syncLibtvState() {
    if (!sb) return
    await run('state', async () => {
      await readCanvasState(false)
      await refresh()
    })
  }

  async function revealPath(rel) {
    if (!rel) { notify('还没有对应的文件夹', 'warn'); return }
    const r = await post('/api/reveal', { path: rel })
    if (!r.ok) notify('打不开：' + (r.error || ''), 'error')
  }

  function openNodeById(n) {
    if (!n) return
    const key = n.node_id || (n.external && n.external.node_id) || n.id
    const url = nodeUrl(sb && sb.libtv && sb.libtv.editor_url, key)
    if (!url) { notify('这一镜还没连到 LibTV，先布线', 'warn'); return }
    window.open(url, '_blank', 'noreferrer')
  }

  async function pushAllNodes() {
    if (!sb) return
    const segs = (graphData && graphData.graph && graphData.graph.segments) || []
    const seg = segs.find((x) => x.id === sb.id) || {}
    const dirty = (seg.nodes || []).filter((n) => n.needs_push)
    if (!dirty.length) return
    await run('push-all', async () => {
      const errors = []
      for (const n of dirty) {
        const r = await post('/api/storyboard/graph/push', {
          unit: uid, segment: sb.id, node: n.id, confirm: true,
        })
        if (!r.ok) errors.push(n.id + '：' + (r.error || '失败'))
      }
      if (errors.length) notify('还有没推上去：' + errors[0], 'error')
      else notify(dirty.length + ' 个节点已更新到 LibTV', 'ok')
      await reloadGraph()
      await refresh()
    })
  }

  async function pullNode(node) {
    if (!sb || !node) return
    const ok = await ask('采用 LibTV 版本 · ' + node,
      '会把 LibTV 节点上的提示词 / 参数写回本地 graph.json，覆盖本地这一版。'
      + '已有输出不会变，要换图 / 换片得在 LibTV 重新生成。')
    if (!ok) return
    await run('pull-' + node, async () => {
      const r = await post('/api/storyboard/graph/pull', {
        unit: uid, segment: sb.id, node, confirm: true, params: true,
      })
      if (!r.ok) { notify('采用失败：' + (r.error || ''), 'error'); return }
      notify(r.message || '已采用 LibTV 版本', 'ok')
      await reloadGraph()
      await refresh()
    })
  }

  function openRef(ref) {
    const url = nodeUrl(sb && sb.libtv && sb.libtv.editor_url, ref.node_id)
    if (!url) { notify('这个素材还没绑定 LibTV 节点', 'warn'); return }
    window.open(url, '_blank', 'noreferrer')
  }

  function bindFirst() {
    const ref = ((curState && curState.external_refs) || [])
      .find((x) => !x.bound || x.missing)
    if (!ref) { notify('没有待绑定的素材', 'info'); return }
    setBindTarget({
      id: ref.id, type: ref.kind, segment: sb.id,
      external: { kind: ref.kind, label: ref.label },
    })
  }

  async function unbindRef(ref) {
    const ok = await ask('解除绑定 · ' + ref.label,
      '只删 Trestle 里的引用，不删除 LibTV 节点；下游节点同步后会标待返修。')
    if (!ok) return
    await run('unbind-' + ref.id, async () => {
      const r = await post('/api/storyboard/graph/unbind', {
        unit: uid, segment: sb.id, node: ref.id,
      })
      if (!r.ok) { notify('解绑失败：' + (r.error || ''), 'error'); return }
      notify('已解绑；LibTV 节点还在', 'ok')
      await refresh()
      await reloadGraph()
    })
  }

  return (
    <section className="page review-page">
      <div className="backbar">
        <Link className="backbtn" to={'/?unit=' + encodeURIComponent(uid)}>← 回总览</Link>
      </div>
      <div className="page-head doc-head">
        <div>
          <h1>{scene.id} · {scene.name}</h1>
          <p>{scene.seconds ? scene.seconds + ' 秒' : fmt(scene.seconds) + 's'}
            {' · '}{sceneSbs.length} 段 · {(scene.shots || []).length} 镜
            {' · '}{(scene.cast || []).length + (scene.assets || []).length} 个资产</p>
        </div>
        <div className="acts">
          <button className="btn" onClick={() => setBriefOpen(true)}>这场设计与构思</button>
          <button className="btn" onClick={() => askScene('', '改这一场')}>打开 WorkBuddy 修改本段</button>
          <button className="btn" onClick={() => setInfoOpen(true)}>引用与文件</button>
          <button className="btn" onClick={openHistory}>历史版本（{history.length}）</button>
          {scene.libtv && scene.libtv.editor_url && (
            <a className="btn" href={scene.libtv.editor_url} target="_blank" rel="noreferrer">打开 LibTV 看整场 ↗</a>
          )}
          <details className="redesign">
            <summary className="btn">重新设计（WorkBuddy）▾</summary>
            <div className="redesign-menu">
              <button onClick={() => askScene('', '重新设计这场戏')}>打开 WorkBuddy 重新设计这场戏</button>
              <button onClick={() => wb('从剧本生成分镜', sceneCtx)}>打开 WorkBuddy 重新设计这个分镜</button>
              <button onClick={askWire}>打开 WorkBuddy 重新设计布线方案</button>
            </div>
          </details>
        </div>
      </div>

      <div className="scene-grid">
        <ReviewTree
          scenes={scenes}
          storyboards={storyboards}
          shots={shots}
          sceneId={scene.id}
          sbId={sb ? sb.id : ''}
          shotH={currentShot ? currentShot.h : ''}
          onPick={pickTree}
        />
        <div className="scene-main">
          {sb && currentShot && (
            <ShotCanvas
              unit={uid}
              segment={sb.id}
              shotId={currentShot.id}
              shotLabel={currentShot.h + ' · ' + (currentShot.framing || '')}
              graph={graphData}
              stateRows={canvasState.rows}
              canvasGraph={canvasState.canvases[sb.id] || null}
              stateAt={canvasState.at}
              hasLibtv={!!(sb.libtv && sb.libtv.project_id)}
              auto={canvasAuto}
              stateBusy={canvasBusy || busy === 'state'}
              onToggleAuto={setCanvasAuto}
              onRefreshState={() => readCanvasState(false)}
              onWbNode={(n) => askNodePrompt(n.id)}
              onPushNode={(n) => pushNodePrompt(n.id)}
              onPullNode={(n) => pullNode(n.id)}
              onOpenNode={openNodeById}
              onWbShot={() => askShot(currentShot,
                shotNodes(currentShot).length ? 'all' : 'shot')}
              onWbWire={askWire}
              onPushAll={pushAllNodes}
              onSyncResults={syncRow}
              onWireShot={() => wireRow(currentShot)}
              onWireSegment={wireSegment}
              onReveal={() => revealPath(sb.dir)}
              onOpenLibTV={() => {
                const u = sb.libtv && sb.libtv.editor_url
                if (u) window.open(u, '_blank', 'noreferrer')
                else notify('这一场还没连到 LibTV', 'warn')
              }}
            />
          )}

          {!!currentShot && (
            <div className="glass-card shot-results">
              <div className="sec-head">
                <b>这一镜的生成结果</b>
                <span className="faint small">
                  {shotFrame ? '首帧' : ''}{shotFrame && shotVideo ? ' · ' : ''}
                  {shotVideo ? '视频' : ''}
                </span>
                <span className="grow" />
                <button className="btn small" disabled={globalBusy} onClick={syncRow}>
                  {busy === 'row-sync' ? '同步中…' : '同步结果'}
                </button>
              </div>
              <div className="result-grid">
                {!!shotFrame && (
                  <figure className="result-card">
                    <img className="zoomable" src={mediaSrc(shotFrame)}
                      alt={currentShot.id}
                      onClick={() => setPreview({
                        src: mediaSrc(shotFrame), kind: 'image',
                        title: '首帧 · ' + currentShot.id,
                      })} />
                    <figcaption>
                      <b>首帧 · {currentShot.id}</b>
                      <span>本地 / 线上最新一版</span>
                    </figcaption>
                    <div className="result-acts">
                      <button className="link-btn"
                        onClick={() => setPreview({
                          src: mediaSrc(shotFrame), kind: 'image',
                          title: '首帧 · ' + currentShot.id,
                        })}>预览</button>
                      {!!curState.frame_file && (
                        <button className="link-btn"
                          onClick={() => revealPath(curState.frame_file)}>定位文件</button>
                      )}
                      {!!curState.frame_node && (
                        <button className="link-btn"
                          onClick={() => openNodeById({ node_id: curState.frame_node })}>
                          在 LibTV 打开
                        </button>
                      )}
                    </div>
                  </figure>
                )}
                {!!shotVideo && (
                  <figure className="result-card">
                    <video src={img(shotVideo)} controls preload="metadata" />
                    <figcaption>
                      <b>视频 · {currentShot.id}</b>
                      <span>已同步回工程</span>
                    </figcaption>
                    <div className="result-acts">
                      <button className="link-btn"
                        onClick={() => setPreview({
                          src: img(shotVideo), kind: 'video',
                          title: '视频 · ' + currentShot.id,
                        })}>播放预览</button>
                      <button className="link-btn"
                        onClick={() => revealPath(shotVideo)}>定位文件</button>
                      {!!curState.video_node && (
                        <button className="link-btn"
                          onClick={() => openNodeById({ node_id: curState.video_node })}>
                          在 LibTV 打开
                        </button>
                      )}
                    </div>
                  </figure>
                )}
                {!shotFrame && !shotVideo && (
                  <p className="empty" style={{ gridColumn: '1 / -1' }}>
                    这一镜还没有生成结果。布线后去 LibTV 生成，再回这里点「同步结果」。
                  </p>
                )}
              </div>
            </div>
          )}

          {!sb && (
            <div className="glass-card sb-card">
              <div className="sec-head">
                <b>这一场还没有分镜</b><span className="grow" />
                <button className="btn small primary" onClick={() => wb('从剧本生成分镜', sceneCtx)}>生成分镜</button>
                <button className="btn small" onClick={() => askScene('', '改这一场')}>打开 WorkBuddy 改这场</button>
              </div>
              <p className="empty">让 WorkBuddy 把这场拆成分镜；有分镜后在这里布线。</p>
            </div>
          )}
        </div>

      </div>

      {infoOpen && (
        <Modal
          title={'引用与文件 · ' + (sb ? sb.id : scene.id)}
          desc="周边信息收在这里；主操作在上面的两个画布里。"
          onClose={() => setInfoOpen(false)}
          footer={<button className="btn" onClick={() => setInfoOpen(false)}>关闭</button>}
        >
          <div className="sec-head"><b>这场用到的资产</b></div>
          {((sb && sb.asset_refs) || []).map((a) => (
            <Link className="asset-mini" key={a.id + '@' + (a.variant || '')}
              to={'/assets?asset=' + encodeURIComponent(a.id)}>
              <b>{a.name}</b>
              <span>{refNo(a) > 0 ? '@Image' + refNo(a) + ' · ' : ''}{a.id} · {
                a.image ? '已生成' : '缺图'}</span>
            </Link>
          ))}
          {!(sb && (sb.asset_refs || []).length) && (
            <p className="empty">这场还没有引用资产。</p>
          )}
          <div className="sec-head" style={{ marginTop: 12 }}><b>这一镜</b></div>
          <div className="meta-list">
            <div><span>镜号</span><b>{currentShot ? currentShot.h : '—'}</b></div>
            <div><span>景别 / 运镜</span>
              <b>{currentShot ? ((currentShot.framing || '—') + ' / ' + (currentShot.move || '—')) : '—'}</b></div>
            <div><span>时长</span><b>{currentShot ? currentShot.sec + 's' : '—'}</b></div>
          </div>
          <div className="quick" style={{ marginTop: 9 }}>
            <button className="btn small" onClick={openHistory}>
              历史版本（{history.length}）
            </button>
            <button className="btn small" disabled={!sb || !sb.dir}
              onClick={() => revealPath(sb && sb.dir)}>打开这一镜文件夹</button>
          </div>
        </Modal>
      )}

      {preview && (
        <MediaPreview {...preview} onClose={() => setPreview(null)} />
      )}

      {briefOpen && scene && (
        <FloatingPanel
          title={'设计与构思 · ' + scene.id}
          subtitle={scene.name}
          onClose={() => setBriefOpen(false)}
        >
          <SceneBrief scene={scene} />
        </FloatingPanel>
      )}

      {rework && (
        <Modal
          title={'返修镜 ' + rework.shot.id}
          desc="只改一个变量，其余三项不动；改完回来点「重跑这一镜」。"
          onClose={() => setRework(null)}
          footer={<>
            <button className="btn" onClick={() => setRework(null)}>取消</button>
            <button className="btn primary" onClick={() => {
              const note = rework.note || ''
              if (rework.variable === 'prompt') {
                if (promptNodeId) askNodePrompt(promptNodeId, note)
                else askShot(rework.shot, 'shot', note)
              } else {
                askShot(rework.shot, rework.variable, note)
              }
              setRework(null)
            }}>交给 WorkBuddy</button>
          </>}
        >
          <div className="rework-opts">
            {REWORK_OPTS.map(([id, label, hint]) => (
              <label key={id} className={'rework-opt' + (rework.variable === id ? ' on' : '')}>
                <input type="radio" name="rework" checked={rework.variable === id}
                  onChange={() => setRework({ ...rework, variable: id })} />
                <b>{label}</b><span>{hint}</span>
              </label>
            ))}
          </div>
          <textarea className="input note" placeholder="这次具体改什么？（只写一处）"
            value={rework.note}
            onChange={(e) => setRework({ ...rework, note: e.target.value })} />
        </Modal>
      )}

      {historyOpen && (
        <Modal title="历史版本" onClose={() => setHistoryOpen(false)}
          footer={<button className="btn" onClick={() => setHistoryOpen(false)}>关闭</button>}>
          {history.length ? history.map((x, i) => (
            <div className="hist-row" key={(x.node_id || '') + i}>
              <b>{x.new_name || x.old_name || x.node_id || '节点'}</b>
              <span>{x.archived_at || ''}{x.reason ? ' · ' + x.reason : ''}</span>
              {x.url && (
                <a className="btn small" href={x.url} target="_blank" rel="noreferrer">在 LibTV 看</a>
              )}
            </div>
          )) : (
            <p className="empty">还没有历史版本。重建或换方案时，旧节点会记在这里，不会被删除。</p>
          )}
        </Modal>
      )}

      <BindNodeDialog
        open={!!bindTarget}
        unit={uid}
        segment={bindTarget ? bindTarget.segment : (sb ? sb.id : '')}
        node={bindTarget}
        onClose={() => setBindTarget(null)}
        onBound={() => { notify('已绑定，去 LibTV 打开节点生成', 'ok'); refresh(); reloadGraph() }}
      />

      <ConfirmDialog
        open={!!confirm}
        title={confirm ? confirm.title : ''}
        desc={confirm ? confirm.desc : ''}
        confirmText="继续"
        cancelText="取消"
        onConfirm={() => closeConfirm(true)}
        onCancel={() => closeConfirm(false)}
      />

      <LinkPicker
        open={!!assetPick}
        type="all"
        assets={snap.assets || []}
        current={assetPick === 'scene'
          ? (scene.asset_refs || []).map((x) => ({ id: x.id, variant: x.variant || '' }))
          : ((sb && sb.asset_refs) || []).map((x) => ({ id: x.id, variant: x.variant || '' }))}
        withVariants
        onClose={() => setAssetPick(null)}
        onSave={saveAssets}
      />

      <Coach id="review.graph" anchor='[data-onb="review-graph"]'
        enabled={graphState === 'ok' && graphHasNodes && !graphWired} />
    </section>
  )
}
