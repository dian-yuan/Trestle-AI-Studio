import { useCallback, useEffect, useRef, useState } from 'react'
import { get, qs } from './api'

export function useSnapshot(unit) {
  const [snap, setSnap] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const inflight = useRef(false)

  const load = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      const d = await get('/api/state' + qs({ unit }))
      if (d && d.meta) {
        setSnap(d)
        setError('')
      } else {
        setError((d && d.error) || '读取工作台失败')
      }
    } catch (e) {
      setError('读取工作台失败：' + (e.message || e))
    } finally {
      inflight.current = false
      setLoading(false)
    }
  }, [unit])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  useEffect(() => {
    let es
    let timer
    try {
      es = new EventSource('/api/events')
      es.addEventListener('change', () => {
        clearTimeout(timer)
        timer = setTimeout(load, 350)
      })
    } catch (e) {
      /* 事件流不可用时保留手动同步 */
    }
    return () => {
      clearTimeout(timer)
      if (es) es.close()
    }
  }, [load])

  return { snap, error, loading, refresh: load }
}

export function useUnits() {
  const [data, setData] = useState(null)
  useEffect(() => {
    get('/api/units').then(setData).catch(() => setData(null))
  }, [])
  return data
}

export function useConn() {
  const [conn, setConn] = useState(null)
  const [loading, setLoading] = useState(false)
  const inflight = useRef(false)

  const load = useCallback(async (net = false) => {
    if (inflight.current) return
    inflight.current = true
    setLoading(true)
    try {
      const d = await get('/api/conn' + (net ? '?net=1' : ''))
      if (d && d.ok !== false) setConn(d)
    } catch (e) {
      /* 连接状态读不到时保留上一次 */
    } finally {
      inflight.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(false) }, [load])

  return { conn, loading, refresh: load }
}

