import { guides, home, guideIndex, repository, publicPages } from './content.mjs'

export function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  )
}

export function siteOptions(env = {}) {
  const input = (env.VITE_SITE_URL || '').trim()
  let origin = ''
  if (input) {
    const url = new URL(input)
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.pathname !== '/' ||
      url.search ||
      url.hash
    ) {
      throw new Error(
        'VITE_SITE_URL must be an http(s) origin without credentials, path, query or fragment'
      )
    }
    origin = url.origin
  }
  return {
    origin,
    googleVerification: (env.VITE_GOOGLE_SITE_VERIFICATION || '').trim(),
    bingVerification: (env.VITE_BING_SITE_VERIFICATION || '').trim(),
  }
}

function guideCards(exclude = '') {
  return guides
    .filter(g => g.path !== exclude)
    .map(
      g =>
        `<a class="guide-card" href="${g.path}"><span class="eyebrow">${g.category}</span><h3>${g.title}</h3><p>${g.description}</p><span class="text-link">阅读教程 <span aria-hidden="true">↗</span></span></a>`
    )
    .join('')
}

function header() {
  return `<a class="skip-link" href="#main">跳到正文</a><header class="site-header wrap"><a class="brand" href="/" aria-label="BiliNote 首页"><img src="/icon.svg" width="36" height="36" alt="" /><span>BiliNote<span class="brand-dot">.</span></span></a><nav aria-label="主导航"><a href="/#features">功能</a><a href="/guides/">教程</a><a href="/#download">下载与源码</a><a class="button button-small" href="/app">进入工作区 <span aria-hidden="true">↗</span></a></nav></header>`
}

function footer() {
  return `<footer class="site-footer wrap"><div><a class="brand" href="/">BiliNote<span class="brand-dot">.</span></a><p>从视频到可检索知识。</p></div><nav aria-label="页脚导航"><a href="/guides/">使用教程</a><a href="${repository}">GitHub</a><a href="${repository}/releases">版本下载</a><a href="${repository}/blob/main/LICENSE">MIT License</a><a href="/app">工作区</a></nav><p class="attribution">基于 <a href="https://github.com/JefferyHcool/BiliNote">JefferyHcool/BiliNote</a> 二次开发。</p></footer>`
}

function homepage() {
  return `<main id="main">
    <section class="hero wrap">
      <p class="eyebrow"><span class="status-dot" aria-hidden="true"></span> 开源 · AI 视频笔记与问答</p>
      <h1>把看过的视频，<br />变成<span class="accent">用得上的知识。</span></h1>
      <p class="hero-description">粘贴视频链接，整理章节与要点。<br class="desktop-break" />带着时间线回看原片，用笔记、思维导图和问答继续学习。</p>
      <div class="actions"><a class="button" href="/app">进入工作区 <span aria-hidden="true">↗</span></a><a class="button button-outline" href="${repository}">查看 GitHub 源码</a></div>
      <p class="hero-note">支持 B 站、YouTube 等平台与本地音视频 · 可 Docker 自部署</p>
    </section>
    <figure class="product-shot wrap"><div class="window-bar" aria-hidden="true"><span>● ● ●</span><span>BiliNote / 视频笔记工作区</span><span>↗</span></div><a href="/marketing/workspace.png" aria-label="查看完整的视频笔记工作区截图"><img src="/marketing/workspace.png" width="2549" height="1403" alt="BiliNote 实际工作区：左侧选择视频与模型，中间管理生成历史，右侧阅读带原片时间跳转的章节笔记" fetchpriority="high" /></a><figcaption>项目实际界面 · 从章节目录直接回到原片，保留知识的来处。</figcaption></figure>
    <section id="features" class="section wrap"><div class="section-intro"><p class="eyebrow">从输入到理解</p><h2>少一点来回拖动，<br />多一点理解和积累。</h2><p>BiliNote 将视频内容整理到一个工作区中，帮助你找到重点，也保留回看原文的入口。</p></div><div class="feature-list">
      <article><span class="feature-number">01</span><div><h3>视频变笔记，重点有结构</h3><p>支持哔哩哔哩、YouTube、抖音、TikTok、快手与本地音视频。优先复用字幕，也可使用语音转写或画面字幕 OCR，整理成带目录和要点的笔记。</p></div></article>
      <article><span class="feature-number">02</span><div><h3>回到原片，让信息有依据</h3><p>沿着章节时间跳转回看视频，对照带时间轴的原文参照。阅读总结时，仍然可以核对术语、数字与上下文。</p></div></article>
      <article><span class="feature-number">03</span><div><h3>换个角度，继续理解</h3><p>将笔记切换成思维导图，梳理知识结构；围绕视频继续提问，结合检索到的来源片段深入阅读。</p></div></article>
      <article><span class="feature-number">04</span><div><h3>保存下来，接入自己的工作流</h3><p>导出 Markdown，保留笔记历史。支持 Docker 自部署、账号登录和用户数据隔离；管理员可集中管理模型配置与任务。</p></div></article>
    </div></section>
    <section class="steps-section"><div class="wrap"><p class="eyebrow">开始使用</p><h2>一条视频，三步整理。</h2><ol class="steps"><li><span>01 / 输入</span><h3>放入视频链接或文件</h3><p>选择平台，粘贴链接，或上传本地音视频。</p></li><li><span>02 / 生成</span><h3>选择模型与笔记风格</h3><p>使用部署中已配置的模型，选择文字提取方式与格式。</p></li><li><span>03 / 学习</span><h3>阅读、回看、继续提问</h3><p>核对原文，梳理导图，将有用的内容导出保存。</p></li></ol></div></section>
    <section class="section wrap guides-section"><div class="section-heading"><div><p class="eyebrow">使用与部署</p><h2>从这里开始。</h2></div><a class="text-link" href="/guides/">全部教程 <span aria-hidden="true">↗</span></a></div><div class="guide-grid">${guideCards()}</div></section>
    <section class="faq-section wrap"><div><p class="eyebrow">你可能想了解</p><h2>关于 BiliNote</h2></div><div class="faq-list">
      <details><summary>BiliNote 是什么？</summary><p>BiliNote 是基于 MIT 许可证开源的 AI 视频笔记与问答工具。它将视频字幕或转写内容整理为结构化笔记，支持时间跳转、思维导图、Markdown 导出与视频问答。</p></details>
      <details><summary>需要自己配置模型吗？</summary><p>首次部署后，由管理员配置模型供应商和可用模型。普通用户使用部署中已开放的配置。模型服务是否收费取决于你选择的供应商；本地转写也需要相应模型与计算资源。</p></details>
      <details><summary>没有字幕的视频可以处理吗？</summary><p>可以根据部署配置使用语音转写；画面中有清晰字幕时，也可使用 OCR。识别效果受音质、画面、语言和模型影响，重要内容应对照原视频核对。</p></details>
      <details><summary>可以在自己的机器上部署吗？</summary><p>可以。项目提供 Docker 镜像和源码部署说明，也有桌面端和浏览器扩展。自部署可以在自己的环境中保存笔记；配置外部模型服务时，生成所需内容仍会发送给所选供应商。</p></details>
    </div></section>
    <section id="download" class="download-section wrap"><div><p class="eyebrow">开始积累你的知识</p><h2>在你的环境里，<br />用你选择的模型。</h2><p>阅读部署教程，或前往 Releases 查看桌面端发布包。</p></div><div class="download-actions"><a class="button" href="/guides/docker-deploy/">Docker 部署教程 <span aria-hidden="true">↗</span></a><a class="button button-outline" href="${repository}/releases">查看桌面端下载</a><a class="text-link" href="${repository}">GitHub 源码与浏览器扩展说明 ↗</a></div></section>
  </main>`
}

function article(guide) {
  return `<main id="main" class="article-layout wrap"><aside class="article-aside"><a class="text-link" href="/guides/">← 所有教程</a><nav aria-label="本页目录">${guide.sections.map(([title], i) => `<a href="#section-${i + 1}">${title}</a>`).join('')}</nav></aside><article class="prose"><p class="eyebrow">${guide.category} / BiliNote</p><h1>${guide.title}</h1><p class="article-lead">${guide.intro}</p><div class="article-body">${guide.sections.map(([title, body], i) => `<section id="section-${i + 1}"><h2>${title}</h2>${body}</section>`).join('')}</div><div class="article-cta"><p>准备好了？打开工作区，开始整理第一条视频。</p><a class="button" href="/app">进入工作区 ↗</a></div></article></main><section class="section wrap"><p class="eyebrow">继续阅读</p><h2>相关教程</h2><div class="guide-grid">${guideCards(guide.path)}</div></section>`
}

export function renderPage(page, options = {}) {
  const origin = options.origin || ''
  const url = origin ? `${origin}${page.path}` : ''
  const absolute = pathname => (origin ? `${origin}${pathname}` : pathname)
  const structuredData =
    page.path === '/'
      ? {
          '@context': 'https://schema.org',
          '@type': 'SoftwareApplication',
          name: 'BiliNote',
          description: home.description,
          applicationCategory: 'ProductivityApplication',
          operatingSystem: 'Web, Windows, macOS',
          ...(url ? { url } : {}),
          license: 'https://opensource.org/license/mit',
          sameAs: repository,
          image: absolute('/marketing/workspace.png'),
        }
      : {
          '@context': 'https://schema.org',
          '@type': page.path === '/guides/' ? 'CollectionPage' : 'TechArticle',
          name: page.title,
          headline: page.title,
          description: page.description,
          inLanguage: 'zh-CN',
          ...(url ? { url } : {}),
          isPartOf: {
            '@type': 'WebSite',
            name: 'BiliNote',
            ...(origin ? { url: `${origin}/` } : {}),
          },
        }
  const body =
    page.path === '/'
      ? homepage()
      : page.path === '/guides/'
        ? `<main id="main" class="guide-index wrap"><p class="eyebrow">BiliNote / 教程</p><h1>把工具用起来。</h1><p class="article-lead">从视频笔记到自部署，一步步完成你的第一份整理。</p><div class="guide-grid">${guideCards()}</div><p class="guide-index-note">更多模型、GPU 与平台配置说明，参见 <a href="${repository}#quick-start">GitHub 项目文档 ↗</a>。</p></main>`
        : article(page)
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHtml(page.title)}</title><meta name="description" content="${escapeHtml(page.description)}" />
<meta name="robots" content="index, follow" /><meta name="theme-color" content="#6e7f64" />
${url ? `<link rel="canonical" href="${escapeHtml(url)}" />` : ''}
<meta property="og:type" content="${page.path.startsWith('/guides/') && page.path !== '/guides/' ? 'article' : 'website'}" /><meta property="og:locale" content="zh_CN" /><meta property="og:site_name" content="BiliNote" />
<meta property="og:title" content="${escapeHtml(page.title)}" /><meta property="og:description" content="${escapeHtml(page.description)}" />${url ? `<meta property="og:url" content="${escapeHtml(url)}" />` : ''}
<meta property="og:image" content="${escapeHtml(absolute('/marketing/workspace.png'))}" /><meta property="og:image:alt" content="BiliNote 视频笔记工作区实际界面" />
<meta name="twitter:card" content="summary_large_image" /><meta name="twitter:title" content="${escapeHtml(page.title)}" /><meta name="twitter:description" content="${escapeHtml(page.description)}" /><meta name="twitter:image" content="${escapeHtml(absolute('/marketing/workspace.png'))}" />
${options.googleVerification ? `<meta name="google-site-verification" content="${escapeHtml(options.googleVerification)}" />` : ''}
${options.bingVerification ? `<meta name="msvalidate.01" content="${escapeHtml(options.bingVerification)}" />` : ''}
<link rel="icon" type="image/svg+xml" href="/icon.svg" /><link rel="stylesheet" href="/marketing/site.css" />
<script type="application/ld+json">${JSON.stringify(structuredData).replace(/</g, '\\u003c')}</script>
</head><body>${header()}${body}${footer()}</body></html>`
}

export function renderRobots(options = {}) {
  return `User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /admin\nDisallow: /settings\nDisallow: /onboarding\nDisallow: /api/\nDisallow: /static/\n${options.origin ? `\nSitemap: ${options.origin}/sitemap.xml\n` : ''}`
}

export function renderSitemap(options) {
  if (!options.origin) return null
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${publicPages.map(page => `\n  <url><loc>${escapeHtml(`${options.origin}${page.path}`)}</loc></url>`).join('')}\n</urlset>\n`
}

export function renderNotFound() {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><meta name="robots" content="noindex" /><title>页面未找到 - BiliNote</title><link rel="stylesheet" href="/marketing/site.css" /></head><body>${header()}<main id="main" class="guide-index wrap"><p class="eyebrow">404 / BiliNote</p><h1>这篇教程还不存在。</h1><p><a class="button" href="/guides/">浏览所有教程</a></p></main>${footer()}</body></html>`
}

export { publicPages, home, guideIndex }
