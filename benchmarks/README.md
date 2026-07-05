# MiniCode Benchmark Catalog

The catalog contains 20 intentionally failing Python bug-fix tasks. Each task
has `repo/`, `public_tests/`, `hidden_tests/`, `prompt.txt`, and `metadata.json`.

| Difficulty | Tasks |
| --- | --- |
| Easy | `bubble_sort_order`, `clamp_values`, `parse_bool_flag`, `word_frequency`, `slugify_title`, `merge_intervals`, `retry_delay` |
| Medium | `config_records_parser`, `csv_row_parser`, `date_window`, `path_validator`, `top_k_words`, `config_defaults`, `token_bucket`, `log_level_parser` |
| Hard | `dependency_order`, `template_renderer`, `rolling_rate_limit`, `multi_file_config_service`, `record_deduplicator` |

Run one task:

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0 --task dependency_order
```

Run the full catalog:

```powershell
python scripts/run_benchmark.py --provider ollama --model qwen2.5-coder:7b --temperature 0
```

Regenerate the eighteen added tasks from their checked-in catalog definition:

```powershell
python scripts/seed_benchmark_suite.py --force
```
