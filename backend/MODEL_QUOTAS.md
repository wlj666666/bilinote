# 阿里云模型免费额度

模型设置页、生成笔记的模型选择和问答模型选择显示剩余免费 Token、到期时间，并每 60 秒刷新。数据来自阿里云免费额度查询接口；输入与输出共用额度。显示未知或同步失败时，不会把余额当成 0，也不会把旧余额标为最新数据。

模型推理 API Key 无法读取免费额度，需要官方百炼 CLI 的控制台登录。登录必须使用与项目 Qwen 供应商相同的账号和 API Key、北京地域、中国站。当前已配置独立登录目录 `backend/config/bailian/`，目录由 Git 忽略，凭证不会下发前端。

登录过期后，在项目根目录运行（已安装官方 CLI 时）：

```bash
BAILIAN_CONFIG_DIR="$PWD/backend/config/bailian" bl auth login --console
```

本次临时安装的 CLI 路径为 `/tmp/bilinote-bailian-cli/node_modules/.bin/bl`；临时目录清理后，可按[官方安装文档](https://help.aliyun.com/zh/model-studio/cli/installation)安装。远程部署时，浏览器需要能访问 CLI 在运行机器上监听的本地回调端口。

服务端默认读取 `backend/config/bailian/config.json`，也支持通过 `BAILIAN_CONFIG_DIR` 指定官方 CLI 的配置目录。查询接口为已登录用户可访问的 `GET /api/model_quotas`，仅返回项目已添加模型的余额，并缓存 60 秒；不会修改阿里云的额度用完即停开关、付费模式或模型权限。

2026-11-06 调整：删除已保存且免费额度耗尽的 `deepseek-v4-pro`，保留 7 个可用模型，添加 8 个，总计 15 个（9 个支持图片输入）。所选模型免费额度均于北京时间 2026-12-05 00:00 到期。调整前模型记录备份在 `backend/backups/model-list-before-2026-11-06.json`。
