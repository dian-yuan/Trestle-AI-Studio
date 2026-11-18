import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useApp } from '../App'
import { useOnboard } from '../onboarding/context'

const STEPS = [
  {
    id: 'why',
    kicker: '为什么',
    title: 'Trestle 是一座桥',
    lead: 'WorkBuddy 写内容，LibTV 出片；Trestle 把两边接起来，把方法和技巧铺在桥上。',
    points: [
      '工具之间：WorkBuddy ↔ Trestle ↔ LibTV。',
      '阶段之间：创意 → 剧本 → 场 → 分镜 → 镜头 → 图谱 → 提示词 → 布线 → 生成 → 同步 → 审片 → 交付。',
      '人和方法之间：内置 skill、技巧和检查；不会就问 AI 教练。',
    ],
    art: 'why',
  },
  {
    id: 'roles',
    kicker: '先认人',
    title: '谁写、谁接、谁出片',
    lead: '记不住按钮没关系，先记住这三件事。',
    points: [
      'WorkBuddy 写内容：剧本、场、分镜、镜头、图谱、提示词。',
      'Trestle 看和接：只读工程文件，把节点接到 LibTV，把结果收回来。',
      'LibTV 出片：你在那里点生成，花你的积分。',
    ],
    art: 'roles',
  },
  {
    id: 'structure',
    kicker: '结构设计',
    title: '一条片子，拆成八层',
    lead: '项目、集、剧本、场、段、镜、图谱、提示词。每一层都有对应的文件。',
    points: [
      '场是同一地点、同一时间的戏；段是节拍；镜是最小单位。',
      '剧本在 copy.md，场在 scenes.json，镜在 shots.json。',
      '图谱在 graph.json，提示词在 prompts.json。',
    ],
    art: 'structure',
  },
  {
    id: 'source',
    kicker: '文件唯一性',
    title: '一个文件夹，就是全部事实',
    lead: '所有内容都是明文文件，可以 git、备份、换电脑。',
    points: [
      'graph.json 是节点、连线、阶段、版本指针的唯一来源。',
      'prompts.json / shots.json / libtv.json 是投影，不是第二份事实。',
      '不建第二份素材表、版本表、任务表；平台只读写这个文件夹。',
    ],
    art: 'source',
  },
  {
    id: 'spine',
    kicker: '思路构建',
    title: '先想清楚，再写第一句',
    lead: '内容链条不能跳：一句话 → 3 个问题 → 导演简报 → 剧本 → 场 / 分镜。',
    points: [
      '先问：给谁看、看完做什么、必须保留哪 3 个瞬间。',
      '简报没齐，不进剧本；剧本没定，不切场。',
      '一次只走一步，跳步会返工。',
    ],
    art: 'spine',
  },
  {
    id: 'skill',
    kicker: 'skill + 教练',
    title: 'skill 和 AI 教练',
    lead: '方法在 skill 里，问题问教练。核心资料由 Trestle 内置交给 WorkBuddy / Claude，不进你的技能列表。',
    points: [
      '通用七步和短剧说好包都在这份 skill 里。',
      '不会的操作直接问 AI 教练，它按当前页面给下一步。',
      '它只教、不替你写、不替你生成。',
    ],
    art: 'skill',
  },
  {
    id: 'pitfalls',
    kicker: '少踩坑',
    title: '把常见的坑，做成检查',
    lead: '新手最容易错的地方，应用直接拦住；不用等生成完才发现。',
    points: [
      '缺资产 / 没绑定：布线前就提示，不让你白跑。',
      '首帧、尾帧、比例、时长：生成前一起确认。',
      '版本和积分：旧版本保留，生成在 LibTV、由你点。',
    ],
    art: 'checks',
  },
  {
    id: 'modes',
    kicker: '两种模式',
    title: '通用七步，短剧五段',
    lead: '模式只决定提示词模板、场的字段和进度写法；已有内容不会被改。',
    points: [
      '通用：创作思路 → 剧本 → 场 → 分镜 → 镜头 → 图谱 → 提示词。',
      '短剧：大纲 → 角色 → 美术 → 剧本 → 站位 → 分镜。',
      '在项目设置里切换，切换前会确认。',
    ],
    art: 'modes',
  },
  {
    id: 'loop',
    kicker: '开始',
    title: '现在，开始第一条片子',
    lead: 'WorkBuddy 写 → Trestle 接 → LibTV 出片 → Trestle 同步、审片、交付。',
    points: [],
    art: 'loop',
  },
]

const LAST = STEPS.length - 1

function Check() {
  return (
    <svg viewBox="0 0 10 10" aria-hidden="true">
      <path d="M2 5.2 L4.2 7.4 L8 3" />
    </svg>
  )
}

function ArtWhy() {
  return (
    <div className="onb-art onb-art-bridge-intro">
      <div className="onb-art-label">一座桥，从创意到成片</div>
      <div className="onb-bridge-row">
        <div className="onb-bridge-end"><b>WorkBuddy</b><span>写内容</span></div>
        <span className="onb-bridge-arrow">→</span>
        <div className="onb-bridge-mid"><b>Trestle · 桥</b><span>方法 · 技巧 · 检查</span></div>
        <span className="onb-bridge-arrow">→</span>
        <div className="onb-bridge-end"><b>LibTV</b><span>出片</span></div>
      </div>
      <div className="onb-bridge-chain">
        <b>创意</b> → 剧本 → 场 → 分镜 → 镜头 → 图谱 → 提示词 → 布线 → 生成 → 同步 → 审片 → <b>交付</b>
      </div>
      <div className="onb-bridge-tag">简单 · 少踩坑 · 能走通</div>
    </div>
  )
}

function ArtRoles() {
  const rows = [
    ['WorkBuddy', '写', '剧本、场、分镜、图谱、提示词'],
    ['Trestle', '看 + 接', '结构、图谱、布线、同步、交付'],
    ['LibTV', '出片', '你点生成，花你的积分'],
  ]
  return (
    <div className="onb-art onb-art-roles">
      <div className="onb-art-label">三个人，三件事</div>
      {rows.map(([name, tag, text]) => (
        <div className="onb-role-row" key={name}>
          <div><b>{name}</b><i>{tag}</i></div>
          <span>{text}</span>
        </div>
      ))}
    </div>
  )
}

function ArtStructure() {
  const rows = [
    ['项目', 'project.json', 0],
    ['集', 'units/S1-E01/', 1],
    ['剧本', 'script/copy.md', 2],
    ['场', 'storyboard/scenes.json', 2],
    ['段', 'production/<场>/<段>/', 3],
    ['镜', 'storyboard/shots.json', 4],
    ['图谱', 'storyboard/graph.json', 2],
    ['提示词', 'prompts/prompts.json', 2],
  ]
  return (
    <div className="onb-art onb-art-structure">
      <div className="onb-art-label">工程结构</div>
      {rows.map(([name, file, level]) => (
        <div className="onb-tree-row" key={name}
          style={{ paddingLeft: (level * 14) + 'px' }}>
          <b>{name}</b><span>{file}</span>
        </div>
      ))}
    </div>
  )
}

function ArtSource() {
  return (
    <div className="onb-art onb-art-source">
      <div className="onb-source-core">
        <div className="onb-art-label">唯一事实来源</div>
        <b>graph.json</b>
        <span>节点 · 连线 · 阶段 · 版本指针</span>
      </div>
      <div className="onb-source-around">
        {['prompts.json', 'shots.json', 'libtv.json'].map((x) => (
          <span key={x}>{x}</span>
        ))}
      </div>
      <p className="onb-art-note">
        这三个是投影，不是第二份事实；不建素材表 / 版本表 / 任务表。
      </p>
    </div>
  )
}

function ArtSpine() {
  const rows = [
    ['1', '一句话', '给谁看、看完做什么'],
    ['2', '3 个问题', '必须保留哪 3 个瞬间'],
    ['3', '导演简报', 'design/导演简报.md'],
    ['4', '剧本', 'script/copy.md'],
    ['5', '场 / 分镜', 'scenes.json / shots.json'],
  ]
  return (
    <div className="onb-art onb-art-spine">
      <div className="onb-art-label">思路构建</div>
      {rows.map(([n, title, sub]) => (
        <div className="onb-spine-row" key={n}>
          <i>{n}</i>
          <div><b>{title}</b><span>{sub}</span></div>
        </div>
      ))}
    </div>
  )
}

function ArtSkill() {
  return (
    <div className="onb-art onb-art-skill">
      <div className="onb-skill-card">
        <div className="onb-art-label">导演 skill</div>
        <b>changji-director</b>
        <span>通用七步 + 说好短剧包</span>
        <div className="onb-skill-rule">问什么 · 写哪些文件 · 不越界</div>
      </div>
      <div className="onb-skill-sync">
        {['WorkBuddy', 'Claude Code'].map((x) => (
          <div key={x}><span className="onb-check"><Check /></span>{x}</div>
        ))}
        <em>核心资料内置，桥接 skill 只做指引</em>
      </div>
    </div>
  )
}

function ArtModes() {
  return (
    <div className="onb-art onb-art-modes">
      <div className="onb-mode-col">
        <div className="onb-art-label">通用 · 七步</div>
        <b>讲解 / 产品 / 概念 / 商业故事</b>
        <ul>
          {['创作思路', '剧本', '场', '分镜', '镜头', '图谱', '提示词'].map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </div>
      <div className="onb-mode-col">
        <div className="onb-art-label">短剧 · 说好五段包</div>
        <b>剧情 / 小说改编</b>
        <ul>
          {['大纲', '角色', '美术', '剧本', '站位', '分镜'].map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </div>
      <p className="onb-art-note">
        模式只改提示词模板、场字段、进度写法；已有内容不改。
      </p>
    </div>
  )
}

function ArtLoop() {
  const rows = [
    ['WorkBuddy', '写'],
    ['Trestle', '看 + 接'],
    ['LibTV', '出片'],
    ['Trestle', '同步 · 审片 · 交付'],
  ]
  return (
    <div className="onb-art onb-art-loop">
      {rows.map(([name, tag], i) => (
        <React.Fragment key={name + i}>
          {i > 0 && <span className="onb-loop-arrow">↓</span>}
          <div className="onb-loop-row"><b>{name}</b><span>{tag}</span></div>
        </React.Fragment>
      ))}
    </div>
  )
}

function Art({ kind }) {
  if (kind === 'why') return <ArtWhy />
  if (kind === 'roles') return <ArtRoles />
  if (kind === 'structure') return <ArtStructure />
  if (kind === 'source') return <ArtSource />
  if (kind === 'spine') return <ArtSpine />
  if (kind === 'skill') return <ArtSkill />
  if (kind === 'modes') return <ArtModes />
  return <ArtLoop />
}

// 第一课：全屏分步引导，只在第一次进入时出现。
// 不只教按钮，也把结构、文件唯一性、skill、思路和模式讲清楚。
export default function FirstLesson({ structure, unit }) {
  const { wb } = useApp()
  const { firstLessonSeen, markFirstLesson, setChecklistOpen } = useOnboard()
  const [idx, setIdx] = useState(0)
  const dialogRef = useRef(null)
  const nextRef = useRef(null)
  const primaryRef = useRef(null)
  const lastFocus = useRef(null)

  const title = (structure && structure.title) || ''
  const hasContent = ((structure && structure.meta && structure.meta.scenes) || 0) > 0
    || !!((structure && structure.script && structure.script.text) || '').trim()

  const finishSeen = () => markFirstLesson()

  const openChecklist = () => {
    markFirstLesson()
    setChecklistOpen(true)
    setTimeout(() => {
      const el = document.getElementById('onb-checklist')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      else document.querySelector('.onb-checklist')?.scrollIntoView({
        behavior: 'smooth', block: 'center',
      })
    }, 60)
  }

  const openCanvas = () => {
    markFirstLesson()
    setTimeout(() => {
      document.querySelector('.ov-card')?.scrollIntoView({
        behavior: 'smooth', block: 'center',
      })
    }, 60)
  }

  const openWb = () => {
    markFirstLesson()
    wb('开始创作', '项目 ' + (title || unit || '当前项目'))
  }

  const primary = () => (hasContent ? openCanvas() : openWb())

  useEffect(() => {
    if (firstLessonSeen) return undefined
    lastFocus.current = document.activeElement
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const t = setTimeout(() => nextRef.current?.focus(), 40)
    const onKey = (e) => {
      if (e.key === 'Escape') { markFirstLesson(); return }
      if (e.key === 'ArrowRight') setIdx((i) => Math.min(LAST, i + 1))
      if (e.key === 'ArrowLeft') setIdx((i) => Math.max(0, i - 1))
      if (e.key !== 'Tab' || !dialogRef.current) return
      const els = [...dialogRef.current.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter((x) => !x.disabled && x.getClientRects().length > 0)
      if (!els.length) return
      const first = els[0]
      const last = els[els.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
      try { lastFocus.current?.focus?.() } catch (e) { /* ignore */ }
    }
  }, [firstLessonSeen, markFirstLesson])

  useEffect(() => {
    if (firstLessonSeen) return
    if (idx === LAST) primaryRef.current?.focus()
    else nextRef.current?.focus()
  }, [idx, firstLessonSeen])

  if (firstLessonSeen) return null
  const step = STEPS[idx]

  const body = (
    <div className="onb-first-layer" role="dialog" aria-modal="true"
      aria-labelledby="onbFirstTitle" ref={dialogRef}>
      <header className="onb-first-head">
        <span className="onb-first-brand">Trestle<span>第一课 · 3 分钟看懂</span></span>
        <span className="onb-first-sub">更简单的开始；一座桥，从创意到成片</span>
        <span className="grow" />
        <button className="onb-first-skip" onClick={finishSeen}>跳过</button>
        <button className="onb-first-x" onClick={finishSeen} aria-label="关闭">×</button>
      </header>

      <main className="onb-first-main">
        <div className="onb-first-stage" key={step.id}>
          <div className="onb-first-copy">
            <div className="onb-first-k">
              {String(idx + 1).padStart(2, '0')} / {String(STEPS.length).padStart(2, '0')} · {step.kicker}
            </div>
            <h1 id="onbFirstTitle">{step.title}</h1>
            <p className="onb-first-lead">{step.lead}</p>
            {!!step.points.length && (
              <ul className="onb-first-points">
                {step.points.map((x) => <li key={x}>{x}</li>)}
              </ul>
            )}
            {idx === LAST && (
              <div className="onb-first-actions">
                <button className="btn primary" onClick={primary} ref={primaryRef}>
                  {hasContent ? '看结构画布' : '打开 WorkBuddy 开始'}
                </button>
                <button className="btn" onClick={openChecklist}>看开拍清单</button>
                <button className="btn ghost" onClick={hasContent ? openWb : openCanvas}>
                  {hasContent ? '在 WorkBuddy 继续' : '先看结构画布'}
                </button>
                <button className="onb-first-later" onClick={finishSeen}>稍后自己看</button>
              </div>
            )}
            {idx === LAST && (
              <p className="onb-first-note">
                Trestle 是桥：你可以走它，也可以学会后直接去用 WorkBuddy + LibTV。工程文件是你的，随时可以带走。
                以后可以在顶栏「?」里重看这一课。
              </p>
            )}
          </div>
          <div className="onb-first-art" aria-hidden="true">
            <Art kind={step.art} />
          </div>
        </div>
      </main>

      <footer className="onb-first-foot">
        <div className="onb-first-dots">
          {STEPS.map((s, i) => (
            <button key={s.id} className={i === idx ? 'on' : ''}
              onClick={() => setIdx(i)} aria-label={'第 ' + (i + 1) + ' 步'} />
          ))}
        </div>
        <div className="onb-first-progress">
          <i style={{ width: ((idx + 1) / STEPS.length * 100) + '%' }} />
        </div>
        <div className="onb-first-nav">
          <button className="btn small ghost" disabled={idx === 0}
            onClick={() => setIdx((i) => Math.max(0, i - 1))}>上一步</button>
          <button className="btn small primary" ref={nextRef}
            onClick={() => (idx === LAST ? primary() : setIdx((i) => Math.min(LAST, i + 1)))}>
            {idx === LAST ? '开始使用' : '下一步 →'}
          </button>
        </div>
      </footer>
    </div>
  )

  return createPortal(body, document.body)
}
