import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react'
import { get, post } from '../api'

// 引导状态只放本机浏览器，不写进客户工程。
// 工程文件应该只有内容；换机器 / 发同事时，引导进度也不该跟着走。
const KEY = 'cj.onboard.v1'
const DEFAULTS = {
  firstLessonAt: '',
  coaches: {},
  // 默认收起：第一课在的时候，先让人看懂三分工，再展开清单。
  checklistOpen: false,
  launcherHelpAt: '',
}

function read() {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULTS, coaches: {} }
    const v = JSON.parse(raw)
    return { ...DEFAULTS, ...(v || {}), coaches: { ...((v || {}).coaches || {}) } }
  } catch (e) {
    return { ...DEFAULTS, coaches: {} }
  }
}

function write(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch (e) { /* 隐私模式：只保留内存态 */ }
}

const Ctx = createContext(null)

export const useOnboard = () => useContext(Ctx)

export function OnboardProvider({ children }) {
  const [state, setState] = useState(read)
  const [ready, setReady] = useState(false)

  useEffect(() => { write(state) }, [state])

  // 引导进度再存一份到本机配置目录（%LOCALAPPDATA%/Changji/onboard.json）。
  // 换端口、换浏览器、更新安装包都不会丢，避免每次进来又弹第一课。
  useEffect(() => {
    let alive = true
    get('/api/onboard').then((r) => {
      if (!alive) return
      const at = (r && r.firstLessonAt) || ''
      const coaches = (r && r.coaches) || {}
      setState((s) => {
        const merged = {
          ...s,
          firstLessonAt: s.firstLessonAt || at,
          coaches: { ...coaches, ...(s.coaches || {}) },
          checklistOpen: s.checklistOpen || !!(r && r.checklistOpen),
        }
        if (!at && s.firstLessonAt) {
          post('/api/onboard', {
            firstLessonAt: s.firstLessonAt,
            coaches: s.coaches || {},
            checklistOpen: s.checklistOpen,
          }).catch(() => {})
        }
        return merged
      })
    }).catch(() => {}).finally(() => { if (alive) setReady(true) })
    return () => { alive = false }
  }, [])

  const sync = useCallback((next) => {
    post('/api/onboard', {
      firstLessonAt: next.firstLessonAt || '',
      coaches: next.coaches || {},
      checklistOpen: !!next.checklistOpen,
    }).catch(() => {})
  }, [])

  const markFirstLesson = useCallback(() => {
    const at = new Date().toISOString()
    setState((s) => {
      const next = s.firstLessonAt ? s : { ...s, firstLessonAt: at }
      sync(next)
      return next
    })
  }, [sync])

  const markCoach = useCallback((id) => {
    setState((s) => {
      const next = {
        ...s,
        coaches: { ...(s.coaches || {}), [id]: true },
      }
      sync(next)
      return next
    })
  }, [sync])

  const setChecklistOpen = useCallback((open) => {
    setState((s) => {
      const next = { ...s, checklistOpen: !!open }
      sync(next)
      return next
    })
  }, [sync])

  const replay = useCallback(() => {
    const next = {
      ...state,
      firstLessonAt: '',
      coaches: {},
      checklistOpen: false,
    }
    setState(next)
    sync(next)
  }, [state, sync])

  const resetCoaches = useCallback(() => {
    setState((s) => ({ ...s, coaches: {} }))
  }, [])

  const value = useMemo(() => ({
    state,
    ready,
    firstLessonSeen: !!state.firstLessonAt,
    coachSeen: (id) => !!(state.coaches && state.coaches[id]),
    markFirstLesson,
    markCoach,
    setChecklistOpen,
    replay,
    resetCoaches,
  }), [state, ready, markFirstLesson, markCoach, setChecklistOpen, replay,
    resetCoaches])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
