import type { ImgHTMLAttributes } from 'react'
import { useCoverImage } from '@/hooks/useCoverImage'
import { privateFilePath } from '@/utils/privateFiles'

export default function PrivateImage({ src, ...props }: ImgHTMLAttributes<HTMLImageElement>) {
  const privatePath = privateFilePath(src)
  const imageUrl = useCoverImage(privatePath ? src : undefined)
  return <img {...props} src={privatePath ? imageUrl || undefined : src} />
}
