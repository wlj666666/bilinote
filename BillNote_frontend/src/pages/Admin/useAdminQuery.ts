import { useCallback, useEffect, useRef, useState } from 'react'

export function adminError(error: unknown): string {
  if (error && typeof error === 'object' && 'msg' in error && typeof error.msg === 'string')
    return error.msg
  return error instanceof Error ? error.message : '读取失败，请稍后重试'
}

/** Sequential polling; changing filters or leaving the page invalidates old responses. */
export function useAdminQuery<T>(fetcher: (signal: AbortSignal) => Promise<T>, interval = 10000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const refreshRef = useRef<() => void>(() => {})

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let busy = false
    setData(null)
    setError('')
    async function run() {
      if (busy || controller.signal.aborted) return
      clearTimeout(timer)
      busy = true
      setLoading(true)
      try {
        const result = await fetcher(controller.signal)
        if (!controller.signal.aborted) {
          setData(result)
          setError('')
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(adminError(e))
      } finally {
        busy = false
        if (!controller.signal.aborted) {
          setLoading(false)
          timer = setTimeout(() => {
            if (document.visibilityState === 'visible') void run()
          }, interval)
        }
      }
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') void run()
    }
    refreshRef.current = () => {
      void run()
    }
    document.addEventListener('visibilitychange', onVisible)
    void run()
    return () => {
      controller.abort()
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [fetcher, interval])

  const refresh = useCallback(() => refreshRef.current(), [])
  return { data, error, loading, refresh }
}
