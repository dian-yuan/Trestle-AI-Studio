import React, { useEffect, useState } from 'react'
import { get, post } from '../api'
import { Modal } from './Modal'

const AGENT_TIP = {
  'claude-code': '终端里能跑 claude 就算装好。',
  workbuddy: '用 deeplink 打开工程，并读内置导演 skill。',
}

const LIBTV_LABEL = {
  online: '已连接', authorizing: '等待授权', offline: '离线',
  expired: '授权过期', signed_out: '未连接', unverified: '待校验',
  account: '账号异常', error: '调用失败', unknown: '未知',
}

function Dot({ ok }) {
  return <span className={'sdot ' + (ok ? 'ok' : 'warn')} />
}

export default function SettingsDrawer({
  open, onClose, snap, conn, loading, refreshConn, notify, theme, setTheme,
  onOpenHelp,
}) {
  const [busy, setBusy] = useState('');
  const [local, setLocal] = useState(conn)
  const [gate, setGate] = useState(null)
  const [gateOpen, setGateOpen] = useState(false)

  useEffect(() => { setLocal(conn) }, [conn])
  useEffect(() => {
    if (open) refreshConn(false)
  }, [open, refreshConn])
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const c = local || conn || {}
  const agents = c.agents || []
  const skill = c.skill || {}
  const libtv = c.libtv || {}
  const libtvOk = libtv.state === 'online'
  const stats = (snap && snap.stats) || {}

  async function run(key, fn) {
    setBusy(key)
    try { await fn() } finally { setBusy('') }
  }

  async function launch(id) {
    await run('launch-' + id, async () => {
      const r = await post('/api/agent/launch', { id })
      if (!r.ok) {
        notify(r.code === 'needs_install' ? '没找到启动入口：' + id : '打开失败：' + (r.error || ''), 'error')
        return
      }
      notify('已交给 ' + (agents.find((a) => a.id === id) || {}).name, 'ok')
    })
  }

  async function setPath(a) {
    const p = window.prompt('填 ' + a.name + ' 可执行文件的完整路径', a.path || '')
    if (!p) return
    await run('path-' + a.id, async () => {
      const r = await post('/api/agent/path', { id: a.id, path: p.trim() })
      if (!r.ok) { notify('没记住：' + (r.error || '路径无效'), 'error'); return }
      notify('路径已保存', 'ok')
      await refreshConn(false)
    })
  }

  async function login() {
    await run('login', async () => {
      const r = await post('/api/libtv/login', {})
      if (!r.ok) { notify('连不上 LibTV：' + (r.error || ''), 'error'); return }
      notify('浏览器里完成授权，回来后自动变绿', 'info')
      for (let i = 0; i < 90; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        const s = await get('/api/libtv/session?net=1')
        if (s && s.state === 'online') {
          notify('LibTV 已连接', 'ok')
          await refreshConn(true)
          return
        }
        if (s && s.state === 'expired') break
      }
      await refreshConn(true)
    })
  }

  async function logout() {
    await run('logout', async () => {
      await post('/api/libtv/logout', {})
      notify('已退出 LibTV', 'ok')
      await refreshConn(false)
    })
  }

  async function recheck() {
    await run('recheck', async () => {
      await refreshConn(true)
      notify('已重新检查连接', 'ok')
    })
  }

  async function openGate() {
    setGateOpen(true)
    setBusy('gate')
    try {
      const g = await get('/api/gate?fresh=1')
      setGate(g)
    } finally { setBusy('') }
  }

  async function openProjectDir() {
    const r = await post('/api/reveal', { path: '.' })
    if (!r.ok) notify('打不开工程目录：' + (r.error || ''), 'error')
  }

  async function copyDiag() {
    try {
      const d = await get('/api/diag')
      await navigator.clipboard.writeText(d.text || '读不到诊断信息')
      notify('诊断信息已复制，可直接发给反馈群', 'ok')
    } catch (e) {
      notify('复制诊断失败：' + String(e), 'error')
    }
  }

  async function ensureAll() {
    setBusy('ensure-all')
    try {
      const r = await post('/api/libtv/ensure', { dry_run: false, confirm: true })
      if (!r.ok) { notify('建设失败：' + (r.error || ''), 'error'); return }
      notify('已建设全部场结构', 'ok')
      await openGate()
    } finally { setBusy('') }
  }

  async function startProject() {
    setBusy('gate-start')
    try {
      const r = await post('/api/gate/start', { auto_create: true })
      if (!r.ok) { notify('还没就绪：' + (r.error || ''), 'error'); return }
      setGate(r)
      notify('已开始项目', 'ok')
      await refreshConn(true)
    } finally { setBusy('') }
  }

  async function setAgentPath(id) {
    const a = agents.find((x) => x.id === id)
    if (a) await setPath(a)
  }

  return (
    <div className="drawer-layer">
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="settings-drawer">
        <div className="drawer-head">
          <div>
            <h2>配置与连接</h2>
            <p>Agent、导演 skill、LibTV、ffmpeg / 磁盘和这一集的状态。</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="drawer-body">
          <section className="ssec">
            <div className="ssec-head"><b>项目</b><span className="tag ok">已打开</span></div>
            <div className="kv-row"><span>{snap ? snap.meta.name + ' / ' + snap.meta.unit : '—'}</span>
              <b>{stats.scenes || 0} 场 · {(stats.storyboards || 0) + (stats.suggested || 0)} 分镜</b></div>
            <div className="quick" style={{ marginTop: 10 }}>
              <button className="btn" onClick={openGate} disabled={busy === 'gate'}>
                {busy === 'gate' ? '检查中…' : '开工检查'}
              </button>
              <button className="btn" onClick={recheck} disabled={busy === 'recheck'}>
                {busy === 'recheck' ? '检查中…' : '全部检查'}
              </button>
              <button className="btn" onClick={copyDiag}>复制诊断</button>
              <button className="btn" onClick={openProjectDir}>打开工程目录</button>
            </div>
            <p className="hint">卡住时点「复制诊断」，把结果和截图一起发给反馈群；诊断不含剧本、提示词和 token。</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>Agent</b><span className="faint">工作台把自动操作交给它</span></div>
            {agents.map((a) => (
              <div className="agent-row" key={a.id}>
                <Dot ok={a.ready} />
                <div className="agent-main">
                  <b>{a.name}</b>
                  <span>{a.ready ? '已连接' : (a.installed ? '已安装 · 未检测到运行' : '未安装')}</span>
                  {a.path && <em>{a.path}</em>}
                </div>
                <div className="agent-acts">
                  <button className="btn small" onClick={() => launch(a.id)} disabled={busy === 'launch-' + a.id}>
                    {a.id === 'workbuddy' ? '打开' : '终端'}
                  </button>
                  <button className="btn small" onClick={() => setAgentPath(a.id)}>路径</button>
                </div>
              </div>
            ))}
            {!agents.length && <p className="empty">还没有检测到 Agent。</p>}
            <p className="hint">{AGENT_TIP['claude-code']} {AGENT_TIP.workbuddy}</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>内置导演 skill</b>
              <span className={'tag ' + (skill.ready ? 'ok' : 'warn')}>{skill.ready ? '已就绪' : '未找到'}</span>
            </div>
            <div className="kv-row"><span>版本</span><b>{skill.version || '—'}</b></div>
            <div className="kv-row"><span>位置</span><b>内置 · 随 Trestle 更新</b></div>
            <div className="quick" style={{ marginTop: 9 }}>
              <button className="btn small" onClick={recheck} disabled={busy === 'recheck'}>重新检查</button>
            </div>
            <p className="hint">Trestle 更新后请新开一个 WorkBuddy 会话；已经开着的旧会话仍读旧版 skill。</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>三处状态</b>
              <span className="faint">谁写 / 谁接 / 谁出片</span>
            </div>
            <div className="state-flow">
              <div><b>WorkBuddy</b><span>写创意、剧本、分镜、图谱、提示词；改的是本地文件。</span></div>
              <div><b>Trestle</b><span>读本地文件，负责校验、布线、同步；不替你写创作内容。</span></div>
              <div><b>LibTV</b><span>保存节点、出图出片；提示词改了要点「更新到 LibTV」才生效。</span></div>
            </div>
            {c.graph && (
              <>
                <div className="kv-row"><span>本集生成节点</span>
                  <b>{c.graph.nodes} 个 · 已布线 {c.graph.wired}</b></div>
                <div className="kv-row"><span>本地已改 · 未推 LibTV</span>
                  <b className={c.graph.needs_push ? 'warn' : ''}>{c.graph.needs_push} 个</b></div>
                <div className="kv-row"><span>缺提示词</span>
                  <b className={c.graph.missing_prompt ? 'warn' : ''}>{c.graph.missing_prompt} 个</b></div>
              </>
            )}
            <p className="hint">方向：WorkBuddy → 本地文件 → Trestle → LibTV；
              LibTV 生成完 → 回 Trestle 点「同步」。三处不是实时同步，改完要各走一步。</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>LibTV</b>
              <span className={'tag ' + (libtvOk ? 'ok' : 'warn')}>
                {LIBTV_LABEL[libtv.state] || libtv.state || '未连接'}
              </span>
            </div>
            {libtv.expires_in ? <div className="kv-row"><span>剩余</span><b>{Math.ceil(libtv.expires_in / 60)} 分钟</b></div> : null}
            <div className="quick" style={{ marginTop: 9 }}>
              {libtvOk
                ? <button className="btn small danger" onClick={logout} disabled={busy === 'logout'}>退出</button>
                : <button className="btn small primary" onClick={login} disabled={busy === 'login'}>
                    {busy === 'login' ? '等待授权…' : '去连接'}
                  </button>}
              <button className="btn small" onClick={recheck} disabled={busy === 'recheck'}>重新检测</button>
            </div>
            <p className="hint">点「去连接」→ 浏览器授权 → 回来自动变绿。</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>配置指引</b>
              <span className="grow" />
              {onOpenHelp && (
                <button className="link-btn" onClick={onOpenHelp}>怎么用 Trestle →</button>
              )}
            </div>
            <p className="hint">1. 装 Agent（Claude Code / WorkBuddy）<br />
              2. 内置导演 skill（随 Trestle 更新，不用安装）<br />
              3. LibTV 授权<br />
              4. 点「开工检查」确认三项都绿。</p>
          </section>

          <section className="ssec">
            <div className="ssec-head"><b>外观</b></div>
            <label className="field"><span>主题</span>
              <select className="input" value={theme || 'dark'}
                onChange={(e) => setTheme && setTheme(e.target.value)}>
                <option value="auto">自动</option>
                <option value="dark">深色</option>
                <option value="light">浅色</option>
                <option value="warm">暖色</option>
              </select>
            </label>
          </section>
        </div>

        <div className="drawer-foot">
          <button className="btn" onClick={onClose}>完成</button>
        </div>
      </aside>

      {gateOpen && (
        <Modal
          title="开工检查"
          desc="Agent、导演 skill、LibTV 都就绪，才能开始这一集。"
          onClose={() => setGateOpen(false)}
          footer={<>
            <button className="btn" onClick={() => setGateOpen(false)}>关闭</button>
            <button className="btn" onClick={openGate} disabled={busy === 'gate'}>重新检查</button>
            <button className="btn primary" onClick={startProject}
              disabled={busy === 'gate-start' || !(gate && gate.ready)}>
              {busy === 'gate-start' ? '建设中…' : '开始项目'}
            </button>
          </>}
        >
          {!gate && <p className="empty">检查中…</p>}
          {gate && (
            <>
              <div className={'gate-state ' + (gate.ready ? 'ok' : 'warn')}>
                {gate.ready ? '已就绪' : '还缺：' + (gate.missing || []).join('、')}
              </div>
              <div className="gate-list">
                <div><b>Agent</b><span>{gate.agent && gate.agent.ready ? '已连接 · ' + gate.agent.name : '未连接'}</span></div>
                <div><b>导演 skill</b><span>{gate.skill && gate.skill.ready ? '已就绪 · ' + (gate.skill.version || '') : '未找到'}</span></div>
                <div><b>LibTV</b><span>{gate.libtv && gate.libtv.ready ? '已连接' : '待授权'}</span></div>
                <div><b>ffmpeg</b><span>{gate.env && gate.env.ffmpeg && gate.env.ffmpeg.ready
                  ? ((gate.env.ffmpeg.bundled ? '内置 ' : '系统 ') + (gate.env.ffmpeg.version || '可用'))
                  : '未找到'}</span></div>
                <div><b>磁盘</b><span>{(gate.env && gate.env.disk && gate.env.disk.free_gb) != null
                  ? '剩 ' + gate.env.disk.free_gb + ' GB' : '读不到'}</span></div>
                <div><b>Edge</b><span>{gate.env && gate.env.edge && gate.env.edge.ready ? '已找到' : '未找到'}</span></div>
                <div><b>这一集</b><span>{(gate.project && gate.project.scenes) || 0} 场 · {(gate.project && gate.project.storyboards) || 0} 分镜</span></div>
              </div>
              {gate.build && (
                <p className="hint">LibTV 结构：项目 {gate.build.projects || 0} · 文件夹 {gate.build.folders || 0} · 工作流组 {gate.build.groups || 0}</p>
              )}
              <div className="quick" style={{ marginTop: 10 }}>
                <button className="btn small" onClick={ensureAll} disabled={busy === 'ensure-all'}>
                  {busy === 'ensure-all' ? '建设中…' : '建设全部场'}
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  )
}
