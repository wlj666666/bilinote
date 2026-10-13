import type { AnchorHTMLAttributes } from 'react'
import { downloadPrivateFile, privateFilePath } from '@/utils/privateFiles'

export default function PrivateLink({ href, onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <a {...props} href={href} onClick={event => {
    if (href && privateFilePath(href)) {
      event.preventDefault()
      void downloadPrivateFile(href).catch(() => {})
    } else onClick?.(event)
  }} />
}
