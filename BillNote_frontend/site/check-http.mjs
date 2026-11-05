import assert from 'node:assert/strict'
import { publicPages } from './content.mjs'

// Read-only smoke check for the real Nginx / tunnel entry, not Vite's dev router.
const origin = new URL(process.argv[2] || 'http://127.0.0.1:3015').origin
async function request(pathname, options = {}) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fetch(`${origin}${pathname}`, {
        signal: AbortSignal.timeout(20_000),
        redirect: 'manual',
        ...options,
      })
    } catch (error) {
      // Quick-tunnel DNS can briefly fail while the new hostname propagates.
      // Retry network resolution only; HTTP failures must still fail the check.
      if (attempt >= 3 || !['ENOTFOUND', 'EAI_AGAIN'].includes(error.cause?.code)) throw error
      await new Promise(resolve => setTimeout(resolve, 1500))
    }
  }
}

for (const page of publicPages) {
  const response = await request(page.path)
  assert.equal(response.status, 200, `${page.path} must return HTML without redirects`)
  const html = await response.text()
  assert.ok(html.includes(`<title>${page.title}</title>`))
  assert.match(html, /<h1[ >]/)
  assert.doesNotMatch(html, /id="root"/)
  console.log(`PASS ${page.path}`)
}

for (const route of ['/app', '/app/', '/admin/tasks', '/settings/model']) {
  const response = await request(route)
  assert.equal(response.status, 200, route)
  const html = await response.text()
  assert.match(html, /id="root"/)
  assert.match(html, /name="robots" content="noindex, nofollow"/)
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)]
  assert.ok(assets.length > 0, 'workspace must reference built assets')
  for (const [, asset] of assets) {
    const result = await request(asset, { method: 'HEAD' })
    assert.equal(result.status, 200, asset)
    assert.doesNotMatch(result.headers.get('content-type'), /text\/html/)
  }
  console.log(`PASS ${route} and workspace assets`)
}

for (const asset of ['/marketing/site.css', '/marketing/workspace.png', '/icon.svg']) {
  const response = await request(asset, { method: 'HEAD' })
  assert.equal(response.status, 200, asset)
  assert.doesNotMatch(response.headers.get('content-type'), /text\/html/)
}
assert.equal((await request('/guides/does-not-exist/')).status, 404)
assert.equal((await request('/marketing/does-not-exist.png')).status, 404)
const index = await request('/index.html')
assert.ok([301, 308].includes(index.status))
assert.equal(index.headers.get('location'), '/')
const guideRedirect = await request('/guides')
assert.ok([301, 308].includes(guideRedirect.status))
assert.equal(guideRedirect.headers.get('location'), '/guides/')
const robots = await request('/robots.txt')
assert.equal(robots.status, 200)
assert.match(await robots.text(), /Disallow: \/app/)
const sitemap = await request('/sitemap.xml')
assert.ok([200, 404].includes(sitemap.status))
if (sitemap.status === 200) {
  assert.equal(((await sitemap.text()).match(/<loc>/g) || []).length, publicPages.length)
  console.log('PASS sitemap enabled')
} else {
  console.log('PASS sitemap disabled until VITE_SITE_URL is configured')
}
const health = await request('/api/sys_check')
assert.equal(health.status, 200)
assert.equal((await health.json()).code, 0)
assert.equal((await request('/api/tasks')).status, 401)
console.log(`PASS ${origin}: static pages, redirects, 404, assets, workspace and API auth`)
