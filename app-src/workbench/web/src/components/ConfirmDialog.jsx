import React, { useEffect, useRef, useState } from 'react'

/**
 * 通用确认框。默认焦点在「取消」，不用转账动画，不做 aria-live 转账播报。
 * danger / requireCheck 用于归档、删除、生成这类有代价的动作。
 */
export default function ConfirmDialog({
  open, title, desc, details, confirmText = '确认', cancelText = '取消',
  danger = false, requireCheck = '', onConfirm, onCancel,
}) {
  const cancelRef = useRef(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    if (!open) return undefined
    setChecked(false)
    const t = setTimeout(() => cancelRef.current && cancelRef.current.focus(), 30)
    const onKey = (e) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('modal-open')
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('modal-open')
    }
  }, [open, onCancel])

  if (!open) return null
  const blocked = !!requireCheck && !checked
  return (
    <div className="modal-scrim"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="modal-card confirm-card" role="dialog" aria-modal="true"
        aria-labelledby="confirm-title">
        <div className="modal-head">
          <h2 id="confirm-title">{title}</h2>
        </div>
        {desc && <p className="modal-desc">{desc}</p>}
        {!!(details || []).length && (
          <ul className="confirm-details">
            {details.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        )}
        {requireCheck && (
          <label className="confirm-check">
            <input type="checkbox" checked={checked}
              onChange={(e) => setChecked(e.target.checked)} />
            <span>{requireCheck}</span>
          </label>
        )}
        <div className="modal-foot">
          <button className="btn" ref={cancelRef} onClick={onCancel}>{cancelText}</button>
          <button className={'btn ' + (danger ? 'danger-solid' : 'primary')}
            disabled={blocked} onClick={onConfirm}>{confirmText}</button>
        </div>
      </div>
    </div>
  )
}
