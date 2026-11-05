import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'
import { publicPages, renderPage, renderRobots, renderSitemap, siteOptions } from './render.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const options = siteOptions({ VITE_SITE_URL: 'https://bilinote.example/' })

test('every public page has visible HTML content, unique metadata, and matching canonical', () => {
  const titles = new Set()
  const descriptions = new Set()
  for (const page of publicPages) {
    const html = renderPage(page, options)
    assert.match(html, /<html lang="zh-CN">/)
    assert.match(html, /<main id="main"/)
    assert.equal((html.match(/<h1[ >]/g) || []).length, 1)
    assert.ok(html.includes(`<link rel="canonical" href="https://bilinote.example${page.path}"`))
    assert.ok(
      html.includes(`<meta property="og:url" content="https://bilinote.example${page.path}"`)
    )
    assert.match(html, /<meta name="robots" content="index, follow"/)
    assert.doesNotMatch(html, /src="\/src\/|type="module"|id="root"/)
    const data = JSON.parse(html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s)[1])
    assert.equal(data.url, `https://bilinote.example${page.path}`)
    assert.equal(data.description, page.description)
    assert.ok(!titles.has(page.title), 'duplicate title')
    assert.ok(!descriptions.has(page.description), 'duplicate description')
    titles.add(page.title)
    descriptions.add(page.description)
  }
})

test('sitemap contains only public canonical URLs, never account or API pages', () => {
  const sitemap = renderSitemap(options)
  const urls = [...sitemap.matchAll(/<loc>(.*?)<\/loc>/g)].map(m => m[1])
  assert.deepEqual(
    urls,
    publicPages.map(page => `https://bilinote.example${page.path}`)
  )
  assert.doesNotMatch(sitemap, /\/app|\/admin|\/settings|\/api\/|localhost|127\.0\.0\.1/)
  const robots = renderRobots(options)
  assert.match(robots, /Sitemap: https:\/\/bilinote.example\/sitemap.xml/)
  for (const route of ['/app', '/admin', '/settings', '/api/', '/static/'])
    assert.ok(robots.includes(`Disallow: ${route}`))
})

test('unset permanent domain produces no invented canonical or sitemap domain', () => {
  const blank = siteOptions()
  assert.equal(renderSitemap(blank), null)
  assert.doesNotMatch(renderRobots(blank), /Sitemap:/)
  const html = renderPage(publicPages[0], blank)
  assert.doesNotMatch(
    html,
    /rel="canonical"|property="og:url"|trycloudflare\.com|https?:\/\/localhost/
  )
  assert.match(html, /href="\/app"/)
})

test('site URL validation and verification markup handle unsafe input', () => {
  for (const url of [
    'javascript:alert(1)',
    'https://user:password@site.example',
    'https://site.example/subpath',
    'https://site.example/?x=1',
    'https://site.example/#fragment',
  ]) {
    assert.throws(() => siteOptions({ VITE_SITE_URL: url }))
  }
  const html = renderPage(publicPages[0], {
    ...options,
    googleVerification: '\"><script>alert(1)</script>',
    bingVerification: 'bing-token',
  })
  assert.doesNotMatch(html, /<script>alert/)
  assert.match(html, /name="google-site-verification" content="&quot;&gt;&lt;script&gt;/)
  assert.match(html, /name="msvalidate.01" content="bing-token"/)
})

test('tutorial local anchors and public navigation lead to existing content', () => {
  const publicPaths = new Set(publicPages.map(page => page.path))
  for (const page of publicPages) {
    const html = renderPage(page, options)
    const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]))
    for (const [, href] of html.matchAll(/\bhref="([^"]+)"/g)) {
      if (href.startsWith('#')) assert.ok(ids.has(href.slice(1)), `missing anchor ${href}`)
      else if (
        href.startsWith('/') &&
        href !== '/app' &&
        !href.startsWith('/marketing/') &&
        href !== '/icon.svg'
      ) {
        assert.ok(publicPaths.has(href.split('#')[0]), `unknown public link ${href}`)
      }
    }
  }
})

let vite
let backend
let origin
let priorSiteUrl

before(async () => {
  priorSiteUrl = process.env.VITE_SITE_URL
  process.env.VITE_SITE_URL = options.origin
  backend = http.createServer((req, res) => {
    const chunks = []
    req.on('data', chunk => chunks.push(chunk))
    req.on('end', () => {
      res.setHeader('Content-Type', 'application/json')
      res.end(
        JSON.stringify({
          url: req.url,
          method: req.method,
          authorization: req.headers.authorization || '',
          body: Buffer.concat(chunks).toString(),
        })
      )
    })
  })
  await new Promise((resolve, reject) => {
    backend.once('error', reject)
    backend.listen(0, '127.0.0.1', resolve)
  })
  const target = `http://127.0.0.1:${backend.address().port}`
  vite = await createServer({
    root,
    mode: 'test',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true, include: [] },
    server: {
      host: '127.0.0.1',
      port: 0,
      open: false,
      proxy: { '/api': { target }, '/static': { target } },
    },
  })
  await vite.listen()
  origin = `http://127.0.0.1:${vite.httpServer.address().port}`
})

after(async () => {
  await vite?.close()
  if (backend?.listening) await new Promise(resolve => backend.close(resolve))
  if (priorSiteUrl === undefined) delete process.env.VITE_SITE_URL
  else process.env.VITE_SITE_URL = priorSiteUrl
})

test('Vite serves full public pages, screenshots and CSS without backend auth', async () => {
  for (const page of publicPages) {
    const response = await fetch(`${origin}${page.path}?source=smoke`)
    assert.equal(response.status, 200)
    assert.ok((await response.text()).includes(`<title>${page.title}</title>`))
  }
  for (const asset of ['/marketing/site.css', '/marketing/workspace.png', '/icon.svg']) {
    const response = await fetch(`${origin}${asset}`)
    assert.equal(response.status, 200)
    assert.ok((await response.arrayBuffer()).byteLength > 100)
  }
})

test('workspace, admin and settings deep links keep the original React entry', async () => {
  for (const pathname of [
    '/app',
    '/app/',
    '/admin',
    '/admin/tasks',
    '/settings/model',
    '/settings/model/new',
    '/onboarding',
  ]) {
    const response = await fetch(`${origin}${pathname}`)
    assert.equal(response.status, 200, pathname)
    assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow')
    const html = await response.text()
    assert.match(html, /id="root"/)
    assert.match(html, /src="\/src\/main.tsx"/)
    assert.match(html, /name="robots" content="noindex, nofollow"/)
  }
})

test('public routing normalizes URLs and returns a real 404 for unknown guides', async () => {
  const redirect = await fetch(`${origin}/guides?from=test`, { redirect: 'manual' })
  assert.equal(redirect.status, 308)
  assert.equal(redirect.headers.get('location'), '/guides/?from=test')
  const index = await fetch(`${origin}/index.html`, { redirect: 'manual' })
  assert.equal(index.status, 308)
  assert.equal(index.headers.get('location'), '/')
  const missing = await fetch(`${origin}/guides/not-a-guide/`)
  assert.equal(missing.status, 404)
  assert.match(await missing.text(), /name="robots" content="noindex"/)
  const head = await fetch(`${origin}/guides/`, { method: 'HEAD' })
  assert.equal(head.status, 200)
  assert.equal(await head.text(), '')
})

test('API POST, bearer tokens and private file requests still reach the proxy intact', async () => {
  const body = JSON.stringify({ username: 'test-only', password: 'not-a-real-password' })
  const response = await fetch(`${origin}/api/auth/login?test=1`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer smoke-test' },
    body,
  })
  assert.deepEqual(await response.json(), {
    url: '/api/auth/login?test=1',
    method: 'POST',
    authorization: 'Bearer smoke-test',
    body,
  })
  const file = await fetch(`${origin}/static/screenshots/test.png`, {
    headers: { Authorization: 'Bearer private-file-test' },
  })
  const payload = await file.json()
  assert.equal(payload.url, '/static/screenshots/test.png')
  assert.equal(payload.authorization, 'Bearer private-file-test')
})

test('forwarded tunnel hostname preserves same-origin workspace and asset links', async () => {
  const html = await new Promise((resolve, reject) => {
    http
      .get(
        `${origin}/`,
        { headers: { Host: 'temporary-test.trycloudflare.com', 'X-Forwarded-Proto': 'https' } },
        res => {
          let body = ''
          res.on('data', chunk => {
            body += chunk
          })
          res.on('end', () => {
            assert.equal(res.statusCode, 200)
            resolve(body)
          })
        }
      )
      .on('error', reject)
  })
  assert.match(html, /href="\/app"/)
  assert.match(html, /src="\/marketing\/workspace.png"/)
  assert.doesNotMatch(html, /127\.0\.0\.1:3015|temporary-test\.trycloudflare\.com/)
})

test('Tauri development explicitly retains desktop mode', async () => {
  const tauri = JSON.parse(await readFile(`${root}/src-tauri/tauri.conf.json`, 'utf8'))
  assert.match(tauri.build.beforeDevCommand, /--mode tauri/)
  assert.match(tauri.build.beforeBuildCommand, /--mode tauri/)
})
