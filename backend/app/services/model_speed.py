"""Observed note completion times for dropdown ordering, measured 2026-11-07.

Use the latest completed ordinary-timestamp run with provider sampling defaults;
Kimi has only a completed strict-timestamp run. The 480-second follow-up supplies
DeepSeek Flash, Qwen 2.4T and GLM. These are single-run observations, not promises
of latency or benchmarks across identical prompts. Models without a completed
run sort after measured models. Apply only to the tested Qwen provider.
"""

MEASURED_COMPLETION_SECONDS: dict[str, float] = {
    "MiniMax-M2.5": 35.000,
    "deepseek-v4-flash-0731": 50.423,
    "qwen3-vl-plus": 55.784,
    "qwen3.7-max-2026-06-08": 73.384,
    "qwen3.7-max": 78.283,
    "qwen3.6-flash": 81.788,
    "qwen3.7-flash": 84.713,
    "qwen3.7-plus": 126.432,
    "qwen3.6-plus": 131.840,
    "deepseek-v4-pro-0813": 198.984,
    "kimi-k3": 230.228,
    "qwen3.8-2.4t-a95b": 306.844,
    "glm-5.3": 397.674,
}
