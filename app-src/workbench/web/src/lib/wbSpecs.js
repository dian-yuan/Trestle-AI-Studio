// 资产层 WorkBuddy 任务卡：资产页和总览资产卡共用同一套目标 / 快捷项。
export function assetTaskSpec(asset) {
  const cur = asset || {}
  const units = (cur.generations || []).flatMap(
    (g) => (g.units || []).map((u) => g.id + '/' + u.id))
  const base = cur.dir
    || ('assets/' + (cur.type ? cur.type + '/' : '') + (cur.id || ''))
  const options = units.length
    ? [{ value: 'manifest', label: '资产设定' },
      { value: 'plan', label: '资产设计方案' },
      { value: 'unit', label: '出图提示词' }]
    : [{ value: 'manifest', label: '资产设定' },
      { value: 'plan', label: '资产设计方案' }]
  return {
    title: '完善这个资产的设定',
    desc: '先选改哪一层；WorkBuddy 只动选中的那一层，不顺手改别的。',
    targets: [
      { label: '设定', value: base + '/manifest.json' },
      { label: '方案', value: base + '/prompts.md（给人看的资产设计方案）' },
      { label: '提示词', value: units.length
        ? base + '/build/<画布>/<单元>/unit.json · ' + units.join('、')
        : '还没有生成单元；先写设计方案，建画布后再拆成提示词' },
    ],
    note: '「资产设计方案」不会原样发给 LibTV；真正出图读的是 unit.json 的 prompt。',
    choices: { label: '先改哪一层', options, defaultValue: 'manifest' },
    presets: ['补齐外形 / 材质 / 颜色', '统一角色与场景的设定', '写完整的设计方案',
      '按设计方案写一版出图提示词'],
    placeholder: '例：这个角色改成三十岁女性，短发，深蓝工装，左眉有一道疤',
    action: '完善' + (cur.type_label || '资产'),
    ctx: cur.id + ' · ' + (cur.name || ''),
    extra: { asset: cur.id },
  }
}
