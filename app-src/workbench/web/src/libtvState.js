// Trestle · LibTV 布线状态 → 界面状态 / 下一步动作
//
// 流程：先把方案图谱整体建好（参考图 / 首帧 / 视频节点一起接到 LibTV 画布），
// 再在 LibTV 里按节点一个一个生成；Trestle 只负责布线、打开节点、同步结果。
//
// 状态口径：
//   没有图谱节点 → 待出图谱（让 WorkBuddy 出图谱）
//   有外部素材没绑定 → 素材待绑定（绑定素材，不是布线）
//   有节点但没 node_id → 待布线
//   布好线没生成 → 待生成（点节点跳 LibTV）
//   已生成 → ✓；已生成但本地没文件 / 还在生成 → 同步
//
// 判定口径：是否已生成只看图谱节点 status（generated / reviewed / approved），
// 本地文件只决定「要不要同步回来」，绝不拿文件反推生成状态。
// 参考视频、白膜、外部参考永不参与首帧 / 成片。

export const MEDIA_DONE = ['generated', 'reviewed', 'approved']
export const MEDIA_RUNNING = ['generating']
export const MEDIA_FAILED = ['failed', 'stale']

export function mediaState(shot) {
  const frameStatus = String(shot.frame_status || '')
  const videoStatus = String(shot.video_status || '')
  const greyboxStatus = String(shot.greybox_status || '')
  const externalRefs = shot.external_refs || []
  const needsBinding = Number(shot.needs_binding || 0)
  const outputNodes = Number(shot.output_nodes ?? shot.nodes ?? 0)
  const hasFrameNode = !!frameStatus
  const hasVideoNode = !!videoStatus
  const hasGreybox = !!greyboxStatus
  const frameDone = MEDIA_DONE.includes(frameStatus)
  const videoDone = MEDIA_DONE.includes(videoStatus)
  const greyboxDone = MEDIA_DONE.includes(greyboxStatus)
  return {
    frameStatus,
    videoStatus,
    greyboxStatus,
    hasFrameNode,
    hasVideoNode,
    hasGreybox,
    hasGraph: outputNodes > 0 || externalRefs.length > 0
      || Number(shot.nodes || 0) > 0,
    outputNodes,
    externalRefs,
    needsBinding,
    frameWired: !!shot.frame_wired,
    videoWired: !!shot.video_wired,
    greyboxWired: !!shot.greybox_wired,
    frameNode: shot.frame_node || '',
    videoNode: shot.video_node || '',
    greyboxNode: shot.greybox_node || '',
    frameDone,
    videoDone,
    greyboxDone,
    framesReady: shot.frames_ready !== false,
    framesMissing: shot.frames_missing || [],
    frameRunning: MEDIA_RUNNING.includes(frameStatus),
    videoRunning: MEDIA_RUNNING.includes(videoStatus),
    greyboxRunning: MEDIA_RUNNING.includes(greyboxStatus),
    frameFailed: MEDIA_FAILED.includes(frameStatus),
    videoFailed: MEDIA_FAILED.includes(videoStatus),
    frameFile: shot.frame_file || shot.render || '',
    videoFile: shot.video_file || '',
    greyboxFile: shot.greybox_file || '',
    frameRev: Number(shot.frame_rev || 0),
    videoRev: Number(shot.video_rev || 0),
    frameHistory: Number(shot.frame_history || 0),
    videoHistory: Number(shot.video_history || 0),
    refVideoNode: shot.ref_video_node || '',
  }
}

const WIRE = { key: 'wire', kind: 'wire', label: '布线到 LibTV' }

// 一张镜卡上可点的动作：[{ key, kind, label, nodeKey?, hint? }]
// kind: graph | bind | wire | open | sync
export function shotActions(shot) {
  const m = mediaState(shot)
  if (!m.hasGraph) {
    return [{ key: 'graph', kind: 'graph', label: '打开 WorkBuddy 出图谱' }]
  }

  const out = []
  if (m.needsBinding) {
    out.push({
      key: 'bind', kind: 'bind', label: '绑定 LibTV 素材',
      hint: m.needsBinding + ' 个外部素材还没绑定',
    })
  }
  if (m.outputNodes === 0
    && (m.externalRefs.length || Number(shot.nodes || 0) > 0)) {
    out.push({
      key: 'graph', kind: 'graph', label: '打开 WorkBuddy 补图谱',
      hint: '只有参考节点，还没有生成节点；让 WorkBuddy 补这一段图谱',
    })
  }
  const frameNeedsWire = m.hasFrameNode && !m.frameWired
  const videoNeedsWire = m.hasVideoNode && !m.videoWired
  if (frameNeedsWire || videoNeedsWire) {
    let hint = m.needsBinding
      ? '未绑定的素材会跳过，先绑定再布这一段'
      : '只布线，不生成'
    if (videoNeedsWire && !frameNeedsWire && m.framesMissing.length) {
      hint = '视频节点要等 ' + m.framesMissing.join('、') + ' 出图后才能布'
    }
    out.push({ ...WIRE, hint })
  }

  // 白膜是一个独立阶段：白膜没完成时先给白膜入口，不把白膜叫首帧。
  if (m.hasGreybox && m.greyboxWired && !m.greyboxDone && !m.greyboxRunning) {
    out.push({
      key: 'open-greybox', kind: 'open', side: 'greybox',
      nodeKey: m.greyboxNode, label: '打开 LibTV 生成白膜',
      hint: '在 LibTV 打开白膜节点生成',
    })
  }

  if (m.hasFrameNode && m.frameWired && !m.frameDone && !m.frameRunning) {
    out.push({
      key: 'open-frame', kind: 'open', side: 'frame', nodeKey: m.frameNode,
      label: m.frameStatus === 'failed' || m.frameStatus === 'stale'
        ? '打开 LibTV 重跑首帧' : '打开 LibTV 生成首帧',
      hint: '在 LibTV 打开首帧节点生成',
    })
  }
  if (m.hasVideoNode && m.videoWired && !m.videoDone && !m.videoRunning) {
    out.push({
      key: 'open-video', kind: 'open', side: 'video', nodeKey: m.videoNode,
      label: m.videoStatus === 'failed' || m.videoStatus === 'stale'
        ? '打开 LibTV 重跑视频' : '打开 LibTV 生成视频',
      hint: !m.framesReady
        ? '在 LibTV 打开视频节点；首帧 / 尾帧生成后再点生成'
        : '在 LibTV 打开视频节点生成',
    })
  }

  // 生成在 LibTV 里做，Trestle 不知道什么时候跑完：已布线未生成也给同步入口。
  const canPull = (m.hasFrameNode && m.frameWired && !m.frameDone)
    || (m.hasVideoNode && m.videoWired && !m.videoDone)
    || m.externalRefs.length > 0
  const needSync = canPull
    || (m.frameDone && !m.frameFile)
    || (m.videoDone && !m.videoFile)
    || m.frameRunning || m.videoRunning
  if (needSync) out.push({ key: 'sync', kind: 'sync', label: '从 LibTV 同步' })
  return out
}

function badge(key, m, done, running, wired, hasNode, failed, label) {
  const name = key === 'frame' ? '首帧' : key === 'greybox' ? '白膜' : '视频'
  if (!hasNode) return null
  if (done) return { key, tone: 'ok', text: name + ' ✓' }
  if (running) return { key, tone: 'info', text: name + ' 生成中' }
  if (!wired) return { key, tone: 'warn', text: name + ' 待布线' }
  if (failed === 'failed') return { key, tone: 'err', text: name + ' 失败' }
  if (failed === 'stale') return { key, tone: 'warn', text: name + ' 待返修' }
  return { key, tone: 'warn', text: name + ' ' + label }
}

// 镜卡上的状态徽标：素材 / 白膜 / 首帧 / 视频。
export function shotBadges(shot) {
  const m = mediaState(shot)
  const out = []
  if (m.needsBinding) {
    out.push({ key: 'material', tone: 'warn',
      text: '素材待绑定 ' + m.needsBinding })
  } else if (m.externalRefs.length) {
    out.push({ key: 'material', tone: 'faint',
      text: '素材 ' + m.externalRefs.length + '/' + m.externalRefs.length })
  }
  if (m.hasGreybox) {
    const b = badge('greybox', m, m.greyboxDone, m.greyboxRunning,
      m.greyboxWired, true, '', '待生成')
    if (b) out.push(b)
  }
  if (m.frameDone) {
    out.push({ key: 'frame', tone: 'ok', text: '首帧 ✓'
      + (m.frameRev > 1 ? ' v' + m.frameRev : '') })
  } else {
    const b = badge('frame', m, false, m.frameRunning, m.frameWired,
      m.hasFrameNode, m.frameStatus,
      m.greyboxDone ? '待定稿' : '待生成')
    if (b) out.push(b)
  }
  if (m.videoDone) {
    out.push({ key: 'video', tone: 'ok', text: '视频 ✓'
      + (m.videoRev > 1 ? ' v' + m.videoRev : '') })
  } else {
    const b = badge('video', m, false, m.videoRunning, m.videoWired,
      m.hasVideoNode, m.videoStatus,
      !m.framesReady ? '待帧' : (m.frameDone ? '待生成' : '待首帧'))
    if (b) out.push(b)
  }
  return out
}

// LibTV 画布深链：把分镜/场链接的 focusNodeId 换成具体节点 key。
export function nodeUrl(editorUrl, nodeKey) {
  if (!editorUrl) return ''
  if (!nodeKey) return editorUrl
  try {
    const u = new URL(editorUrl)
    u.searchParams.set('focusNodeId', nodeKey)
    return u.toString()
  } catch (e) {
    return editorUrl
  }
}

// 布线结果的 deferred 分类：待绑定 / 需关键帧 / 等帧，不要一律说成等首帧。
export function deferredMessage(res) {
  const d = (res && res.deferred) || []
  const bind = d.filter((x) => x.needs_binding).length
  const keyf = d.filter((x) => x.needs_keyframes).length
  const missingFrames = d.filter((x) => x.needs_frame)
  const frameIds = missingFrames.map((x) => x.frame).filter(Boolean)
  const wait = d.length - bind - keyf - missingFrames.length
  const parts = []
  if (bind) parts.push(bind + ' 个素材待绑定')
  if (keyf) parts.push(keyf + ' 个参考视频需关键帧')
  if (missingFrames.length) {
    parts.push('先出首帧 ' + (frameIds.length
      ? frameIds.join('、') : (missingFrames.length + ' 个')) + ' 再补布视频')
  }
  if (wait) parts.push(wait + ' 个节点等首帧 / 尾帧')
  return parts.join('；')
}

// 一镜只给一个「下一步」：按阻塞优先级挑一个，避免同一行出现一排按钮。
export function nextAction(shot) {
  const m = mediaState(shot)
  const acts = shotActions(shot)
  const find = (kind) => acts.find((a) => a.kind === kind) || null
  if (!m.hasGraph) return find('graph')
  if (m.needsBinding) return find('bind')
  if (m.outputNodes === 0
    && (m.externalRefs.length || Number(shot.nodes || 0) > 0)) return find('graph')
  if (m.hasFrameNode && !m.frameWired) return find('wire')
  if (m.hasGreybox && m.greyboxWired && !m.greyboxDone
    && !m.greyboxRunning) return find('open')
  if (m.hasFrameNode && m.frameWired && !m.frameDone
    && !m.frameRunning) return find('open')
  if (m.hasVideoNode && !m.videoWired && m.framesReady) return find('wire')
  if (m.hasVideoNode && m.videoWired && !m.videoDone
    && !m.videoRunning) return find('open')
  return find('sync')
}

// 按钮在前端是否可用（后端还会再拦一次）
export function canRun(shot, kind) {
  const m = mediaState(shot)
  if (kind === 'wire') return m.hasGraph
  if (kind === 'graph') return true
  if (kind === 'sync') return true
  if (kind === 'bind' || kind === 'unbind' || kind === 'version') return true
  if (kind === 'open') return true
  return false
}
