import React, { useMemo } from 'react'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: false })

const NUMERIC = /^[\s\d.,%+\-–—/×xX:：s秒分钟]+$/
const NUM_HEAD = /^(时长|频率|数量|秒|镜|场|资产|预算|占比|比例|人数|次数|集数)/

export default function Markdown({ text, className = '', ...rest }) {
  const html = useMemo(() => {
    const raw = marked.parse(text || '')
    if (typeof DOMParser === 'undefined') return raw
    const doc = new DOMParser().parseFromString(raw, 'text/html')
    // 页面自己有一个 h1；正文里的 h1 降成 h2，避免一页多个 h1
    doc.querySelectorAll('h1').forEach((h) => {
      const h2 = doc.createElement('h2')
      h2.innerHTML = h.innerHTML
      h.replaceWith(h2)
    })
    doc.querySelectorAll('th').forEach((th) => {
      if (NUM_HEAD.test((th.textContent || '').trim())) th.classList.add('num')
    })
    doc.querySelectorAll('td').forEach((td) => {
      const s = (td.textContent || '').trim()
      if (s && NUMERIC.test(s) && /\d/.test(s)) td.classList.add('num')
    })
    // 表格套一层横向滚动容器；窄栏（总览右侧抽屉）里表头不会被挤成竖排。
    doc.querySelectorAll('table').forEach((table) => {
      const heads = Array.from(table.querySelectorAll('thead th'))
        .map((th) => (th.textContent || '').trim())
      table.querySelectorAll('tbody tr').forEach((tr) => {
        Array.from(tr.children).forEach((td, i) => {
          if (heads[i]) td.setAttribute('data-label', heads[i])
        })
      })
      const wrap = doc.createElement('div')
      wrap.className = 'md-table-wrap'
      table.replaceWith(wrap)
      wrap.appendChild(table)
    })
    return doc.body.innerHTML
  }, [text])

  return (
    <article
      className={'md ' + className}
      dangerouslySetInnerHTML={{ __html: html }}
      {...rest}
    />
  )
}
