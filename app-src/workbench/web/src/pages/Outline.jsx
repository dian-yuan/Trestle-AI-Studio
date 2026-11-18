import React, { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { get, post, qs, exportHref } from '../api'
import { useApp } from '../App'
import Markdown from '../components/Markdown'
import AssetMini from '../components/AssetMini'
import { LinkPicker, Modal } from '../components/Modal'
import ConfirmDialog from '../components/ConfirmDialog'
import { statusLabel, levelLabel } from '../labels'

export default function Outline() {
  const { snap, refresh, wb, notify } = useApp()
  const [params] = useSearchParams()
  const nav = useNavigate()
  const ideas = (snap && snap.script && snap.script.ideas) || []
  const ideaPath = params.get('idea') || (ideas[0] && ideas[0].path) || ''
  const [detail, setDetail] = useState(null)
  const [picker, setPicker] = useState(null)
  const [toDelete, setToDelete] = useState(false)
  const [newScript, setNewScript] = useState(false)
  const [scriptTitle, setScriptTitle] = useState('')

  useEffect(() => {
    let alive = true
    if (!ideaPath) { setDetail(null); return }
    get('/api/idea/detail' + qs({ idea: ideaPath })).then((d) => {
      if (alive) setDetail(d && d.ok ? d : null)
    }).catch(() => { if (alive) setDetail(null) })
    return () => { alive = false }
  }, [ideaPath])

  const idea = detail && detail.idea
  const byId = useMemo(() => {
    const m = {}
    ;(snap && snap.assets || []).forEach((a) => { m[a.id] = a })
    return m
  }, [snap])
  const ids = (detail && detail.assets) || {}
  const chars = (ids.characters || []).map((id) => byId[id]).filter(Boolean)
  const scenes = (ids.scenes || []).map((id) => byId[id]).filter(Boolean)
  const props = (ids.props || []).map((id) => byId[id]).filter(Boolean)
  const styles = (ids.styles || []).map((id) => byId[id]).filter(Boolean)
  const scripts = (detail && detail.scripts) || []

  async function saveLink(idsPicked) {
    const r = await post('/api/idea/assets', { path: ideaPath, assets: idsPicked })
    setPicker(null)
    if (!r.ok) { notify('保存关联失败：' + (r.error || ''), 'error'); return }
    notify('关联已保存', 'ok')
    const d = await get('/api/idea/detail' + qs({ idea: ideaPath }))
    if (d && d.ok) setDetail(d)
    refresh()
  }

  async function createScript() {
    const r = await post('/api/idea/script/new', {
      idea: ideaPath, title: scriptTitle.trim(),
    })
    if (!r.ok) { notify('新建失败：' + (r.error || ''), 'error'); return }
    setNewScript(false)
    setScriptTitle('')
    notify('已新建这一集', 'ok')
    refresh()
    nav('/script?unit=' + encodeURIComponent(r.unit))
  }

  async function del() {
    if (!idea) return
    setToDelete(false)
    const r = await post('/api/idea/delete', { path: ideaPath })
    if (!r.ok) { notify('删除失败：' + (r.error || ''), 'error'); return }
    notify('已删除', 'ok')
    nav('/')
  }

  if (!idea) {
    return (
      <section className="page">
        <div className="backbar"><Link className="backbtn" to="/">← 创作思路</Link></div>
        <p className="empty">还没有创作思路。<Link to="/">回创作思路</Link></p>
      </section>
    )
  }

  const ctx = '《' + (idea.title || idea.name) + '》'

  return (
    <section className="page outline-page">
      <div className="backbar"><Link className="backbtn" to="/">← 创作思路</Link></div>
      <div className="page-head doc-head">
        <div>
          <h1>《{idea.title || idea.name}》</h1>
          <p>{idea.excerpt || ''}</p>
        </div>
        <div className="acts">
          <span className={'tag ' + (idea.status === 'ready' ? 'ok' : 'warn')}>
            {statusLabel(idea.status)} · {levelLabel(idea.level)}
          </span>
          <Link className="btn" to={'/ideas?idea=' + encodeURIComponent(ideaPath)}>编辑大纲</Link>
          <button className="btn danger" onClick={() => setToDelete(true)}>删除</button>
          <a className="btn" href={exportHref('/api/export/outline', { idea: ideaPath })}>导出大纲</a>
          <button className="btn" onClick={() => wb('延申思路', ctx, null,
            { targets: [{ label: '文件', value: ideaPath }] })}>延申思路</button>
          <button className="btn primary" onClick={() => wb('完善大纲', ctx, null,
            { targets: [{ label: '文件', value: ideaPath }] })}>
            打开 WorkBuddy 完善大纲
          </button>
        </div>
      </div>

      <div className="split">
        <div className="main-col scroll-col">
          <div className="glass-card doc-card">
            <Markdown text={idea.text || ''} />
          </div>
        </div>
        <aside className="side-col">
          <div className="side-sticky">
            <div className="glass-card side-card">
              <div className="sec-head">
                <b>剧本 <i>{scripts.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => {
                  setScriptTitle(title + ' · 新一集')
                  setNewScript(true)
                }}>＋ 新建</button>
                <button className="btn small" onClick={() => wb('写剧本', ctx)}>打开 WorkBuddy 写剧本</button>
              </div>
              {scripts.map((s) => (
                <Link className="script-row" key={s.unit}
                  to={'/script?unit=' + encodeURIComponent(s.unit) + '&idea=' + encodeURIComponent(ideaPath)}>
                  <span className="no">{s.unit}</span>
                  <div>
                    <b>{s.title}</b>
                    <div className="sub">{s.duration || ''} · {statusLabel(s.status)}</div>
                    <div className="desc">{s.desc || ''}</div>
                  </div>
                  <span className={'tag ' + (s.status === 'final' ? 'ok' : 'warn')}>{statusLabel(s.status)}</span>
                </Link>
              ))}
              {!scripts.length && <p className="empty">还没有剧本。用 WorkBuddy 从这个思路写一集。</p>}
            </div>

            <div className="glass-card side-card">
              <div className="sec-head">
                <b>关联角色 <i>{chars.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => setPicker('characters')}>＋ 关联</button>
                <button className="btn small" onClick={() => wb('完善角色', ctx)}>打开 WorkBuddy</button>
              </div>
              {chars.map((a) => <AssetMini key={a.id} asset={a} desc />)}
              {!chars.length && <p className="empty">还没有关联角色。</p>}
            </div>

            <div className="glass-card side-card">
              <div className="sec-head">
                <b>关联场景 <i>{scenes.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => setPicker('scenes')}>＋ 关联</button>
                <button className="btn small" onClick={() => wb('完善场景', ctx)}>打开 WorkBuddy</button>
              </div>
              {scenes.map((a) => <AssetMini key={a.id} asset={a} desc />)}
              {!scenes.length && <p className="empty">还没有关联场景。</p>}
            </div>

            <div className="glass-card side-card">
              <div className="sec-head">
                <b>道具 / 画风 <i>{props.length + styles.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => setPicker('props')}>＋ 道具</button>
                <button className="btn small" onClick={() => setPicker('style')}>＋ 画风</button>
              </div>
              <div className="chips">
                {props.concat(styles).map((a) => <span className="chip" key={a.id}>{a.name}</span>)}
                {!props.length && !styles.length && <span className="faint">—</span>}
              </div>
            </div>
          </div>
        </aside>
      </div>

      <LinkPicker
        open={!!picker}
        type={picker || 'characters'}
        assets={snap.assets || []}
        current={picker === 'characters' ? ids.characters : picker === 'scenes' ? ids.scenes
          : picker === 'props' ? ids.props : ids.styles}
        onClose={() => setPicker(null)}
        onSave={saveLink}
      />

      {newScript && (
        <Modal
          title="新建这一集"
          desc="会在 units/ 下建一集空壳，并把 source_idea 记到剧本里；之后用 WorkBuddy 写正文。"
          onClose={() => setNewScript(false)}
          footer={<>
            <button className="btn" onClick={() => setNewScript(false)}>取消</button>
            <button className="btn primary" onClick={createScript}>创建</button>
          </>}
        >
          <label className="field"><span>剧本标题</span>
            <input className="input" autoFocus value={scriptTitle}
              onChange={(e) => setScriptTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') createScript() }} />
          </label>
        </Modal>
      )}

      <ConfirmDialog
        open={toDelete}
        danger
        title="删除这篇创意？"
        desc={'《' + (idea.title || idea.name) + '》会移到 .workbuddy/trash，需要时还能找回。'}
        confirmText="删除"
        cancelText="取消"
        onConfirm={del}
        onCancel={() => setToDelete(false)}
      />
    </section>
  )
}
