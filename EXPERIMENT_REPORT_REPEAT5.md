# Benchmark Experiment Report

- baseline: `benchmark_results\baseline_repeat5\run_20260626-121009.json`
- candidate: `benchmark_results\retry_repeat5\run_20260626-122631.json`
- model: `ollama` / `qwen2.5-coder:7b`

## Overall Metrics

| Metric | Baseline | Candidate | Delta | Baseline Std | Candidate Std |
| --- | --- | --- | --- | --- | --- |
| Public Pass Rate | 41.0% | 58.0% | +17.0 pp | 3.7% | 2.5% |
| Hidden Pass Rate | 32.0% | 39.0% | +7.0 pp | 5.1% | 2.0% |
| Avg Score | 37.83 | 55.21 | +17.38 | 3.74 | 1.90 |
| Avg Tool Calls | 2.98 | 4.83 | +1.85 | 0.05 | 0.29 |
| Avg Wall Time Sec | 8.28 | 13.42 | +5.14 | 0.85 | 0.55 |
| Avg Patch Lines | 7.38 | 8.62 | +1.24 | 0.72 | 0.13 |
| Avg Patch Files | 0.95 | 1.02 | +0.07 | 0.03 | 0.02 |

## Visual Summary

| Metric | Baseline Bar | Baseline | Candidate Bar | Candidate |
| --- | --- | --- | --- | --- |
| Public | ████████░░░░░░░░░░░░ | 41.0% | ████████████░░░░░░░░ | 58.0% |
| Hidden | ██████░░░░░░░░░░░░░░ | 32.0% | ████████░░░░░░░░░░░░ | 39.0% |

## Failure Type Transition Matrix

| Baseline Failure | Candidate Failure | Tasks |
| --- | --- | --- |
| none | none | 7 |
| no_test_run | test_failure | 6 |
| no_test_run | hidden_test_failed | 2 |
| no_test_run | no_test_run | 2 |
| no_test_run | none | 1 |
| no_test_run | repeated_tool_call | 1 |
| syntax_error | syntax_error | 1 |

## Difficulty Breakdown

| Difficulty | Baseline Hidden | Candidate Hidden | Delta | Baseline Score | Candidate Score | Candidate Tools | Candidate Patch Lines | Candidate Wall Time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| easy | 40.0% | 42.9% | +2.9 pp | 46.93 | 60.87 | 4.20 | 4.60 | 10.35 |
| hard | 0.0% | 0.0% | +0.0 pp | 9.86 | 26.33 | 5.68 | 9.96 | 15.75 |
| medium | 45.0% | 60.0% | +15.0 pp | 47.34 | 68.31 | 4.85 | 11.30 | 14.65 |

## Per-Task Changes

| Task | Difficulty | Public | Hidden | Score Delta | Tool Delta | Patch Delta | Failure Transition | Retry Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| top_k_words | medium | 0/5 -> 5/5 | 0/5 -> 5/5 | +86.00 | +3.00 | +0.00 | no_test_run -> none | completed |
| word_frequency | easy | 3/5 -> 5/5 | 3/5 -> 5/5 | +32.00 | +0.00 | +1.20 | none -> none | - |
| config_records_parser | medium | 3/5 -> 4/5 | 3/5 -> 4/5 | +17.60 | +0.00 | +4.20 | none -> none | diagnosis_failed |
| config_defaults | medium | 0/5 -> 5/5 | 0/5 -> 0/5 | +43.13 | +2.80 | +16.60 | no_test_run -> hidden_test_failed | completed |
| parse_bool_flag | easy | 0/5 -> 4/5 | 0/5 -> 0/5 | +38.60 | +3.00 | +0.00 | no_test_run -> hidden_test_failed | completed |
| template_renderer | hard | 0/5 -> 0/5 | 0/5 -> 0/5 | +21.73 | +2.40 | +1.20 | no_test_run -> test_failure | candidate_rejected |
| dependency_order | hard | 0/5 -> 0/5 | 0/5 -> 0/5 | +21.42 | +3.00 | +0.00 | no_test_run -> test_failure | candidate_rejected |
| record_deduplicator | hard | 0/5 -> 0/5 | 0/5 -> 0/5 | +21.42 | +3.00 | +0.00 | no_test_run -> test_failure | candidate_rejected |
| csv_row_parser | medium | 0/5 -> 0/5 | 0/5 -> 0/5 | +21.00 | +3.00 | +0.00 | no_test_run -> test_failure | candidate_rejected |
| merge_intervals | easy | 0/5 -> 0/5 | 0/5 -> 0/5 | +20.86 | +3.20 | +1.40 | no_test_run -> test_failure | candidate_rejected |
| slugify_title | easy | 0/5 -> 0/5 | 0/5 -> 0/5 | +15.66 | +5.60 | +0.00 | no_test_run -> test_failure | candidate_rejected |
| rolling_rate_limit | hard | 0/5 -> 0/5 | 0/5 -> 0/5 | +12.90 | +4.00 | +0.00 | syntax_error -> syntax_error | candidate_rejected |
| multi_file_config_service | hard | 0/5 -> 0/5 | 0/5 -> 0/5 | +4.89 | +4.00 | +0.00 | no_test_run -> repeated_tool_call | candidate_rejected |
| bubble_sort_order | easy | 5/5 -> 5/5 | 5/5 -> 5/5 | +0.00 | +0.00 | +0.00 | none -> none | - |
| clamp_values | easy | 5/5 -> 5/5 | 5/5 -> 5/5 | +0.00 | +0.00 | +0.00 | none -> none | - |
| date_window | medium | 5/5 -> 5/5 | 5/5 -> 5/5 | +0.00 | +0.00 | +0.00 | none -> none | - |
| log_level_parser | medium | 5/5 -> 5/5 | 0/5 -> 0/5 | +0.00 | +0.00 | +0.00 | no_test_run -> no_test_run | - |
| path_validator | medium | 5/5 -> 5/5 | 5/5 -> 5/5 | +0.00 | +0.00 | +0.00 | none -> none | - |
| token_bucket | medium | 5/5 -> 5/5 | 5/5 -> 5/5 | +0.00 | +0.00 | +0.20 | none -> none | - |
| retry_delay | easy | 5/5 -> 5/5 | 1/5 -> 0/5 | -9.60 | +0.00 | +0.00 | no_test_run -> no_test_run | - |

## Hard Task Case Study

| Task | Hidden | Score Delta | Tool Delta | Patch Delta | Wall Time Delta | Failure Transition | Retry Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| template_renderer | 0/5 -> 0/5 | +21.73 | +2.40 | +1.20 | +6.13 | no_test_run -> test_failure | candidate_rejected |
| dependency_order | 0/5 -> 0/5 | +21.42 | +3.00 | +0.00 | +7.39 | no_test_run -> test_failure | candidate_rejected |
| record_deduplicator | 0/5 -> 0/5 | +21.42 | +3.00 | +0.00 | +8.50 | no_test_run -> test_failure | candidate_rejected |
| rolling_rate_limit | 0/5 -> 0/5 | +12.90 | +4.00 | +0.00 | +10.94 | syntax_error -> syntax_error | candidate_rejected |
| multi_file_config_service | 0/5 -> 0/5 | +4.89 | +4.00 | +0.00 | +10.44 | no_test_run -> repeated_tool_call | candidate_rejected |

## Reading Notes

- Hidden-pass improved tasks: 3
- Hidden-pass regressed tasks: 1
- Score improved without hidden-pass gain: 9
- Treat this as an evaluation report, not proof of model capability by itself.
