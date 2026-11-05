# BiliNote 官网与搜索收录

公开页面与现有工作区共用服务、端口和隧道：

| 地址 | 内容 |
| --- | --- |
| `/` | 产品首页、截图、功能、下载与 GitHub 入口 |
| `/guides/` | 教程目录 |
| `/guides/bilibili-video-notes/` | B 站视频笔记教程 |
| `/guides/youtube-video-summary/` | YouTube 视频总结教程 |
| `/guides/docker-deploy/` | Docker 部署和隧道访问教程 |
| `/app` | 原有登录与笔记工作区 |
| `/admin`、`/settings/*` | 原有管理与设置页面 |

首页与教程直接返回完整 HTML，不依赖登录、后端就绪检查或浏览器执行 React。
页面提供独立标题与描述、分享卡片和与正文一致的 JSON-LD；工作区入口带 `noindex`，
robots 排除工作区、管理页、API 和私人截图。robots 仅用于爬虫提示，原有后端鉴权继续生效。

## 保留现有 Cloudflare Tunnel

继续启动原服务到 `3015`，然后使用原命令即可：

```bash
cd /home/unbekannter/bilinote/BiliNote
mkdir -p logs
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:3015 2>&1 | tee logs/cloudflared.log
```

公网地址打开官网，点击「进入工作区」访问同域名 `/app`。
官网链接、样式和截图都使用根相对路径，API 和私人文件的代理保持原配置。
隧道命令、API 端口和登录鉴权无需因官网功能调整。

源码开发仍在 `BillNote_frontend` 中运行 `pnpm dev`；Docker Compose 部署需重建前端：

```bash
docker compose up -d --build frontend
```

GPU Compose 使用原来的 `-f docker-compose.gpu.yml`。单镜像部署需重新构建镜像。
Tauri 开发和构建使用 `--mode tauri`，保留原桌面入口，不生成官网。

## 配置固定域名和站长验证

快速隧道的域名可能随重启变化，不适合作为长期收录地址。因此默认不写入 canonical、
`og:url` 和 sitemap 的域名，也不从 Host 或请求参数推导正式域名。未配置时
`/sitemap.xml` 返回 404，避免提交 localhost、示例或临时域名。

有固定域名后，在仓库根目录 `.env` 中配置：

```dotenv
VITE_SITE_URL=https://你的固定域名
VITE_GOOGLE_SITE_VERIFICATION=Google提供的content值
VITE_BING_SITE_VERIFICATION=Bing提供的content值
```

`VITE_SITE_URL` 只接受 http(s) 域名及可选端口，不能包含子路径、查询参数、账号密码或片段。
验证码可留空。它们是公开的站点所有权验证标记，不能填写账号密码或 API key。

- 源码开发：修改后重启 Vite；构建时从根目录 `.env` 读取。
- Compose：这些值已通过 build args 传入，修改后重建 `frontend`。
- 单镜像：通过 `docker build --build-arg VITE_SITE_URL=https://你的固定域名` 等参数传入。
- 预构建镜像：这些是构建时设置，仅修改容器运行时环境不会更新静态页面。需要自行构建包含正式域名的镜像。

固定域名配置后，所有公开页面输出对应的绝对 canonical 和分享地址，
`/sitemap.xml` 列出 5 个公开页面，`/robots.txt` 加入 sitemap 的绝对地址。
工作区和用户内容不会进入 sitemap。新增教程时维护 `BillNote_frontend/site/content.mjs`，
导航、HTML 和 sitemap 会同步生成。

## 提交与查看搜索效果

1. 在 [Google Search Console](https://search.google.com/search-console) 和
   [Bing Webmaster Tools](https://www.bing.com/webmasters/) 添加正式域名，按提示验证。
   可使用上面的 meta 验证配置，或使用站长平台要求的 DNS 验证。
2. 用 URL 检查确认首页能抓取，查看原始 HTML 是否包含正文与正确 canonical。
3. 提交 `https://你的固定域名/sitemap.xml`。提交和验证需要站点所有者在自己的账号中完成。
4. 后续查看搜索曝光、点击、查询词和收录页面。不能用本地测试证明已被搜索引擎收录；
   上线与提交后，收录和展示仍由搜索引擎决定。

没有嵌入第三方访问追踪脚本。站长平台可先提供自然搜索表现；如需统计 GitHub 跳转和
下载按钮点击，可在明确采用的统计服务后补充。

## 本地验证

```bash
cd BillNote_frontend
pnpm test:seo
pnpm check:ci
pnpm build
# 服务运行后检查真实 Nginx 入口（含首页重定向、静态资源、工作区和 API）：
pnpm check:seo:http http://127.0.0.1:3015
# 也可将地址替换为 cloudflared 输出的公网网址。
```

SEO 测试使用独立临时后端，仅验证代理转发，不接触真实用户、密码或数据库。
覆盖公开 HTML、链接、元数据、sitemap、404、工作区深链接、API POST 和授权头、
私人文件代理及隧道 Host 访问。使用生产构建后，还应检查 `/app` 与 `/admin/tasks`
刷新能加载 React 入口，`/guides/不存在/` 返回 404。

参考：[Google sitemap 文档](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)、
[canonical 文档](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)。
