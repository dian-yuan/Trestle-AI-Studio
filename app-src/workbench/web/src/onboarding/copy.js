// 引导文案只放这里；启动器的短版说明在 ui/picker.html，不复制整套帮助。
export const PHASES = [
  { id: 'setup', label: '准备', rows: ['tools', 'libtv'] },
  { id: 'content', label: '写内容', rows: ['script', 'scenes', 'graph'] },
  { id: 'output', label: '出片', rows: ['wire', 'output', 'review', 'deliver'] },
]

export const ROWS = {
  tools: {
    title: '工具就绪',
    todo: '先连上 WorkBuddy，确认导演 skill 已加载',
    done: 'WorkBuddy 已连接 · 导演 skill 已加载',
    stuck: '没装 WorkBuddy？先装客户端；也可以只用 Claude Code。导演 skill 会随 Trestle 同步。',
  },
  libtv: {
    title: 'LibTV 授权',
    todo: '出片前再连也行；现在连好，后面少一步',
    done: 'LibTV 已连接',
    stuck: '授权过期？点「去连接」重新授权；浏览器里完成后回来自动变绿。',
  },
  script: {
    title: '剧本',
    todo: '让 WorkBuddy 先问 3 个问题，再写第一版',
    done: '剧本已有正文',
    stuck: '没想法？先说清给谁看、看完做什么、必须保留哪 3 个瞬间。',
  },
  scenes: {
    title: '场与分镜',
    todo: '剧本有了，接下来切成场，再拆成段和镜',
    done: '场、段、镜已经拆好',
    stuck: '场太多？先只做一场 20 秒。先切场，再拆段，最后拆镜。',
  },
  graph: {
    title: '图谱',
    todo: '让 WorkBuddy 替这一场选技法、出节点和连线',
    done: '这一场已经有图谱',
    stuck: '图谱是这一镜的施工图：先出什么图、再怎么出视频。',
  },
  wire: {
    title: '布线',
    todo: '把节点在 LibTV 画布上摆好、连好；只搭架子，不生成',
    done: '节点已经接到 LibTV',
    blocked: '先连 LibTV；连上后把节点搭到画布',
    stuck: '有「待绑定」？客户素材先在 LibTV 上传，再回来绑定。',
  },
  output: {
    title: '生成并同步',
    todo: '在 LibTV 点生成，回 Trestle 点同步',
    done: '已经收回生成结果',
    blocked: '先连 LibTV；生成在 LibTV 里点',
    stuck: '生成中？等节点跑完再同步。积分在 LibTV 扣，Trestle 不代扣。',
  },
  review: {
    title: '审片',
    todo: '逐镜看结果，过了标通过；不过就返修这一镜',
    done: '已经审过片',
    stuck: '返修让 WorkBuddy 只改这一镜，别重写全场。',
  },
  deliver: {
    title: '交付',
    todo: '导出成片和清单，发给客户或剪辑',
    done: '已经有成片',
    stuck: '还没成片？先完成「生成并同步」。',
  },
}

export const ROLES = [
  {
    name: 'WorkBuddy', tag: '写',
    lines: ['问你 3 个会改变结构的问题', '写剧本、场、分镜、镜头', '写图谱、提示词'],
  },
  {
    name: 'Trestle', tag: '看 + 接',
    lines: ['只读工程文件', '把节点接到 LibTV 画布', '把结果收回来、审片、交付'],
  },
  {
    name: 'LibTV', tag: '出片',
    lines: ['你在这里点生成', '花你的积分', 'Trestle 不代生成、不代扣'],
  },
]

export const CHAIN = [
  { who: 'WorkBuddy', steps: ['创意', '剧本', '场', '段', '镜', '图谱', '提示词'] },
  { who: 'Trestle + 你', steps: ['布线', '在 LibTV 生成', '同步', '审片', '交付'] },
]

export const TERMS = [
  ['场', '一段发生在同一地点、同一时间的戏。'],
  ['段', '一场里的一组镜，一个节拍。'],
  ['镜', '一个机位拍的一下，是最小单位。'],
  ['关键帧', '这一镜最重要的一帧画面，给生成定调。'],
  ['白膜', '用灰模先看构图和站位，不追求好看。'],
  ['资产', '会重复出现的角色、场景、道具；先做，后面复用。'],
  ['图谱', '这一镜的施工图：节点 + 连线 + 阶段。'],
  ['提示词', '写在节点上，告诉模型这一镜长什么样、什么在动。'],
  ['布线', '在 LibTV 画布上把节点和线搭好；只搭架子，不生成。'],
  ['绑定', '把客户自己上传的素材节点接到图谱槽位。'],
  ['同步', '把 LibTV 生成好的结果取回工程。'],
  ['审片', '逐镜看结果，通过或返修。'],
]

export const FAQ = [
  ['为什么 Trestle 不能改剧本？',
    '内容只有一个作者。Trestle 只读工程文件，避免两处修改同一份内容；要改就去 WorkBuddy。'],
  ['为什么生成要去 LibTV 点？',
    '生成花的是你的积分。Trestle 只负责把节点接好、把结果收回来，不替你按生成。'],
  ['积分是谁扣的？',
    'LibTV。Trestle 不代生成、不代扣。'],
  ['内容存在哪？换电脑怎么办？',
    '存在你选的工程文件夹里，全是明文文件。拷走整个文件夹，装好 Trestle 后「打开文件夹」即可。'],
  ['WorkBuddy 没装怎么办？',
    '设置里可以指定路径；也可以只用 Claude Code。导演 skill 会随 Trestle 同步。'],
  ['LibTV 授权过期怎么办？',
    '设置 → LibTV → 去连接；浏览器授权后回来自动变绿。'],
  ['生成失败 / 想返修一镜怎么办？',
    '在镜卡上点「返修」，让 WorkBuddy 只改这一镜；重新布线生成，旧版本会保留。'],
  ['提示词改了，LibTV 怎么没变？',
    'WorkBuddy 和 Trestle 改的是本地 graph.json。回审片右侧「布线方案」，'
    + '点这个节点的「更新到 LibTV」（或「全部更新到 LibTV」）。'
    + '已有输出不会自动重跑，需要时在 LibTV 重新生成。'],
  ['为什么有「待绑定」？',
    '客户素材在 LibTV 里，Trestle 只记引用、不搬运。先上传，再绑定到图谱槽位。'],
]

export const COACHES = {
  'home.canvas': {
    title: '这里是「看」，不是「改」',
    body: '这些节点是工程里的文件。点一个看内容；改内容去 WorkBuddy。',
  },
  'review.graph': {
    title: '布线 ≠ 生成',
    body: '布线只把节点搭到 LibTV，不按生成。生成在 LibTV 里由你点，积分也在那里扣。',
  },
  'assets.build': {
    title: '先做资产，再布线',
    body: '角色和场景会重复出现在多个镜里。缺图时，分镜不能布线。',
  },
}
