import request from '@/utils/request'
import { getAuthToken } from '@/utils/authSession'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatSource {
  text: string
  source_type: 'meta' | 'markdown' | 'transcript'
  section_title?: string
  start_time?: number
  end_time?: number
}

export interface AskResponse {
  answer: string
  sources: ChatSource[]
}

export type IndexStatus = 'idle' | 'indexing' | 'indexed' | 'failed'

export interface ChatStatusResponse {
  indexed: boolean
  status: IndexStatus
}

export const indexTask = async (taskId: string): Promise<void> => {
  return await request.post('/chat/index', { task_id: taskId })
}

interface ChatJob {
  job_id: string
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  result: AskResponse | null
  error: string | null
}

const pause = (signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const abort = () => {
    clearTimeout(timer)
    reject(new DOMException('已停止等待', 'AbortError'))
  }
  const timer = setTimeout(() => {
    signal?.removeEventListener('abort', abort)
    resolve()
  }, 1000)
  if (signal?.aborted) abort()
  else signal?.addEventListener('abort', abort, { once: true })
})

export const getActiveChat = (taskId: string, signal?: AbortSignal): Promise<ChatJob | null> =>
  request.get('/chat/jobs', { params: { task_id: taskId }, signal, suppressToast: true })

export const waitForAnswer = async (
  initial: ChatJob,
  signal?: AbortSignal,
  onProgress?: (message: string) => void,
  sessionToken = getAuthToken(),
): Promise<AskResponse> => {
  let job = initial
  let failures = 0
  const deadline = Date.now() + 10 * 60 * 1000
  while (Date.now() < deadline) {
    signal?.throwIfAborted()
    if (sessionToken !== getAuthToken()) throw new Error('登录状态已变更')
    if (job.status === 'completed' && job.result) return job.result
    if (job.status === 'failed') throw new Error(job.error || '问答失败，请稍后重试')
    onProgress?.(job.status === 'queued' ? '正在排队，请稍候…' : '正在生成回答…')
    await pause(signal)
    try {
      job = await request.get(`/chat/jobs/${job.job_id}`, { signal, sessionToken, suppressToast: true })
      failures = 0
    } catch (error: any) {
      signal?.throwIfAborted()
      if ([401, 403, 404].includes(error?.code) || ++failures >= 3) throw error
    }
  }
  throw new Error('等待回答超时，可重新打开问答查看处理结果')
}

export const askQuestion = async (data: {
  task_id: string
  question: string
  history: ChatMessage[]
  provider_id: string
  model_name: string
}, signal?: AbortSignal, onProgress?: (message: string) => void): Promise<AskResponse> => {
  const sessionToken = getAuthToken()
  const job: ChatJob = await request.post('/chat/jobs', {
    ...data, request_id: crypto.randomUUID(),
  }, { signal, sessionToken, suppressToast: true })
  return waitForAnswer(job, signal, onProgress, sessionToken)
}

export const getChatStatus = async (taskId: string): Promise<ChatStatusResponse> => {
  return await request.get(`/chat/status?task_id=${taskId}`)
}
