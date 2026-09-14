import request from '@/utils/request'
import toast from 'react-hot-toast'
import type { AudioMeta, Transcript, TaskStatus } from '@/store/taskStore'

export interface GenerateNoteRequest {
  video_url: string
  platform: string
  quality: "fast" | "medium" | "slow"
  model_name: string
  provider_id: string
  task_id?: string
  format?: Array<string>
  style?: string
  extras?: string
  text_extraction_method?: "asr" | "ocr"
  video_understanding?: boolean
  video_interval?: number
  grid_size?: [number, number]
  link?: boolean
  screenshot?: boolean
}

export const generateNote = async (data: GenerateNoteRequest) => {
  try {
    console.log('generateNote', data)
    const response = await request.post<unknown, { task_id: string }>('/generate_note', data)

    if (!response) throw new Error('任务提交未返回有效结果')
    toast.success('笔记生成任务已提交！')

    console.log('res', response)
    // 成功提示

    return response
  } catch (e: any) {
    console.error('❌ 请求出错', e)

    // 错误提示
    // toast.error('笔记生成失败，请稍后重试')

    throw e // 抛出错误以便调用方处理
  }
}

export const delete_task = async ({ video_id, platform }: { video_id: string; platform: string }) => {
  try {
    const data = {
      video_id,
      platform,
    }
    const res = await request.post('/delete_task', data)


      toast.success('任务已成功删除')
      return res
  } catch (e) {
    toast.error('请求异常，删除任务失败')
    console.error('❌ 删除任务失败:', e)
    throw e
  }
}

export const get_task_status = async (task_id: string) => {
  try {
    // 成功提示

    return await request.get<unknown, { status: TaskStatus; message?: string; result: { markdown: string; transcript: Transcript; audio_meta: AudioMeta } }>('/task_status/' + task_id)
  } catch (e) {
    console.error('❌ 请求出错', e)

    // 错误提示
    toast.error('笔记生成失败，请稍后重试')

    throw e // 抛出错误以便调用方处理
  }
}
