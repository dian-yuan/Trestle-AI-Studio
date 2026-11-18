import React, { useEffect, useMemo, useState } from 'react'
import { get, post } from '../api'
import { Modal } from './Modal'

// 从一个 LibTV 链接里取 focusNodeId；没有就当成裸 node key。
function parseNodeId(text) {
  const s = String(text || '').trim()
  if (!s) return ''
  try {
    const u = new URL(s)
    return u.searchParams.get('focusNodeId') || u.searchParams.get('nodeId') || ''
  } catch (e) {
    return s
  }
}

export default function BindNodeDialog({ open, unit, segment, node, onClose, onBound }) {
  const [rows, setRows] = useState([])
  const [state, setState] = useState('loading')   // loading / ok / error
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState('')
  const [paste, setPaste] = useState('')
  const [busy, setBusy] = useState(false)
  const kind = (node && (node.external?.kind || node.type)) || 'image'
  const label = (node && (node.external?.label || node.name || node.id)) || ''

  const load = async () => {
    setState('loading')
    setError('')
    try {
      const r = await get('/api/libtv/project/nodes?' + new URLSearchParams({
        unit, segment,
      }))
      if (!r || !r.ok) {
        setState('error')
        setError((r && r.error) || '读不到 LibTV 画布')
        return
      }
      setRows((r.nodes || []).filter((x) => x.type === kind))
      setState('ok')
    } catch (e) {
      setState('error')
      setError(String(e.message || e))
    }
  }

  useEffect(() => {
    if (!open) return
    setQ('')
    setPicked('')
    setPaste('')
    load()
  }, [open, unit, segment, node && node.id])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return rows
    return rows.filter((x) => (x.name + ' ' + x.id).toLowerCase().includes(s))
  }, [rows, q])

  if (!open) return null

  const bind = async (nodeId) => {
    if (!nodeId || busy) return
    setBusy(true)
    try {
      const r = await post('/api/storyboard/graph/bind', {
        unit, segment, node: node.id, node_id: nodeId,
      })
      if (!r.ok) { setError(r.error || '绑定失败'); return }
      onBound(r.node || null)
      onClose()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={'绑定素材 · ' + (label || (kind === 'video' ? '参考视频' : '参考图'))}
      desc={'把「' + (label || node.id) + '」绑到 LibTV 上的'
        + (kind === 'video' ? '视频' : '图片') + '节点。只记 node_id，不上传文件。'}
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn primary" disabled={!picked || busy}
          onClick={() => bind(picked)}>{busy ? '绑定中…' : '绑定'}</button>
      </>}
    >
      {error && <p className="graph-warn">{error}</p>}
      <div className="bind-paste">
        <input className="input" placeholder="或粘贴 LibTV 节点链接 / node key"
          value={paste} onChange={(e) => setPaste(e.target.value)} />
        <button className="btn" disabled={!parseNodeId(paste) || busy}
          onClick={() => bind(parseNodeId(paste))}>用它</button>
      </div>
      <input className="input" placeholder="搜索节点名或 ID" value={q}
        onChange={(e) => setQ(e.target.value)} />
      <div className="link-list">
        {state === 'loading' && <p className="empty">正在读 LibTV 画布…</p>}
        {state === 'error' && (
          <div className="graph-hint">
            <span>{error}</span>
            <button className="link-btn" onClick={load}>重试</button>
          </div>
        )}
        {state === 'ok' && !list.length && (
          <p className="empty">
            画布上还没有可绑定的{kind === 'video' ? '视频' : '图片'}节点。
            先在 LibTV 上传，名字用「{label || node.id}」，再回来点刷新。
          </p>
        )}
        {state === 'ok' && list.map((x) => (
          <label className={'link-item' + (picked === x.id ? ' on' : '')} key={x.id}>
            <input type="radio" name="libtv-node" checked={picked === x.id}
              onChange={() => setPicked(x.id)} />
            <b>{x.name || x.id}</b>
            <span>{x.type} · {x.has_result ? '已有结果' : '未生成'} · {x.id}</span>
          </label>
        ))}
      </div>
      {state === 'ok' && (
        <button className="link-btn" onClick={load}>刷新画布节点</button>
      )}
    </Modal>
  )
}
