import { create } from 'zustand'
import request from '@/utils/request'
import { getAuthToken, setAuthToken } from '@/utils/authSession'
import { useProviderStore } from '@/store/providerStore'
import { useModelStore } from '@/store/modelStore'
import { useTaskStore } from '@/store/taskStore'
import { useChatStore } from '@/store/chatStore'

export interface AuthUser {
  id: number
  username: string
  role: 'admin' | 'user'
  enabled: boolean
}

interface AuthState {
  user: AuthUser | null
  ready: boolean
  bootstrap: () => Promise<void>
  login: (username: string, password: string, remember?: boolean) => Promise<void>
  register: (username: string, password: string, remember?: boolean) => Promise<void>
  syncSession: () => Promise<void>
  logout: () => Promise<void>
  expire: () => void
}

function clearSensitiveState() {
  useProviderStore.setState({ provider: [] })
  useModelStore.setState({ models: [], modelList: [], selectedModel: '', loading: false })
  useTaskStore.getState().setOwner(null)
  useChatStore.setState({ chatHistory: {} })
}

let bootstrapPromise: Promise<void> | null = null
let sessionRevision = 0

export const useAuthStore = create<AuthState>((set, get) => {
  const authenticate = async (path: string, username: string, password: string, remember = false) => {
    const revision = ++sessionRevision
    bootstrapPromise = null
    const result = await request.post(path, { username, password, remember_me: remember }, { suppressToast: true }) as unknown as { token: string; user: AuthUser }
    if (revision !== sessionRevision) throw { msg: '登录状态已变更，请重试' }
    clearSensitiveState()
    setAuthToken(result.token, remember)
    useTaskStore.getState().setOwner(result.user.id)
    set({ user: result.user, ready: true })
  }
  return {
    user: null,
    ready: false,
    bootstrap: () => {
      if (get().ready) return Promise.resolve()
      if (bootstrapPromise) return bootstrapPromise
      const revision = sessionRevision
      const token = getAuthToken()
      const job = (async () => {
        try {
          if (token) {
            const user = await request.get('/auth/me', { suppressToast: true }) as unknown as AuthUser
            if (revision !== sessionRevision || token !== getAuthToken()) return
            clearSensitiveState()
            useTaskStore.getState().setOwner(user.id)
            set({ user })
          }
        } catch {
          if (revision !== sessionRevision || token !== getAuthToken()) return
          setAuthToken(null)
          clearSensitiveState()
          set({ user: null })
        } finally {
          if (revision === sessionRevision) set({ ready: true })
        }
      })()
      bootstrapPromise = job
      void job.finally(() => { if (bootstrapPromise === job) bootstrapPromise = null }).catch(() => {})
      return job
    },
    login: (username, password, remember) => authenticate('/auth/login', username, password, remember),
    register: (username, password, remember) => authenticate('/auth/register', username, password, remember),
    syncSession: () => {
      sessionRevision++
      bootstrapPromise = null
      clearSensitiveState()
      set({ user: null, ready: false })
      return get().bootstrap()
    },
    logout: async () => {
      const token = getAuthToken()
      try {
        await request.post('/auth/logout', {}, { suppressToast: true })
      } finally {
        if (token === getAuthToken()) get().expire()
      }
    },
    expire: () => {
      sessionRevision++
      bootstrapPromise = null
      setAuthToken(null)
      clearSensitiveState()
      set({ user: null, ready: true })
    },
  }
})
