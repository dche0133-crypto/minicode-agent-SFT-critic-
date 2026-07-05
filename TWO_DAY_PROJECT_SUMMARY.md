# MiniCode-Agent：这两天做了什么

这份文档用简单的话记录项目目前做到哪里、实验说明了什么，以及还没有做到什么。

## 1. 项目现在是什么

这是一个可以修改代码的命令行 Coding Agent。它会让模型选择工具，例如读文件、写文件、打补丁和运行测试；工具结果会再交给模型，形成“观察 -> 行动 -> 再观察”的循环。

```text
用户任务
-> Agent 读代码 / 改代码 / 跑测试
-> 保存 session 和 trajectory
-> Benchmark 评测
-> Failure Analysis 分析失败
-> Critic 给出下一步建议
-> 受控 Retry
```

## 2. 这两天完成的主要工作

| 模块 | 已完成内容 | 作用 |
| --- | --- | --- |
| Agent | DeepSeek 与 Ollama 后端、结构化工具调用、文件/补丁/测试/Git 工具、审批策略 | 让模型能实际操作代码仓库 |
| Session 与轨迹 | session 持久化、memory、history、普通 trajectory、training trajectory | 可以复盘 Agent 每一步做了什么 |
| Benchmark | 建立 20 个 Python bugfix 任务，含 easy / medium / hard、public tests、hidden tests | 用统一方式测 Agent 的代码修复能力 |
| Failure Analysis | 识别 no_test_run、重复调用、错误文件、语法错误、协议错误、隐藏测试失败等 | 将失败整理成结构化原因和建议 |
| Critic 策略 | 集中定义 failure_type、next_action、allowed_tools、风险等级、置信度和 abstain | 把“失败后怎么办”变成可执行规则 |
| 受控 Retry | 拆成 Diagnose -> Edit -> Verify；工具白名单、阶段门禁、Harness 自动收集文件和 diff 证据；候选补丁测试无改善自动回滚；每轮记录 retry strategy 与 patch_score | 防止模型只说不做、连续乱改、忘记测试，也不保留越修越坏的代码 |
| 评分 | 公开/隐藏测试、测试行为、工具效率、编辑纪律、完成度和扣分项 | 除了通过率外，还能衡量行为质量 |
| 实验报告 | 自动生成 baseline / retry 对比报告、failure type 转移矩阵、难度分组和 per-task 变化表 | 把实验结果变成可解释、可展示的证据 |
| SFT 与 LoRA | 从 trajectory、测试输出、diff、归因生成 Critic SFT；加入质量过滤与 diagnosis + decision 标签；在云端完成过 QLoRA 训练和离线验证 | 训练专门做失败诊断与下一步决策的 Critic |

## 3. Retry 机制现在怎么防止越修越坏

之前的 Retry 更像“失败了就再让模型修一次”。这样有个风险：模型第二次可能改得更差，但系统仍然保留了坏补丁。

现在改成了“候选补丁”机制：

```text
保存当前代码快照
-> 让 Agent 按 Critic 建议修复
-> 重新运行 public tests
-> 判断这次修改有没有改善
-> 有改善就接受，没有改善或变坏就回滚
```

具体规则是：

| 情况 | 处理 |
| --- | --- |
| public tests 全部通过 | 接受这次修改 |
| 测试失败数或错误数减少 | 接受这次修改，继续下一轮 |
| 没有任何改善 | 回滚到 Retry 前的代码 |
| 引入语法错误 | 回滚 |
| 引入 pytest 收集错误 | 回滚 |
| Edit 阶段没有真正编辑文件 | 停止该轮 Retry |
| Verify 阶段没有运行测试 | 不接受这次修改 |

所以现在的 Retry 不再是“盲目多试几次”，而是每一轮都要经过 public tests 检查。它的目标不是保证一定修好，而是避免把代码越修越坏。

最新版本还把 Retry 拆得更清楚：每轮都会保存 `strategy` 和 `patch_score`。

`strategy` 说明这轮为什么这样修：

```json
{
  "failure_type": "no_test_run",
  "next_action": "execute_test_command",
  "diagnose_tools": ["run_tests"],
  "edit_tools": ["patch_file", "apply_patch", "write_file"],
  "verify_tools": ["run_tests"],
  "rollback_on_reject": true
}
```

`patch_score` 说明候选补丁质量怎么样：

```json
{
  "score": 80,
  "reasons": [
    "public_tests_passed:+70",
    "small_patch:+10"
  ]
}
```

这样 Retry 不只是“失败后再试”，而是变成：

```text
先根据 failure_type 选策略
-> Agent 生成候选补丁
-> 用测试改善、patch 大小、是否改错文件、是否改 tests 等信号打分
-> 决定接受或回滚
```

## 4. Benchmark 任务集

目前共有 20 个题目：

| 难度 | 数量 | 例子 |
| --- | ---: | --- |
| Easy | 7 | bubble sort、布尔解析、slug、区间合并、重试延迟 |
| Medium | 8 | 配置解析、CSV、日期、路径安全、Top-K、Token Bucket |
| Hard | 5 | 依赖排序、模板渲染、限流窗口、多文件配置、记录去重 |

每个任务都有下面的结构：

```text
repo/          初始有 bug 的代码
public_tests/  Agent 可以通过运行测试看到的测试
hidden_tests/  最后才运行，用来检查是否只适配了公开样例
prompt.txt     给 Agent 的任务说明
metadata.json  难度、目标文件、步数等配置
```

已校验：20 个初始任务都会在 public tests 上失败，因此不是“天生就能通过”的无效题目。

## 5. 评测可信度现在怎么提高

之前的 benchmark 多数是单次运行。单次结果可以用来观察现象，但不适合直接下结论，因为本地模型即使 `temperature=0`，不同运行之间也可能有波动。

现在 benchmark 支持 `--repeat N`，可以把同一组任务完整跑多次，然后输出均值和标准差：

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --repeat 5 --out benchmark_results/baseline_repeat5

python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --critic-retries 1 --retry-policy configs/retry_policy.strict.json --repeat 5 --out benchmark_results/retry_repeat5
```

这样对比时不只看“一次赢没赢”，而是看：

| 指标 | 含义 |
| --- | --- |
| mean_public_pass_rate | 多次运行后 public tests 平均通过率 |
| std_public_pass_rate | public 通过率波动有多大 |
| mean_hidden_pass_rate | 多次运行后 hidden tests 平均通过率 |
| std_hidden_pass_rate | hidden 通过率是否稳定 |
| mean_avg_score | 平均综合分 |
| mean_avg_tool_calls | 平均工具调用成本 |
| mean_avg_total_wall_time_sec | 平均总耗时 |
| mean_avg_patch_changed_lines | 平均 patch 修改行数 |
| mean_avg_patch_changed_files | 平均 patch 修改文件数 |

如果 Retry 的平均 hidden pass 更高、平均分更高，并且标准差不大，才更能说明它真的有帮助。反过来，如果只是一轮结果变好，就只能说“这次运行表现更好”，不能说系统稳定提升。

更准确地说，现在 Evaluation 的目标不是单独追求某一个指标，而是把 Agent 当成一个需要优化的系统：

```text
Agent Score = correctness + recovery + patch_quality - cost - instability
```

| 部分 | 项目里的对应指标 |
| --- | --- |
| correctness | public pass、hidden pass、测试通过率 |
| recovery | no_test_run 是否减少、失败后是否进入有效 retry |
| patch_quality | patch_score、修改文件数、修改行数、是否改 tests、是否改错文件 |
| cost | tool calls、agent duration、total wall time |
| instability | repeat 多次运行的标准差 |

这也是后续优化的主线：不是让模型“不惜代价多试几次”，而是在通过率、成本、稳定性和补丁质量之间取得更好的平衡。

## 6. 真实 Benchmark 结果

下面是本地 Ollama `qwen2.5-coder:7b`、`temperature=0` 的 `repeat=5` 对比实验结果。Baseline 不使用 Critic Retry；Candidate 使用规则 Critic Retry 与 `configs/retry_policy.strict.json`。

| 指标 | Baseline | Critic Retry | 变化 |
| --- | ---: | ---: | ---: |
| Public Pass Rate | 41.0% | 58.0% | +17.0 pp |
| Hidden Pass Rate | 32.0% | 39.0% | +7.0 pp |
| Avg Score | 37.83 | 55.21 | +17.38 |
| Avg Tool Calls | 2.98 | 4.83 | +1.85 |
| Avg Wall Time | 8.28s | 13.42s | +5.14s |
| Avg Patch Lines | 7.38 | 8.62 | +1.24 |
| Avg Patch Files | 0.95 | 1.02 | +0.07 |

这说明规则 Critic Retry 提升了 Agent 的执行流程和失败恢复能力，但也增加了工具调用、耗时和 patch 规模成本。正式报告已生成到 `EXPERIMENT_REPORT_REPEAT5.md`，per-task CSV 明细在 `EXPERIMENT_REPORT_REPEAT5.tasks.csv`。

Failure type 转移矩阵显示，Retry 的主要作用不是把所有任务都修好，而是把很多“没测就停”的失败推进成“有测试证据的失败”或直接修复成功：

| Baseline Failure | Retry Failure | 任务数 |
| --- | --- | ---: |
| none | none | 7 |
| no_test_run | test_failure | 6 |
| no_test_run | hidden_test_failed | 2 |
| no_test_run | no_test_run | 2 |
| no_test_run | none | 1 |
| no_test_run | repeated_tool_call | 1 |
| syntax_error | syntax_error | 1 |

按难度看，Retry 对 easy / medium 更有效，对 hard 任务仍然有限：

| 难度 | Baseline Hidden | Retry Hidden | 变化 | Retry 平均 Patch Lines | Retry 平均耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Easy | 40.0% | 42.9% | +2.9 pp | 4.60 | 10.35s |
| Medium | 45.0% | 60.0% | +15.0 pp | 11.30 | 14.65s |
| Hard | 0.0% | 0.0% | +0.0 pp | 9.96 | 15.75s |

典型改善任务：

| 任务 | Hidden 变化 | 说明 |
| --- | ---: | --- |
| top_k_words | 0/5 -> 5/5 | Retry 后稳定修复 |
| word_frequency | 3/5 -> 5/5 | Retry 或后续运行稳定性更好 |
| config_records_parser | 3/5 -> 4/5 | Retry 有提升，但仍有波动 |
| config_defaults | 0/5 -> 0/5 | public 从 0/5 到 5/5，但 hidden 仍失败，说明还有泛化问题 |

典型困难任务：

| 任务 | Hidden 变化 | Score 变化 | 说明 |
| --- | ---: | ---: | --- |
| dependency_order | 0/5 -> 0/5 | +21.42 | Retry 把 no_test_run 推进到 test_failure，但模型仍没理解依赖方向 |
| template_renderer | 0/5 -> 0/5 | +21.73 | 有更多测试证据，但候选补丁被拒绝 |
| record_deduplicator | 0/5 -> 0/5 | +21.42 | 仍失败，但失败类型更可诊断 |
| rolling_rate_limit | 0/5 -> 0/5 | +12.90 | syntax_error 仍未解决 |
| multi_file_config_service | 0/5 -> 0/5 | +4.89 | 仍有 repeated_tool_call 问题 |

更完整的 hard task 专项分析已经整理到 `HARD_TASK_ANALYSIS.md`。那里把 hard 任务失败拆成三类：`model reasoning failure`、`tool misuse` 和 `representation issue`，用于解释为什么 Retry 改善了执行纪律，但还没有真正解决复杂算法和多文件语义任务。

## 7. 一次困难题的过程：dependency_order

这题要求输出“依赖在前”的顺序：

```text
build 依赖 compile
compile 依赖 parse
正确顺序：parse -> compile -> build
```

Agent 第一次写了一个看起来像拓扑排序的算法，但把图的边方向理解反了，仍返回：

```text
build -> compile -> parse
```

受控 Retry 的表现是：

```text
第一次：Diagnose(run_tests) -> Edit(patch_file) -> Verify(run_tests)
第二次：Harness 自动读取 dependency_order.py 和 git diff -> Agent 仍未调用编辑工具 -> Edit 阶段停止
```

最终仍未通过。这说明 Harness 已经能正确地收集证据、限制步骤并记录失败；但 7B 主模型在这道图算法题上没有理解“依赖方向”的语义。这是模型能力边界，不是系统偷偷把失败当成功。

## 8. Critic SFT 和 LoRA 的结果

当前最新的训练数据是：

```text
datasets/critic_sft_v2_retry_repeat3_clean.jsonl
```

它基于 `retry_repeat3` 的最新真实轨迹生成，并加入了质量过滤和 diagnosis + decision 标签：

```text
总样本：142
真实 benchmark 样本：42
synthetic 样本：100
avg_quality_score：1.0
quality_reasons：ok 142
label_schema：critic_diagnosis_decision.v2
```

现在的 SFT 输出不只是“失败分析”，而是拆成两部分：

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

这意味着 Critic 不只是学习“错在哪里”，也学习“下一步该做什么”。

在 AutoDL RTX 4090 上，用 `Qwen2.5-Coder-7B-Instruct` 做过 QLoRA 微调，并在 20 条小规模验证样本上做了离线对比：

| 指标 | Base Model | LoRA Critic |
| --- | ---: | ---: |
| JSON 合法率 | 100% | 100% |
| failure_type 字段匹配 | 0% | 90% |
| next_action 字段匹配 | 0% | 90% |
| target_file 字段匹配 | 15% | 95% |

这个实验能说明：LoRA 后的 Critic 更能按约定的标签体系输出结构化失败诊断。它**还不能说明**主 Agent 的 Pass@2 已经提升，因为 LoRA Critic 还没有接入本地在线 Retry。

所以现阶段不建议马上再训练一版 LoRA。原因是：如果只是重新训练，但没有把 LoRA Critic 接入在线 Retry，并和规则 Critic 做对比，就只能说明“又训练了一版诊断模型”，不能证明 Agent 的真实修复能力提升。

从 learning vs no-learning 的角度看，现在项目状态是：

| 组别 | 含义 | 当前状态 | 能证明什么 |
| --- | --- | --- | --- |
| baseline agent | 不使用 Critic Retry | 已完成 repeat=5 | 原始 Agent 的代码修复能力 |
| rule agent | 使用规则 Critic Retry | 已完成 repeat=5 | 规则诊断和受控 retry 能提升流程与部分通过率 |
| learned agent | 使用 LoRA Critic 在线决策 | 还没完成 | 暂时不能证明 learned critic 提升 Agent 在线修复能力 |

所以目前最稳的结论是：`rule agent` 相比 `baseline agent` 有可复现实验提升；`learned agent` 还停留在离线诊断验证阶段。

更合理的后续训练条件是：

```text
用最新 strategy + patch_score 轨迹重新生成 SFT v3
-> 训练 Critic LoRA v2
-> 接入在线 Retry
-> 对比 no critic / rule critic / LoRA critic
-> 看 Pass@2、hidden pass、工具成本是否真的改善
```

## 9. 目前已经证明了什么

- Agent 能在隔离复制的任务仓库中读代码、改代码、跑测试。
- Benchmark 能区分 public pass 和 hidden pass。
- Trajectory、测试输出和 diff 能被整理为训练数据。
- Failure Analysis 能找出“没测就结束、重复调用、语法错误、协议错误”等行为问题。
- 严格 Retry 能把“模型只输出文字”与“模型真实编辑并测试”区分开。
- 候选补丁接受/回滚机制能防止 Retry 保留明显变坏的代码。
- Retry 已显式记录 `strategy` 和 `patch_score`，能解释每轮为什么这样修、候选补丁为什么被接受或拒绝。
- `--repeat` 重复评测能让结果从“单次现象”变成“可比较的均值和波动”。
- 在 20 个任务、repeat=5 实验中，规则 Critic Retry 将 public pass rate 从 41.0% 提升到 58.0%，hidden pass rate 从 32.0% 提升到 39.0%，综合分从 37.83 提升到 55.21。
- 自动实验报告能输出总体指标、文本可视化条形图、failure type 转移矩阵、难度分组、hard task case study、per-task 变化和 CSV 明细。
- Evaluation 已加入成本指标与 patch 质量指标，例如平均工具调用、总耗时、修改文件数、修改行数、是否改 tests、是否修改非目标文件。
- SFT 数据构造已加入质量过滤，最新 clean 数据集有 142 条 diagnosis-decision 样本。
- QLoRA Critic 在小规模离线结构化诊断任务上优于 base model。

## 10. 目前还没有证明什么

- LoRA Critic 已接入本地 Agent 在线 Retry，并稳定提高真实 Agent 的 Pass@2。
- 重新训练 LoRA Critic 能带来在线 Agent 修复能力提升。当前 LoRA 只证明了离线结构化诊断能力。
- 7B 主模型能稳定解决 hard 图算法、多文件和复杂语义修复任务。
- 多轮 Retry 一定比单轮更好；它也可能增加工具成本或产生错误补丁。

## 11. 下一步最值得做的事

1. 对被回滚的候选补丁单独统计原因，例如“无改善”“语法错误”“测试收集错误”，定位 Critic 建议的薄弱点。
2. 做更细的 ablation：`no retry / loose retry / strict retry / strict retry + rollback`，看每个机制到底贡献了多少。
3. 围绕 hard 任务继续做 case study，尤其是 `dependency_order`、`template_renderer`、`multi_file_config_service`，分析是 prompt 问题、工具策略问题，还是模型算法理解问题。
4. 先不要急着重新训练 LoRA；更优先的是把最新 `strategy` 和 `patch_score` 写入 SFT v3 数据，并准备好在线接入接口。
5. 真要训练下一版时，再用 `no critic / rule critic / LoRA critic` 三组在线对比，而不是只做离线诊断对比。

## 一句话总结

我已经做出一个 Coding Agent 原型，并把它扩展成“执行、评测、记录轨迹、分析失败、生成训练数据、训练 Critic、受控 Retry、策略选择、候选补丁评分、重复评测、成本/patch 质量统计、实验报告”的闭环。当前最清楚的收获不是“模型已经很强”，而是系统已经能诚实地记录模型在哪里做对、在哪里失败，并用 repeat=5 的可复现实验说明 Retry 对 public pass、hidden pass 和综合分有帮助，但会增加工具调用与耗时，对 hard 任务仍然有限。
