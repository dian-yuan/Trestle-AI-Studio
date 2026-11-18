import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// 可拖动的悬浮窗：按住标题条，放到页面任意位置。
export default function FloatingPanel({ title, subtitle, onClose, children, width = 440 }) {
  const [pos, setPos] = useState(() => ({
    x: Math.max(16, (window.innerWidth || 1400) - width - 28),
    y: 84,
  }))
  const [dragging, setDragging] = useState(false)
  const start = useRef(null)

  const clampX = (x) => Math.min(
    Math.max(8, (window.innerWidth || 1400) - 120), Math.max(-width + 120, x))
  const clampY = (y) => Math.min(
    Math.max(8, (window.innerHeight || 900) - 48), Math.max(8, y))

  const onDown = (e) => {
    if (e.button !== 0) return
    start.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }
    setDragging(true)
    e.preventDefault()
  }

  useEffect(() => {
    if (!dragging) return undefined
    const move = (e) => {
      const s = start.current
      if (!s) return
      setPos({ x: clampX(e.clientX - s.x), y: clampY(e.clientY - s.y) })
    }
    const up = () => setDragging(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [dragging])

  return createPortal((
    <div className={'float-panel' + (dragging ? ' dragging' : '')}
      style={{ left: pos.x, top: pos.y, width }}>
      <div className="float-head" onMouseDown={onDown}>
        <b>{title}</b>
        {!!subtitle && <span>{subtitle}</span>}
        <span className="grow" />
        <button className="icon-btn" onClick={onClose} aria-label="关闭">×</button>
      </div>
      <div className="float-body">{children}</div>
    </div>
  ), document.body)
}
