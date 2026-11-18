import React, { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { post, exportHref } from '../api'
import { useApp } from '../App'
import Markdown from '../components/Markdown'
import AssetMini from '../components/AssetMini'
import StyleCard from '../components/StyleCard'
import { LinkPicker } from '../components/Modal'
import { statusLabel } from '../labels'

export default function Script() {
  const { snap, refresh, wb, notify, unit } = useApp()
  const [params] = useSearchParams()
  const uid = params.get('unit') || (snap && snap.meta && snap.meta.unit) || unit
  const official = snap && snap.script && snap.script.official
  const [picker, setPicker] = useState(null)

  const scenes = (snap && snap.scenes) || []
  const unitAssets = useMemo(
    () => ((snap && snap.assets) || []).filter((a) => a.in_unit),
    [snap]
  )
  const byType = useMemo(() => {
    const m = {}
    unitAssets.forEach((a) => { (m[a.type] = m[a.type] || []).push(a) })
    return m
  }, [unitAssets])
  const usedStyle = (a) => a.in_unit || (a.used_by || []).includes(uid)
  const styles = useMemo(() => {
    const all = ((snap && snap.assets) || []).filter((a) => a.type === 'style')
    return all.slice().sort((a, b) => Number(usedStyle(b)) - Number(usedStyle(a))
      || String(a.name).localeCompare(String(b.name), 'zh'))
  }, [snap, uid])

  if (!official || !official.path) {
    return (
      <section className="page">
        <div className="backbar"><Link className="backbtn" to="/">← 创作思路</Link></div>
        <p className="empty">还没有剧本。先回创作思路，用 WorkBuddy 写一集。</p>
      </section>
    )
  }

  const title = official.title || '剧本'
  const scriptCtx = '剧本《' + title + '》· ' + official.path
  const back = official.source_idea
    ? '/outline?idea=' + encodeURIComponent(official.source_idea)
    : '/'

  async function saveLink(ids) {
    const r = await post('/api/unit/assets', { unit: uid, assets: ids })
    setPicker(null)
    if (!r.ok) { notify('保存关联失败：' + (r.error || ''), 'error'); return }
    notify('关联已保存', 'ok')
    refresh()
  }

  return (
    <section className="page script-page">
      <div className="backbar">
        <Link className="backbtn" to={back}>{official.source_idea ? '← 大纲' : '← 创作思路'}</Link>
      </div>
      <div className="page-head doc-head">
        <div>
          <h1>{title}</h1>
          <p>{uid} ｜ {statusLabel(official.status, '')} ｜ {official.path}</p>
        </div>
        <div className="acts">
          <span className={'tag ' + (official.final ? 'ok' : 'warn')}>
            {statusLabel(official.status || (official.final ? 'final' : 'draft'))}
          </span>
          <a className="btn" href={exportHref('/api/export', { unit: uid })}>导出剧本</a>
          <button className="btn" onClick={() => wb('完善剧本', '剧本《' + title + '》')}>用 WorkBuddy 完善剧本</button>
          <Link className="btn primary" to={'/review?unit=' + encodeURIComponent(uid)}>去编排 →</Link>
        </div>
      </div>

      <div className="split">
        <div className="main-col scroll-col">
          <div className="glass-card doc-card">
            <Markdown text={official.text || ''} data-sel-src data-sel-ctx={scriptCtx} />
          </div>
        </div>
        <aside className="side-col">
          <div className="side-sticky">
            <div className="glass-card side-card">
              <div className="sec-head">
                <b>场次 <i>{scenes.length}</i></b>
                <span className="grow" />
                {!scenes.length && (
                  <Link className="btn small primary" to={'/review?unit=' + encodeURIComponent(uid)}>去编排</Link>
                )}
                <button className="btn small" onClick={() => wb('改这场', '剧本《' + title + '》')}>WorkBuddy 改这场</button>
              </div>
              {scenes.map((sc) => (
                <Link className="asset-mini" key={sc.id}
                  to={'/review?unit=' + encodeURIComponent(uid) + '&scene=' + encodeURIComponent(sc.id)}>
                  <b>{sc.id} · {sc.name}</b>
                  <span>{(sc.shots || []).length} 镜 · {Number(sc.seconds || 0).toFixed(1)}s</span>
                </Link>
              ))}
              {!scenes.length && <p className="empty">还没有场次。</p>}
            </div>

            <div className="glass-card side-card">
              <div className="sec-head">
                <b>艺术风格设计 <i>{styles.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => setPicker('style')}>＋ 画风</button>
              </div>
              <div className="art-stats">
                <span><b>{(byType.scenes || []).length}</b>场景资产</span>
                <span><b>{(byType.props || []).length}</b>道具资产</span>
                <span><b>{styles.length}</b>画风</span>
              </div>
              {styles.map((a) => <StyleCard key={a.id} asset={a} inUnit={usedStyle(a)} />)}
              {!styles.length && (
                <p className="empty">
                  还没有画风设定。novel-art 会一次产出场景锚点、道具状态和一套统一画风。
                </p>
              )}
              <div className="quick" style={{ marginTop: 8 }}>
                <button className="btn" onClick={() => wb('做美术设定', scriptCtx)}>
                  用 WorkBuddy 做美术设定
                </button>
              </div>
              <p className="hint">
                场景锚点 3–5 个、空景无人、道具白底无手、全片共用一组画风词。
              </p>
            </div>

            <div className="glass-card side-card">
              <div className="sec-head">
                <b>本剧本使用 <i>{unitAssets.length}</i></b>
                <span className="grow" />
                <button className="btn small" onClick={() => setPicker('characters')}>＋ 角色</button>
                <button className="btn small" onClick={() => setPicker('scenes')}>＋ 场景</button>
                <button className="btn small" onClick={() => setPicker('props')}>＋ 道具</button>
              </div>
              {unitAssets.map((a) => <AssetMini key={a.id} asset={a} />)}
              {!unitAssets.length && <p className="empty">还没有手动关联资产。</p>}
            </div>

            <div className="glass-card side-card">
              <div className="sec-head"><b>从剧本构建资产</b></div>
              <div className="quick">
                <button className="btn" onClick={() => wb('从剧本构建角色', scriptCtx)}>构建角色</button>
                <button className="btn" onClick={() => wb('从剧本构建装造', scriptCtx)}>构建装造</button>
                <button className="btn" onClick={() => wb('从剧本构建场景', scriptCtx)}>构建场景</button>
              </div>
              <p className="hint">也可以选中剧本里的一段，用浮动条按选中内容构建。角色建好后到资产库看构建状态。</p>
            </div>

            <div className="glass-card side-card">
              <div className="sec-head"><b>导出</b></div>
              <div className="quick">
                <a className="btn" href={exportHref('/api/export', { unit: uid })}>导出剧本 .md</a>
                <a className="btn" href={exportHref('/api/export/storyboard', { unit: uid })}>导出分镜 .md</a>
                <a className="btn" href={exportHref('/api/export/assets', { unit: uid })}>导出资产清单 .md</a>
                <a className="btn" href={exportHref('/api/export/prompts', { unit: uid })}>导出提示词 .json</a>
              </div>
            </div>
          </div>
        </aside>
      </div>

      <LinkPicker
        open={!!picker}
        type={picker || 'characters'}
        assets={snap.assets || []}
        current={unitAssets.map((a) => a.id)}
        onClose={() => setPicker(null)}
        onSave={saveLink}
      />
    </section>
  )
}
