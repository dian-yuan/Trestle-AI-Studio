import React, { useEffect, useMemo, useRef, useState } from 'react'
import { openWorkbuddy } from '../api'
import { Modal } from './Modal'

// 所有「改内容」的 WorkBuddy 入口共用这一张任务卡：
//   1. 先钉死这次只改哪个文件 / 哪个字段；
//   2. 留一块给用户写「我想怎么改」；
//   3. 允许先看完整任务书，确认后再打开。
// 不再把 asset id、镜号之类的定位信息塞进「用户想要的效果」里。
export default function WbTaskDialog({ spec, onClose, onSubmit }) {
  const [intent, setIntent] = useState('')
  const [choice, setChoice] = useState('')
  const [preview, setPreview] = useState('')
  const [previewFile, setPreviewFile] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  const taRef = useRef(null)

  useEffect(() => {
    if (!spec) return undefined
    setIntent(spec.initialIntent || '')
    setChoice((spec.choices && spec.choices.defaultValue) || '')
    setPreview('')
    setPreviewFile('')
    setPreviewBusy(false)
    const t = setTimeout(() => {
      try { if (taRef.current) taRef.current.focus() } catch (e) { /* ignore */ }
    }, 40)
    return () => clearTimeout(t)
  }, [spec])

  const choiceLabel = useMemo(() => {
    const opts = (spec && spec.choices && spec.choices.options) || []
    const hit = opts.find((x) => x.value === choice)
    return hit ? hit.label : ''
  }, [spec, choice])

  if (!spec) return null

  const silentChoice = !!(spec.choices && spec.choices.silent)
  const composed = [
    (choiceLabel && !silentChoice) ? '本轮先改：' + choiceLabel + '。' : '',
    String(intent || '').trim(),
  ].filter(Boolean).join('\n')

  const payload = (extra) => ({
    ...(spec.extra || {}),
    ...(choice ? { focus: choice } : {}),
    ...(extra || {}),
  })

  const send = () => {
    onSubmit(spec.action, spec.ctx || '', spec.selection || null,
      payload({ intent: composed }))
    onClose()
  }

  const loadPreview = async () => {
    setPreviewBusy(true)
    try {
      const r = await openWorkbuddy(spec.action, spec.ctx || '', spec.selection || null,
        payload({ intent: composed, preview: true }))
      setPreview((r && (r.taskbook || r.prompt || r.error)) || '没有返回任务书')
      setPreviewFile((r && r.task_file) || '')
    } catch (e) {
      setPreview('读取任务书失败：' + (e && e.message ? e.message : e))
      setPreviewFile('')
    } finally {
      setPreviewBusy(false)
    }
  }

  const addPreset = (p) => {
    setPreview('')
    setIntent((s) => {
      const t = String(s || '').trim()
      if (!t) return p
      if (t.includes(p)) return t
      return t + '\n' + p
    })
  }

  return (
    <Modal
      title={spec.title || '交给 WorkBuddy'}
      desc={spec.desc || '只说你要改什么；WorkBuddy 只动下面这一处，其他文件不碰。'}
      onClose={onClose}
      className="wb-task-modal"
      footer={<>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn primary" onClick={send}>
          {spec.sendLabel || '打开 WorkBuddy'}
        </button>
      </>}
    >
      {!!(spec.targets || []).length && (
        <div className="wb-targets">
          <div className="wb-targets-head">
            <b>这次只改</b>
            <span>点「查看任务书」可以先看完整指令</span>
          </div>
          {spec.targets.map((t, i) => (
            <div className="wb-target" key={i}>
              <span>{t.label}</span>
              <code>{t.value}</code>
            </div>
          ))}
          {spec.note && <p className="wb-target-note">{spec.note}</p>}
        </div>
      )}

      {spec.choices && !!spec.choices.options.length && (
        <div className="wb-choice">
          <span>{spec.choices.label || '这次先改'}</span>
          <div className="wb-choice-row">
            {spec.choices.options.map((o) => (
              <button key={o.value} type="button"
                className={'wb-choice-btn' + (choice === o.value ? ' on' : '')}
                onClick={() => { setChoice(o.value); setPreview('') }}>
                {o.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <label className="field wb-ask">
        <span>你想怎么改？</span>
        <textarea ref={taRef} className="input wb-intent" rows={4}
          value={intent}
          placeholder={spec.placeholder || '例：主体再大一点，背景换成纯白，光线从左上打进来'}
          onChange={(e) => { setIntent(e.target.value); setPreview('') }}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send()
          }} />
      </label>
      <p className="wb-hint">
        不写也可以：打开后 WorkBuddy 会先问你要改哪里，不会直接动文件。
        <span className="kbd-hint">Ctrl / ⌘ + Enter 直接打开</span>
      </p>

      {!!(spec.presets || []).length && (
        <div className="wb-chips">
          <span className="wb-chips-label">没想好？试试</span>
          {spec.presets.map((p) => (
            <button key={p} type="button" className="wb-chip"
              onClick={() => addPreset(p)}>{p}</button>
          ))}
        </div>
      )}

      <div className="wb-preview-block">
        <button className="link-btn" onClick={loadPreview} disabled={previewBusy}>
          {previewBusy ? '读取中…' : (preview ? '刷新任务书' : '查看将发给 WorkBuddy 的完整任务书')}
        </button>
        {!!previewFile && (
          <p className="wb-taskfile">
            任务书已写入 <code>{previewFile}</code>；WorkBuddy 输入框只放短指令。
          </p>
        )}
        {!!preview && <pre className="wb-preview">{preview}</pre>}
      </div>
    </Modal>
  )
}
