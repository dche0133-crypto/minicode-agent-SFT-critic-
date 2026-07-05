# MiniCode-Agent Hard Task Analysis

这份文档专门分析 hard 任务为什么没有被解决。重点不是证明模型很强，而是把失败拆清楚：到底是模型推理问题、工具使用问题，还是任务表达问题。

## 1. 总体结论

在 `repeat=5` 实验中，规则 Critic Retry 对 easy / medium 任务有明显帮助，但 hard 任务的 hidden pass 仍然是 `0.0% -> 0.0%`。

| 难度 | Baseline Hidden | Critic Retry Hidden | Score 变化 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Easy | 40.0% | 42.9% | +13.94 | 有轻微提升 |
| Medium | 45.0% | 60.0% | +20.97 | 提升最明显 |
| Hard | 0.0% | 0.0% | +16.47 | 行为更规范，但没有真正解题 |

这说明 Retry 主要改善了“执行纪律”：让 Agent 更愿意跑测试、收集证据、进入修复流程。但 hard 任务需要更强的算法理解、多文件定位和边界条件推理，单靠规则 Retry 还不够。

## 2. 三类失败原因

| 类型 | 含义 | 在项目里的表现 |
| --- | --- | --- |
| Model reasoning failure | 模型理解错算法或业务语义 | 写了代码，但核心逻辑方向错了 |
| Tool misuse | 模型没有正确使用工具 | 没跑测试、重复调用、没有真正编辑 |
| Representation issue | 任务/测试/轨迹表达不够帮助模型定位问题 | 报错信息不足，hidden 边界没有被显式暴露 |

这三类不是互斥的。一个任务可能同时有模型推理问题和表达问题。

## 3. Hard Task 结果表

| 任务 | Hidden 变化 | Score 变化 | Failure 转移 | Retry 状态 | 主要问题 |
| --- | ---: | ---: | --- | --- | --- |
| `dependency_order` | 0/5 -> 0/5 | +21.42 | `no_test_run -> test_failure` | candidate_rejected | 模型推理失败 |
| `template_renderer` | 0/5 -> 0/5 | +21.73 | `no_test_run -> test_failure` | candidate_rejected | 表达与边界条件复杂 |
| `record_deduplicator` | 0/5 -> 0/5 | +21.42 | `no_test_run -> test_failure` | candidate_rejected | 数据语义和去重规则理解不足 |
| `rolling_rate_limit` | 0/5 -> 0/5 | +12.90 | `syntax_error -> syntax_error` | candidate_rejected | 工具/补丁生成质量问题 |
| `multi_file_config_service` | 0/5 -> 0/5 | +4.89 | `no_test_run -> repeated_tool_call` | candidate_rejected | 多文件定位和工具控制问题 |

## 4. Case Study: dependency_order

### 任务本质

这题要求对依赖关系做排序。简单说：

```text
build 依赖 compile
compile 依赖 parse
正确顺序应该是：parse -> compile -> build
```

也就是“被依赖的节点要排在前面”。

### Agent 的典型错误

Agent 写出了看起来像拓扑排序的代码，但把边的方向理解反了。它更像是在输出：

```text
build -> compile -> parse
```

这说明它不是完全没有写算法，而是对“依赖方向”的语义理解错了。

### 失败分类

| 维度 | 判断 |
| --- | --- |
| Model reasoning failure | 是。核心错误是依赖方向理解反了 |
| Tool misuse | 部分是。Retry 后能进入测试，但仍没有形成有效修复 |
| Representation issue | 有一点。prompt 可以更明确说明 `A depends on B` 时 `B` 必须在 `A` 前面 |

### 为什么 Retry 没救回来

Retry 把原来的 `no_test_run` 推进成了 `test_failure`，说明系统让 Agent 看到更多证据了。但看到测试失败不等于模型能理解失败背后的算法语义。

换句话说，系统控制流变好了，模型推理能力没有被自动增强。

## 5. Case Study: template_renderer

### 任务本质

这类题通常包含模板占位符、默认值、转义、缺失字段等边界条件。表面是字符串处理，实际容易踩规则细节。

### 失败分类

| 维度 | 判断 |
| --- | --- |
| Model reasoning failure | 部分是。模型可能只覆盖了最常见模板 |
| Tool misuse | 较少。Retry 已经把失败推进到 `test_failure` |
| Representation issue | 明显。hidden case 可能覆盖转义、缺失值或多个占位符组合 |

### 改进方向

可以在 failure analysis 中增加“边界条件不足”的诊断，比如当 public 失败信息显示字符串差异时，让 Critic 强制读取 public tests 并归纳缺失规则，而不是直接改代码。

## 6. Case Study: multi_file_config_service

### 任务本质

这类任务涉及多个文件之间的配置读取、默认值、路径或服务调用关系。难点不是某一行代码，而是定位“真正应该改哪个文件”。

### 失败分类

| 维度 | 判断 |
| --- | --- |
| Model reasoning failure | 部分是。需要理解模块协作 |
| Tool misuse | 明显。出现 `repeated_tool_call` |
| Representation issue | 明显。trajectory 需要更突出文件关系和调用链 |

### 改进方向

对多文件任务，Retry 的 Diagnose 阶段不应该只跑测试，还应该允许一次受控的 `search_code` 或读取 metadata 中的相关文件列表。否则模型可能反复读错地方。

## 7. 对系统的启发

Hard 任务没有过，不代表系统没价值。恰恰相反，它暴露了下一步优化方向：

| 问题 | 下一步优化 |
| --- | --- |
| 模型理解错算法语义 | 在 Critic 决策中加入 `reasoning_failure` 和“重新解释问题约束”动作 |
| 模型只看到测试失败但不会归纳规则 | 增加 test-diff summarization，把 expected/got 转成自然语言约束 |
| 多文件任务定位困难 | 在 metadata 中加入 related_files，并让 Diagnose 阶段受控读取 |
| Retry 成本增加 | 用 Agent Score 同时惩罚工具调用、耗时和 patch 规模 |
| 候选补丁被拒绝但信息没被充分利用 | 把 rejected patch、reject reason 写入 SFT v3 |

## 8. 面试时怎么说

可以这样表达：

> 我发现规则 Critic Retry 对 easy 和 medium 任务有效，但 hard 任务没有提升 hidden pass。进一步分析后，我把 hard task 失败拆成三类：模型推理失败、工具误用和任务表达问题。比如 dependency_order 的核心问题不是没跑测试，而是模型把依赖边方向理解反了。这说明当前系统已经能改善执行纪律，但要解决 hard task，还需要更强的约束表达、测试差异总结和 learned critic 的在线决策能力。

这个说法比“hard task 没做出来”更有价值，因为它说明你知道系统瓶颈在哪里。
