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
  login: (username: string, password: string) => Promise<void>
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

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  ready: false,
  bootstrap: () => {
    if (get().ready) return Promise.resolve()
    if (bootstrapPromise) return bootstrapPromise
    bootstrapPromise = (async () => {
      try {
        if (getAuthToken()) {
          const user = await request.get('/auth/me', { suppressToast: true }) as unknown as AuthUser
          clearSensitiveState()
          useTaskStore.getState().setOwner(user.id)
          set({ user })
        }
      } catch {
        setAuthToken(null)
        clearSensitiveState()
        set({ user: null })
      } finally {
        set({ ready: true })
        bootstrapPromise = null
      }
    })()
    return bootstrapPromise
  },
  login: async (username, password) => {
    const result = await request.post('/auth/login', { username, password }, { suppressToast: true }) as unknown as { token: string; user: AuthUser }
    clearSensitiveState()
    setAuthToken(result.token)
    useTaskStore.getState().setOwner(result.user.id)
    set({ user: result.user, ready: true })
  },
  logout: async () => {
    try {
      await request.post('/auth/logout', {}, { suppressToast: true })
    } finally {
      get().expire()
    }
  },
  expire: () => {
    setAuthToken(null)
    clearSensitiveState()
    set({ user: null, ready: true })
  },
}))
