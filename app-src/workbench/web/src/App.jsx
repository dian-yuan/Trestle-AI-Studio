import React, {
  createContext, useCallback, useContext, useEffect, useRef, useState,
} from 'react'
import { NavLink, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { openWorkbuddy, post } from './api'
import { useConn, useSnapshot } from './hooks'
import Home from './pages/Home'
import Outline from './pages/Outline'
import Script from './pages/Script'
import Review from './pages/Review'
import Assets from './pages/Assets'
import Ideas from './pages/Ideas'
import Deliver from './pages/Deliver'
import Lineage from './pages/Lineage'
import LeiBoxLoader from './components/LeiBoxLoader'
import SettingsDrawer from './components/SettingsDrawer'
import HelpDrawer from './components/HelpDrawer'
import ConfirmDialog from './components/ConfirmDialog'
import WbTaskDialog from './components/WbTaskDialog'
import { OnboardProvider } from './onboarding/context'

export const AppCtx = createContext(null)
export const useApp = () => useContext(AppCtx)

// 所有「修改 / 完善 / 接手」类入口都先过任务卡；生成类动作保持一键直开。
const EDIT_RX = /改|完善|接手|修改|重做|重新设计|延申|延伸/

function defaultEditTargets(action, ctx, unitId) {
  if (/(剧本|改这场)/.test(action) && unitId) {
    return [{ label: '剧本', value: 'units/' + unitId + '/script/copy.md（对应场次）' }]
  }
  if (/(大纲|思路)/.test(action)) {
    return [{ label: '文件', value: 'design/创意库（当前大纲 / 创意文件）' }]
  }
  return [{ label: '位置', value: ctx || '当前页面' }]
}

const PAGE_LABEL = {
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

function Topbar({ snap, conn, onOpenSettings, onOpenHelp, onAskAI }) {
  const { wb, notify, refresh } = useApp()
  const mode = (snap && snap.meta && snap.meta.mode) || 'general'
  const [modeBusy, setModeBusy] = useState(false)
  const [askMode, setAskMode] = useState(false)
  const launcher = (document.querySelector('meta[name="launcher-url"]') || {}).content || ''
  const agents = (conn && conn.agents) || []
  const libtv = (conn && conn.libtv) || {}
  const readyAgent = agents.find((a) => a.ready)
  const agentOnline = !!readyAgent
  const libtvOnline = libtv.state === 'online'
  const online = agentOnline || libtvOnline
  const connLabel = agentOnline && libtvOnline ? '已连接'
    : agentOnline ? ((readyAgent && readyAgent.name) || 'Agent') + ' 已连接'
      : libtvOnline ? 'LibTV 已连接' : '待连接'
  const nextMode = mode === 'short_drama' ? 'general' : 'short_drama'
  const flipMode = async () => {
    setAskMode(false)
    setModeBusy(true)
    try {
      const r = await post('/api/project/mode', { mode: nextMode })
      if (r.ok) {
        notify(nextMode === 'short_drama' ? '已切到短剧模式' : '已切到通用模式', 'ok')
        await refresh()
      } else {
        notify('切换失败：' + (r.error || ''), 'error')
      }
    } finally { setModeBusy(false) }
  }
  return (
    <header className="topbar">
      {launcher ? (
        <a className="back-project" href={launcher} title="回到选择项目">← 项目</a>
      ) : (
        <button className="back-project" title="当前不是从启动器打开，找不到项目列表"
          onClick={() => notify('当前不是从启动器打开；用桌面图标启动 Trestle 后，这里可以回到项目列表', 'warn')}>
          ← 项目
        </button>
      )}
      <svg className="brand-lockup" viewBox="0 0 243.425 51.24"
        role="img" aria-label="Trestle">
        <g transform="translate(0,4.62) scale(0.175)">
          <path fill="currentColor"
            d="M34 45H206V77H154L159 195H81L86 77H34Z" />
        </g>
        <g transform="translate(61.8,40.62) scale(0.41666666666666674)">
          <path fill="currentColor"
            d="M18.41 0V-65.19H29.74V0ZM-1.37 -60.16V-71H49.51V-60.16Z M67.28 -29.44V-40.28H91.69Q95.55 -40.28 97.88 -43.02Q100.22 -45.75 100.24 -50.24Q100.24 -54.74 97.9 -57.47Q95.57 -60.21 91.69 -60.21H67.28V-71.04H91.89Q97.97 -71.04 102.52 -68.45Q107.07 -65.86 109.59 -61.19Q112.1 -56.52 112.1 -50.24Q112.1 -43.99 109.56 -39.31Q107.03 -34.63 102.5 -32.04Q97.97 -29.44 91.89 -29.44ZM62.54 0V-71.04H73.87V0ZM101.51 0 84.86 -31.64 96.53 -33.89 115.08 0Z M133.73 0V-71H145.06V0ZM138.56 0V-10.84H180.12V0ZM138.56 -29.83V-40.67H174.74V-29.83ZM138.56 -60.16V-71H180.12V-60.16Z M221.22 0.73Q216.09 0.73 211.4 -0.26Q206.72 -1.26 202.63 -3.22Q198.55 -5.19 195.1 -8.06L201.3 -17.04Q205.37 -13.58 210.45 -11.8Q215.53 -10.01 221.22 -10.01Q228.47 -10.01 232.46 -12.63Q236.46 -15.24 236.46 -19.97V-20.02Q236.46 -23.68 234.45 -25.63Q232.43 -27.57 229.16 -28.51Q225.88 -29.45 222 -30.08Q217.94 -30.74 213.8 -31.68Q209.65 -32.62 206.17 -34.63Q202.7 -36.65 200.58 -40.42Q198.47 -44.19 198.47 -50.49V-50.54Q198.47 -60.64 205.16 -66.19Q211.85 -71.73 224.05 -71.73Q229.79 -71.73 235.45 -69.86Q241.12 -67.99 246.27 -64.31L240.61 -55.03Q236.54 -58 232.33 -59.49Q228.13 -60.99 224.05 -60.99Q217.23 -60.99 213.46 -58.43Q209.7 -55.88 209.7 -51.22V-51.17Q209.7 -47.4 211.91 -45.44Q214.11 -43.48 217.67 -42.53Q221.23 -41.59 225.32 -40.77Q229.31 -39.99 233.27 -38.87Q237.24 -37.75 240.5 -35.65Q243.77 -33.54 245.73 -29.87Q247.69 -26.2 247.69 -20.31V-20.21Q247.69 -10.22 240.75 -4.75Q233.82 0.73 221.22 0.73Z M277.08 0V-65.19H288.4V0ZM257.3 -60.16V-71H308.18V-60.16Z M321.21 0V-71H332.54V0ZM326.58 0V-10.84H367.6V0Z M384.54 0V-71H395.86V0ZM389.37 0V-10.84H430.92V0ZM389.37 -29.83V-40.67H425.55V-29.83ZM389.37 -60.16V-71H430.92V-60.16Z" />
        </g>
      </svg>
      <span className="proj-text">
        {snap ? [snap.meta.name, snap.meta.unit].filter(Boolean).join(' · ') : ''}
      </span>
      <nav className="nav">
        <NavLink end to="/">总览</NavLink>
        <NavLink to="/deliver">交付</NavLink>
      </nav>
      <span className="grow" />
      <ConfirmDialog
        open={askMode}
        title={nextMode === 'short_drama' ? '切换到短剧模式？' : '切换到通用模式？'}
        desc="模式决定 WorkBuddy 的提示词模板、场的字段和进度条的写法。已有内容不会被改。"
        details={nextMode === 'short_drama'
          ? ['之后按说好五段提示 WorkBuddy：大纲 → 角色 → 美术 → 剧本 → 站位 → 分镜']
          : ['之后按通用七步提示 WorkBuddy：创作思路 → 剧本 → 场 → 段 → 镜']}
        confirmText="切换"
        onConfirm={flipMode}
        onCancel={() => setAskMode(false)}
      />
      <button className="mode-chip" disabled={modeBusy} onClick={() => setAskMode(true)}
        title="项目模式：切换前会确认">
        {mode === 'short_drama' ? '短剧' : '通用'}
      </button>
      <button className="conn-chip" onClick={onOpenSettings} title="配置与连接">
        <span className={'cdot ' + (online ? 'ok' : 'warn')} />
        <span>{connLabel}</span>
      </button>
      <button className="btn small ask-ai" onClick={onAskAI} title="直接打开 WorkBuddy 问 Trestle 教师">
        问 AI
      </button>
      <button className="icon-btn gear help" onClick={onOpenHelp} title="怎么用 Trestle"
        aria-label="怎么用 Trestle">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.2" />
          <path d="M6.2 6.2a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.3" />
          <circle cx="8" cy="11.6" r=".7" fill="currentColor" stroke="none" />
        </svg>
      </button>
      <button className="icon-btn gear" onClick={onOpenSettings} title="配置与连接">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2 4.5h6M11 4.5h3M2 8h2M7 8h7M2 11.5h6M11 11.5h3" />
          <circle cx="9.5" cy="4.5" r="1.6" /><circle cx="5" cy="8" r="1.6" />
          <circle cx="9.5" cy="11.5" r="1.6" />
        </svg>
      </button>
      <button className="btn ghost"
        onClick={() => wb('接手当前页面', '创作工作台', null, { skipAsk: true })}>
        打开 WorkBuddy <em>↗</em>
      </button>
    </header>
  )
}

function SyncFab({ onRefresh }) {
  const [on, setOn] = useState(false)
  return (
    <button
      className={'sync-fab' + (on ? ' on' : '')}
      title="同步 WorkBuddy 新写入的内容"
      onClick={async () => {
        setOn(true)
        await onRefresh()
        setOn(false)
      }}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3.2h-3.2" />
      </svg>
      <span>同步</span>
    </button>
  )
}

function SelectionBar({ onPick }) {
  const [sel, setSel] = useState(null)
  const barRef = useRef(null)

  useEffect(() => {
    const read = () => {
      const s = window.getSelection()
      if (!s || s.isCollapsed || !s.rangeCount) { setSel(null); return }
      const text = s.toString().trim()
      if (text.length < 4) { setSel(null); return }
      let node = s.anchorNode
      let el = node && (node.nodeType === 1 ? node : node.parentElement)
      const src = el && el.closest('[data-sel-src]')
      if (!src || !s.focusNode || !src.contains(s.focusNode)) { setSel(null); return }
      const rect = s.getRangeAt(0).getBoundingClientRect()
      setSel({ text: text.slice(0, 2000), ctx: src.dataset.selCtx || '', rect })
    }
    const up = (e) => {
      if (e.target.closest && e.target.closest('#selBar')) return
      setTimeout(read, 0)
    }
    const down = (e) => {
      if (!e.target.closest || !e.target.closest('#selBar')) setSel(null)
    }
    const key = (e) => {
      if (e.key === 'Escape') setSel(null)
      else if (e.key === 'Shift' || e.key.indexOf('Arrow') === 0) read()
    }
    document.addEventListener('mouseup', up)
    document.addEventListener('mousedown', down)
    document.addEventListener('keyup', key)
    window.addEventListener('scroll', () => setSel(null), true)
    return () => {
      document.removeEventListener('mouseup', up)
      document.removeEventListener('mousedown', down)
      document.removeEventListener('keyup', key)
    }
  }, [])

  if (!sel) return null
  const style = {
    left: Math.max(8, Math.min(sel.rect.left + sel.rect.width / 2 - 140,
      window.innerWidth - 288)),
    top: Math.max(8, sel.rect.top - 46),
  }
  return (
    <div className="selbar" id="selBar" style={style} ref={barRef}>
      <span className="selbar-t">选中内容</span>
      {['从选中内容构建角色', '从选中内容构建装造', '从选中内容构建场景'].map((a) => (
        <button key={a} onClick={() => { onPick(a, sel); setSel(null) }}>
          {a.replace('从选中内容', '')}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const [params] = useSearchParams()
  const location = useLocation()
  const docRoute = location.pathname === '/script' || location.pathname === '/outline'
  const unit = params.get('unit') || undefined
  const { snap, error, loading, refresh } = useSnapshot(unit)
  const { conn, loading: connLoading, refresh: refreshConn } = useConn()
  const [busy, setBusy] = useState(null)
  const [wbTask, setWbTask] = useState(null)
  const [toast, setToast] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('wb-theme') || 'dark' } catch (e) { return 'dark' }
  })

  useEffect(() => {
    const resolved = theme === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
      : theme
    document.documentElement.dataset.theme = resolved
    try { localStorage.setItem('wb-theme', theme) } catch (e) { /* ignore */ }
  }, [theme])

  const notify = useCallback((msg, tone = 'info') => {
    const id = Date.now()
    setToast({ msg, tone, id })
    setTimeout(() => setToast((t) => (t && t.id === id ? null : t)), 3200)
  }, [])

  const wb = useCallback(async (action, ctx, selection, extra) => {
    const ex = extra || {}
    // 兜底：以后新加的「改…」按钮忘了走任务卡，也不会直接打开。
    if (EDIT_RX.test(action) && !('intent' in ex) && !ex.skipAsk) {
      const uid = (snap && snap.meta && snap.meta.unit) || unit || ''
      const targets = ex.targets || defaultEditTargets(action, ctx, uid)
      setWbTask({
        title: action,
        desc: '先说清这次要改什么；WorkBuddy 只会改任务书点名的文件。',
        targets,
        presets: ex.presets || ['我还没想好，先给 2 个改法'],
        placeholder: ex.placeholder || '例：把这一处改成……',
        action, ctx, selection,
        extra: { ...ex, targets },
      })
      return
    }
    setBusy({ action, ctx })
    try {
      const r = await openWorkbuddy(action, ctx, selection, ex)
      if (!r.ok) { notify('打不开 WorkBuddy：' + (r.error || ''), 'error'); return }
      if (r.url) {
        window.location.href = r.url
        notify('已在 WorkBuddy 打开', 'ok')
        setTimeout(async () => {
          try {
            await navigator.clipboard.writeText(r.short_prompt || r.prompt || '')
            notify('如果没打开：任务指令已复制', 'ok')
          } catch (e) { /* ignore */ }
        }, 1600)
      } else {
        try { await navigator.clipboard.writeText(r.short_prompt || r.prompt || '') } catch (e) { /* ignore */ }
        notify('已复制任务指令，粘贴给 Agent 即可', 'ok')
      }
    } finally {
      setBusy(null)
    }
  }, [notify, snap, unit])

  // 打开统一的「WorkBuddy 任务卡」：先让用户说清要改什么，再启动。
  const wbAsk = useCallback((spec) => setWbTask(spec), [])

  const askAI = useCallback(() => {
    const page = PAGE_LABEL[location.pathname] || location.pathname
    const unitId = (snap && snap.meta && snap.meta.unit) || unit || ''
    wb('怎么用 Trestle',
      '我在「' + page + '」页'
      + (unitId ? '（' + unitId + '）' : '')
      + '。请告诉我现在该做什么、下一步点哪里。')
  }, [wb, location.pathname, snap, unit])

  const ctx = {
    snap, refresh, unit, wb, wbAsk, notify, conn, refreshConn, theme, setTheme,
    openSettings: () => setSettingsOpen(true),
    openHelp: () => setHelpOpen(true),
  }

  if (loading && !snap) {
    return <div className="boot">正在读工作台…</div>
  }
  if (error && !snap) {
    return <div className="boot error">{error}</div>
  }

  return (
    <AppCtx.Provider value={ctx}>
      <OnboardProvider>
      <div className="app">
        <Topbar snap={snap} conn={conn} onOpenSettings={() => setSettingsOpen(true)}
          onOpenHelp={() => setHelpOpen(true)} onAskAI={askAI} />
        <main className={'shell' + (docRoute ? ' shell-doc' : '')}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/outline" element={<Outline />} />
            <Route path="/script" element={<Script />} />
            <Route path="/review" element={<Review />} />
            <Route path="/storyboard" element={<Review />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/asset" element={<Assets />} />
            <Route path="/ideas" element={<Ideas />} />
            <Route path="/deliver" element={<Deliver />} />
            <Route path="/lineage" element={<Lineage />} />
          </Routes>
        </main>
        <SyncFab onRefresh={refresh} />
        <SelectionBar onPick={(a, s) => wb(a, s.ctx, s.text)} />
        {busy && (
          <div className="busy-scrim">
            <div className="busy-card">
              <LeiBoxLoader title="WorkBuddy is taking over" />
              <h3>WorkBuddy 正在接手</h3>
              <p>{busy.action}</p>
              <small>{busy.ctx}</small>
            </div>
          </div>
        )}
        <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
        <SettingsDrawer
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          snap={snap}
          conn={conn}
          loading={connLoading}
          refreshConn={refreshConn}
          notify={notify}
          theme={theme}
          setTheme={setTheme}
          onOpenHelp={() => { setSettingsOpen(false); setHelpOpen(true) }}
        />
        <WbTaskDialog spec={wbTask} onClose={() => setWbTask(null)} onSubmit={wb} />
        {toast && <div className={'toast ' + toast.tone}>{toast.msg}</div>}
      </div>
      </OnboardProvider>
    </AppCtx.Provider>
  )
}
