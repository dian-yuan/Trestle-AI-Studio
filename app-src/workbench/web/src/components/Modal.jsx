import React, { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { assetLevelLabel } from '../labels'

export function Modal({ title, desc, children, onClose, footer, className = '' }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('modal-open')
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('modal-open')
    }
  }, [onClose])
  return createPortal((
    <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className={'modal-card' + (className ? ' ' + className : '')}
        role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">×</button>
        </div>
        {desc && <p className="modal-desc">{desc}</p>}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  ), document.body)
}

const TYPE_LABEL = {
  characters: '角色', scenes: '场景', props: '道具', style: '画风',
  brand: '品牌', all: '资产',
}

export function LinkPicker({ open, type, assets, current, onClose, onSave, withVariants = false }) {
  const [picked, setPicked] = useState({})
  const [q, setQ] = useState('')

  useEffect(() => {
    if (open) {
      const init = {}
      ;(current || []).forEach((x) => {
        if (typeof x === 'string') init[x] = ''
        else if (x && x.id) init[x.id] = x.variant || ''
      })
      setPicked(init)
      setQ('')
    }
  }, [open, current])

  const rows = useMemo(() => {
    const list = type === 'all' ? (assets || []) : (assets || []).filter((a) => a.type === type)
    if (!q.trim()) return list
    const s = q.trim().toLowerCase()
    return list.filter((a) => (a.name + ' ' + a.id).toLowerCase().includes(s))
  }, [assets, type, q])

  if (!open) return null

  const toggle = (id) => {
    const next = { ...picked }
    if (id in next) delete next[id]; else next[id] = ''
    setPicked(next)
  }

  const save = () => {
    const out = Object.entries(picked).map(([id, variant]) =>
      withVariants && variant ? { id, variant } : id)
    onSave(out)
  }

  return (
    <Modal
      title={'关联' + (TYPE_LABEL[type] || type)}
      desc="勾选项目里的资产挂到这份内容；取消勾选不会删除资产本身。"
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn primary" onClick={save}>保存关联</button>
      </>}
    >
      <input className="input" placeholder="搜索名字或 ID" value={q}
        onChange={(e) => setQ(e.target.value)} />
      <div className="link-list">
        {rows.map((a) => (
          <label className={'link-item' + (a.id in picked ? ' on' : '')} key={a.id}>
            <input type="checkbox" checked={a.id in picked}
              onChange={() => toggle(a.id)} />
            <b>{a.name}</b>
            <span>{a.id} · {assetLevelLabel(a.level, a.status || '')}</span>
            {withVariants && a.id in picked && (a.variants || []).length > 0 && (
              <select className="input variant-select" value={picked[a.id]}
                onChange={(e) => setPicked({ ...picked, [a.id]: e.target.value })}
                onClick={(e) => e.stopPropagation()}>
                <option value="">主图</option>
                {a.variants.map((v) => (
                  <option key={v.id} value={v.id}>{v.label || v.id}</option>
                ))}
              </select>
            )}
          </label>
        ))}
        {!rows.length && <p className="empty">没有可关联的资产。</p>}
      </div>
    </Modal>
  )
}
