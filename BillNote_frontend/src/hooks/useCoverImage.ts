import { useEffect, useState } from 'react'
import request from '@/utils/request'
import { privateFilePath } from '@/utils/privateFiles'

/** Fetch external covers through the authenticated proxy; never put tokens in URLs. */
export function useCoverImage(source: string | undefined, enabled = true) {
  const [imageUrl, setImageUrl] = useState('')
  useEffect(() => {
    let cancelled = false
    let objectUrl = ''
    setImageUrl('')
    if (source && enabled) {
      const privatePath = privateFilePath(source)
      void request.get<unknown, Blob>(privatePath || '/image_proxy', { params: privatePath ? undefined : { url: source }, responseType: 'blob', suppressToast: true })
        .then(blob => {
          if (cancelled) return
          objectUrl = URL.createObjectURL(blob)
          setImageUrl(objectUrl)
        }).catch(() => {})
    }
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [source, enabled])
  return imageUrl
}
