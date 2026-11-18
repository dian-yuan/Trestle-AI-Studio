import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { get, post } from '../api'
import { useApp } from '../App'

const rawHref = (rel) => '/raw/' + String(rel || '').replace(/\\/g, '/')
  .split('/').map(encodeURIComponent).join('/')
const isVideo = (f) => /\.(mp4|webm|mov|m4v)$/i.test(String(f || ''))
const fmtSize = (n) => {
  const x = Number(n || 0)
  if (!x) return ''
  if (x > 1024 * 1024) return (x / 1024 / 1024).toFixed(1) + ' MB'
  return Math.max(1, Math.round(x / 1024)) + ' KB'
}

export default function Deliver() {
  const { snap, notify, refresh } = useApp()
  const unit = (snap && snap.meta && snap.meta.unit) || ''
  const [tree, setTree] = useState(null)
  const [sel, setSel] = useState({ kind: 'all', id: '', shot: '' })
  const [play, setPlay] = useState(null)
  const [busy, setBusy] = useState('')

  const load = async () => {
    try {
      const r = await get('/api/deliver/tree?' + new URLSearchParams({ unit }))
      if (r && r.ok) setTree(r)
    } catch (e) { /* ignore */ }
  }

  useEffect(() => {
    load()
  }, [unit, snap && snap.versions && snap.versions.storyboards])

  const items = useMemo(() => {
    if (!tree) return []
    const all = (tree.scenes || []).flatMap((sc) =>
      (sc.segments || []).flatMap((sg) =>
        (sg.videos || []).map((v) => ({ ...v, scene: sc.id, seg: sg.id }))))
    if (sel.kind === 'scene') return all.filter((v) => v.scene === sel.id)
    if (sel.kind === 'segment') return all.filter((v) => v.seg === sel.id)
    if (sel.kind === 'shot') {
      const exact = all.filter((v) => v.seg === sel.id && v.shot === sel.shot)
      return exact.length ? exact : all.filter((v) => v.seg === sel.id)
    }
    if (sel.kind === 'film') {
      return [{
        name: '全片', film: true, file: tree.film.file,
        poster: tree.film.poster,
        ok: !!(tree.film.ready || tree.film.exists), size: tree.film.size,
      }]
    }
    if (sel.kind === 'pack') return (tree.pack || []).map((f) => ({ ...f, pack: true }))
    return all
  }, [tree, sel])

  const title = useMemo(() => {
    if (!tree) return ''
    if (sel.kind === 'all') return tree.title
    if (sel.kind === 'film') return '全片'
    if (sel.kind === 'pack') return '交付包'
    if (sel.kind === 'scene') {
      return (tree.scenes.find((x) => x.id === sel.id) || {}).name || ('场 ' + sel.id)
    }
    if (sel.kind === 'segment') {
      for (const sc of tree.scenes) {
        const sg = (sc.segments || []).find((x) => x.id === sel.id)
        if (sg) return sc.id + ' · ' + (sg.name || sg.id)
      }
    }
    if (sel.kind === 'shot') return '镜 ' + sel.shot
    return tree.title
  }, [tree, sel])

  async function openFolder(file) {
    if (!file) return
    const r = await post('/api/deliver/open', { path: file })
    if (!r.ok) notify('打不开文件夹：' + (r.error || ''), 'error')
    else notify('已在资源管理器中定位', 'ok')
  }

  function playItem(it) {
    if (it.film) {
      setPlay({ name: '全片', src: '/api/deliver/film?' + new URLSearchParams({ unit }) })
      return
    }
    if (isVideo(it.file)) {
      setPlay({ name: it.label || it.name || it.file, src: rawHref(it.file), file: it.file })
    } else {
      openFolder(it.file)
    }
  }

  async function exportPack() {
    setBusy('export')
    try {
      const r = await post('/api/deliver/export', { unit, allow_missing: true })
      if (!r.ok) { notify('导出失败：' + (r.error || ''), 'error'); return }
      const notes = []
      if ((r.missing || []).length) notes.push('缺节点 ' + r.missing.join('、'))
      if ((r.warnings || []).length) notes.push(...r.warnings)
      if (notes.length) {
        notify('已导出交付包；' + notes.join('；'), 'warn')
      } else {
        notify('已导出交付包：分镜 / 分段 / 全片 / 视频介绍', 'ok')
      }
      await refresh()
      await load()
      window.location.href = r.download
    } finally {
      setBusy('')
    }
  }

  if (!tree) {
    return <section className="page deliver-page"><p className="empty">正在读交付信息…</p></section>
  }

  const on = (kind, id, shot) => sel.kind === kind
    && (id === undefined || sel.id === id)
    && (shot === undefined || sel.shot === shot)

  return (
    <section className="page deliver-page">
      <div className="page-head">
        <div>
          <h1>交付</h1>
          <p>左边是项目结构，点哪一层，右边就列出哪一层的视频。</p>
        </div>
        <div className="acts">
          <Link className="btn" to={'/review?unit=' + encodeURIComponent(unit)}>去编排</Link>
          <button className="btn primary" disabled={!!busy} onClick={exportPack}>
            {busy === 'export' ? '正在导出…' : '导出交付包'}
          </button>
        </div>
      </div>

      <div className="dv-layout">
        <aside className="glass-card dv-tree">
          <div className="sec-head"><b>项目结构</b><span className="faint">{tree.videos} 条视频</span></div>
          <div className={'dv-row root' + (on('all') ? ' on' : '')}
            onClick={() => setSel({ kind: 'all', id: '', shot: '' })}>
            {tree.title || '当前项目'}
          </div>
          {tree.scenes.map((sc) => (
            <React.Fragment key={sc.id}>
              <div className={'dv-row l1' + (on('scene', sc.id) ? ' on' : '')}
                onClick={() => setSel({ kind: 'scene', id: sc.id, shot: '' })}>
                场 {sc.id} · {sc.name}
              </div>
              {(sc.segments || []).map((sg) => (
                <React.Fragment key={sg.id}>
                  <div className={'dv-row l2' + (on('segment', sg.id) ? ' on' : '')}
                    onClick={() => setSel({ kind: 'segment', id: sg.id, shot: '' })}>
                    段 {sg.id}
                  </div>
                  {(sg.shots || []).map((shot) => (
                    <div key={shot}
                      className={'dv-row l3' + (on('shot', sg.id, shot) ? ' on' : '')}
                      onClick={() => setSel({ kind: 'shot', id: sg.id, shot })}>
                      镜 {shot}
                    </div>
                  ))}
                </React.Fragment>
              ))}
            </React.Fragment>
          ))}
          <div className={'dv-row l1' + (on('film') ? ' on' : '')}
            onClick={() => setSel({ kind: 'film', id: '', shot: '' })}>全片</div>
          {!!tree.pack.length && (
            <div className={'dv-row l1' + (on('pack') ? ' on' : '')}
              onClick={() => setSel({ kind: 'pack', id: '', shot: '' })}>
              交付包
            </div>
          )}
        </aside>

        <div className="glass-card dv-main">
          <div className="sec-head">
            <b>{title}</b>
            <span className="faint">{items.length} 项</span>
            {sel.kind === 'pack' && tree.version.at && (
              <span className="faint">上次导出 {tree.version.at}</span>
            )}
            {sel.kind === 'pack'
              && !tree.pack.some((f) => f.name.startsWith('segments/')) && (
                <span className="faint">旧包没有分段 / 全片；重新导出会生成</span>
              )}
            <span className="grow" />
            {sel.kind === 'film' && (
              <a className="btn small" href={'/api/deliver/film?unit=' + encodeURIComponent(unit) + '&download=1'}>
                下载全片
              </a>
            )}
          </div>

          <div className="dv-list">
            {items.map((it, i) => (
              <div className="dv-video" key={(it.file || '') + i}>
                <div className={'dv-poster' + (it.film ? ' film' : '')
                  + (it.poster ? ' has-img' : '')}>
                  {it.poster && (
                    <img src={it.poster} alt="" loading="lazy"
                      onError={(e) => {
                        const box = e.currentTarget.parentElement
                        if (box) box.classList.remove('has-img')
                        e.currentTarget.remove()
                      }} />
                  )}
                  <span>{it.film ? '全片' : (it.short || it.label || it.name || '').slice(0, 8)}</span>
                </div>
                <div className="dv-info">
                  <b title={it.name || ''}>{it.label || it.name || it.file}</b>
                  <span>
                    {it.film ? '拼接成片' : (it.pack ? '交付包文件'
                      : (it.rev ? '当前版本 v' + it.rev : '当前版本'))}
                    {it.size ? ' · ' + fmtSize(it.size) : ''}
                  </span>
                </div>
                <div className="dv-actions">
                  {!it.pack || isVideo(it.file) || it.film ? (
                    <button className="btn small" onClick={() => playItem(it)}>播放</button>
                  ) : null}
                  <button className="btn small" onClick={() => openFolder(it.file)}>在文件夹中显示</button>
                </div>
              </div>
            ))}
            {!items.length && (
              <div className="onb-empty-inline">
                <p className="empty">
                  {sel.kind === 'film'
                    ? '还没有成片。先把各段在 LibTV 生成完，回来同步，再来拼全片。'
                    : '这一层还没有视频。先回编排页布线，在 LibTV 生成，再回 Trestle 同步。'}
                </p>
                <Link className="btn small"
                  to={'/review?unit=' + encodeURIComponent(unit)}>去编排</Link>
              </div>
            )}
          </div>
        </div>
      </div>

      {play && (
        <div className="modal-scrim on" onClick={(e) => { if (e.target === e.currentTarget) setPlay(null) }}>
          <div className="modal dv-player">
            <div className="modal-head">
              <h2>{play.name}</h2>
              <button className="icon-btn" onClick={() => setPlay(null)}>×</button>
            </div>
            <video src={play.src} controls autoPlay playsInline />
            <div className="modal-foot">
              <button className="btn" onClick={() => setPlay(null)}>关闭</button>
              {play.file && (
                <button className="btn" onClick={() => openFolder(play.file)}>在文件夹中显示</button>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
