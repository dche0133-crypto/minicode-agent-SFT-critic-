# MiniCode-Agent：Coding Agent 评测、失败归因与 Critic 训练框架

MiniCode-Agent 是一个面向代码修复任务的轻量级 Coding Agent 原型。项目重点不是只做一个能调用工具的 Agent，而是围绕 Agent 的执行过程建立一套可评测、可复盘、可诊断、可训练的闭环。

```text
Agent 执行
-> Benchmark 评测
-> Trajectory 采集
-> Failure Analysis 失败归因
-> Critic Policy / Retry
-> Critic SFT 数据构造
-> LoRA Critic 离线验证
```

## 项目亮点

- 实现 ReAct 风格 Agent 循环：模型输出工具调用，工具结果作为 observation 回传给模型。
- 支持文件读取、代码搜索、补丁修改、测试运行、Git diff、rollback 等代码修复工具。
- 构建 20 个 Python bugfix benchmark，包含 easy / medium / hard 难度、public tests 和 hidden tests。
- 自动记录 session、raw trajectory 和 training trajectory，便于复盘与训练数据构造。
- 实现规则化 Failure Analysis，识别 `no_test_run`、`test_failure`、`hidden_test_failed`、`syntax_error`、`repeated_tool_call` 等失败类型。
- 实现 Critic-guided Retry，将失败后的重试拆成 Diagnose、Edit、Verify、Accept/Rollback。
- 引入候选补丁质量评价，避免 Retry 越修越坏。
- 支持 repeat 多次评测，统计均值、标准差、成本指标和 patch 质量指标。
- 构造 Critic SFT 数据，并在云端完成过 QLoRA Critic 离线验证。

## 目录结构

```text
mini_coding_agent.py          Agent 主程序
scripts/
  run_benchmark.py            Benchmark 运行器
  failure_analysis.py         失败归因
  critic_policy.py            Critic 决策标签与工具约束
  scoring.py                  综合评分
  build_critic_sft.py         Critic SFT 数据构造
  compare_benchmark_reports.py 实验报告对比
benchmarks/bugfix/            20 个代码修复任务
configs/                      Retry 策略配置
cloud/                        云端 LoRA 训练与推理脚本
datasets/                     精简后的 Critic SFT 数据
tests/                        单元测试
```

## 安装环境

需要 Python 3.10+。

推荐先创建虚拟环境，然后安装项目：

```powershell
pip install -e .
```

如果只想直接运行，也可以使用：

```powershell
python mini_coding_agent.py --help
```

## 模型后端

项目支持两种模型后端：

- `deepseek`：通过 API 调用，需要 `.env` 中配置 `DEEPSEEK_API_KEY`
- `ollama`：本地模型推理，例如 `qwen2.5-coder:7b`

`.env` 示例：

```text
DEEPSEEK_API_KEY=your-api-key
```

注意：不要把 `.env` 上传到 GitHub。

## 运行 Agent

使用默认 DeepSeek 后端：

```powershell
python mini_coding_agent.py
```

使用本地 Ollama：

```powershell
python mini_coding_agent.py --provider ollama --model qwen2.5-coder:7b
```

常用参数：

```text
--provider       模型后端：deepseek 或 ollama
--model          模型名称
--cwd            Agent 工作目录
--approval       风险工具审批模式：ask / auto / never
--max-steps      单次任务最大工具轮数
--temperature    采样温度
```

## 交互命令

进入 Agent 后，可以使用以下 slash commands：

```text
/help                         查看命令
/tools                        查看可用工具
/history                      查看最近历史
/sessions                     查看保存的会话
/resume latest                恢复最近会话
/critic                       基于最近测试结果生成诊断
/test-fix [command]           固定测试修复流程
/export-trajectory            导出普通轨迹
/export-training-trajectory   导出训练轨迹
/memory                       查看压缩记忆
/exit                         退出
```

## Benchmark 评测

不调用模型的 smoke test：

```powershell
python scripts/run_benchmark.py --fake-agent-success --task bubble_sort_order
```

使用本地 Ollama 跑一个任务：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --task bubble_sort_order
```

跑完整 20 个任务：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0
```

每个 benchmark task 包含：

```text
prompt.txt       任务说明
repo/            初始有 bug 的代码
public_tests/    Agent 可以运行看到的测试
hidden_tests/    最终评估泛化能力的测试
metadata.json    难度、目标文件、标签等元数据
```

## Critic Retry

开启规则 Critic Retry：

```powershell
python scripts/run_benchmark.py `
  --provider ollama `
  --model qwen2.5-coder:7b `
  --temperature 0 `
  --critic-retries 1 `
  --retry-policy configs/retry_policy.strict.json
```

Retry 流程：

```text
Diagnose：先看测试失败和轨迹证据
Edit：只允许使用受控编辑工具
Verify：必须重新运行 public tests
Accept/Rollback：测试有改善才接受，否则回滚
```

这样可以避免模型只输出解释文字、忘记运行测试，或者把代码越修越坏。

## Repeat 评测

单次 benchmark 容易受模型波动影响，因此项目支持 repeat 多次运行：

```powershell
python scripts/run_benchmark.py `
  --provider ollama `
  --model qwen2.5-coder:7b `
  --temperature 0 `
  --repeat 5 `
  --out benchmark_results/baseline_repeat5

python scripts/run_benchmark.py `
  --provider ollama `
  --model qwen2.5-coder:7b `
  --temperature 0 `
  --critic-retries 1 `
  --retry-policy configs/retry_policy.strict.json `
  --repeat 5 `
  --out benchmark_results/retry_repeat5
```

项目会统计：

- public pass rate
- hidden pass rate
- 综合分
- 工具调用次数
- 总耗时
- patch 修改文件数
- patch 修改行数
- 多次运行标准差

## 实验结果

本地 Ollama `qwen2.5-coder:7b`、`temperature=0`、20 个任务、`repeat=5` 的结果：

| 指标 | Baseline | Critic Retry | 变化 |
| --- | ---: | ---: | ---: |
| Public Pass Rate | 41.0% | 58.0% | +17.0 pp |
| Hidden Pass Rate | 32.0% | 39.0% | +7.0 pp |
| Avg Score | 37.83 | 55.21 | +17.38 |
| Avg Tool Calls | 2.98 | 4.83 | +1.85 |
| Avg Wall Time | 8.28s | 13.42s | +5.14s |
| Avg Patch Lines | 7.38 | 8.62 | +1.24 |

结论：规则 Critic Retry 能提升执行流程、public/hidden pass 和综合分，但代价是更多工具调用和更长耗时。Hard task 仍然困难，尤其是图算法、多文件和复杂语义任务。

详细报告见：

```text
EXPERIMENT_REPORT_REPEAT5.md
HARD_TASK_ANALYSIS.md
ABLATION_REPORT.md
TWO_DAY_PROJECT_SUMMARY.md
```

## 生成 Critic SFT 数据

从 benchmark report 构造 Critic SFT 数据：

```powershell
python scripts/build_critic_sft.py `
  --reports benchmark_results/retry_repeat3 `
  --include-success `
  --synthetic-per-type 10 `
  --min-quality-score 0.9 `
  --out datasets/critic_sft_v2_retry_repeat3_clean.jsonl
```

SFT 数据输入：

```text
任务说明 + Agent trajectory + 测试报错 + git diff
```

SFT 数据输出：

```json
{
  "diagnosis": {
    "failure_type": "...",
    "reason": "...",
    "evidence": "...",
    "confidence": 0.9
  },
  "decision": {
    "next_action": "...",
    "target_file": "...",
    "allowed_tools": ["..."],
    "risk_level": "...",
    "abstain": false,
    "confidence": 0.9
  }
}
```

当前保留的精简数据：

```text
datasets/critic_sft_v2_retry_repeat3_clean.jsonl
```

## LoRA Critic

云端训练脚本在 `cloud/` 目录下：

```text
cloud/train_critic_lora.py
cloud/eval_critic_lora.py
cloud/eval_critic_compare.py
cloud/requirements-train.txt
```

项目已经在 AutoDL RTX 4090 上用 `Qwen2.5-Coder-7B-Instruct` 做过 QLoRA Critic 离线验证。

小规模验证结果：

| 指标 | Base Model | LoRA Critic |
| --- | ---: | ---: |
| JSON 合法率 | 100% | 100% |
| failure_type 字段匹配 | 0% | 90% |
| next_action 字段匹配 | 0% | 90% |
| target_file 字段匹配 | 15% | 95% |

注意：LoRA Critic 目前只证明了离线结构化诊断能力，还没有完整接入在线 Retry，因此不能声称 LoRA 已经提升 Agent Pass@2。

## 测试

运行单元测试：

```powershell
python -m pytest -q
```

此前本地验证结果：

```text
73 passed, 1 skipped
```

## 项目边界

已经完成：

- Coding Agent 工具循环
- Benchmark Harness
- Trajectory 采集
- Failure Analysis
- Critic Policy
- 受控 Retry
- Patch 质量评价
- Repeat 评测
- 实验报告
- Critic SFT 数据构造
- LoRA Critic 离线验证

尚未完成：

- LoRA Critic 在线接入 Retry
- `no critic / rule critic / learned critic` 三组在线对比
- Hard task 的稳定解决
- DPO 或偏好优化

## License

Apache-2.0
