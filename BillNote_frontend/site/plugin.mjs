import { publicPages, renderNotFound, renderPage, renderRobots, renderSitemap } from './render.mjs'

export function publicSite(options) {
  const pages = new Map(publicPages.map(page => [page.path, renderPage(page, options)]))
  const robots = renderRobots(options)
  const sitemap = renderSitemap(options)

  const middleware =
    (preview = false) =>
    (req, res, next) => {
      if (!['GET', 'HEAD'].includes(req.method || '')) return next()
      const [pathname, query] = (req.url || '/').split('?')
      const send = (status, type, body) => {
        res.statusCode = status
        res.setHeader('Content-Type', `${type}; charset=utf-8`)
        res.setHeader('Cache-Control', 'no-cache')
        res.end(req.method === 'HEAD' ? undefined : body)
      }
      if (pathname === '/robots.txt') return send(200, 'text/plain', robots)
      if (pathname === '/sitemap.xml') {
        return sitemap
          ? send(200, 'application/xml', sitemap)
          : send(
              404,
              'text/plain',
              'Set VITE_SITE_URL to your permanent public origin to enable sitemap.xml.\n'
            )
      }
      if (pathname === '/index.html') {
        res.statusCode = 308
        res.setHeader('Location', `/${query ? `?${query}` : ''}`)
        return res.end()
      }
      const normalized = pathname === '/' ? '/' : `${pathname.replace(/\/$/, '')}/`
      if (pages.has(normalized)) {
        if (pathname !== normalized) {
          res.statusCode = 308
          res.setHeader('Location', `${normalized}${query ? `?${query}` : ''}`)
          return res.end()
        }
        return send(200, 'text/html', pages.get(normalized))
      }
      if (pathname === '/guides' || pathname.startsWith('/guides/')) {
        return send(404, 'text/html', renderNotFound())
      }
      // Leave API/static proxies and Vite assets alone; only rewrite app documents.
      if (/^\/(app(?:\/|$)|admin(?:\/|$)|settings(?:\/|$)|onboarding(?:\/|$))/.test(pathname)) {
        res.setHeader('X-Robots-Tag', 'noindex, nofollow')
        req.url = `${preview ? '/app/index.html' : '/index.html'}${query ? `?${query}` : ''}`
      }
      next()
    }

  return {
    name: 'bilinote-public-site',
    enforce: 'post',
    configureServer(server) {
      server.middlewares.use(middleware())
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware(true))
    },
    generateBundle: {
      order: 'post',
      handler(_outputOptions, bundle) {
        const app = bundle['index.html']
        if (!app || app.type !== 'asset') throw new Error('Missing workspace index.html')
        this.emitFile({ type: 'asset', fileName: 'app/index.html', source: app.source })
        app.source = pages.get('/')
        for (const [pathname, source] of pages) {
          if (pathname !== '/')
            this.emitFile({ type: 'asset', fileName: `${pathname.slice(1)}index.html`, source })
        }
        this.emitFile({ type: 'asset', fileName: 'robots.txt', source: robots })
        if (sitemap) this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: sitemap })
        this.emitFile({ type: 'asset', fileName: '404.html', source: renderNotFound() })
      },
    },
  }
}
