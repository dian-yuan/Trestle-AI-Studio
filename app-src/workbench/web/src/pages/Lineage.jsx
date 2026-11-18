import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { get, qs } from '../api'
import { useApp } from '../App'
import { statusLabel } from '../labels'

export default function Lineage() {
  const { snap } = useApp()
  const ideas = (snap && snap.script && snap.script.ideas) || []
  const [details, setDetails] = useState({})

  useEffect(() => {
    let alive = true
    ;(async () => {
      const out = {}
      await Promise.all(ideas.map(async (i) => {
        try {
          const d = await get('/api/idea/detail' + qs({ idea: i.path }))
          if (d && d.ok) out[i.path] = d
        } catch (e) { /* ignore */ }
      }))
      if (alive) setDetails(out)
    })()
    return () => { alive = false }
  }, [ideas])

  const byId = {}
  ;(snap && snap.assets || []).forEach((a) => { byId[a.id] = a })

  return (
    <section className="page">
      <div className="backbar"><Link className="backbtn" to="/">← 创作思路</Link></div>
      <div className="page-head">
        <div>
          <h1>关系图</h1>
          <p>一个创作思路衍生出的剧本、角色、场景和画风；资产可以在多个思路之间复用。</p>
        </div>
      </div>
      <div className="lineage-list">
        {ideas.map((i) => {
          const d = details[i.path] || { scripts: [], assets: {} }
          const a = d.assets || {}
          return (
            <div className="glass-card lineage-card" key={i.path}>
              <div className="lineage-head">
                <Link to={'/outline?idea=' + encodeURIComponent(i.path)}>
                  <b>《{i.title || i.name}》</b>
                </Link>
                <span className={'tag ' + (i.status === 'ready' ? 'ok' : 'warn')}>{statusLabel(i.status)}</span>
                <span className="grow" />
                <span className="faint">{(d.scripts || []).length} 剧本 · {(a.characters || []).length} 角色 · {(a.scenes || []).length} 场景</span>
              </div>
              <div className="lineage-branches">
                <div>
                  <span className="lineage-label">剧本</span>
                  {(d.scripts || []).map((s) => (
                    <Link className="link-chip" key={s.unit}
                      to={'/script?unit=' + encodeURIComponent(s.unit) + '&idea=' + encodeURIComponent(i.path)}>
                      {s.unit} · {s.title}
                    </Link>
                  ))}
                  {!(d.scripts || []).length && <span className="faint">—</span>}
                </div>
                <div>
                  <span className="lineage-label">角色</span>
                  {(a.characters || []).map((id) => (
                    <Link className="link-chip" key={id} to={'/assets?asset=' + encodeURIComponent(id)}>
                      {byId[id] ? byId[id].name : id}
                    </Link>
                  ))}
                  {!(a.characters || []).length && <span className="faint">—</span>}
                </div>
                <div>
                  <span className="lineage-label">场景 / 道具 / 画风</span>
                  {(a.scenes || []).concat(a.props || [], a.styles || []).map((id) => (
                    <Link className="link-chip" key={id} to={'/assets?asset=' + encodeURIComponent(id)}>
                      {byId[id] ? byId[id].name : id}
                    </Link>
                  ))}
                  {!(a.scenes || []).length && !(a.props || []).length && !(a.styles || []).length && <span className="faint">—</span>}
                </div>
              </div>
            </div>
          )
        })}
        {!ideas.length && <p className="empty">还没有创作思路。</p>}
      </div>
    </section>
  )
}
