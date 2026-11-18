import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useApp } from '../App'
import { exportHref, post } from '../api'
import LeiBoxLoader from '../components/LeiBoxLoader'
import ConfirmDialog from '../components/ConfirmDialog'
import Coach from '../components/Coach'
import { Modal } from '../components/Modal'
import LibtvCompare from '../components/LibtvCompare'
import MediaPreview from '../components/MediaPreview'
import { nodeUrl } from '../libtvState'
import { assetLevelLabel } from '../labels'
import { assetTaskSpec } from '../lib/wbSpecs'

const TYPE_LABEL = {
  characters: '角色', scenes: '场景', props: '道具', style: '画风', brand: '品牌',
}

const img = (rel) => rel ? '/img/' + String(rel).replace(/\\/g, '/')
  .split('/').map(encodeURIComponent).join('/') : ''

function AssetNav({ groups, cur, q, onQ }) {
  const hit = (a) => {
    const s = String(q || '').trim().toLowerCase()
    if (!s) return true
    return (a.name + ' ' + a.id + ' ' + (a.type_label || '')).toLowerCase().includes(s)
  }
  return (
    <div className="glass-card side-card asset-nav">
      <input className="input asset-search" placeholder="搜索资产 / ID"
        value={q} onChange={(e) => onQ(e.target.value)} />
      <div className="asset-tree">
        {Object.entries(groups).map(([type, rows]) => {
          const list = rows.filter(hit)
          if (!list.length) return null
          return (
            <details className="asset-tree-group" open key={type}>
              <summary>
                <span>{TYPE_LABEL[type] || type}</span>
                <i>{list.length}</i>
              </summary>
              {list.map((a) => (
                <Link key={a.id}
                  className={'asset-mini' + (cur && a.id === cur.id ? ' on' : '')}
                  to={'/assets?asset=' + encodeURIComponent(a.id)}>
                  <b>{a.name}</b>
                  <span className={'meta-state ' + (a.build_state || 'empty')}>
                    {a.id} · {assetLevelLabel(a.level, a.status || '')}
                    {a.build_state_label ? ' · ' + a.build_state_label : ''}
                  </span>
                </Link>
              ))}
            </details>
          )
        })}
      </div>
    </div>
  )
}

const PALETTES = [
  { id: 'auto', label: '不预设', rows: [] },
  { id: 'brand', label: '品牌黑白', rows: [
    { name: '墨', hex: '#0D0D0D' }, { name: '纸', hex: '#FFFFFF' }] },
  { id: 'warm', label: '暖调', rows: [
    { name: '奶油', hex: '#F2E9DC' }, { name: '灰米', hex: '#D9D4CC' },
    { name: '暖灰', hex: '#8A8378' }] },
  { id: 'cool', label: '冷调', rows: [
    { name: '深蓝', hex: '#1B2A41' }, { name: '灰蓝', hex: '#324A5F' },
    { name: '雾紫', hex: '#CCC9DC' }] },
  { id: 'muted', label: '低饱和', rows: [
    { name: '纸白', hex: '#E8E6E1' }, { name: '灰褐', hex: '#B7B0A8' },
    { name: '深灰', hex: '#6E6A65' }] },
  { id: 'contrast', label: '高对比', rows: [
    { name: '黑', hex: '#111111' }, { name: '白', hex: '#FFFFFF' },
    { name: '红', hex: '#E63946' }] },
]

const HEX_RX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i

function styleLabel(u) {
  const mode = (u && u.style && u.style.mode) || 'libtv'
  if (mode === 'asset') return '风格 ' + ((u.style || {}).asset || '')
  if (mode === 'none') return '纯形'
  if (mode === 'custom') return '自定义风格'
  return '风格 LibTV'
}

function parsePaletteText(text) {
  return String(text || '').split(/[，,;；\n]/).map((s) => s.trim())
    .filter(Boolean).slice(0, 8).map((s) => {
      const hex = (s.match(/#[0-9a-f]{3,6}/i) || [''])[0]
      const name = s.replace(/#[0-9a-f]{3,6}/i, '').trim()
      return { name, hex, usage: '' }
    }).filter((x) => HEX_RX.test(x.hex) || x.name)
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = reject
    r.readAsDataURL(file)
  })
}

export default function Assets() {
  const { snap, wb, wbAsk, notify, refresh } = useApp()
  const nav = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const assets = (snap && snap.assets) || []
  const groups = useMemo(() => {
    const g = {}
    assets.forEach((a) => { (g[a.type] = g[a.type] || []).push(a) })
    return g
  }, [assets])
  const cur = assets.find((a) => a.id === (params.get('asset') || params.get('id')))
    || assets.find((a) => a.scene_count) || assets[0]
  const styleAssets = assets.filter((a) => a.type === 'style')
  const canBack = location.key !== 'default'

  const [upload, setUpload] = useState(null)
  const [uploadLabel, setUploadLabel] = useState('')
  const [uploadFile, setUploadFile] = useState(null)
  const [newOpen, setNewOpen] = useState(false)
  const [newForm, setNewForm] = useState({ type: 'characters', name: '', code: '' })
  const [toDelete, setToDelete] = useState(null)
  const [busy, setBusy] = useState('')
  const [statusOpen, setStatusOpen] = useState(false)
  const [statusText, setStatusText] = useState('')
  const [unitEdit, setUnitEdit] = useState(null)
  const [unitPrompt, setUnitPrompt] = useState('')
  const [unitStyleMode, setUnitStyleMode] = useState('libtv')
  const [unitStyleAsset, setUnitStyleAsset] = useState('')
  const [unitStyleText, setUnitStyleText] = useState('')
  const [unitPalette, setUnitPalette] = useState([])
  const [paletteText, setPaletteText] = useState('')
  const [wireAsk, setWireAsk] = useState(null)
  const [pullAsk, setPullAsk] = useState(null)
  const [infoOpen, setInfoOpen] = useState(false)
  const [assetQ, setAssetQ] = useState('')
  const [preview, setPreview] = useState(null)
  // LibTV ↔ 本地实时差异栏
  const [cmpRows, setCmpRows] = useState([])
  const [cmpCounts, setCmpCounts] = useState({})
  const [cmpGraph, setCmpGraph] = useState({})
  const [cmpErrors, setCmpErrors] = useState([])
  const [cmpAt, setCmpAt] = useState('')
  const [cmpLoading, setCmpLoading] = useState(false)
  const [cmpAuto, setCmpAuto] = useState(true)
  const cmpSig = useRef('')
  const genSig = ((cur && cur.generations) || [])
    .map((g) => g.id + ':' + (g.project_id || '')).join(',')

  useEffect(() => {
    setCmpRows([])
    setCmpCounts({})
    setCmpGraph({})
    setCmpErrors([])
    setCmpAt('')
    cmpSig.current = ''
    if (cur) readLibtv(true)
    // 切资产 / 新增或重连画布时重读。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cur && cur.id, genSig])

  useEffect(() => {
    if (!cmpAuto || !cur) return undefined
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') readLibtv(true)
    }, 15000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmpAuto, cur && cur.id, genSig])

  const backBar = (
    <div className="backbar">
      {canBack ? (
        <button className="backbtn" onClick={() => nav(-1)}>← 返回</button>
      ) : (
        <Link className="backbtn" to="/">← 创作思路</Link>
      )}
      {snap && <span className="faint">{snap.meta.unit} ｜ 资产库</span>}
    </div>
  )

  if (!cur) {
    return (
      <section className="page assets-page">
        {backBar}
        <div className="page-head">
          <div>
            <h1>资产库</h1>
            <p>角色 / 场景 / 道具 / 画风；一个资产可以被多个创作思路和剧本引用。</p>
          </div>
        </div>
        <div className="glass-card onb-empty">
          <h2>还没有资产</h2>
          <p>角色、场景、道具会重复出现在多个镜里；先做，后面省事。缺图时，分镜不能布线。</p>
          <div className="quick">
            <button className="btn primary"
              onClick={() => wb('做美术设定', '项目 ' + ((snap && snap.meta && snap.meta.name) || ''))}>
              让 WorkBuddy 建资产
            </button>
            <Link className="btn" to="/">看剧本</Link>
          </div>
        </div>
      </section>
    )
  }

  const variant = cur.variants && cur.variants.find((v) => v.primary)
  const results = (cur.generations || []).flatMap((g) =>
    (g.units || []).filter((u) => u.output).map((u) => ({ g, u })))
  const scenes = cur.scenes || []
  const storyboards = cur.storyboards || []

  function openUpload(variantKey = '', label = '') {
    setUpload({ asset: cur.id, variant: variantKey })
    setUploadLabel(label)
    setUploadFile(null)
  }

  async function doUpload() {
    if (!uploadFile) { notify('先选一张 PNG / JPG / WebP', 'warn'); return }
    setBusy('upload')
    try {
      const data = await readFile(uploadFile)
      const r = await post('/api/asset/upload', {
        asset: upload.asset, variant: upload.variant || '',
        label: uploadLabel.trim(), data,
      })
      if (!r.ok) { notify('上传失败：' + (r.error || ''), 'error'); return }
      setUpload(null)
      notify('图片已保存', 'ok')
      refresh()
    } finally { setBusy('') }
  }

  async function newAsset() {
    setBusy('new')
    try {
      const r = await post('/api/asset/new', newForm)
      if (!r.ok) { notify('新建失败：' + (r.error || ''), 'error'); return }
      setNewOpen(false)
      setNewForm({ type: 'characters', name: '', code: '' })
      notify('资产已新建', 'ok')
      refresh()
      nav('/assets?asset=' + encodeURIComponent(r.id))
    } finally { setBusy('') }
  }

  async function saveStatus() {
    setBusy('status')
    try {
      const r = await post('/api/asset/manifest', { asset: cur.id, status: statusText.trim() })
      if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
      setStatusOpen(false)
      notify('状态已保存', 'ok')
      refresh()
    } finally { setBusy('') }
  }

  function openUnit(g, u) {
    setUnitEdit({ g, u })
    setUnitPrompt(u.prompt || '')
    const st = (u.style && typeof u.style === 'object') ? u.style : {}
    setUnitStyleMode(st.mode || 'libtv')
    setUnitStyleAsset(st.asset || '')
    setUnitStyleText(st.text || '')
    setUnitPalette(Array.isArray(u.palette) ? u.palette : [])
    setPaletteText('')
  }

  async function saveUnitPrompt(close = true) {
    if (!unitEdit) return null
    const { g, u } = unitEdit
    setBusy('unit-prompt')
    try {
      const r = await post('/api/asset/unit/prompt', {
        asset: cur.id, canvas: g.id, unit: u.id, prompt: unitPrompt,
        style: {
          mode: unitStyleMode,
          asset: unitStyleAsset,
          text: unitStyleText,
        },
        palette: unitPalette,
      })
      if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return null }
      if (close) setUnitEdit(null)
      notify('提示词 / 风格 / 色卡已保存', 'ok')
      refresh()
      return r
    } finally { setBusy('') }
  }

  function applyPaletteText() {
    const rows = parsePaletteText(paletteText)
    if (rows.length) setUnitPalette(rows)
  }

  async function reveal(rel) {
    if (!rel) { notify('还没有对应的文件夹', 'warn'); return }
    const r = await post('/api/reveal', { path: rel })
    if (!r.ok) notify('打不开：' + (r.error || ''), 'error')
  }

  // 读 LibTV 上每个单元的最新状态 / 方案；只读，不覆盖本地。
  // silent=true 给 30 秒自动刷新用，不显示 loading。
  async function readLibtv(silent = false) {
    const targets = ((cur && cur.generations) || []).filter((g) => g.project_id)
    if (!targets.length) {
      setCmpRows([])
      setCmpCounts({})
      setCmpGraph({})
      setCmpErrors([])
      setCmpAt('')
      return
    }
    if (!silent) setCmpLoading(true)
    const rows = []
    const errors = []
    const counts = {}
    const graph = {}
    try {
      for (const g of targets) {
        let r = null
        try {
          r = await post('/api/asset/libtv/state', {
            asset: cur.id, canvas: g.id,
          })
        } catch (e) {
          errors.push(String(e.message || e))
        }
        if (!r) continue
        if (r.ok === false && !(r.rows || []).length) {
          errors.push(r.error || '读取失败')
          continue
        }
        if (r.counts && r.counts.nodes) counts[g.id] = r.counts
        if ((r.canvas_nodes || []).length) {
          graph[g.id] = { nodes: r.canvas_nodes, edges: r.edges || [] }
        }
        for (const x of (r.rows || [])) {
          rows.push({
            ...x, canvas: g.id,
            editor_url: (g.libtv || {}).editor_url || '',
          })
        }
        for (const e of (r.errors || [])) {
          errors.push((e.unit || '') + '：' + (e.error || '读取失败'))
        }
      }
      setCmpRows(rows)
      setCmpCounts(counts)
      setCmpGraph(graph)
      setCmpErrors(errors)
      setCmpAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      const sig = rows.map((x) => [x.canvas, x.unit, x.status,
        x.remote_changed, x.prompt].join('~')).join('|')
      if (cmpSig.current && sig !== cmpSig.current) refresh()
      cmpSig.current = sig
    } finally {
      if (!silent) setCmpLoading(false)
    }
  }

  function cmpCanvas(row) {
    return ((cur && cur.generations) || []).find((g) => g.id === row.canvas)
  }


  // 生成单元任务卡：目标文件 / 字段先钉死，中间留一块给用户写想改什么。
  function unitSpec(g, u, initialIntent) {
    return {
      title: '改这一张的出图提示词',
      desc: '只改这个生成单元的 prompt / negative；画布结构和其他单元不动。',
      targets: [
        { label: '文件', value: cur.dir + '/build/' + g.id + '/' + u.id + '/unit.json' },
        { label: '字段', value: 'prompt（画面描述）/ negative（负面词）' },
        { label: '画布', value: g.id + ' · ' + (g.title || '') + ' / ' + u.id + ' · ' + (u.name || '') },
        { label: 'LibTV', value: u.wired ? '已布线；改完要回来点「更新到 LibTV」' : '还没布线' },
      ],
      note: '这条 prompt 会原样发给 LibTV 节点；只写画面，不写制作流程。',
      presets: ['按资产设计方案重写这一张', '更贴近资产设定', '主体更大、构图更简洁',
        '光线和背景更明确', '我还没想好，先给 2 个改法'],
      placeholder: '例：背景换成纯白，主体居中并放大，衣服改成深蓝；不要加文字和装饰',
      initialIntent: initialIntent || '',
      action: '写生成单元提示词',
      ctx: cur.id + ' · ' + g.id + ' · ' + u.id,
      extra: { asset: cur.id, canvas: g.id, asset_unit: u.id },
    }
  }

  // 选中节点：只让 WorkBuddy 改这个生成单元的提示词。
  function wbUnit(g, u) {
    wbAsk(unitSpec(g, u, ''))
  }

  // 右键画布：修改画布 / 修改布线方案，只动 build/<画布>。
  function wbCanvas(g, kind) {
    const wire = kind === 'wire'
    const units = g.units || []
    wbAsk({
      title: wire ? '改这个画布的布线方案' : '改这个画布的结构',
      desc: wire
        ? '只重排这个画布的生成步骤和引用关系；已有提示词默认不动。'
        : '只改这个画布的节点、层级和引用；不碰其他画布和资产设定。',
      targets: [
        { label: '画布', value: cur.dir + '/build/' + g.id + '/canvas.json' },
        { label: '单元', value: g.id + ' 下 ' + units.length + ' 个 unit.json 的 refs / style / size / model' },
        { label: '不改', value: 'unit.json 的 prompt、manifest.json、prompts.md、其他画布' },
      ],
      presets: wire
        ? ['减少中间节点，流程更短', '给关键单元加参考图', '换一个更稳的配方',
          '先给两个布线方案让我选']
        : ['调整节点顺序 / 层级', '补一个参考节点', '精简到最少步骤',
          '修掉连错的引用', '先给两个画布结构让我选'],
      placeholder: wire
        ? '例：把这张拆成「草图 → 定稿」两步，去掉多余的参考节点'
        : '例：加一个局部细节节点，参考主图；把两个变体节点并到同一层',
      action: wire ? '改资产布线方案' : '改资产画布',
      ctx: cur.id + ' · ' + g.id,
      extra: { asset: cur.id, canvas: g.id },
    })
  }

  function openCmpNode(row) {
    const g = cmpCanvas(row)
    const base = row.editor_url || (g && g.libtv && g.libtv.editor_url) || ''
    const url = nodeUrl(base, row.node_id)
    if (!url) { notify('这个节点还没连到 LibTV', 'warn'); return }
    window.open(url, '_blank', 'noreferrer')
  }





  async function variantAction(v, action) {
    setBusy(action + '-' + v.id)
    try {
      const r = await post('/api/asset/variant', { asset: cur.id, variant: v.id, action })
      if (!r.ok) { notify('操作失败：' + (r.error || ''), 'error'); return }
      notify(action === 'primary' ? '已设为主图' : '变体已删除', 'ok')
      refresh()
    } finally { setBusy('') }
  }

  async function canvasInit() {
    setBusy('canvas-init')
    try {
      const r = await post('/api/asset/canvas/new', { asset: cur.id, canvas: 'C01' })
      if (!r.ok) { notify('建画布失败：' + (r.error || ''), 'error'); return }
      notify('生成画布已建：写提示词后布线并生成', 'ok')
      refresh()
    } finally { setBusy('') }
  }

  async function prepareWireGen(g) {
    setBusy('wiregen-' + g.id)
    try {
      const p = await post('/api/asset/wire', {
        asset: cur.id, canvas: g.id, dry_run: true, run: true,
      })
      if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
      if ((p.warnings || []).length) {
        const w = p.warnings[0]
        notify('生成单元 ' + w.unit + ' 的提示词不是出图描述：' + w.reason
          + '。先改提示词再布线。', 'error')
        return
      }
      const ready = (p.units || []).filter((u) => !u.skip)
      if (!ready.length) {
        notify('这个画布还没有可生成的提示词：先给生成单元写提示词', 'warn')
        return
      }
      refresh()
      setWireAsk({
        asset: cur.id, assetName: cur.name, g,
        desc: '会写入 LibTV ' + ready.length + ' 个生成节点并立即开始生成，可能消耗积分。'
          + '生成完成后回到本页点「同步结果」，资产才算完成。',
      })
    } finally { setBusy('') }
  }

  async function runWireGen() {
    const ask = wireAsk
    setWireAsk(null)
    if (!ask) return
    setBusy('wiregen-run-' + ask.g.id)
    try {
      const r = await post('/api/asset/wire', {
        asset: ask.asset, canvas: ask.g.id, dry_run: false, confirm: true,
        run: true, confirm_generate: true,
      })
      if (!r.ok) { notify('布线生成失败：' + (r.error || ''), 'error'); return }
      const errs = (r.errors || []).length
      notify('已布线并开始生成：' + (r.wired || 0) + ' 个单元' +
        (errs ? '，' + errs + ' 个失败' : '') +
        '。去 LibTV 看结果；回到本页点「同步结果」才算完成。', errs ? 'warn' : 'ok')
      refresh()
    } finally { setBusy('') }
  }

  async function pushUnit(g, u) {
    setBusy('push-' + g.id + '-' + u.id)
    try {
      const r = await post('/api/asset/push', {
        asset: cur.id, canvas: g.id, unit: u.id, confirm: true,
      })
      if (!r.ok) { notify('更新到 LibTV 失败：' + (r.error || ''), 'error'); return }
      notify(r.message || ('单元 ' + u.id + ' 已更新到 LibTV'), 'ok')
      refresh()
    } finally {
      setBusy('')
    }
  }

  async function pushDirty(g) {
    const dirty = (g.units || []).filter((u) => u.wired && u.prompt_dirty)
    if (!dirty.length) return
    setBusy('pushall-' + g.id)
    const errors = []
    try {
      for (const u of dirty) {
        const r = await post('/api/asset/push', {
          asset: cur.id, canvas: g.id, unit: u.id, confirm: true,
        })
        if (!r.ok) errors.push(u.id + '：' + (r.error || '失败'))
      }
      if (errors.length) notify('还有没推上去：' + errors[0], 'error')
      else notify(dirty.length + ' 个单元已更新到 LibTV', 'ok')
      refresh()
    } finally {
      setBusy('')
    }
  }

  async function syncLibtvState(g, u) {
    setBusy('state-' + g.id + (u ? '-' + u.id : ''))
    try {
      const r = await post('/api/asset/libtv/state', {
        asset: cur.id, canvas: g.id, unit: u ? u.id : '',
      })
      if (!r.ok) {
        notify('同步 LibTV 状态失败：' + (r.error || ''), 'error')
      } else {
        const changed = (r.rows || []).filter((x) => x.remote_changed)
        if (changed.length) {
          notify('LibTV 上有 ' + changed.length + ' 个单元改过方案，可点「采用 LibTV 版本」',
            'warn')
        } else {
          notify('LibTV 状态已更新', 'ok')
        }
      }
      refresh()
      readLibtv(true)
    } finally {
      setBusy('')
    }
  }

  async function pullUnit(g, u) {
    setBusy('pull-' + g.id + '-' + u.id)
    try {
      const r = await post('/api/asset/libtv/pull', {
        asset: cur.id, canvas: g.id, unit: u.id, confirm: true,
        params: true,
      })
      if (!r.ok) { notify('采用失败：' + (r.error || ''), 'error'); return }
      setPullAsk(null)
      notify(r.message || '已采用 LibTV 的方案', 'ok')
      refresh()
      readLibtv(true)
    } finally {
      setBusy('')
    }
  }

  async function wireUnit(g, u) {
    setBusy('wireunit-' + g.id + '-' + u.id)
    try {
      const p = await post('/api/asset/wire', {
        asset: cur.id, canvas: g.id, unit: u.id, dry_run: true,
      })
      if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
      if ((p.warnings || []).length) {
        notify('提示词不是出图描述：' + p.warnings[0].reason + '。先改提示词。', 'error')
        return
      }
      const r = await post('/api/asset/wire', {
        asset: cur.id, canvas: g.id, unit: u.id, dry_run: false, confirm: true,
      })
      if (!r.ok) { notify('布线失败：' + (r.error || ''), 'error'); return }
      notify('单元 ' + u.id + ' 已布线到 LibTV', 'ok')
      refresh()
    } finally {
      setBusy('')
    }
  }

  async function canvasAction(g, action) {
    if (action === 'wiregen') {
      await prepareWireGen(g)
      return
    }
    setBusy(action + '-' + g.id)
    try {
      if (action === 'ensure') {
        const r = await post('/api/asset/canvas', { asset: cur.id, canvas: g.id })
        if (!r.ok) { notify('补齐失败：' + (r.error || ''), 'error'); return }
        notify('画布已补齐', 'ok')
      } else if (action === 'wire') {
        const p = await post('/api/asset/wire', { asset: cur.id, canvas: g.id, dry_run: true })
        if (!p.ok) { notify('检查失败：' + (p.error || ''), 'error'); return }
        if ((p.warnings || []).length) {
          const w = p.warnings[0]
          notify('生成单元 ' + w.unit + ' 的提示词不是出图描述：' + w.reason
            + '。先改提示词再布线。', 'error')
          return
        }
        const r = await post('/api/asset/wire', {
          asset: cur.id, canvas: g.id, dry_run: false, confirm: true,
        })
        if (!r.ok) { notify('布线失败：' + (r.error || ''), 'error'); return }
        notify('画布已布线', 'ok')
      } else {
        const r = await post('/api/asset/sync', { asset: cur.id, canvas: g.id })
        if (!r.ok) { notify('同步失败：' + (r.error || ''), 'error'); return }
        const rows = r.units || []
        const done = rows.filter((x) => x.status === 'done').length
        const wait = rows.filter((x) => x.status === 'generating' || x.status === 'pending').length
        const errs = rows.filter((x) => x.status === 'error').length
        notify('已同步：' + done + ' 个完成' +
          (wait ? '，' + wait + ' 个还在生成' : '') +
          (errs ? '，' + errs + ' 个失败' : '') +
          (done ? '；资产已完成' : ''), done && !errs ? 'ok' : 'warn')
      }
      refresh()
    } finally { setBusy('') }
  }

  async function promote(g, u) {
    setBusy('promote-' + g.id + u.id)
    try {
      const r = await post('/api/asset/promote', {
        asset: cur.id, from: u.output, label: u.name, key: '',
        canvas: g.id, unit: u.id,
      })
      if (!r.ok) { notify('收变体失败：' + (r.error || ''), 'error'); return }
      notify('已收为变体', 'ok')
      refresh()
    } finally { setBusy('') }
  }

  return (
    <section className="page assets-page">
      {backBar}
      <div className="page-head">
        <div>
          <h1>资产库</h1>
          <p>角色 / 场景 / 道具 / 画风；一个资产可以被多个创作思路和剧本引用。</p>
        </div>
        <div className="acts">
          <button className="btn" onClick={() => setNewOpen(true)}>＋ 新建资产</button>
          <button className="btn" onClick={() => openUpload('', '定妆')}>上传图片</button>
          <a className="btn" href={exportHref('/api/export/assets', { unit: snap.meta.unit })}>导出资产清单</a>
        </div>
      </div>

      <div className="assets-grid">
        <AssetNav groups={groups} cur={cur} q={assetQ} onQ={setAssetQ} />

        <div className="asset-center">
          <div className="glass-card asset-head-card">
            <div className="ah-thumb">
              {cur.primary_image
                ? <img className="zoomable" src={img(cur.primary_image)} alt={cur.name}
                  onClick={() => setPreview({
                    src: img(cur.primary_image), kind: 'image',
                    title: cur.name,
                  })} />
                : <div className="ah-none">还没有图</div>}
            </div>
            <div className="ah-main">
              <div className="ah-title">
                <h2>{cur.name}</h2>
                <span className="tag">{cur.status}</span>
                <span className="faint small">
                  {cur.id} · {cur.type_label} · {cur.build_state_label || '未构建'}
                  {cur.role ? ' · ' + cur.role : ''}
                </span>
              </div>
              <div className="ah-acts">
                <button className="btn small"
                  onClick={() => openUpload(variant ? variant.id : '', variant ? variant.label : '定妆')}>
                  上传图片
                </button>
                <button className="btn small" onClick={() => openUpload('', '新变体')}>
                  ＋ 新建变体
                </button>
                {cur.primary_image && (
                  <button className="btn small" title={cur.primary_image}
                    onClick={() => reveal(cur.primary_image)}>定位主图</button>
                )}
                <button className="btn small primary"
                  onClick={() => wbAsk(assetTaskSpec(cur))}>
                  完善资产设定
                </button>
                <button className="btn small" onClick={() => setInfoOpen(true)}>
                  引用与文件
                </button>
              </div>
            </div>
          </div>

          <LibtvCompare
            asset={cur}
            canvases={cur.generations || []}
            rows={cmpRows}
            counts={cmpCounts}
            canvasGraph={cmpGraph}
            errors={cmpErrors}
            loading={cmpLoading}
            lastRead={cmpAt}
            auto={cmpAuto}
            busy={busy}
            onToggleAuto={setCmpAuto}
            onRefresh={() => readLibtv(false)}
            onOpen={openCmpNode}
            onPull={(g, u) => setPullAsk({ g, u })}
            onPush={(g, u) => pushUnit(g, u).then(() => readLibtv(true))}
            onPushAll={(g) => pushDirty(g)}
            onEditUnit={(g, u) => openUnit(g, u)}
            onWireUnit={(g, u) => wireUnit(g, u)}
            onPromote={(g, u) => promote(g, u)}
            onCanvasAction={(g, action) => canvasAction(g, action)}
            onSyncState={(g) => syncLibtvState(g)}
            onRevealCanvas={(g) => reveal(cur.dir + '/build/' + g.id)}
            onRevealImage={(path) => reveal(path)}
            onCanvasInit={canvasInit}
            onAskAsset={() => wbAsk(assetTaskSpec(cur))}
            onWbUnit={wbUnit}
            onWbCanvas={wbCanvas}
          />
          <div className="glass-card asset-results">
            <div className="sec-head">
              <b>生成结果</b><i>{results.length}</i>
              <span className="grow" />
              <span className="faint small">来自生成画布；可定位成图 / 收为变体 / 去 LibTV</span>
            </div>
            {results.length ? (
              <div className="result-grid">
                {results.map(({ g, u }) => {
                  const row = cmpRows.find((r) => r.canvas === g.id && r.unit === u.id)
                  return (
                    <figure className="result-card" key={g.id + '-' + u.id}>
                      <img className="zoomable" src={img(u.output)} alt={u.name}
                        onClick={() => setPreview({
                          src: img(u.output), kind: 'image',
                          title: u.id + ' · ' + u.name,
                        })} />
                      <figcaption>
                        <b>{u.id} · {u.name}</b>
                        <span>{g.id} · {u.wired ? '已布线' : '未布线'}
                          {row ? ' · ' + (row.status === 'done' ? 'LibTV 已生成' : row.status) : ''}</span>
                      </figcaption>
                      <div className="result-acts">
                        <button className="link-btn"
                          onClick={() => setPreview({
                            src: img(u.output), kind: 'image',
                            title: u.id + ' · ' + u.name,
                          })}>预览</button>
                        <button className="link-btn" onClick={() => reveal(u.output)}>定位成图</button>
                        <button className="link-btn" onClick={() => promote(g, u)}>收为变体</button>
                        {!!(row && row.node_id) && (
                          <button className="link-btn"
                            onClick={() => openCmpNode({ ...row, canvas: g.id, unit: u.id })}>
                            在 LibTV 打开
                          </button>
                        )}
                      </div>
                    </figure>
                  )
                })}
              </div>
            ) : (
              <p className="empty">还没有生成结果。布线并生成后，回这里同步结果。</p>
            )}
          </div>

          <details className="glass-card side-card asset-plan"
            open={(cur.variants || []).length <= 8}>
            <summary className="sec-head">
              <b>变体 <i>{(cur.variants || []).length}</i></b>
              <span className="grow" />
              <span className="faint small">展开</span>
              <button className="btn small"
                onClick={(e) => { e.preventDefault(); openUpload('', '新变体') }}>＋ 新建</button>
            </summary>
            <p className="hint">
              先有主图 / 母图，再建变体；装造 / 状态从主图派生，不另造脸。
            </p>
            <div className="vgrid">
              {(cur.variants || []).map((v) => (
                <figure className={'variant' + (v.primary ? ' on' : '')} key={v.id}>
                  {v.image
                    ? <img className="zoomable" src={img(v.image)} alt={v.label}
                      onClick={() => setPreview({
                        src: img(v.image), kind: 'image', title: v.label,
                      })} />
                    : <div className="variant-none">{v.label}</div>}
                  <figcaption>{v.label} <i>{v.id}</i></figcaption>
                  <div className="variant-acts">
                    {v.image && !v.primary && (
                      <button className="link-btn" onClick={() => variantAction(v, 'primary')}>设为主图</button>
                    )}
                    <button className="link-btn" onClick={() => openUpload(v.id, v.label)}>换图</button>
                    {v.image && (
                      <button className="link-btn" title={v.image}
                        onClick={() => reveal(v.image)}>定位图片</button>
                    )}
                    <button className="link-btn danger" onClick={() => setToDelete(v)}>删除</button>
                  </div>
                </figure>
              ))}
              {!(cur.variants || []).length && <p className="empty">还没有变体。</p>}
            </div>
          </details>

          <details className="glass-card side-card asset-plan">
            <summary className="sec-head">
              <b>资产设计方案</b>
              <span className="grow" />
              <span className="faint small">展开</span>
            </summary>
            <p className="hint">
              给人和 WorkBuddy 看的设定 / 方案，不会原样发给 LibTV；
              真正出图的提示词在画布的生成单元里。
            </p>
            <pre className="prompt-box">{cur.prompts_text || '还没有方案文档。'}</pre>
          </details>

        </div>

      </div>

      {infoOpen && (
        <Modal
          title={'引用与文件 · ' + cur.id}
          desc="周边信息收在这里；主操作在上面的两个画布里。"
          onClose={() => setInfoOpen(false)}
          footer={<button className="btn" onClick={() => setInfoOpen(false)}>关闭</button>}
        >
          <div className="sec-head"><b>用在这些场</b></div>
          {scenes.map((r) => (
            <Link className="asset-mini" key={r.scene}
              to={'/review?unit=' + encodeURIComponent(snap.meta.unit) + '&scene=' + encodeURIComponent(r.scene)}>
              <b>{r.scene} · {r.scene_name}</b>
              <span>{r.variant_label || r.variant || '主图'}</span>
            </Link>
          ))}
          {!scenes.length && <p className="empty">这一集还没有场引用它。</p>}

          <div className="sec-head" style={{ marginTop: 12 }}><b>用在这些分镜</b></div>
          {storyboards.map((r) => (
            <div className="asset-mini" key={r.storyboard}>
              <b>{r.storyboard}</b><span>{r.scene}</span>
            </div>
          ))}
          {!storyboards.length && <p className="empty">这一集还没有分镜引用它。</p>}

          <div className="sec-head" style={{ marginTop: 12 }}><b>文件</b></div>
          <div className="meta-list">
            <div><span>清单</span><b title={cur.manifest || ''}>{cur.manifest || '—'}</b></div>
            <div><span>方案文档</span><b title={cur.prompts || ''}>{cur.prompts || '—'}</b></div>
            <div><span>目录</span><b title={cur.dir || ''}>{cur.dir || '—'}</b></div>
          </div>
          <div className="quick" style={{ marginTop: 9 }}>
            <button className="btn small" disabled={!cur.dir}
              onClick={() => reveal(cur.dir)}>打开资产目录</button>
            {!!cur.manifest && (
              <button className="btn small" onClick={() => reveal(cur.manifest)}>
                定位 manifest
              </button>
            )}
            {!!cur.prompts && (
              <button className="btn small" onClick={() => reveal(cur.prompts)}>
                定位方案文档
              </button>
            )}
          </div>
          {!!(cur.files || []).length && (
            <div className="meta-list" style={{ marginTop: 8 }}>
              {cur.files.map((f) => (
                <div key={f.key}>
                  <span>{f.key}</span>
                  <button className="link-btn" title={f.path}
                    onClick={() => reveal(f.path)}>定位文件</button>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}

      {preview && (
        <MediaPreview {...preview} onClose={() => setPreview(null)} />
      )}

      {upload && (
        <Modal
          title={upload.variant ? '换图 · ' + (uploadLabel || upload.variant) : '上传图片'}
          desc="只收 PNG / JPG / WebP，单张不超过 16MB。上传后会出现在变体里。"
          onClose={() => setUpload(null)}
          footer={<>
            <button className="btn" onClick={() => setUpload(null)}>取消</button>
            <button className="btn primary" onClick={doUpload} disabled={busy === 'upload'}>
              {busy === 'upload' ? '上传中…' : '上传'}
            </button>
          </>}
        >
          <label className="field"><span>变体名</span>
            <input className="input" value={uploadLabel} onChange={(e) => setUploadLabel(e.target.value)}
              placeholder="例如：定妆 v2 / 雨夜 / 左机位" />
          </label>
          <label className="field"><span>图片</span>
            <input className="input" type="file" accept="image/png,image/jpeg,image/webp"
              onChange={(e) => setUploadFile(e.target.files && e.target.files[0])} />
          </label>
        </Modal>
      )}

      {unitEdit && (
        <Modal
          title={'生成单元提示词 · ' + unitEdit.u.id}
          desc="这条会原样作为 LibTV 节点的提示词；只写画面，不要写制作流程。"
          onClose={() => setUnitEdit(null)}
          footer={<>
            <button className="btn" onClick={() => setUnitEdit(null)}>取消</button>
            <button className="btn" onClick={async () => {
              const r = await saveUnitPrompt(false)
              if (!r || !r.ok) return
              const { g, u } = unitEdit
              wbAsk(unitSpec(g, u, unitPrompt.trim()))
              setUnitEdit(null)
            }}>让 WorkBuddy 写这一张</button>
            <button className="btn primary" onClick={() => saveUnitPrompt(true)}
              disabled={busy === 'unit-prompt'}>保存</button>
          </>}
        >
          <label className="field"><span>设计风格</span>
            <select className="input"
              value={unitStyleMode === 'asset'
                ? 'asset:' + unitStyleAsset : unitStyleMode}
              onChange={(e) => {
                const v = e.target.value
                if (v.startsWith('asset:')) {
                  setUnitStyleMode('asset')
                  setUnitStyleAsset(v.slice(6))
                } else {
                  setUnitStyleMode(v)
                  setUnitStyleAsset('')
                }
              }}>
              <option value="libtv">在 LibTV 里选（推荐）</option>
              <option value="none">纯形 / 无风格</option>
              {styleAssets.map((a) => (
                <option key={a.id} value={'asset:' + a.id}>
                  {a.name} · {a.id}
                </option>
              ))}
              <option value="custom">自定义风格</option>
            </select>
          </label>
          {unitStyleMode === 'custom' && (
            <label className="field"><span>自定义风格</span>
              <input className="input" value={unitStyleText}
                onChange={(e) => setUnitStyleText(e.target.value)}
                placeholder="例如：品牌极简，黑白，无渐变，无 3D 质感" />
            </label>
          )}
          <div className="field"><span>色卡</span>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              {PALETTES.map((p) => (
                <button type="button" key={p.id} className="btn small"
                  onClick={() => setUnitPalette(p.rows)}>{p.label}</button>
              ))}
            </div>
            {!!unitPalette.length && (
              <div className="swatches" style={{ marginTop: 8 }}>
                {unitPalette.map((p, i) => (
                  <span className="swatch" key={i} title={p.usage || ''}>
                    <i style={{ background: p.hex || 'transparent' }} />
                    <em>{p.name || p.hex || ''}</em>
                  </span>
                ))}
              </div>
            )}
            <div className="row" style={{ gap: 6, marginTop: 8 }}>
              <input className="input" value={paletteText}
                onChange={(e) => setPaletteText(e.target.value)}
                placeholder="自定义：墨 #0D0D0D，纸 #FFFFFF" />
              <button type="button" className="btn small"
                onClick={applyPaletteText}>应用</button>
            </div>
          </div>
          <textarea className="input" rows="8" value={unitPrompt}
            onChange={(e) => setUnitPrompt(e.target.value)}
            placeholder="描述这一张图：主体 / 外形 / 服装 / 构图 / 机位 / 光线 / 背景" />
          <p className="hint">风格来源选「LibTV」时，WorkBuddy 不写画风，只写画面；
            选了项目风格资产或自定义风格，才会把风格写进提示词。色卡只作为
            画面事实（主体 / 背景 / 点缀的颜色），不会单独列色号清单。</p>
          <p className="hint">像「母图出来之后……」这类制作说明不是出图提示词，
            布线时会被拦下。布线时会自动把资产主图作为参考图；主图不对，
            先在资产卡片上换主图。</p>
        </Modal>
      )}

      {statusOpen && (
        <Modal
          title="改资产状态"
          desc="写一句当前进度，例如：设定定稿 · 待定妆锚点。"
          onClose={() => setStatusOpen(false)}
          footer={<>
            <button className="btn" onClick={() => setStatusOpen(false)}>取消</button>
            <button className="btn primary" onClick={saveStatus} disabled={busy === 'status'}>保存</button>
          </>}
        >
          <input className="input" autoFocus value={statusText}
            onChange={(e) => setStatusText(e.target.value)} />
        </Modal>
      )}

      {newOpen && (
        <Modal
          title="新建资产"
          desc="先建一个空壳；图可以稍后上传，也可以从生成画布收。"
          onClose={() => setNewOpen(false)}
          footer={<>
            <button className="btn" onClick={() => setNewOpen(false)}>取消</button>
            <button className="btn primary" onClick={newAsset} disabled={busy === 'new'}>创建</button>
          </>}
        >
          <label className="field"><span>类型</span>
            <select className="input" value={newForm.type}
              onChange={(e) => setNewForm({ ...newForm, type: e.target.value })}>
              <option value="characters">角色</option>
              <option value="scenes">场景</option>
              <option value="props">道具</option>
              <option value="style">画风</option>
            </select>
          </label>
          <label className="field"><span>名字</span>
            <input className="input" value={newForm.name}
              onChange={(e) => setNewForm({ ...newForm, name: e.target.value })}
              placeholder="例如：零一" />
          </label>
          <label className="field"><span>短码</span>
            <input className="input" value={newForm.code}
              onChange={(e) => setNewForm({ ...newForm, code: e.target.value })}
              placeholder="两位以上，例如 SH / ARC" />
          </label>
        </Modal>
      )}

      <ConfirmDialog
        open={!!toDelete}
        danger
        title="删除这个变体？"
        desc={toDelete ? '变体会移到 .workbuddy/trash，需要时还能找回。' : ''}
        confirmText="删除"
        cancelText="取消"
        onConfirm={() => { const v = toDelete; setToDelete(null); if (v) variantAction(v, 'delete') }}
        onCancel={() => setToDelete(null)}
      />

      <ConfirmDialog
        open={!!pullAsk}
        title={pullAsk ? '采用 LibTV 上的方案 · ' + pullAsk.u.id : ''}
        desc={pullAsk
          ? '会把 LibTV 节点上的提示词、模型、画幅写回本地 unit.json；'
            + '本地这行提示词会被覆盖。已有输出不会变，要换图还得在 LibTV 重新生成。'
          : ''}
        confirmText="采用"
        cancelText="取消"
        onConfirm={() => pullAsk && pullUnit(pullAsk.g, pullAsk.u)}
        onCancel={() => setPullAsk(null)}
      />

      <ConfirmDialog
        open={!!wireAsk}
        title={wireAsk ? '布线并生成 · ' + wireAsk.assetName : ''}
        desc={wireAsk ? wireAsk.desc : ''}
        confirmText="布线并生成"
        cancelText="取消"
        onConfirm={runWireGen}
        onCancel={() => setWireAsk(null)}
      />

      <Coach id="assets.build" anchor='[data-onb="assets-build"]'
        enabled={!assets.some((a) => a.build_state === 'done' || a.build_state === 'wired')} />
    </section>
  )
}
