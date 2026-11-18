import React from 'react'
import { Link } from 'react-router-dom'

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i

function parseBible(text) {
  const rows = []
  String(text || '').split(/\r?\n/).forEach((line) => {
    const m = line.match(/^\s*[-*]\s*([^：:]{1,12})[：:]\s*(.+)$/)
    if (m) rows.push({ k: m[1].trim(), v: m[2].trim() })
  })
  return rows
}

function paletteLine(p) {
  return [p.name, p.usage].filter(Boolean).join(' · ')
}

export default function StyleCard({ asset, inUnit }) {
  if (!asset) return null
  const palette = Array.isArray(asset.palette) ? asset.palette : []
  const swatches = palette.filter((p) => p && HEX.test(String(p.hex || ''))
    && !/^#000000$/i.test(String(p.hex)))
  const plain = palette.filter((p) => swatches.indexOf(p) < 0)
  const light = asset.light && typeof asset.light === 'object' ? asset.light : {}
  const lens = asset.lens && typeof asset.lens === 'object' ? asset.lens : {}
  const bible = parseBible(asset.style_bible)
  const lightScale = Boolean(light.ratio || light.kelvin)
  const lightLine = [
    light.ratio && '光比 ' + light.ratio,
    light.kelvin && '色温 ' + light.kelvin,
  ].filter(Boolean).join(' · ')
  const lensLine = [
    (lens.filters || []).join(' / '),
    lens.notes,
  ].filter(Boolean).join(' · ')

  return (
    <div className="style-mini">
      <Link className="style-mini-head" to={'/assets?asset=' + encodeURIComponent(asset.id)}>
        <b>{asset.name}</b>
        {inUnit && <span className="tag">本集</span>}
        <span className="style-mini-id">{asset.id}</span>
      </Link>
      <div className="style-mini-status">{asset.status || '未标状态'}{asset.dynasty ? ' · ' + asset.dynasty : ''}</div>

      {swatches.length > 0 && (
        <div className="swatches">
          {swatches.map((p, i) => (
            <span className="swatch" key={i} title={paletteLine(p)}>
              <i style={{ background: p.hex }} />
              <em>{p.name}</em>
            </span>
          ))}
        </div>
      )}
      {plain.length > 0 && (
        <ul className="style-lines">
          {plain.slice(0, 5).map((p, i) => <li key={i}>{paletteLine(p)}</li>)}
        </ul>
      )}

      {lightScale && (
        <p className="style-line"><span>光</span>{[lightLine, light.notes].filter(Boolean).join(' · ')}</p>
      )}
      {!lightScale && light.notes && (
        <p className="style-line"><span>注</span>{light.notes}</p>
      )}
      {lensLine && (
        <p className="style-line"><span>镜头</span>{lensLine}</p>
      )}
      {bible.length > 0 && (
        <dl className="style-bible">
          {bible.slice(0, 8).map((row, i) => (
            <React.Fragment key={i}>
              <dt>{row.k}</dt>
              <dd>{row.v}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
      {!palette.length && !lightScale && !light.notes && !lensLine && !bible.length && (
        <p className="style-line muted">{asset.notes || '还没有风格内容，用 WorkBuddy 补齐。'}</p>
      )}
    </div>
  )
}
