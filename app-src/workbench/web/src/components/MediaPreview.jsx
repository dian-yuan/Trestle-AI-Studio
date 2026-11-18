import React, { useEffect } from 'react'
import { createPortal } from 'react-dom'

// 生成结果的大图 / 视频预览。
export default function MediaPreview({ src, kind = 'image', title, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('modal-open')
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('modal-open')
    }
  }, [onClose])

  if (!src) return null
  return createPortal((
    <div className="media-preview"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="mp-head">
        <b>{title || (kind === 'video' ? '视频预览' : '图片预览')}</b>
        <span className="grow" />
        <button className="icon-btn" onClick={onClose} aria-label="关闭">×</button>
      </div>
      <div className="mp-body" onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}>
        {kind === 'video'
          ? <video src={src} controls autoPlay playsInline />
          : <img src={src} alt={title || ''} />}
      </div>
    </div>
  ), document.body)
}
