# 视频问答 Agent 对照评测

本评测比较同一视频、同一问题、同一模型下的普通 RAG 与实际问答代码中的
RAG + Function Calling Agent。40 个问题先固定，再运行模型，不根据结果删除失败题。

## 输入和对照

- 数据集：[evals/agent_qa_v1.json](evals/agent_qa_v1.json)。5 段既有公开视频，
  每段 8 题，共 40 题：元信息 5、概括 5、事实 10、原文 5、时间定位 5、
  跨片段 5、材料未提供答案 5。
- 参考答案来自现有笔记及 ASR/OCR 转录。它们不是对原视频逐字人工校对后的真值，
  指标衡量对这些材料的回答能力，不衡量语音或字幕识别准确率。
- RAG：复用真实 `VectorStoreManager.query()`，按元信息 / 笔记 / 转录 1/2/3 配额检索，
  使用相同 `_build_context()`，单次模型调用，不提供工具。
- Agent：执行真实 `chat_service.chat()`，包括原有系统提示、相同检索、3 类真实工具、
  最多 3 轮工具调用及最后一次无工具收束。没有使用脚本化答案或模拟模型响应。
- 两组均从空对话开始，温度 0、输出预算 8192 token、单题 180 秒预算；
  生产问答的默认温度仍为 0.7，本评测没有修改生产默认参数。
- 基线系统提示去掉要求调用工具的指令，并明确参考不足时说明；Agent 使用原有提示。
  因而这是两种实际问答配置的比较，不能把全部差异归因于单一参数。
- 索引预先构建、Embedding 预热，计时包含查询、客户端准备、模型请求、工具执行，
  不包含首次索引 / 模型下载 / 浏览器 / 公网隧道 / 问答任务队列。
- 两个 worker、固定种子随机打乱题目与模式，保留每个请求的实际耗时、usage、
  finish reason、检索片段和工具结果。每种配置每题运行一次，不报告统计显著性。

## 指标口径

1. **参考答案规则通过率**：回答必须命中该题全部关键事实正则组；时间定位题同时
   要求显式时间在参考起点 ±2 秒内；无答案题要求明确说明未提供，且不能命中预设的
   数字捏造模式。空回答、异常、输出截断一律计失败，并保留在分母中。
   这是固定 rubric 的通过率，不等同于通用“问答准确率”或全面的幻觉率。
2. **证据可得率**：有标注证据的题中，实际送入模型的初始检索片段或工具结果是否
   覆盖全部标注证据组。它衡量证据是否进入上下文，不代表回答逐句引用正确。
3. **工具执行成功率**：工具实际调用返回有效 JSON 且没有 `error` 字段的比例。
   这与“模型是否选对工具”及“答案是否正确”分别统计。
4. **时延和用量**：记录均值、P50、线性插值 P95，以及供应商返回的 prompt /
   completion / total tokens。未核验服务商价格，不将 token 数伪装成货币成本。
5. **成对变化**：同时报告 Agent 独有通过题和 RAG 独有通过题，避免只展示成功案例。

原文题要求保留特定原句，时间题要求准确定位，规则比宽松的语义判断更严格；
概括题的关键词匹配不能充分发现所有逻辑错误。结果还需逐题检查后再用于简历。

## 2026-11-05 评分校准与修复回归

初次运行完整保留 v1.0 数据集和原始评分。检查答案后发现部分规则误扣：时间定位题
只要求给出时间，不应额外要求重复目标句中的数字；“用来运行 ASGI 应用”、
“保持接口约定不变”、证书名称和明确的“没有实测数据”等同义表述也应接受。
[v1.1 评分文件](evals/agent_qa_v1_1.json)保留全部问题、材料、时间容差和参考事实，
仅修正这些同义表述规则；对基线、原 Agent、修复后 Agent 全部使用同一版评分器。
原始得分另行保留，报告不得把评分规则变化描述成模型效果提升。

初次运行还发现：工具轮数用尽后，仅省略 `tools` 时，兼容模型仍可能返回
`finish_reason=tool_calls`，导致空答案或“继续查询”的中间文字被保存为最终结果。
代码改为显式 `tool_choice="none"`，添加基于现有证据收束的指令，并拒绝空文本及
最终仍请求工具的响应。修复后完整重跑 40 题；用于发现问题的原始结果均保留。
这是同一题集的工程回归，不是独立留出集上的泛化结论。

只读生成统一报告（路径按实际运行目录替换）：

```bash
python backend/scripts/report_agent_evaluation.py \
  --rag-run backend/data/agent_eval/2026-11-05-main \
  --original-run backend/data/agent_eval/2026-11-05-main \
  --agent-run backend/data/agent_eval/2026-11-05-fixed \
  --rubric backend/evals/agent_qa_v1_1.json \
  --output backend/benchmark_records/2026-11-05-agent-eval
```

报告器拒绝缺题、重复题、题目变化、材料快照变化或模型设置不一致的输入；同时
核对三组初始检索结果是否一致。该步骤不调用模型，不修改原始记录。

## 隔离和复现

脚本只读生产供应商配置及选定的公开视频笔记；将必要内容复制到独立目录，去除
下载 headers、媒体路径等无关字段。数据库、索引、资源锁和日志均使用独立目录，
不修改生产任务、聊天历史、用户或原有向量索引。API Key 不写入评测产物。
评测会调用所选供应商的真实模型并消耗其 API 配额。

在仓库根目录使用已安装完整后端依赖的 Python：

```bash
python backend/scripts/evaluate_agent.py \
  --output backend/data/agent_eval/new-run \
  --validate-only

python backend/scripts/evaluate_agent.py \
  --output backend/data/agent_eval/new-run \
  --provider qwen --model deepseek-v4-pro --workers 2
```

可用 `--provider-db` / `--source-root` 选择本地配置和材料目录，`--dataset` 指定其他
标注集。源码笔记路径保存在数据集中；其他机器需要提供对应材料或显式调整新版本
数据集。完整转录和运行索引位于 Git 忽略的 `backend/data/agent_eval/`，不会自动发布。

每次运行保存 `corpus_manifest.json`、`metadata.json`、`results.jsonl` 和
`summary.json`，包含输入、运行脚本及问答代码的 SHA-256。已有结果不可覆盖；
重新评测需使用新输出目录，不能把追加补跑当作原始完整实验。

独立验证评分器：

```bash
python -m unittest discover -s backend/tests -p test_agent_evaluation.py -v
```
