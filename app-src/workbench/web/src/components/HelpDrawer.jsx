import React, { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import { useOnboard } from '../onboarding/context'
import { CHAIN, FAQ, ROLES, TERMS } from '../onboarding/copy'

const PAGE_NAME = {
  '/': '总览',
  '/review': '审片',
  '/storyboard': '审片',
  '/assets': '资产',
  '/deliver': '交付',
  '/script': '剧本',
  '/outline': '大纲',
  '/ideas': '创意',
  '/lineage': '血缘',
}

const ASK_SUGGEST = [
  '布线是什么意思？',
  '为什么现在不能生成？',
  '交付包里有什么？',
  'Seedance 2.5 首尾帧怎么用？',
]

export default function HelpDrawer({ open, onClose }) {
  const nav = useNavigate()
  const location = useLocation()
  const { wb, snap, unit } = useApp()
  const { replay, resetCoaches } = useOnboard()
  const closeRef = useRef(null)
  const askRef = useRef(null)
  const [note, setNote] = useState('')
  const [ask, setAsk] = useState('')

  const pageName = PAGE_NAME[location.pathname] || location.pathname
  const unitLabel = (snap && snap.meta && snap.meta.unit) || unit || ''

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const t = setTimeout(() => (askRef.current || closeRef.current)?.focus(), 40)
    return () => {
      document.removeEventListener('keydown', onKey)
      clearTimeout(t)
    }
  }, [open, onClose])

  if (!open) return null

  const askAI = (text) => {
    const q = String(text || ask || '').trim()
    const head = '我在「' + pageName + '」页'
      + (unitLabel ? '（' + unitLabel + '）' : '')
    const prompt = q
      ? head + '。我的问题是：' + q
      : head + '。请告诉我这个页面怎么用、下一步点哪里。'
    onClose()
    wb('怎么用 Trestle', prompt)
  }

  const pickAsk = (q) => {
    setAsk(q)
    if (askRef.current) askRef.current.focus()
  }

  const replayLesson = () => {
    replay()
    onClose()
    nav('/')
  }

  const reset = () => {
    resetCoaches()
    setNote('小贴士已重置；下次到对应页面会再出现。')
    setTimeout(() => setNote(''), 3200)
  }

  return (
    <div className="drawer-layer">
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="settings-drawer onb-help" role="dialog" aria-modal="true"
        aria-labelledby="onbHelpTitle">
        <div className="drawer-head">
          <div>
            <h2 id="onbHelpTitle">怎么用 Trestle</h2>
            <p>不会就先问 Trestle 教师；也可以看三分工、名词和常见问题。</p>
          </div>
          <button className="icon-btn" ref={closeRef} onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="drawer-body">
          <section className="onb-ask">
            <label htmlFor="onbAsk">哪里不会？直接问 Trestle 教师</label>
            <div className="onb-ask-row">
              <input id="onbAsk" ref={askRef} className="input" value={ask}
                onChange={(e) => setAsk(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') askAI() }}
                placeholder="例如：为什么现在不能生成？" />
              <button className="btn small primary" onClick={() => askAI()}>问 AI</button>
            </div>
            <div className="onb-ask-suggest">
              {ASK_SUGGEST.map((q) => (
                <button key={q} onClick={() => pickAsk(q)}>{q}</button>
              ))}
            </div>
            <p className="onb-ask-note">
              会带上当前页面（{pageName}）；Trestle 教师只教，不替你写、不生成。
            </p>
          </section>

          <section className="onb-hsec">
            <h3>三分工</h3>
            <div className="onb-roles">
              {ROLES.map((r) => (
                <div className="onb-role" key={r.name}>
                  <b>{r.name}</b>
                  <i>{r.tag}</i>
                  <span>{r.lines.join('；')}。</span>
                </div>
              ))}
            </div>
          </section>

          <section className="onb-hsec">
            <h3>七步 + 收尾</h3>
            <p className="onb-hint">七步是导演 skill 的分法；生成 / 同步 / 交付是工作台的收尾。</p>
            <div className="onb-chain">
              {CHAIN.map((g) => (
                <div className="onb-chain-row" key={g.who}>
                  <b>{g.who}</b>
                  <div>
                    {g.steps.map((x, i) => (
                      <React.Fragment key={x}>
                        {i > 0 && <em>→</em>}
                        <span>{x}</span>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="onb-hsec">
            <h3>名词表</h3>
            <dl className="onb-terms">
              {TERMS.map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt>{k}</dt><dd>{v}</dd>
                </React.Fragment>
              ))}
            </dl>
          </section>

          <section className="onb-hsec">
            <h3>常见问题</h3>
            {FAQ.map(([q, a], i) => (
              <details className="onb-faq" key={q} open={i === 0}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          </section>

          {note && <p className="onb-note">{note}</p>}
        </div>

        <div className="drawer-foot onb-help-foot">
          <button className="btn small" onClick={replayLesson}>重看第一课</button>
          <button className="btn small" onClick={reset}>重置小贴士</button>
          <span className="grow" />
          <button className="btn small" onClick={onClose}>完成</button>
        </div>
      </aside>
    </div>
  )
}
