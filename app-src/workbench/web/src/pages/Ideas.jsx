import React, { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { post } from '../api'
import { useApp } from '../App'
import Markdown from '../components/Markdown'
import ConfirmDialog from '../components/ConfirmDialog'
import { Modal } from '../components/Modal'
import { statusLabel, levelLabel } from '../labels'

export default function Ideas() {
  const { snap, refresh, wb, notify } = useApp()
  const [params] = useSearchParams()
  const nav = useNavigate()
  const ideas = (snap && snap.script && snap.script.ideas) || []
  const cur = ideas.find((i) => i.path === params.get('idea')) || ideas[0]
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [editing, setEditing] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [toDelete, setToDelete] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [newTitle, setNewTitle] = useState('')

  useEffect(() => {
    if (cur) {
      setText(cur.text || '')
      setTitle(cur.title || cur.name || '')
      setEditing(false)
      setDirty(false)
    }
  }, [cur && cur.path])

  if (!cur) {
    return (
      <section className="page ideas-page">
        <div className="page-head">
          <div>
            <h1>创意 / 大纲</h1>
            <p>先有创意，再有剧本。可以用 WorkBuddy 聊出一篇，或从大纲页新建。</p>
          </div>
        </div>
        <div className="glass-card onb-empty">
          <h2>还没有创意</h2>
          <p>创意是内容的起点。先让 WorkBuddy 问你 3 个问题，或者从大纲页新建一篇。</p>
          <div className="quick">
            <button className="btn primary"
              onClick={() => wb('延申思路', '项目 ' + ((snap && snap.meta && snap.meta.name) || ''))}>
              打开 WorkBuddy 聊创意
            </button>
            <Link className="btn" to="/outline">去大纲</Link>
          </div>
        </div>
      </section>
    )
  }

  async function save() {
    const r = await post('/api/idea/save', { path: cur.path, title, text })
    if (!r.ok) { notify('保存失败：' + (r.error || ''), 'error'); return }
    setDirty(false)
    notify('草稿已保存', 'ok')
    refresh()
  }

  async function del() {
    setToDelete(false)
    const r = await post('/api/idea/delete', { path: cur.path })
    if (!r.ok) { notify('删除失败：' + (r.error || ''), 'error'); return }
    notify('已删除', 'ok')
    nav('/')
  }

  async function newIdea() {
    const t = newTitle.trim()
    if (!t) { notify('先起一个名字', 'warn'); return }
    const r = await post('/api/idea/new', { title: t })
    if (!r.ok) { notify('新建失败：' + (r.error || ''), 'error'); return }
    setNewOpen(false)
    setNewTitle('')
    notify('已新建', 'ok')
    refresh()
    nav('/ideas?idea=' + encodeURIComponent(r.path))
  }

  async function openWb(kind) {
    const api = kind === 'script' ? '/api/idea/script' : '/api/idea/open'
    const r = await post(api, { path: cur.path })
    if (!r.ok) { notify('打不开：' + (r.error || ''), 'error'); return }
    if (r.url) {
      window.location.href = r.url
      notify(kind === 'script' ? '已在 WorkBuddy 打开：用这个创意写剧本' : '已在 WorkBuddy 打开', 'ok')
      setTimeout(async () => {
        try {
          await navigator.clipboard.writeText(r.short_prompt || r.prompt || '')
        } catch (e) { /* ignore */ }
      }, 1600)
    } else {
      try {
        await navigator.clipboard.writeText(r.short_prompt || r.prompt || '')
      } catch (e) { /* ignore */ }
      notify('已复制迭代提示词，粘贴给 Agent 即可', 'ok')
    }
  }

  return (
    <section className="page ideas-page">
      <div className="backbar">
        <Link className="backbtn" to={'/outline?idea=' + encodeURIComponent(cur.path)}>← 大纲</Link>
      </div>

      <div className="ideas-layout">
        <aside className="glass-card ideas-list">
          <div className="sec-head"><b>创作思路 <i>{ideas.length}</i></b><span className="grow" /></div>
          <button className="btn small" onClick={() => setNewOpen(true)}>＋ 新建</button>
          <div className="idea-rows">
            {ideas.map((i) => (
              <Link key={i.path} className={'idea-row' + (i.path === cur.path ? ' on' : '')}
                to={'/ideas?idea=' + encodeURIComponent(i.path)}>
                <b>{i.title || i.name}</b>
                <span>{statusLabel(i.status)} · {levelLabel(i.level)}</span>
              </Link>
            ))}
          </div>
        </aside>

        <div className="ideas-main">
          <div className="page-head">
            <div>
              <h1>编辑大纲</h1>
              <p>{cur.path} · 非定稿</p>
            </div>
            <div className="acts">
              <input className="input title-input" value={title}
                onChange={(e) => { setTitle(e.target.value); setDirty(true) }} />
              <button className="btn" onClick={() => setEditing((v) => !v)}>
                {editing ? '预览' : '编辑'}
              </button>
              <button className="btn primary" onClick={save} disabled={!dirty}>保存草稿</button>
              <button className="btn danger" onClick={() => setToDelete(true)}>删除</button>
              <button className="btn" onClick={() => openWb('script')}>用这个创意写剧本</button>
              <button className="btn" onClick={() => openWb('open')}>打开 WorkBuddy 继续</button>
            </div>
          </div>

          <div className="glass-card editor-card">
            {editing ? (
              <textarea className="editor" value={text}
                onChange={(e) => { setText(e.target.value); setDirty(true) }} spellCheck={false} />
            ) : (
              <Markdown text={text} />
            )}
          </div>
        </div>
      </div>

      {newOpen && (
        <Modal
          title="新建创作思路"
          desc="先起个名字；正文可以下一步用 WorkBuddy 聊，也可以直接写。"
          onClose={() => setNewOpen(false)}
          footer={<>
            <button className="btn" onClick={() => setNewOpen(false)}>取消</button>
            <button className="btn primary" onClick={newIdea}>创建</button>
          </>}
        >
          <input className="input" autoFocus placeholder="例如：零一·轻松版"
            value={newTitle} onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') newIdea() }} />
        </Modal>
      )}

      <ConfirmDialog
        open={toDelete}
        danger
        title="删除这篇创意？"
        desc={'《' + (cur.title || cur.name) + '》会移到 .workbuddy/trash，需要时还能找回。'}
        confirmText="删除"
        cancelText="取消"
        onConfirm={del}
        onCancel={() => setToDelete(false)}
      />
    </section>
  )
}
