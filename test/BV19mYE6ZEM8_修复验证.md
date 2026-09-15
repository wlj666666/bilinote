2026-09-15 修复与验证结果

原任务 c0ff2d03-bb0c-4dc5-9e1d-376250ad4faa 已成功生成并保存。模型请求于 09:40:15 发起，09:41:10 完成保存；请求日志记录模型调用耗时 51.7 秒，首次尝试成功。完整笔记保存在 backend/note_results/c0ff2d03-bb0c-4dc5-9e1d-376250ad4faa.json，另导出到 test/BV19mYE6ZEM8_视频总结.md。

最后一次服务重启后，实际 HTTP 验证结果：

- 后端任务接口：HTTP 200，SUCCESS。
- 视频 ID：BV19mYE6ZEM8，平台字幕 413 段。
- 最终 Markdown：3768 字符（包含链接与 Markdown 标记），14 个原片时间跳转链接。
- 前端 http://localhost:3015/：HTTP 200。
- 扫描主任务状态文件，未发现仍处于 PENDING / DOWNLOADING / SUMMARIZING 的遗留任务。

已落实的修改：

1. 总结状态使用原任务 ID，checkpoint 继续使用原有 UUID_markdown 键；前端现在能正确看到 SUMMARIZING。
2. 网络连接/写入/读取超时默认分别为 10/30/180 秒；SDK 自动重试关闭，应用层最多尝试 2 次。记录每次请求字节数、尝试次数、耗时和失败类型。
3. 支持 OPENAI_NO_PROXY 及标准 NO_PROXY/no_proxy。之前显式代理客户端会忽略排除列表；现在可按域名直连。已在本机 .env 的 OPENAI_NO_PROXY 中加入当前千问服务域名，没有修改 API Key。
4. 当前 qwen-plus 请求跳过画面理解，使用平台字幕总结；前端明确提示并关闭不适用的视觉开关。已有历史请求即便仍传 video_understanding=true，也不会再次上传无效图片。视觉模型仍可启用画面理解。
5. 视觉采样限制为最多 24 张拼图，缩小图片并在整个视频均匀采样；请求分块预算从 45 MiB 调整为 8 MiB。仅需要笔记截图时不再额外进行视觉模型上传。
6. 启动时将已丢失执行上下文的旧任务标记为中断、提示重试，保留字幕和媒体缓存。重复提交同一在途任务不会重复入队。不存在的任务明确返回失败。
7. 最终结果以临时文件原子替换方式保存，保存完成后才发布 SUCCESS；后台异常会写入失败状态并释放任务占位。
8. 修复原片跳转中的局部变量引用错误、普通 B 站链接错误使用 &t= 的问题，以及时间标记残留和目录中的嵌套链接。

实测过程补充：

- 修复状态与超时后，第一次完整字幕请求经代理出现 RemoteProtocolError（连接在响应前断开），任务明确结束为失败，未再拖数十分钟。
- 最小文本请求直连和代理均可响应，但这不足以证明完整请求稳定。配置当前千问域名直连后，完整字幕总结成功。
- 本地重新处理同一视频，得到 24 张 960×540 拼图，Base64 总长度 2,774,636 字节（约 2.65 MiB），最后采样帧为 11:30。此前为 86 张、约 31.3 MiB。此测试只在本地处理媒体，没有重新发送整组图片到模型服务。

验证：49 项测试通过，另有 12 个参数子测试通过；前端 npm run build 成功；git diff --check 通过。测试按文件分组运行，避免仓库原有测试全局替换 sys.modules 互相污染。

```bash
cd backend
python -m pytest tests/test_task_reliability.py tests/test_text_extraction_routes.py -q
# 27 passed

# 以下文件分别运行：
python -m pytest tests/test_task_serial_executor.py -q
python -m pytest tests/test_video_reader_dedupe.py -q
python -m pytest tests/test_request_chunker.py -q
python -m pytest tests/test_universal_gpt_checkpoint.py -q
python -m pytest tests/test_universal_gpt_content_format.py -q
python -m pytest tests/test_proxy_apply_env.py -q
# 合计 18 passed

python -m pytest tests/test_note_helper.py -q
# 4 passed, 12 subtests passed
```

前后端已启动并加载修复。刷新浏览器后选择原历史记录即可读取成功结果，无需重新生成。当前笔记依据平台 AI 字幕生成，未使用画面理解；游戏专有名词的准确性仍受原字幕质量影响。
