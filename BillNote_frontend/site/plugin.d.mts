import type { Plugin } from 'vite'

export interface SiteOptions {
  origin: string
  googleVerification: string
  bingVerification: string
}

export function publicSite(options: SiteOptions): Plugin
