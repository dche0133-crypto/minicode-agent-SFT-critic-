# Benchmark Experiment Report

- baseline: `benchmark_results\baseline_repeat3\run_20260625-080805.json`
- candidate: `benchmark_results\retry_repeat3\run_20260625-081454.json`
- model: `ollama` / `qwen2.5-coder:7b`

## Overall Metrics

| Metric | Baseline | Candidate | Delta | Baseline Std | Candidate Std |
| --- | --- | --- | --- | --- | --- |
| Public Pass Rate | 40.0% | 58.3% | +18.3 pp | 0.0% | 2.4% |
| Hidden Pass Rate | 30.0% | 41.7% | +11.7 pp | 0.0% | 2.4% |
| Avg Score | 36.29 | 56.49 | +20.19 | 0.02 | 1.58 |
| Avg Tool Calls | 2.95 | 4.63 | +1.68 | 0.04 | 0.10 |

## Failure Type Transition Matrix

| Baseline Failure | Candidate Failure | Tasks |
| --- | --- | --- |
| no_test_run | test_failure | 6 |
| none | none | 6 |
| no_test_run | hidden_test_failed | 2 |
| no_test_run | no_test_run | 2 |
| no_test_run | none | 2 |
| no_test_run | repeated_tool_call | 1 |
| syntax_error | syntax_error | 1 |

## Difficulty Breakdown

| Difficulty | Baseline Hidden | Candidate Hidden | Delta | Baseline Score | Candidate Score | Candidate Tools |
| --- | --- | --- | --- | --- | --- | --- |
| easy | 42.9% | 52.4% | +9.5 pp | 50.16 | 66.73 | 3.76 |
| hard | 0.0% | 0.0% | +0.0 pp | 9.90 | 26.94 | 5.53 |
| medium | 37.5% | 58.3% | +20.8 pp | 40.67 | 65.98 | 4.83 |

## Per-Task Changes

| Task | Difficulty | Public | Hidden | Score Delta | Failure Transition | Retry Status |
| --- | --- | --- | --- | --- | --- | --- |
| top_k_words | medium | 0/3 -> 3/3 | 0/3 -> 3/3 | +86.00 | no_test_run -> none | completed |
| config_records_parser | medium | 0/3 -> 2/3 | 0/3 -> 2/3 | +59.89 | no_test_run -> none | diagnosis_failed |
| merge_intervals | easy | 0/3 -> 1/3 | 0/3 -> 1/3 | +42.22 | no_test_run -> test_failure | candidate_rejected |
| slugify_title | easy | 0/3 -> 1/3 | 0/3 -> 1/3 | +38.11 | no_test_run -> test_failure | candidate_rejected |
| config_defaults | medium | 0/3 -> 2/3 | 0/3 -> 0/3 | +35.67 | no_test_run -> hidden_test_failed | completed |
| parse_bool_flag | easy | 0/3 -> 2/3 | 0/3 -> 0/3 | +35.67 | no_test_run -> hidden_test_failed | completed |
| template_renderer | hard | 0/3 -> 0/3 | 0/3 -> 0/3 | +21.77 | no_test_run -> test_failure | candidate_rejected |
| dependency_order | hard | 0/3 -> 0/3 | 0/3 -> 0/3 | +21.42 | no_test_run -> test_failure | candidate_rejected |
| record_deduplicator | hard | 0/3 -> 0/3 | 0/3 -> 0/3 | +21.42 | no_test_run -> test_failure | candidate_rejected |
| csv_row_parser | medium | 0/3 -> 0/3 | 0/3 -> 0/3 | +21.00 | no_test_run -> test_failure | candidate_rejected |
| rolling_rate_limit | hard | 0/3 -> 0/3 | 0/3 -> 0/3 | +13.07 | syntax_error -> syntax_error | candidate_rejected |
| multi_file_config_service | hard | 0/3 -> 0/3 | 0/3 -> 0/3 | +7.56 | no_test_run -> repeated_tool_call | candidate_rejected |
| bubble_sort_order | easy | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |
| clamp_values | easy | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |
| date_window | medium | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |
| log_level_parser | medium | 3/3 -> 3/3 | 0/3 -> 0/3 | +0.00 | no_test_run -> no_test_run | - |
| path_validator | medium | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |
| retry_delay | easy | 3/3 -> 3/3 | 0/3 -> 0/3 | +0.00 | no_test_run -> no_test_run | - |
| token_bucket | medium | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |
| word_frequency | easy | 3/3 -> 3/3 | 3/3 -> 3/3 | +0.00 | none -> none | - |

## Reading Notes

- Hidden-pass improved tasks: 4
- Hidden-pass regressed tasks: 0
- Score improved without hidden-pass gain: 7
- Treat this as an evaluation report, not proof of model capability by itself.
