// 开拍清单的进度全部从工程状态推导：
// /api/structure（场 / 段 / 镜 / 节点 / 首帧 / 视频 / 审片 / 成片）+ /api/conn（Agent / skill / LibTV）。
// 不记录点击，不新增接口，不写文件。
import { MEDIA_DONE } from '../libtvState'

export function flattenShots(structure) {
  return ((structure && structure.scenes) || []).flatMap((sc) =>
    ((sc.segments) || []).flatMap((sg) => sg.shots || []))
}

export function firstScene(structure) {
  return ((structure && structure.scenes) || [])[0] || null
}

export function firstSegment(structure) {
  const sc = firstScene(structure)
  return (sc && (sc.segments || [])[0]) || null
}

export function deriveOnboarding({ structure, conn }) {
  const s = structure || {}
  const meta = s.meta || {}
  const shots = flattenShots(s)
  const c = conn || {}

  const readyAgent = (c.agents || []).find((a) => a.ready)
  const agentReady = !!readyAgent
  const skillReady = !!(c.skill && c.skill.ready)
  const libtvReady = !!(c.libtv && c.libtv.state === 'online')

  const scriptText = ((s.script && s.script.text) || '').trim()
  const sceneCount = Number(meta.scenes || 0)
  const segmentCount = Number(meta.segments || 0)
  const shotCount = Number(meta.shots || 0)

  const hasBrief = !!s.brief
  const hasScriptText = !!scriptText
  const hasScript = hasScriptText || sceneCount > 0
  const hasScenes = sceneCount > 0 && segmentCount > 0
  const hasGraph = shots.some((x) => Number(x.nodes || 0) > 0)
  const hasWired = shots.some((x) => !!x.wired)
  // 不能只看 frame_rev：布线后也可能有本地占位图 / rev=1，但状态还是 wired。
  const hasOutput = shots.some((x) =>
    MEDIA_DONE.includes(x.frame_status) || MEDIA_DONE.includes(x.video_status))
  const hasReview = shots.some((x) => !!x.verdict)
  const hasFilm = !!(s.film && s.film.exists)

  const rows = {
    tools: { done: agentReady && skillReady },
    libtv: { done: libtvReady },
    script: { done: hasScript },
    scenes: { done: hasScenes },
    graph: { done: hasGraph },
    wire: { done: hasWired, blocked: !libtvReady },
    output: { done: hasOutput, blocked: !libtvReady },
    review: { done: hasReview },
    deliver: { done: hasFilm },
  }

  const order = ['script', 'scenes', 'graph', 'wire', 'output', 'review', 'deliver']
  let nextId = order.find((id) => !rows[id].done) || 'deliver'
  // 写内容之前先要有 Agent + skill；布线 / 生成之前先要有 LibTV。
  // 只在这两步之前把连接项提为「当前」，不让连接坏掉时把审片 / 交付也拉回去。
  if (['script', 'scenes', 'graph'].includes(nextId) && !(agentReady && skillReady)) {
    nextId = 'tools'
  } else if ((nextId === 'wire' || nextId === 'output') && !libtvReady) {
    nextId = 'libtv'
  }

  return {
    rows,
    nextId,
    doneCount: Object.values(rows).filter((x) => x.done).length,
    total: 9,
    agentReady,
    agentName: (readyAgent && readyAgent.name) || '',
    skillReady,
    libtvReady,
    sceneCount,
    segmentCount,
    shotCount,
    hasContent: hasScript || sceneCount > 0,
    hasBrief,
    hasScriptText,
    scenesOnly: sceneCount > 0 && segmentCount === 0,
  }
}
