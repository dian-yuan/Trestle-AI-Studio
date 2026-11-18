import React, { useEffect, useState } from 'react'
import { get, post } from '../api'
import { useApp } from '../App'
import { nodeUrl } from '../libtvState'
import { groupNodes, nodeKey, STAGE_LABEL } from '../lib/graphView'
import { Modal } from './Modal'

const STATUS = {
  planned: ['待布线', ''],
  wired: ['已布线', ''],
  generating: ['生成中', 'info'],
  generated: ['已生成', 'ok'],
  reviewed: ['已审', 'ok'],
  approved: ['已通过', 'ok'],
  failed: ['失败', 'err'],
  stale: ['待返修', 'warn'],
  missing: ['节点已删', 'err'],
}

const ROLE = {
  reference: '参考图',
  reference_video: '参考视频',
  image2image: '图生图',
  first_frame: '首帧',
  last_frame: '尾帧',
  video: '成片',
}

function remoteBits(v) {
  const parts = []
  if (v.remote_status) parts.push(STATUS[v.remote_status]?.[0] || v.remote_status)
  if (v.remote_model) parts.push(v.remote_model)
  const p = v.remote_params || {}
  if (p.ratio) parts.push(p.ratio)
  if (p.resolution) parts.push(p.resolution)
  if (p.duration) parts.push(p.duration + 's')
  const style = (v.remote_style || []).map((x) => x.name).filter(Boolean)
  if (style.length) parts.push('风格 ' + style.join(' / '))
  return parts.join(' · ')
}

function NodeRow({ node, onOpen, onVersion, onAsk, onPush, onPull, busy, pushing }) {
  const v = node
  const [st, tone] = STATUS[v.status] || [v.status || '未知', '']
  const versions = v.historyCount || 0
  const canPrompt = !v.external && !v.missing && v.role !== 'reference'
    && v.role !== 'reference_video'
  return (
    <div className={'gp-node-row' + (v.external ? ' external' : '')
      + (v.bound ? '' : ' unbound')}>
      <div className="gp-head">
        <b title={v.id}>{v.id}</b>
        <span>{ROLE[v.role] || v.roleLabel || v.role || ''}</span>
        <span className="gp-stage">{STAGE_LABEL[v.stage] || v.stage}</span>
        <em className={v.missing ? 'err' : tone}>
          {v.missing ? '节点已删' : (v.external && !v.bound ? '待绑定' : st)}
        </em>
        {v.needsPush && <em className="warn" title="本地提示词 / 参数已改，LibTV 还是旧版">待推 LibTV</em>}
        {!!v.remote_changed && !v.needsPush && (
          <em className="info" title="LibTV 上的提示词和本地不同">LibTV 改过方案</em>
        )}
        {versions > 1 && <span className="vchip">v{v.rev || 1}</span>}
        <span className="grow" />
        {canPrompt && (
          <button className="link" onClick={() => onAsk(v)}>AI 写提示词</button>
        )}
        {v.needsPush && (
          <button className="link" disabled={pushing}
            onClick={() => onPush(v)}>
            {pushing ? '推送中…' : '更新到 LibTV'}
          </button>
        )}
        {!!v.remote_changed && !v.needsPush && (
          <>
            <button className="link" onClick={() => onPull(v)}>采用 LibTV 版本</button>
            <button className="link" disabled={pushing}
              onClick={() => onPush(v)}>用本地覆盖</button>
          </>
        )}
        {v.external && !v.bound ? (
          <span className="faint small">在左侧素材带绑定</span>
        ) : v.nodeKey ? (
          <button className="link" onClick={() => onOpen(v)}>打开 LibTV ↗</button>
        ) : null}
      </div>
      {versions > 1 && (
        <div className="gp-versions">
          <span>当前</span>
          <select value={v.rev || 1} disabled={busy}
            onChange={(e) => onVersion(v, Number(e.target.value))}>
            {(v.raw?.history || []).map((h) => (
              <option key={h.rev} value={h.rev}>
                v{h.rev}{h.at ? ' · ' + h.at : ''}
              </option>
            ))}
          </select>
        </div>
      )}
      {!!v.prompt && !v.external && (
        <details className="gp-prompt-fold">
          <summary>提示词</summary>
          <div className="gp-prompt">{v.prompt}</div>
        </details>
      )}
      {!!v.remote_at && (
        <details className="gp-prompt-fold">
          <summary>
            LibTV 最新方案{remoteBits(v) ? ' · ' + remoteBits(v) : ''}
          </summary>
          <div className="gp-prompt">{v.remote_prompt || '读不到提示词'}</div>
        </details>
      )}
    </div>
  )
}

export default function GraphPanel({ unit, segment }) {
  const { notify, refresh, wb, snap } = useApp()
  const [data, setData] = useState(null)
  const [state, setState] = useState('loading')   // loading / ok / error
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pushing, setPushing] = useState('')
  const [ask, setAsk] = useState(null)
  const [want, setWant] = useState('')
  const [stateBusy, setStateBusy] = useState(false)
  const [pullAsk, setPullAsk] = useState(null)
  const rev = (snap && snap.versions && (snap.versions.storyboards || 0)) || 0

  const load = async (silent = false) => {
    if (!segment) { setData(null); setState('empty'); return }
    if (!silent) setState('loading')
    setError('')
    try {
      const r = await get('/api/storyboard/graph?' + new URLSearchParams({ unit }))
      if (!r || !r.ok) {
        setState('error')
        setError((r && r.error) || '读不到图谱')
        return
      }
      setData(r)
      setState('ok')
    } catch (e) {
      if (!silent) setData(null)
      setState('error')
      setError(String(e.message || e))
    }
  }

  useEffect(() => {
    setData(null)
    load()
  }, [unit, segment])

  // WorkBuddy 改完 graph.json 会触发工作台版本号；侧栏跟着重读，
  // 否则「待推 LibTV」和新提示词要刷新页面才出现。
  useEffect(() => {
    if (rev && state === 'ok') load(true)
  }, [rev])

  const openNode = async (node) => {
    if (!node || !node.nodeKey) { notify('这个节点还没绑定 / 布线', 'warn'); return }
    const r = await get('/api/libtv/link?' + new URLSearchParams({
      unit, kind: 'segment', id: segment,
    }))
    if (r.ok && r.url) {
      window.open(nodeUrl(r.url, node.nodeKey), '_blank', 'noreferrer')
    } else {
      notify(r.error || '这一段还没连线', 'error')
    }
  }

  const switchVersion = async (v, rev) => {
    setBusy(true)
    try {
      const r = await post('/api/storyboard/graph/version', {
        unit, segment, node: v.id, rev,
      })
      if (!r.ok) { notify('切换版本失败：' + (r.error || ''), 'error'); return }
      notify('已切到 v' + rev, 'ok')
      await load()
      refresh()
    } finally {
      setBusy(false)
    }
  }

  const pushNode = async (v) => {
    setPushing(v.id)
    try {
      const r = await post('/api/storyboard/graph/push', {
        unit, segment, node: v.id, confirm: true,
      })
      if (!r.ok) { notify('推到 LibTV 失败：' + (r.error || ''), 'error'); return }
      notify('节点 ' + v.id + ' 已更新到 LibTV', 'ok')
      await load(true)
      refresh()
    } catch (e) {
      notify('推到 LibTV 失败：' + String(e.message || e), 'error')
    } finally {
      setPushing('')
    }
  }

  const pushAllDirty = async () => {
    const list = (data && data.graph && (data.graph.segments || [])
      .find((s) => s.id === segment) || {}).nodes || []
    const dirty = list.filter((n) => n.needs_push)
    if (!dirty.length) return
    setPushing('*')
    let ok = 0
    const errors = []
    try {
      for (const n of dirty) {
        const r = await post('/api/storyboard/graph/push', {
          unit, segment, node: n.id, confirm: true,
        })
        if (r.ok) ok += 1
        else errors.push(n.id + '：' + (r.error || '失败'))
      }
      if (ok) notify(ok + ' 个节点已更新到 LibTV', 'ok')
      if (errors.length) notify('还有没推上去：' + errors[0], 'error')
      await load(true)
      refresh()
    } finally {
      setPushing('')
    }
  }

  const syncState = async () => {
    if (!segment) return
    setStateBusy(true)
    try {
      const r = await post('/api/storyboard/graph/state', { unit, segment })
      if (!r.ok) {
        notify('同步 LibTV 状态失败：'
          + ((((r.errors || [])[0] || {}).error) || r.error || ''), 'error')
      } else {
        const n = (r.rows || []).filter((x) => x.remote_changed).length
        notify(n
          ? 'LibTV 上有 ' + n + ' 个节点改过方案，点节点上的「采用 LibTV 版本」'
          : 'LibTV 节点状态已更新', n ? 'warn' : 'ok')
      }
      await load(true)
      refresh()
    } finally {
      setStateBusy(false)
    }
  }

  const doPull = async () => {
    const v = pullAsk
    setPullAsk(null)
    if (!v) return
    setPushing('pull-' + v.id)
    try {
      const r = await post('/api/storyboard/graph/pull', {
        unit, segment, node: v.id, confirm: true, params: true,
      })
      if (!r.ok) { notify('采用失败：' + (r.error || ''), 'error'); return }
      notify(r.message || '已采用 LibTV 版本', 'ok')
      await load(true)
      refresh()
    } finally {
      setPushing('')
    }
  }

  const openAsk = (v) => { setAsk(v); setWant('') }
  const submitAsk = async () => {
    if (!ask) return
    await wb('写这个节点提示词', want.trim(), null,
      { unit, segment, node: ask.id })
    setAsk(null)
    setWant('')
  }

  const seg = data && data.graph && (data.graph.segments || [])
    .find((s) => s.id === segment)
  if (state === 'loading') {
    return (
      <div className="glass-card side-card">
        <div className="sec-head"><b>布线方案</b></div>
        <p className="empty">正在读图谱…</p>
      </div>
    )
  }
  if (state === 'error') {
    return (
      <div className="glass-card side-card">
        <div className="sec-head"><b>布线方案</b></div>
        <p className="graph-warn">{error}</p>
        <button className="btn small" onClick={load}>重试</button>
      </div>
    )
  }
  if (!seg) {
    return (
      <div className="glass-card side-card">
        <div className="sec-head"><b>布线方案</b></div>
        <p className="empty">这一段还没有布线方案。先在总览让 WorkBuddy 出图谱。</p>
      </div>
    )
  }

  const plan = data.plan || {}
  const nodes = seg.nodes || []
  const dirtyCount = nodes.filter((n) => n.needs_push).length
  const g = groupNodes(nodes)
  const hard = (plan.blockers || []).filter((b) => !b.defer)
  const needsBinding = g.materials.filter((v) => !v.bound || v.missing).length
  const groups = [
    { key: 'reference', title: '参考', nodes: g.materials },
    { key: 'greybox', title: '白膜', nodes: g.greybox },
    { key: 'final', title: '定稿', nodes: g.frames },
    { key: 'video', title: '成片', nodes: g.videos },
    { key: 'other', title: '其它', nodes: g.other },
  ].filter((x) => x.nodes.length)

  return (
    <>
    <div className="glass-card side-card">
      <div className="sec-head">
        <b>布线方案{segment ? ' · ' + segment : ''}</b>
        <span className="tag">{seg.recipe || '—'}</span>
      </div>
      <p className="gp-hint">
        本地 graph.json 是源；WorkBuddy 改完提示词要点「更新到 LibTV」才会进画布。
        绑定素材、布线、生成、同步都在左侧操作。
      </p>
      <p className="gp-why">{seg.why || '—'}</p>
      <div className="gp-cost">
        <span>节点 {nodes.length}</span>
        <span>已布线 {nodes.filter((n) => nodeKey(n)).length}</span>
        <span>上传 {plan.cost?.uploads || 0}</span>
        <span>出图 {plan.cost?.images || 0}</span>
        <span>出片 {plan.cost?.videos || 0}</span>
      </div>
      <div className="trunk-acts">
        <button className="btn small" disabled={stateBusy || !!pushing}
          onClick={syncState}>
          {stateBusy ? '读取中…' : '同步 LibTV 状态'}
        </button>
        <span className="faint small">读回最新生成状态和方案；改了也不会覆盖本地</span>
      </div>

      {!!needsBinding && (
        <div className="gp-alert">
          <span>{needsBinding} 个素材待绑定</span>
          <span className="faint small">在左侧选中镜的素材带绑定</span>
        </div>
      )}
      {!!dirtyCount && (
        <div className="gp-alert">
          <span>{dirtyCount} 个节点本地已改，LibTV 还是旧版</span>
          <button className="btn small" onClick={pushAllDirty}
            disabled={!!pushing}>
            {pushing === '*' ? '推送中…' : '全部更新到 LibTV'}
          </button>
        </div>
      )}
      {!!hard.length && <p className="graph-warn">{hard[0].message}</p>}

      <div className="gp-stages">
        {groups.map((grp) => (
          <div className={'gp-stage-group stage-' + grp.key} key={grp.key}>
            <div className="gp-stage-head">
              <b>{grp.title}</b><span>{grp.nodes.length}</span>
            </div>
            {grp.nodes.map((v) => (
              <NodeRow key={v.id} node={v} onOpen={openNode}
                onVersion={switchVersion} onAsk={openAsk} onPush={pushNode}
                onPull={setPullAsk}
                busy={busy} pushing={pushing === v.id || pushing === 'pull-' + v.id} />
            ))}
          </div>
        ))}
      </div>
    </div>
    {ask && (
      <Modal
        title={'让 WorkBuddy 写 ' + ask.id + ' 的提示词'}
        desc={'只改这一个节点（' + (ROLE[ask.role] || ask.role || '') + ' · '
          + (ask.model || '未填模型') + '）；不重排分镜，不碰其他节点。'}
        onClose={() => setAsk(null)}
        footer={<>
          <button className="btn" onClick={() => setAsk(null)}>取消</button>
          <button className="btn primary" onClick={submitAsk}>交给 WorkBuddy</button>
        </>}
      >
        <p className="hint" style={{ marginTop: 0 }}>
          描述你想要的效果。缺关键信息时会让 WorkBuddy 先问，不会自己猜。
          写完回这里点「更新到 LibTV」。
        </p>
        <textarea className="input note" rows={5}
          placeholder={'比如：这一帧要中景，人物在画面右侧，窗外逆光；' +
            '桌面上的盒子占下三分之一；表情克制。'}
          value={want} onChange={(e) => setWant(e.target.value)} />
      </Modal>
    )}
    {pullAsk && (
      <Modal
        title={'采用 LibTV 版本 · ' + pullAsk.id}
        desc="会把 LibTV 节点上的提示词 / 参数写回本地 graph.json，覆盖本地这一版。"
        onClose={() => setPullAsk(null)}
        footer={<>
          <button className="btn" onClick={() => setPullAsk(null)}>取消</button>
          <button className="btn primary" onClick={doPull}>采用</button>
        </>}
      >
        <p className="hint" style={{ marginTop: 0 }}>
          LibTV 最新方案：{remoteBits(pullAsk) || '读不到参数'}
          {pullAsk.remote_at ? ' · ' + pullAsk.remote_at : ''}
        </p>
        <div className="gp-prompt">{pullAsk.remote_prompt || '读不到提示词'}</div>
        <p className="faint small">
          已有输出不会变；要换图 / 换片得在 LibTV 重新生成。
        </p>
      </Modal>
    )}
    </>
  )
}
