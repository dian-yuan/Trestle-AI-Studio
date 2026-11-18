import React, { useEffect, useRef } from 'react'
import { mountTransferGlow } from '../leibox/transfer-glow/TransferGlowEngine.js'

/**
 * 用 LeiBox「转账光雾确认」做通用确认框：
 * 保留光雾/确认动画，隐藏转账金额与明细，只留标题、说明和两个按钮。
 */
export default function ConfirmGlow({
  open, title, desc, confirmText = '确认', cancelText = '取消',
  onConfirm, onCancel,
}) {
  const host = useRef(null)
  const confirmRef = useRef(onConfirm)
  const cancelRef = useRef(onCancel)
  useEffect(() => { confirmRef.current = onConfirm }, [onConfirm])
  useEffect(() => { cancelRef.current = onCancel }, [onCancel])

  useEffect(() => {
    if (!open || !host.current) return
    let done = false
    const c = mountTransferGlow(host.current, {
      open: true,
      amount: '',
      account: '',
      recipient: '',
      receivedAmount: '',
      duration: 1400,
      onComplete: () => {
        if (!done) { done = true; confirmRef.current() }
      },
    })
    const root = c.root
    const style = document.createElement('style')
    style.textContent = `
      .tg-dashboard,.tg-launch,.tg-details,.tg-flag,.tg-status{display:none!important}
      .tg-scene{background:transparent!important;min-height:100vh;padding:0!important}
      .tg-modal-wrap{position:fixed!important;inset:0!important;display:grid!important;
        place-items:center!important;padding:16px!important}
      .tg-modal,.tg-content{background:#171b24!important;
        color:#e8ebf2!important;border-color:rgba(255,255,255,.16)!important}
      .tg-modal{width:min(460px,92vw)!important;border:1px solid rgba(255,255,255,.16)!important;
        border-radius:18px!important;box-shadow:0 30px 90px rgba(0,0,0,.65)!important}
      .tg-content{padding:26px 26px 22px!important}
      .tg-title{font-size:20px!important;color:#e8ebf2!important}
      .tg-subtitle{font-size:12.5px!important;line-height:1.65!important;color:#a9b0c0!important}
      .tg-cancel{background:rgba(255,255,255,.06)!important;color:#e8ebf2!important;
        border:1px solid rgba(255,255,255,.14)!important}
      .tg-send{background:#3f62d8!important;color:#fff!important;
        border:1px solid #5273e3!important}
      .tg-close{background:rgba(255,255,255,.08)!important;color:#e8ebf2!important;
        border:1px solid rgba(255,255,255,.14)!important}
      .tg-white-veil{background:#0b0d12!important}
    `
    root.appendChild(style)
    const set = (sel, val) => {
      const el = root.querySelector(sel)
      if (el) el.textContent = val
    }
    set('.tg-title', title || '确认操作')
    set('.tg-subtitle', desc || '')
    set('.tg-cancel', cancelText)
    set('.tg-send', confirmText)
    const cancel = root.querySelector('.tg-cancel')
    const close = root.querySelector('.tg-close')
    const cancelClick = (e) => { e.preventDefault(); cancelRef.current() }
    cancel && cancel.addEventListener('click', cancelClick)
    close && close.addEventListener('click', cancelClick)
    return () => {
      cancel && cancel.removeEventListener('click', cancelClick)
      close && close.removeEventListener('click', cancelClick)
      c.destroy()
    }
  }, [open, title, desc, confirmText, cancelText])

  if (!open) return null
  return (
    <div className="confirm-layer">
      <div ref={host} className="confirm-glow" />
    </div>
  )
}
