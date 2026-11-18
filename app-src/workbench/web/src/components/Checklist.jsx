import React, { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import { useOnboard } from '../onboarding/context'
import { PHASES, ROWS } from '../onboarding/copy'
import { deriveOnboarding, firstScene, firstSegment } from '../onboarding/derive'

function CheckMark() {
  return (
    <span className="onb-cl-mark" aria-hidden="true">
      <svg viewBox="0 0 10 10"><path d="M2 5.2 4.2 7.4 8 3" /></svg>
    </span>
  )
}

// 开拍清单：状态全部从工程文件推导，点击不写进度。
export default function Checklist({ structure, unit }) {
  const { wb, conn, openSettings } = useApp()
  const nav = useNavigate()
  const { state, setChecklistOpen } = useOnboard()
  const [openRow, setOpenRow] = useState('')

  const d = useMemo(() => deriveOnboarding({ structure, conn }), [structure, conn])
  const open = state.checklistOpen !== false
  const scene = firstScene(structure)
  const segment = firstSegment(structure)
  const title = (structure && structure.script && structure.script.title)
    || (structure && structure.title) || ''
  const sceneCtx = '剧本《' + title + '》' + (scene ? ' · 场 ' + scene.id : '')
  const segCtx = sceneCtx + (segment ? ' · 段 ' + segment.id : '')
  const reviewPath = '/review' + (unit ? '?unit=' + encodeURIComponent(unit) : '')
    + (scene ? (unit ? '&' : '?') + 'scene=' + encodeURIComponent(scene.id) : '')
  const next = ROWS[d.nextId] || ROWS.deliver

  function detail(id, row) {
    if (row.done) {
      if (id === 'tools') {
        return (d.agentName || 'Agent') + ' 已连接 · 导演 skill 已加载'
      }
      if (id === 'script') {
        return d.hasScriptText ? '剧本已有正文' : '已经按内容切了场'
      }
      if (id === 'scenes') return d.sceneCount + ' 场 · ' + d.segmentCount + ' 段 · ' + d.shotCount + ' 镜'
      return ROWS[id].done
    }
    if (id === 'tools') {
      return (d.agentName || 'Agent') + (d.agentReady ? ' 已连接' : ' 未连接')
        + ' · 导演 skill ' + (d.skillReady ? '已加载' : '未找到')
    }
    if (id === 'script' && d.hasBrief) {
      return '简报有了，让 WorkBuddy 写第一版剧本'
    }
    if (row.blocked) return ROWS[id].blocked || ROWS[id].todo
    return ROWS[id].todo
  }

  function action(id, row) {
    if (id === 'tools') return { label: '设置', run: openSettings }
    if (id === 'libtv') {
      if (row.done) return null
      return { label: '去连接', run: openSettings }
    }
    if (id === 'script') {
      if (row.done) return { label: '看', run: () => nav('/script' + (unit ? '?unit=' + encodeURIComponent(unit) : '')) }
      if (d.hasBrief) {
        return { label: '写剧本', run: () => wb('写剧本', '剧本《' + title + '》') }
      }
      return { label: '开始创作', run: () => wb('开始创作', '项目 ' + (title || unit || '当前项目')) }
    }
    if (id === 'scenes') {
      if (row.done) return { label: '看', run: () => nav(reviewPath) }
      if (d.sceneCount === 0) {
        return { label: '生成场', run: () => wb('从剧本生成场', sceneCtx) }
      }
      return { label: '生成分镜', run: () => wb('从剧本生成分镜', sceneCtx) }
    }
    if (id === 'graph') {
      if (row.done) return { label: '看', run: () => nav(reviewPath) }
      return { label: '出图谱', run: () => wb('生成布线图谱', segCtx) }
    }
    if (id === 'wire') {
      if (row.done) return { label: '看', run: () => nav(reviewPath) }
      if (!d.libtvReady) return { label: '先连 LibTV', run: openSettings }
      return { label: '去布线', run: () => nav(reviewPath) }
    }
    if (id === 'output') {
      if (row.done) return { label: '看', run: () => nav(reviewPath) }
      if (!d.libtvReady) return { label: '先连 LibTV', run: openSettings }
      const url = scene && scene.libtv && scene.libtv.editor_url
      return {
        label: '打开 LibTV',
        run: () => {
          if (url) window.open(url, '_blank', 'noreferrer')
          else nav(reviewPath)
        },
      }
    }
    if (id === 'review') {
      return { label: row.done ? '看' : '去审片', run: () => nav(reviewPath) }
    }
    if (id === 'deliver') {
      return {
        label: row.done ? '看' : '去交付',
        run: () => nav('/deliver' + (unit ? '?unit=' + encodeURIComponent(unit) : '')),
      }
    }
    return null
  }

  const rowIds = open ? PHASES : []

  return (
    <section className={'onb-checklist' + (open ? ' open' : '')} id="onb-checklist"
      aria-labelledby="onbChecklistTitle">
      <div className="onb-cl-head">
        <b id="onbChecklistTitle">开拍清单</b>
        <span className="onb-cl-count">{d.doneCount}/{d.total}</span>
        <span className="onb-cl-next">
          {d.doneCount >= d.total
            ? '全部完成 · 看交付'
            : <>下一步：<b>{next.title}</b></>}
        </span>
        <span className="grow" />
        <button className="onb-quiet" onClick={() => setChecklistOpen(!open)}>
          {open ? '收起' : '展开'}
        </button>
      </div>

      {open && (
        <div className="onb-cl-body">
          {rowIds.map((phase) => (
            <div className="onb-cl-phase-group" key={phase.id}>
              <div className="onb-cl-phase">{phase.label}</div>
              {phase.rows.map((id) => {
                const row = d.rows[id]
                const a = action(id, row)
                const cls = 'onb-cl-row'
                  + (row.done ? ' done' : '')
                  + (d.nextId === id && !row.done ? ' cur' : '')
                  + (row.blocked && !row.done ? ' blocked' : '')
                return (
                  <div className={cls} key={id}>
                    <CheckMark />
                    <button className="onb-cl-main" aria-expanded={openRow === id}
                      onClick={() => setOpenRow((x) => (x === id ? '' : id))}>
                      <b>{ROWS[id].title}</b>
                      <span>{detail(id, row)}</span>
                    </button>
                    {a && (!row.blocked || d.nextId === id) && (
                      <button className={'btn small onb-cl-act'
                        + (!row.done && !row.blocked && d.nextId === id ? ' primary' : '')}
                        onClick={a.run}>
                        {a.label}
                      </button>
                    )}
                    {openRow === id && (
                      <p className="onb-cl-more">{ROWS[id].stuck}</p>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
