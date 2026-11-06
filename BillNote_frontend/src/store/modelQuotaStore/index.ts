import { useEffect } from 'react'
import { create } from 'zustand'
import { getAuthToken } from '@/utils/authSession'
import { fetchModelQuotas, type ModelQuotaSnapshot } from '@/services/model'

interface QuotaState {
  snapshot: ModelQuotaSnapshot | null
  loading: boolean
  refresh: () => Promise<void>
}

let pending: Promise<void> | null = null
let lastAttempt = 0
let revision = 0
export const useQuotaStore = create<QuotaState>((set) => ({
  snapshot: null,
  loading: false,
  refresh: () => {
    if (pending) return pending
    if (!getAuthToken()) return Promise.resolve()
    const started = revision
    set({ loading: true })
    lastAttempt = Date.now()
    const job = fetchModelQuotas().then(snapshot => {
      if (started === revision) set({ snapshot })
    }).catch(() => {
      if (started === revision) {
        set({ snapshot: { status: 'error', message: '额度同步失败，请稍后刷新', updated_at: null, refresh_interval_seconds: 60, models: [] } })
      }
    }).finally(() => {
      if (pending === job) pending = null
      if (started === revision) set({ loading: false })
    })
    pending = job
    return job
  },
}))

export function resetModelQuotas() {
  revision += 1
  pending = null
  lastAttempt = 0
  useQuotaStore.setState({ snapshot: null, loading: false })
}

let subscribers = 0
let timer: ReturnType<typeof setInterval> | undefined
const poll = () => {
  if (document.visibilityState !== 'hidden' && Date.now() - lastAttempt >= 60000) {
    void useQuotaStore.getState().refresh()
  }
}

export function useModelQuotas() {
  const state = useQuotaStore()
  useEffect(() => {
    subscribers += 1
    if (subscribers === 1) {
      poll()
      timer = setInterval(poll, 60000)
      document.addEventListener('visibilitychange', poll)
    }
    return () => {
      subscribers -= 1
      if (subscribers === 0) {
        clearInterval(timer)
        document.removeEventListener('visibilitychange', poll)
      }
    }
  }, [])
  return state
}
