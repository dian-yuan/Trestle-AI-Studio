import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useOnboard } from '../onboarding/context'
import { COACHES } from '../onboarding/copy'

// 一次性小贴士：只解释一个最容易误解的点，不阻断页面操作。
export default function Coach({ id, anchor, title, body, enabled = true }) {
  const { coachSeen, markCoach } = useOnboard()
  const seen = coachSeen(id)
  const [rect, setRect] = useState(null)
  const bubbleRef = useRef(null)
  const closeTimer = useRef(0)

  useEffect(() => {
    if (seen || !enabled) return undefined
    let raf = 0
    let timer = 0
    const update = () => {
      const el = document.querySelector(anchor)
      if (!el) { setRect(null); return }
      const r = el.getBoundingClientRect()
      if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > window.innerHeight) {
        setRect(null)
        return
      }
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height })
    }
    const schedule = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(update)
    }
    schedule()
    // 页面刚渲染 / 动画结束时再量一次，避免拿到 0 尺寸。
    timer = setTimeout(schedule, 500)
    window.addEventListener('resize', schedule)
    window.addEventListener('scroll', schedule, true)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(timer)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('scroll', schedule, true)
    }
  }, [seen, enabled, anchor])

  useEffect(() => {
    if (!rect || seen) return undefined
    const onKey = (e) => { if (e.key === 'Escape') markCoach(id) }
    const onDown = (e) => {
      if (bubbleRef.current && bubbleRef.current.contains(e.target)) return
      markCoach(id)
    }
    document.addEventListener('keydown', onKey)
    // 延迟挂：避免触发引导的那次点击立刻把它关掉。
    closeTimer.current = setTimeout(() => {
      document.addEventListener('mousedown', onDown)
    }, 120)
    const focusTimer = setTimeout(() => bubbleRef.current?.focus(), 40)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
      clearTimeout(closeTimer.current)
      clearTimeout(focusTimer)
    }
  }, [rect, seen, id, markCoach])

  if (seen || !enabled || !rect) return null

  const copy = COACHES[id] || {}
  const W = 280
  const GAP = 14
  const viewportW = window.innerWidth
  const viewportH = window.innerHeight
  let left = Math.min(rect.left + 20, viewportW - W - 16)
  left = Math.max(12, left)
  let top = rect.top + rect.height + GAP
  let pointerTop = true
  if (top + 150 > viewportH - 16) {
    top = Math.max(12, rect.top - 150 - GAP)
    pointerTop = false
  }
  const pointerLeft = Math.max(18, Math.min(W - 30, rect.left + 40 - left))

  return createPortal(
    <div className="onb-coach-layer" aria-live="polite">
      <div className="onb-coach-hole" style={{
        left: rect.left - 6,
        top: rect.top - 6,
        width: rect.width + 12,
        height: rect.height + 12,
      }} />
      <div className="onb-coach-bubble" ref={bubbleRef} tabIndex={-1}
        role="dialog" aria-modal="false" aria-label={title || copy.title}
        style={{ left, top, width: W }}>
        <button className="onb-coach-x" onClick={() => markCoach(id)} aria-label="关闭">×</button>
        <h2>{title || copy.title}</h2>
        <p>{body || copy.body}</p>
        <div className="onb-coach-foot">
          <span>第 1 次提示</span>
          <span className="grow" />
          <button className="btn small" onClick={() => markCoach(id)}>知道了</button>
        </div>
        <span className={'onb-coach-pointer' + (pointerTop ? '' : ' up')}
          style={{ left: pointerLeft }} />
      </div>
    </div>,
    document.body,
  )
}
