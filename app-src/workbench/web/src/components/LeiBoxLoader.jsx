import React, { useEffect, useRef } from 'react'
import { mountAiImageGeneration } from '../leibox/ai-generating/AiImageGenerationEngine.js'

/** LeiBox 点阵加载：mount 后把演示标题换成本项目的文案。 */
export default function LeiBoxLoader({ title, className = '' }) {
  const host = useRef(null)
  useEffect(() => {
    if (!host.current) return
    const c = mountAiImageGeneration(host.current)
    if (title) {
      const t = c.root.querySelector('.title')
      if (t) t.textContent = title
    }
    return () => c.destroy()
  }, [title])
  return <div ref={host} className={'leibox-loader ' + className} />
}
