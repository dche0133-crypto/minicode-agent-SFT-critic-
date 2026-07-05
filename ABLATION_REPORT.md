# MiniCode-Agent Ablation 小实验报告

这份报告用于面试解释：系统里的 Retry、Critic Policy、Rollback 到底分别起什么作用。

## 1. 实验问题

面试官可能会问：

> 你的效果提升到底来自 Critic，还是因为失败后多试了一次？

所以这里做一个小型 ablation，对比三组：

| 组别 | 含义 |
| --- | --- |
| Baseline | 不使用 Critic Retry |
| Strict Retry + Rollback | 使用规则 Critic、阶段化 Retry、候选补丁检查和回滚 |
| Strict Retry + No Rollback | 保留 Critic Retry，但关闭回滚 |

## 2. 实验命令

Baseline：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --repeat 3 --out benchmark_results/baseline_repeat3
```

Strict Retry + Rollback：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --critic-retries 1 --retry-policy configs/retry_policy.strict.json --repeat 3 --out benchmark_results/retry_repeat3
```

Strict Retry + No Rollback：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --critic-retries 1 --retry-policy configs/retry_policy.strict_no_rollback.json --repeat 3 --out benchmark_results/retry_no_rollback_repeat3
```

## 3. 结果对比

| 组别 | Public Pass | Hidden Pass | Avg Score | Avg Tool Calls |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 40.0% | 30.0% | 36.29 | 2.95 |
| Strict Retry + Rollback | 58.3% | 41.7% | 56.49 | 4.63 |
| Strict Retry + No Rollback | 56.7% | 43.3% | 56.09 | 5.65 |

## 4. 怎么解读

第一，Critic Retry 相比 Baseline 有明显提升：

- Public Pass 从 40.0% 提升到 58.3% 左右；
- Hidden Pass 从 30.0% 提升到 41.7% / 43.3%；
- Avg Score 从 36.29 提升到 56 分左右。

这说明“失败后基于诊断再修一次”确实比直接结束更强。

第二，No Rollback 的 Hidden Pass 在这次小实验里略高，但工具调用更多：

- Rollback：Avg Tool Calls = 4.63；
- No Rollback：Avg Tool Calls = 5.65。

所以不能简单说“rollback 一定提升 pass rate”。更准确的说法是：

> Rollback 的价值主要是风险控制和成本控制：当候选补丁没有改善、引入语法错误或破坏测试时，系统不会保留坏状态。它让 Retry 更像一个受控决策流程，而不是盲目多试。

第三，这还是小实验，不是最终论文结论。更严格的 ablation 还应该加入：

- loose retry；
- strict retry without allowed-tools gate；
- strict retry without candidate acceptance；
- strict retry with rollback；
- rule critic vs LoRA critic。

## 5. 面试时推荐说法

可以这样讲：

> 我做了一个小规模 ablation。Baseline 的 hidden pass 是 30.0%，加入规则 Critic Retry 后提升到 41.7% 左右；关闭 rollback 后 hidden pass 接近，但工具调用从 4.63 增加到 5.65。这个结果说明 Retry 本身能提升失败恢复能力，而 rollback 更偏向可靠性和成本控制，避免系统保留明显变坏的候选补丁。

## 6. 产物位置

| 文件 | 说明 |
| --- | --- |
| `configs/retry_policy.strict_no_rollback.json` | no-rollback ablation 配置 |
| `benchmark_results/baseline_repeat3/run_20260625-080805.json` | baseline repeat=3 结果 |
| `benchmark_results/retry_repeat3/run_20260625-081454.json` | strict retry + rollback repeat=3 结果 |
| `benchmark_results/retry_no_rollback_repeat3/run_20260626-132503.json` | strict retry + no rollback repeat=3 结果 |
| `benchmark_results/retry_no_rollback_repeat3/run_20260626-132503.per_task.md` | no-rollback per-task 明细 |

