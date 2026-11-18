import React from 'react'
import { Link } from 'react-router-dom'
import { assetLevelLabel } from '../labels'

export default function AssetMini({ asset, desc = false }) {
  if (!asset) return null
  const text = desc ? (asset.role || asset.notes || '') : ''
  return (
    <Link className={'asset-mini' + (text ? ' with-desc' : '')}
      to={'/assets?asset=' + encodeURIComponent(asset.id)}>
      <b>{asset.name}</b>
      <span className={'meta-state ' + (asset.build_state || 'empty')}>
        {asset.id} · {assetLevelLabel(asset.level, asset.status || '')}
        {asset.build_state_label ? ' · ' + asset.build_state_label : ''}
      </span>
      {text && <em>{text}</em>}
    </Link>
  )
}
