export const phaseLabels: Record<string, string> = {
  QUEUED: '排队中',
  PENDING: '排队中',
  RUNNING: '处理中',
  PARSING: '解析链接',
  DOWNLOADING: '下载媒体',
  DETECTING_SUBTITLES: '定位字幕',
  EXTRACTING_SUBTITLES: '提取字幕',
  TRANSCRIBING: '转写中',
  SUMMARIZING: '生成笔记',
  FORMATTING: '整理格式',
  SAVING: '保存中',
  WAITING_RESOURCE: '等待资源',
  WAITING_RETRY: '等待重试',
  SUCCESS: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  UNKNOWN: '未知',
  queued: '排队中',
  running: '回答中',
  completed: '已完成',
  failed: '失败',
}

export function formatTime(value?: number | null) {
  return value == null
    ? '未知'
    : new Date(value * 1000).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
}

export function duration(value?: number | null) {
  if (value == null) return '未知'
  const seconds = Math.floor(value)
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
}

export function bytes(value?: number | null) {
  if (value == null) return '未知'
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
