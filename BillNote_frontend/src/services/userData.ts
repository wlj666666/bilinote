import { get, del } from 'idb-keyval'
import request from '@/utils/request'
import type { AuthUser } from '@/store/authStore'
import { getAuthToken } from '@/utils/authSession'

let importPromise: Promise<void> | null = null
let importingToken: string | null = null

/** Read old shared caches only after the original administrator has logged in. */
export function importLegacyBrowserData(user: AuthUser): Promise<void> {
  if (user.role !== 'admin') return Promise.resolve()
  const token = getAuthToken()
  if (importPromise && importingToken === token) return importPromise
  importingToken = token
  const job = performImport(user, token)
  importPromise = job
  void job.finally(() => { if (importPromise === job) importPromise = null }).catch(() => {})
  return job
}

async function performImport(user: AuthUser, token: string | null) {
  const raw = await get<string>('task-storage')
  const chatRaw = localStorage.getItem('bilinote-chat-storage')
  if (!raw && !chatRaw) return
  const legacy = raw ? JSON.parse(raw) : {}
  const chats = chatRaw ? JSON.parse(chatRaw)?.state?.chatHistory || {} : {}
  const tasks: Record<string, unknown>[] = legacy?.state?.tasks || []
  for (let offset = 0; offset < tasks.length; offset += 100) {
    await request.post('/history/import', { tasks: tasks.slice(offset, offset + 100) }, { timeout: 60000, sessionToken: token })
  }
  await request.post('/history/import', { tasks: [], chats }, { timeout: 60000, sessionToken: token })
  if (token !== getAuthToken()) return
  // Keep the old copy until all server imports have succeeded.
  if (legacy?.state?.currentTaskId) sessionStorage.setItem(`bilinote-current-task:${user.id}`, legacy.state.currentTaskId)
  await del('task-storage')
  localStorage.removeItem('bilinote-chat-storage')
}
