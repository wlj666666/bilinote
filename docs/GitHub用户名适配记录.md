# GitHub 用户名适配：Brief4code

## 地址与展示

- 当前仓库：`https://github.com/Brief4code/bilinote`。
- 当前镜像命名空间：`ghcr.io/brief4code/bilinote`，保持全小写。
- 官网首页、教程、分享结构化数据、源码与下载入口使用新仓库地址。
- 工作区关于页、克隆命令、启动失败反馈及桌面引导中的扩展入口已更新。
- README、发版文档、Issue/Discussion 模板和提交规范帮助链接已更新。
- Tauri 包元数据的作者和仓库地址已更新；应用标识保持稳定。
- Docker 发布 workflow 根据实际仓库名动态生成小写镜像路径，避免账号含大写字母时
  生成无法直接使用的拉取命令。

## Git 身份

当前本地仓库的 `origin` 已更新到新地址，仓库级 `user.name` 为 `Brief4code`，
提交邮箱保持原值。没有修改其他项目使用的全局 Git 配置。

新增 `.mailmap`，将原署名映射为 `Brief4code`。已确认 135 条原提交在支持 mailmap
的视图中使用新署名，所有提交 SHA 保持不变。可通过以下命令核对：

```bash
git remote -v
git config --show-origin --get user.name
git log -1 --format='raw author: %an | mapped author: %aN'
git shortlog -sne --all
```

`.mailmap` 中保留旧署名是为了匹配历史提交，不应删除或替换映射右侧的旧身份。

## 发布状态

本次只读校验确认新仓库可访问且 Git HEAD 可读取；新账号下的 GHCR 镜像匿名访问
返回 403，因此尚不能确认公众可拉取。官网教程与 README 提供源码 Compose 构建方式，
作为预构建镜像不可用时的替代路径。

校验时 GitHub 公开 Release 列表为空，最新正式 Release 接口返回 404。

镜像实际发布需将更新后的 workflow 推送到 GitHub 后按原流程执行，并检查包访问权限。
桌面端源代码与元数据已更新，已下载的旧安装包不会因仓库改名自动改变内置链接，
需要后续构建和发布新版本。

本次没有推送提交、创建发行版或上传镜像。源码与当前网站的适配不代表已产生新的发布产物。

## 验证结果

- 11 项官网与路由回归测试通过。
- 仓库现有 CI 诊断检查通过，类型与 lint 均无新增错误。
- 前端 Docker 生产构建与 Tauri 前端构建通过。
- 工作流 YAML、GHCR 小写路径生成和桌面包元数据检查通过。
- 当前 `3015` 网站已更新；公开页面、JSON-LD、镜像地址与源码构建命令均使用新账号。
- 工作区、管理/设置深链接、静态资源和 API 鉴权检查通过，后端容器未重启。
- 全部已发布前端静态文件未检出旧用户名；源码中的旧身份仅保留在 `.mailmap` 匹配项中。
