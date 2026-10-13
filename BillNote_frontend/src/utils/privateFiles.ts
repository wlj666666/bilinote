import request from '@/utils/request'

export function privateFilePath(source: string | undefined): string | null {
  if (!source) return null
  let path: string
  try { path = new URL(source, window.location.href).pathname } catch { return null }
  if (path.startsWith('/api/files/')) return path.slice('/api'.length)
  if (path.startsWith('/static/')) return '/files/static/' + path.slice('/static/'.length)
  if (path.startsWith('/uploads/')) return '/files/uploads/' + path.slice('/uploads/'.length)
  return null
}

export async function downloadPrivateFile(source: string) {
  const path = privateFilePath(source)
  if (!path) return
  const blob = await request.get<unknown, Blob>(path, { responseType: 'blob', timeout: 60000 })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = decodeURIComponent(path.split('/').pop() || 'download')
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
