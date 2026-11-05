export const repository = 'https://github.com/Brief4code/bilinote'

export const guides = [
  {
    path: '/guides/bilibili-video-notes/',
    title: 'B 站视频怎么生成笔记？',
    description:
      '用 BiliNote 将哔哩哔哩视频整理为带章节、时间跳转的 AI 笔记，再导出 Markdown 或切换思维导图。',
    category: '视频笔记',
    intro:
      '看课程、讲座或长视频时，可以先用 BiliNote 整理结构，再沿着时间标记回到原片核对。下面从视频链接开始，完成一份可回看的笔记。',
    sections: [
      [
        '开始前准备什么',
        '<p>先部署 BiliNote，或打开你已有的部署。首次部署由管理员创建账号，并在设置中配置模型供应商和可用模型；普通账号使用管理员已开放的配置。</p><p>尚未部署时，先阅读 <a href="/guides/docker-deploy/">Docker 部署教程</a>。网页注册创建的是普通用户，不能代替首次管理员初始化。</p>',
      ],
      [
        '1. 粘贴 B 站视频链接',
        '<p>进入工作区，新建笔记，选择「哔哩哔哩」，粘贴视频页面链接。选择可用模型和笔记风格，例如「精简」「详细」或「教程」。</p>',
      ],
      [
        '2. 选择文字来源和笔记格式',
        '<p>程序优先复用可用的平台字幕；没有合适字幕时，使用所选的语音转写或画面字幕 OCR。OCR 适合画面中清晰、位置相对稳定的字幕，识别结果仍需核对。</p><p>勾选「目录」「原片跳转」和「AI 总结」，便于快速浏览和回看片段。画面理解需要选择支持图片输入的模型，不是普通字幕笔记的必要条件。</p>',
      ],
      [
        '3. 生成后对照原文',
        '<p>提交任务后，在历史记录中查看进度。完成后阅读章节和要点，点击原片时间链接回到相应位置；打开「原文参照」检查字幕或转写片段。</p><p>重要的人名、数字、公式和引用应对照原视频确认。AI 笔记是整理辅助，不能替代对原片的核验。</p>',
      ],
      [
        '4. 导出与继续提问',
        '<p>使用「导出 Markdown」保存笔记，或切换思维导图查看知识结构。也可以打开「AI 问答」，围绕这条视频继续提问，并查看返回的来源片段。</p>',
      ],
      [
        '视频无法下载或没有字幕怎么办',
        '<p>先确认视频本身可访问，以及部署环境能访问对应平台。部分视频可能需要有效的平台 Cookie，具体取决于平台访问限制。无字幕时可以尝试语音转写；有清晰硬字幕时可以选择 OCR。</p><p>仅处理你有权访问和使用的素材。更多配置和排查方法见 <a href="https://github.com/Brief4code/bilinote#quick-start">项目部署说明</a>。</p>',
      ],
    ],
  },
  {
    path: '/guides/youtube-video-summary/',
    title: '如何用 BiliNote 总结 YouTube 视频',
    description:
      '从 YouTube 链接生成中文视频摘要和结构化笔记，了解字幕复用、语音转写、原文核对及 Markdown 导出流程。',
    category: '视频总结',
    intro:
      'BiliNote 可以把 YouTube 视频整理成中文笔记，适合先了解长视频的主题，再按章节深入观看。生成结果的质量取决于字幕、转写和所选模型。',
    sections: [
      [
        '开始前：确认部署与网络',
        '<p>准备好已配置模型供应商的 BiliNote，登录工作区。运行后端的机器需要能够访问 YouTube；浏览器能播放视频，并不代表服务器也能下载。</p><p>需要自己搭建时，先阅读 <a href="/guides/docker-deploy/">Docker 部署教程</a>。网络配置应按你的实际部署环境设置。</p>',
      ],
      [
        '1. 输入 YouTube 视频地址',
        '<p>新建笔记，选择 YouTube，粘贴视频链接。选择已配置的模型和笔记风格：快速了解内容可选精简，学习课程可选详细或教程。</p>',
      ],
      [
        '2. 使用字幕或语音转写',
        '<p>程序优先尝试可用的平台字幕；字幕不可用时，按照所选方式提取文字。语音转写需要相应转写器已就绪，本地模型首次使用可能需要下载。</p><p>当前笔记提示词要求中文输出，技术名词和专有名称可以保留英文。外语语音的识别效果取决于转写器与模型，不保证每句话都准确。</p>',
      ],
      [
        '3. 阅读摘要并定位原片',
        '<p>启用目录、原片跳转和 AI 总结后，可以从章节结构判断哪些内容值得细看，再点击时间链接回到原视频。打开原文参照，核对术语、数字和关键表述。</p>',
      ],
      [
        '4. 保存笔记与追问',
        '<p>将笔记导出为 Markdown，或切换为思维导图回顾结构。使用视频 AI 问答追问具体概念，结合来源片段判断回答是否有原文依据。</p>',
      ],
      [
        '下载或生成失败时检查什么',
        '<p>先查看任务失败原因，检查服务器到 YouTube 的连通性、视频访问权限及必要的 Cookie。模型请求失败则检查供应商地址、凭据、模型是否可用和服务额度。</p><p>字幕缺失与模型调用失败是不同阶段的问题，应根据任务信息分别排查。更多说明见 <a href="https://github.com/Brief4code/bilinote#quick-start">项目文档</a>。</p>',
      ],
    ],
  },
  {
    path: '/guides/docker-deploy/',
    title: 'BiliNote Docker 部署教程',
    description:
      '使用 Docker 部署 BiliNote，持久化笔记和配置，初始化管理员，并保留 3015 端口与 Cloudflare Tunnel 公网访问方式。',
    category: '部署指南',
    intro:
      '下面使用项目提供的完整镜像部署，将服务映射到本机 3015 端口。适合希望在自己的机器上管理视频笔记、模型配置和历史记录的使用者。',
    sections: [
      [
        '1. 准备 Docker',
        '<p>准备可运行 Docker 的机器，并确认 3015 端口未被其他服务占用。需要能够拉取项目镜像；模型下载和视频下载也需要对应的网络条件。</p><p>下面的单镜像方式使用默认 CPU 配置。GPU 部署需要额外环境，参见 <a href="https://github.com/Brief4code/bilinote#quick-start">仓库中的 GPU 部署说明</a>。</p>',
      ],
      [
        '2. 拉取镜像并启动',
        `<p>以下镜像需要已发布且具备访问权限。若拉取返回 403、denied 或 manifest unknown，可以使用文末的源码构建方式。</p><pre><code>docker pull ghcr.io/brief4code/bilinote:latest

docker run -d --name bilinote -p 3015:80 \\
  -v bilinote-data:/app/backend/data \\
  -v bilinote-config:/app/backend/config \\
  -v bilinote-static:/app/backend/static \\
  -v bilinote-uploads:/app/backend/uploads \\
  -v bilinote-vector-db:/app/backend/vector_db \\
  -v bilinote-models:/app/backend/models \\
  ghcr.io/brief4code/bilinote:latest</code></pre><p>这些卷分别保存数据库与笔记、配置、截图、上传文件、向量索引和模型缓存。升级时继续使用同一组卷；不要把整个后端代码目录挂成数据卷。</p>`,
      ],
      [
        '3. 初始化管理员',
        '<pre><code>docker exec -it bilinote python scripts/manage_users.py init-admin admin</code></pre><p>按照交互提示设置 12–128 字符的密码。管理员需要通过这条命令初始化，网页注册创建的是普通用户。</p>',
      ],
      [
        '4. 打开工作区并配置模型',
        '<p>浏览器访问 <code>http://127.0.0.1:3015/app</code>，使用刚创建的管理员账号登录。在设置中配置模型供应商和可用模型，再选择合适的转写器，提交一条短视频确认完整流程。</p><p>查看启动情况可使用 <code>docker logs -f bilinote</code>。日志有助于区分服务未启动、模型下载、视频下载与模型调用问题。</p>',
      ],
      [
        '5. 通过 Cloudflare Tunnel 访问',
        `<p>已有 cloudflared 时，让隧道继续指向同一个 3015 端口：</p><pre><code>mkdir -p logs
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:3015 2&gt;&amp;1 | tee logs/cloudflared.log</code></pre><p>打开命令返回的网址即可访问首页，点击「进入工作区」前往同一域名下的 <code>/app</code>。不要把外网访问的 API 地址配置成访问者机器上的 <code>127.0.0.1</code>；Docker 默认使用同源 <code>/api</code>。</p><p>快速隧道的网址可能在重启后变化。需要长期对外服务时，可使用固定域名与固定隧道。</p>`,
      ],
      [
        '镜像不可用时：从源码构建',
        `<p>可直接从当前 GitHub 仓库构建，不依赖预构建的 BiliNote 镜像：</p><pre><code>git clone https://github.com/Brief4code/bilinote.git
cd bilinote
cp .env.example .env
bash deploy.sh cpu
docker compose exec backend python scripts/manage_users.py init-admin admin</code></pre><p>需要先安装 Docker Compose v2，首次部署还需要下载依赖和准备本地 Embedding 模型。已有部署继续使用原有数据目录；前端页面变更需重建前端，修改后端代码后按原有方式重启服务。完整操作、数据备份和故障排查以 <a href="https://github.com/Brief4code/bilinote#quick-start">README</a> 为准。</p>`,
      ],
    ],
  },
]

export const home = {
  path: '/',
  title: 'BiliNote - 开源 AI 视频笔记工具 | B 站、YouTube 视频总结',
  description:
    'BiliNote 是开源 AI 视频笔记与问答工具，支持 B 站、YouTube 等平台及本地音视频，通过字幕、语音转写或 OCR 生成结构化笔记、思维导图，支持 Markdown 导出和 Docker 自部署。',
}

export const guideIndex = {
  path: '/guides/',
  title: 'BiliNote 使用与部署教程 | B 站笔记、YouTube 总结、Docker 部署',
  description:
    '阅读 BiliNote 的视频笔记和部署教程：B 站视频生成笔记、YouTube 视频总结，以及 Docker 部署和 Cloudflare Tunnel 访问方法。',
}

export const publicPages = [home, guideIndex, ...guides]
