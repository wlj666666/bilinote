import request from '@/utils/request'

export interface AdminTask {
  id: string
  task_id: string
  kind: 'note' | 'chat'
  user_id: number | null
  username: string | null
  user_enabled: boolean | null
  title: string | null
  platform: string
  state: string
  phase: string
  created_at: number | null
  started_at: number | null
  finished_at: number | null
  elapsed_seconds: number | null
  updated_at: number | null
  model_name: string | null
  provider_id: string | null
  can_cancel: boolean
  can_retry: boolean
  message?: string
  error_type?: string | null
  error_detail?: string | null
  attempts?: number | null
  recoveries?: number | null
  heartbeat_at?: number | null
  stage_times?: Record<string, number>
}

export interface AdminUser {
  id: number
  username: string
  role: 'admin' | 'user'
  enabled: boolean
  created_at: number | null
  last_login_at: number | null
  task_count: number
}

export interface AdminPage<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  observed_at: number
}

export interface AdminOverview {
  observed_at: number
  user_count: number
  note_total: number
  note_states: Record<string, number>
  chat_states: Record<string, number>
  recent_failed_notes: AdminTask[]
  recent_failed_chats: AdminTask[]
}

export interface ResourceMetric {
  status: 'ok' | 'error' | 'unsupported'
  percent?: number | null
  used_bytes?: number | null
  total_bytes?: number | null
  free_bytes?: number | null
  scope?: 'system' | 'container' | 'filesystem' | 'device'
  cores?: number
  label?: string
  reason?: string
}

export interface AdminResources {
  resources: {
    observed_at: number
    environment: 'container' | 'system'
    cpu: ResourceMetric
    memory: ResourceMetric
    disk: ResourceMetric
    gpu: ResourceMetric & {
      devices?: {
        name: string
        percent: number | null
        used_bytes: number | null
        total_bytes: number | null
      }[]
    }
  }
  services: {
    observed_at: number
    backend: ServiceStatus
    scheduler: ServiceStatus
    inference: ServiceStatus
  }
}

interface ServiceStatus {
  status: 'ok' | 'error'
  detail: string
  heartbeat_at?: number | null
}

export interface TaskFilters {
  kind: 'note' | 'chat'
  page: number
  page_size?: number
  state?: string
  user_id?: number
  q?: string
}

const config = (signal?: AbortSignal) => ({ signal, suppressToast: true })
export const getAdminOverview = (signal?: AbortSignal) =>
  request.get<unknown, AdminOverview>('/admin/overview', config(signal))
export const getAdminResources = (signal?: AbortSignal) =>
  request.get<unknown, AdminResources>('/admin/resources', config(signal))
export const getAdminTasks = (params: TaskFilters, signal?: AbortSignal) =>
  request.get<unknown, AdminPage<AdminTask>>('/admin/tasks', { ...config(signal), params })
export const getAdminTask = (kind: string, id: string, signal?: AbortSignal) =>
  request.get<unknown, AdminTask>(`/admin/tasks/${kind}/${encodeURIComponent(id)}`, config(signal))
export const cancelAdminTask = (id: string) =>
  request.post(`/admin/tasks/${encodeURIComponent(id)}/cancel`, {}, config())
export const retryAdminTask = (task: AdminTask, requestId: string) =>
  request.post(
    `/admin/tasks/${encodeURIComponent(task.id)}/retry`,
    {
      request_id: requestId,
      expected_updated_at: task.updated_at,
    },
    config()
  )
export const getAdminUsers = (
  params: { q?: string; page: number; enabled?: boolean },
  signal?: AbortSignal
) => request.get<unknown, AdminPage<AdminUser>>('/admin/users', { ...config(signal), params })
export const createAdminUser = (username: string, password: string) =>
  request.post('/auth/users', { username, password }, config())
export const updateAdminUser = (id: number, data: { enabled?: boolean; password?: string }) =>
  request.patch(`/auth/users/${id}`, data, config())
