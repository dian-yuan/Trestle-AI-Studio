const token = () => {
  const m = document.querySelector('meta[name="wb-token"]')
  return m ? m.content : ''
}

async function request(url, opts = {}) {
  const r = await fetch(url, opts)
  const ct = r.headers.get('content-type') || ''
  if (ct.includes('application/json')) return r.json()
  return r.text()
}

export const get = (url) => request(url)

export const post = (url, body) =>
  request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-WB-Token': token() },
    body: JSON.stringify(body || {}),
  })

export const qs = (params) => {
  const u = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') u.set(k, v)
  })
  const s = u.toString()
  return s ? '?' + s : ''
}

export const exportHref = (path, params) => path + qs(params)

export async function openWorkbuddy(action, ctx, selection, extra) {
  const payload = { action, ctx: ctx || '', ...(extra || {}) }
  if (selection) payload.selection = selection
  return post('/api/wb/open', payload)
}
