import { create } from 'zustand'
import { delete_task, generateNote, type GenerateNoteRequest } from '@/services/note.ts'
import { v4 as uuidv4 } from 'uuid'
import toast from 'react-hot-toast'
import request from '@/utils/request'
import { getAuthToken } from '@/utils/authSession'


export type TaskStatus = 'PENDING' | 'RUNNING' | 'PARSING' | 'DOWNLOADING' | 'DETECTING_SUBTITLES' | 'EXTRACTING_SUBTITLES' | 'TRANSCRIBING' | 'SUMMARIZING' | 'FORMATTING' | 'SAVING' | 'SUCCESS' | 'FAILED'

export interface AudioMeta {
  cover_url: string
  duration: number
  file_path: string
  platform: string
  raw_info: any
  title: string
  video_id: string
}

export interface Segment {
  start: number
  end: number
  text: string
  speaker?: string
}

export interface Transcript {
  full_text: string
  language: string
  raw: any
  segments: Segment[]
}
export interface Markdown {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at: string
}

export interface Task {
  id: string
  markdown: string|Markdown [] //为了兼容之前的笔记
  transcript: Transcript
  status: TaskStatus
  message?: string
  audioMeta: AudioMeta
  createdAt: string
  platform: string
  formData: GenerateNoteRequest
}

interface TaskStore {
  tasks: Task[]
  ownerId: number | null
  setOwner: (id: number | null) => void
  loadTasks: () => Promise<void>
  currentTaskId: string | null
  addPendingTask: (taskId: string, platform: string, formData: GenerateNoteRequest) => void
  updateTaskContent: (id: string, data: Partial<Omit<Task, 'id' | 'createdAt'>>) => void
  removeTask: (id: string) => void
  clearTasks: () => void
  setCurrentTask: (taskId: string | null) => void
  getCurrentTask: () => Task | null
  retryTask: (id: string, payload?: GenerateNoteRequest) => void
}

export const useTaskStore = create<TaskStore>((set, get) => ({
      tasks: [],
      ownerId: null,
      currentTaskId: null,
      setOwner: ownerId => set({ ownerId, tasks: [], currentTaskId: null }),
      loadTasks: async () => {
        const ownerId = get().ownerId
        const token = getAuthToken()
        if (ownerId == null || !token) return
        const tasks: Task[] = []
        let offset = 0
        while (true) {
          const page = await request.get<unknown, { tasks: Task[]; has_more: boolean }>('/tasks', { params: { offset, limit: 100 }, sessionToken: token })
          if (ownerId !== get().ownerId || token !== getAuthToken()) return
          tasks.push(...page.tasks)
          if (!page.has_more) break
          offset += 100
        }
        const selected = sessionStorage.getItem(`bilinote-current-task:${ownerId}`)
        tasks.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
        set({ tasks, currentTaskId: tasks.some(t => t.id === selected) ? selected : null })
      },

      addPendingTask: (taskId: string, platform: string, formData: any) =>

        set(state => ({
          tasks: [
            {
              formData: formData,
              id: taskId,
              status: 'PENDING',
              markdown: '',
              platform: platform,
              transcript: {
                full_text: '',
                language: '',
                raw: null,
                segments: [],
              },
              createdAt: new Date().toISOString(),
              audioMeta: {
                cover_url: '',
                duration: 0,
                file_path: '',
                platform: '',
                raw_info: null,
                title: '',
                video_id: '',
              },
            },
            ...state.tasks,
          ],
          currentTaskId: taskId, // 默认设置为当前任务
        })),

      updateTaskContent: (id, data) => {
          set(state => ({
            tasks: state.tasks.map(task => {
              if (task.id !== id) return task

              if (task.status === 'SUCCESS' && data.status === 'SUCCESS') return task

              // 如果是 markdown 字符串，封装为版本
              if (typeof data.markdown === 'string') {
                const prev = task.markdown
                const newVersion: Markdown = {
                  ver_id: `${task.id}-${uuidv4()}`,
                  content: data.markdown,
                  style: task.formData.style || '',
                  model_name: task.formData.model_name || '',
                  created_at: new Date().toISOString(),
                }

                let updatedMarkdown: Markdown[]
                if (Array.isArray(prev)) {
                  updatedMarkdown = [newVersion, ...prev]
                } else {
                  updatedMarkdown = [
                    newVersion,
                    ...(typeof prev === 'string' && prev
                        ? [{
                          ver_id: `${task.id}-${uuidv4()}`,
                          content: prev,
                          style: task.formData.style || '',
                          model_name: task.formData.model_name || '',
                          created_at: new Date().toISOString(),
                        }]
                        : []),
                  ]
                }

                return {
                  ...task,
                  ...data,
                  markdown: updatedMarkdown,
                }
              }

              return { ...task, ...data }
            }),
          }))
          if (data.markdown !== undefined) {
            const task = get().tasks.find(task => task.id === id)
            if (task) void request.patch(`/tasks/${id}/display`, { markdown: task.markdown }, { suppressToast: true }).catch(() => {})
          }
      },

      getCurrentTask: () => {
        const currentTaskId = get().currentTaskId
        return get().tasks.find(task => task.id === currentTaskId) || null
      },
      retryTask: async (id: string, payload?: any) => {

        if (!id){
          toast.error('任务不存在')
          return
        }
        const task = get().tasks.find(task => task.id === id)
        console.log('retry',task)
        if (!task) return

        const newFormData = payload || task.formData
        if ((newFormData.text_extraction_method || 'asr') !== (task.formData.text_extraction_method || 'asr')) {
          try {
            const response = await generateNote({ ...newFormData, task_id: undefined })
            get().addPendingTask(response.task_id, newFormData.platform, newFormData)
          } catch (error) {
            console.error('切换提取方式失败', error)
          }
          return
        }
        try {
          await generateNote({
            ...newFormData,
            task_id: id,
          })
        } catch (e: any) {
          // 就绪门禁：转写模型未下载好。不要把任务标成 PENDING（会一直转），
          // 给提示让用户先去下载。
          if (e?.data?.reason === 'transcriber_model_not_ready') {
            toast.error(
              e?.data?.downloading
                ? '转写模型正在下载中，请稍候再重试'
                : '转写模型尚未下载，请先去「设置 → 音频转写配置」页下载',
            )
            return
          }
          console.error('重试任务失败：', e)
          return
        }

        set(state => ({
          tasks: state.tasks.map(t =>
              t.id === id
                  ? {
                    ...t,
                    formData: newFormData, // ✅ 显式更新 formData
                    status: 'PENDING',
                    message: '',
                  }
                  : t
          ),
        }))
      },


      removeTask: async id => {
        const ownerId = get().ownerId
        try {
          await delete_task({ task_id: id })
          if (ownerId !== get().ownerId) return
          set(state => ({ tasks: state.tasks.filter(task => task.id !== id), currentTaskId: state.currentTaskId === id ? null : state.currentTaskId }))
        } catch { /* The request interceptor shows the error; keep the task visible. */ }
      },

      clearTasks: () => set({ tasks: [], currentTaskId: null }),

      setCurrentTask: taskId => {
        const ownerId = get().ownerId
        if (ownerId != null) {
          const key = `bilinote-current-task:${ownerId}`
          if (taskId) sessionStorage.setItem(key, taskId)
          else sessionStorage.removeItem(key)
        }
        set({ currentTaskId: taskId })
      },
    }))
