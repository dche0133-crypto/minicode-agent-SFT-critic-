"""Create the built-in 20-task MiniCode benchmark catalog.

The two original tasks remain untouched; this script adds eighteen deterministic
bug-fix tasks with public and hidden tests. It is safe to run again because
existing task directories are not overwritten unless --force is supplied.
"""

import argparse
import json
import shutil
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks" / "bugfix"


def task(source, public, hidden, prompt, difficulty, expected_files, tags, extra_files=None):
    return {
        "source": source,
        "public": public,
        "hidden": hidden,
        "prompt": prompt,
        "difficulty": difficulty,
        "expected_files": expected_files,
        "tags": tags,
        "extra_files": extra_files or {},
    }


TASKS = {
    "clamp_values": task(
        "def clamp(value, lower, upper):\n    return min(max(value, upper), lower)\n",
        "from clamp_values import clamp\n\ndef test_inside_range():\n    assert clamp(5, 0, 10) == 5\n\ndef test_lower_bound():\n    assert clamp(-2, 0, 10) == 0\n",
        "from clamp_values import clamp\n\ndef test_upper_bound_and_float():\n    assert clamp(12, 0, 10) == 10\n    assert clamp(1.5, 0.0, 2.0) == 1.5\n",
        "Fix clamp_values.py. clamp(value, lower, upper) must return value constrained to the inclusive range. Keep the API unchanged and run tests.",
        "easy", ["clamp_values.py"], ["python", "numeric", "boundary", "easy"],
    ),
    "parse_bool_flag": task(
        "def parse_bool(value):\n    return bool(value)\n",
        "import pytest\nfrom parse_bool_flag import parse_bool\n\ndef test_common_values():\n    assert parse_bool('true') is True\n    assert parse_bool('false') is False\n\ndef test_boolean_input():\n    assert parse_bool(True) is True\n",
        "import pytest\nfrom parse_bool_flag import parse_bool\n\ndef test_case_and_whitespace():\n    assert parse_bool(' YES ') is True\n    assert parse_bool('0') is False\n\ndef test_invalid_value():\n    with pytest.raises(ValueError):\n        parse_bool('sometimes')\n",
        "Fix parse_bool_flag.py. parse_bool accepts bools and case-insensitive true/false, yes/no, 1/0 strings with surrounding whitespace; unknown strings raise ValueError.",
        "easy", ["parse_bool_flag.py"], ["python", "parsing", "validation", "easy"],
    ),
    "word_frequency": task(
        "def word_frequency(text):\n    words = text.split(' ')\n    return {word: words.count(word) for word in words}\n",
        "from word_frequency import word_frequency\n\ndef test_counts_words_case_insensitively():\n    assert word_frequency('Red red blue') == {'red': 2, 'blue': 1}\n",
        "from word_frequency import word_frequency\n\ndef test_ignores_punctuation_and_extra_whitespace():\n    assert word_frequency('hello,  hello!\\nworld') == {'hello': 2, 'world': 1}\n",
        "Fix word_frequency.py. Count alphabetic words case-insensitively, ignoring punctuation and repeated whitespace.",
        "easy", ["word_frequency.py"], ["python", "text", "normalization", "easy"],
    ),
    "slugify_title": task(
        "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
        "from slugify_title import slugify\n\ndef test_basic_slug_and_punctuation():\n    assert slugify('Hello World') == 'hello-world'\n    assert slugify('Hello, World!') == 'hello-world'\n",
        "from slugify_title import slugify\n\ndef test_punctuation_and_whitespace():\n    assert slugify('  Hello,   World!  ') == 'hello-world'\n    assert slugify('CafE & Tea') == 'cafe-tea'\n",
        "Fix slugify_title.py. Produce lowercase ASCII slugs: trim, keep letters/digits, collapse non-alphanumeric runs into one hyphen, and remove edge hyphens.",
        "easy", ["slugify_title.py"], ["python", "text", "regex", "easy"],
    ),
    "merge_intervals": task(
        "def merge_intervals(intervals):\n    if not intervals:\n        return []\n    result = [list(sorted(intervals)[0])]\n    for start, end in sorted(intervals)[1:]:\n        if start < result[-1][1]:\n            result[-1][1] = max(result[-1][1], end)\n        else:\n            result.append([start, end])\n    return result\n",
        "from merge_intervals import merge_intervals\n\ndef test_overlapping_and_touching_ranges():\n    assert merge_intervals([(1, 3), (2, 4), (8, 9)]) == [[1, 4], [8, 9]]\n    assert merge_intervals([(1, 2), (2, 5)]) == [[1, 5]]\n",
        "from merge_intervals import merge_intervals\n\ndef test_touching_and_unsorted_ranges():\n    assert merge_intervals([(5, 7), (1, 2), (2, 5)]) == [[1, 7]]\n",
        "Fix merge_intervals.py. Merge overlapping or touching inclusive intervals and support unsorted input without mutating it.",
        "easy", ["merge_intervals.py"], ["python", "algorithms", "intervals", "easy"],
    ),
    "retry_delay": task(
        "def retry_delay(attempt, base=1, cap=30):\n    return base * attempt\n",
        "from retry_delay import retry_delay\n\ndef test_exponential_delay():\n    assert retry_delay(0) == 1\n    assert retry_delay(2, base=2) == 8\n",
        "from retry_delay import retry_delay\n\ndef test_cap_and_invalid_attempt():\n    assert retry_delay(10, base=4, cap=30) == 30\n    try:\n        retry_delay(-1)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('negative attempts must fail')\n",
        "Fix retry_delay.py. Use capped exponential backoff base * 2**attempt; reject negative attempts.",
        "easy", ["retry_delay.py"], ["python", "backoff", "boundary", "easy"],
    ),
    "csv_row_parser": task(
        "def parse_row(line):\n    return [cell.strip() for cell in line.split(',')]\n",
        "from csv_row_parser import parse_row\n\ndef test_plain_and_quoted_cells():\n    assert parse_row('a, b, c') == ['a', 'b', 'c']\n    assert parse_row('a,\"b,c\",d') == ['a', 'b,c', 'd']\n",
        '''from csv_row_parser import parse_row

def test_quoted_commas_and_escaped_quotes():
    assert parse_row('a,"b,c","say ""hi"""') == ['a', 'b,c', 'say "hi"']
''',
        "Fix csv_row_parser.py. Parse one CSV row with Python CSV semantics, including quoted commas and doubled quote escaping.",
        "medium", ["csv_row_parser.py"], ["python", "csv", "parsing", "medium"],
    ),
    "date_window": task(
        "from datetime import timedelta\n\ndef days_between(start, end):\n    return (end - start).days\n",
        "from datetime import date\nfrom date_window import days_between\n\ndef test_inclusive_window():\n    assert days_between(date(2024, 1, 1), date(2024, 1, 1)) == 1\n    assert days_between(date(2024, 1, 1), date(2024, 1, 3)) == 3\n",
        "from datetime import date\nimport pytest\nfrom date_window import days_between\n\ndef test_leap_day_and_reverse_range():\n    assert days_between(date(2024, 2, 28), date(2024, 3, 1)) == 3\n    with pytest.raises(ValueError):\n        days_between(date(2024, 3, 1), date(2024, 2, 28))\n",
        "Fix date_window.py. days_between must count both endpoints and reject an end date before start.",
        "medium", ["date_window.py"], ["python", "datetime", "edge-case", "medium"],
    ),
    "path_validator": task(
        "def is_safe_relative(path):\n    return not path.startswith('/')\n",
        "from path_validator import is_safe_relative\n\ndef test_accepts_normal_and_rejects_parent_traversal():\n    assert is_safe_relative('src/app.py')\n    assert is_safe_relative('README.md')\n    assert not is_safe_relative('../secret.txt')\n",
        "from path_validator import is_safe_relative\n\ndef test_rejects_escape_and_absolute_paths():\n    assert not is_safe_relative('../secret.txt')\n    assert not is_safe_relative('/etc/passwd')\n    assert not is_safe_relative('C:\\\\temp\\\\x.txt')\n",
        "Fix path_validator.py. Accept only non-empty relative paths that do not contain parent traversal and reject Unix or Windows absolute paths.",
        "medium", ["path_validator.py"], ["python", "security", "paths", "medium"],
    ),
    "top_k_words": task(
        "def top_k_words(words, k):\n    counts = {word: words.count(word) for word in set(words)}\n    return sorted(counts, key=counts.get, reverse=True)[:k]\n",
        "from top_k_words import top_k_words\n\ndef test_frequency_order_and_ties():\n    assert top_k_words(['a', 'b', 'a', 'c', 'a', 'b'], 2) == ['a', 'b']\n    assert top_k_words(['b', 'a', 'c', 'b', 'a', 'c'], 3) == ['a', 'b', 'c']\n",
        "from top_k_words import top_k_words\n\ndef test_tie_break_and_large_k():\n    assert top_k_words(['b', 'a', 'c', 'b', 'a', 'c'], 3) == ['a', 'b', 'c']\n    assert top_k_words(['x'], 5) == ['x']\n",
        "Fix top_k_words.py. Return distinct words by descending frequency and alphabetical order for ties. Reject negative k.",
        "medium", ["top_k_words.py"], ["python", "collections", "sorting", "medium"],
    ),
    "config_defaults": task(
        "def apply_defaults(config):\n    config.setdefault('retries', 3)\n    config.setdefault('timeout', 30)\n    return config\n",
        "from config_defaults import apply_defaults\n\ndef test_adds_top_level_defaults_without_mutating_input():\n    source = {'timeout': 5}\n    result = apply_defaults(source)\n    assert result == {'retries': 3, 'timeout': 5, 'database': {'port': 5432}}\n    assert source == {'timeout': 5}\n",
        "from config_defaults import apply_defaults\n\ndef test_preserves_nested_values():\n    assert apply_defaults({'database': {'port': 3306}})['database'] == {'port': 3306}\n",
        "Fix config_defaults.py. Return a new configuration with retries=3, timeout=30, and database.port=5432 defaults; never mutate caller input.",
        "medium", ["config_defaults.py"], ["python", "configuration", "immutability", "medium"],
    ),
    "token_bucket": task(
        "class TokenBucket:\n    def __init__(self, capacity, tokens=None):\n        self.capacity = capacity\n        self.tokens = capacity if tokens is None else tokens\n\n    def consume(self, amount=1):\n        if self.tokens > 0:\n            self.tokens -= amount\n            return True\n        return False\n",
        "from token_bucket import TokenBucket\n\ndef test_consumes_only_available_tokens():\n    bucket = TokenBucket(3)\n    assert bucket.consume(2)\n    assert bucket.tokens == 1\n    assert not bucket.consume(2)\n    assert bucket.tokens == 1\n",
        "import pytest\nfrom token_bucket import TokenBucket\n\ndef test_rejects_invalid_amounts_and_respects_capacity():\n    bucket = TokenBucket(2, tokens=5)\n    assert bucket.tokens == 2\n    with pytest.raises(ValueError):\n        bucket.consume(0)\n",
        "Fix token_bucket.py. Never allow consumption beyond available tokens, clamp initial tokens to capacity, and require a positive amount.",
        "medium", ["token_bucket.py"], ["python", "state", "validation", "medium"],
    ),
    "log_level_parser": task(
        "def parse_log(line):\n    level, message = line.split(':', 1)\n    return {'level': level, 'message': message}\n",
        "from log_level_parser import parse_log\n\ndef test_parses_level_and_message():\n    assert parse_log('INFO: started') == {'level': 'INFO', 'message': 'started'}\n",
        "import pytest\nfrom log_level_parser import parse_log\n\ndef test_normalizes_and_rejects_unknown_level():\n    assert parse_log(' warning : disk: 90% ') == {'level': 'WARNING', 'message': 'disk: 90%'}\n    with pytest.raises(ValueError):\n        parse_log('TRACE: detail')\n",
        "Fix log_level_parser.py. Parse INFO/WARNING/ERROR case-insensitively, trim fields, preserve colons in messages, and reject unknown levels or blank messages.",
        "medium", ["log_level_parser.py"], ["python", "parsing", "validation", "medium"],
    ),
    "dependency_order": task(
        "def dependency_order(graph):\n    return sorted(graph)\n",
        "from dependency_order import dependency_order\n\ndef test_orders_dependencies_first():\n    graph = {'build': ['compile'], 'compile': ['parse'], 'parse': []}\n    assert dependency_order(graph) == ['parse', 'compile', 'build']\n",
        "import pytest\nfrom dependency_order import dependency_order\n\ndef test_includes_leaf_dependencies_and_detects_cycles():\n    assert dependency_order({'app': ['lib'], 'lib': ['core']}) == ['core', 'lib', 'app']\n    with pytest.raises(ValueError):\n        dependency_order({'a': ['b'], 'b': ['a']})\n",
        "Fix dependency_order.py. Return a deterministic topological order with dependencies first, include referenced leaves absent from graph keys, and raise ValueError on cycles.",
        "hard", ["dependency_order.py"], ["python", "graphs", "topological-sort", "hard"],
    ),
    "template_renderer": task(
        "def render(template, values):\n    for key, value in values.items():\n        template = template.replace('{{' + key + '}}', str(value))\n    return template\n",
        "from template_renderer import render\n\ndef test_replaces_known_values_and_defaults():\n    assert render('Hello {{name}}', {'name': 'Ada'}) == 'Hello Ada'\n    assert render('{{name|guest}}', {}) == 'guest'\n",
        "import pytest\nfrom template_renderer import render\n\ndef test_defaults_escaping_and_missing_key():\n    assert render('{{name|guest}}', {}) == 'guest'\n    assert render('\\\\{{name}}', {'name': 'Ada'}) == '{{name}}'\n    with pytest.raises(KeyError):\n        render('{{name}}', {})\n",
        "Fix template_renderer.py. Support {{name}}, {{name|default}}, and escaped placeholders written as \\{{name}}. Unknown required placeholders raise KeyError.",
        "hard", ["template_renderer.py"], ["python", "parsing", "templating", "hard"],
    ),
    "rolling_rate_limit": task(
        "def allow(events, now, limit, window):\n    return len(events) < limit\n",
        "from rolling_rate_limit import allow\n\ndef test_allows_under_limit_and_discards_expired():\n    assert allow([8, 9], now=10, limit=3, window=5)\n    assert not allow([8, 9, 10], now=10, limit=3, window=5)\n    assert allow([1, 2, 8], now=10, limit=2, window=5)\n",
        "import pytest\nfrom rolling_rate_limit import allow\n\ndef test_discards_expired_and_validates_inputs():\n    assert allow([1, 2, 8], now=10, limit=2, window=5)\n    with pytest.raises(ValueError):\n        allow([], now=1, limit=0, window=5)\n",
        "Fix rolling_rate_limit.py. Count only events in the inclusive rolling window [now-window+1, now], and validate positive limit/window.",
        "hard", ["rolling_rate_limit.py"], ["python", "algorithms", "time-window", "hard"],
    ),
    "multi_file_config_service": task(
        "from settings import DEFAULT_TIMEOUT\n\ndef request_options(config):\n    return {'timeout': config.get('timeout', DEFAULT_TIMEOUT), 'retries': config.get('retries') or 1}\n",
        "from service import request_options\n\ndef test_uses_defaults_and_explicit_values():\n    assert request_options({}) == {'timeout': 30, 'retries': 3}\n    assert request_options({'timeout': 5, 'retries': 2}) == {'timeout': 5, 'retries': 2}\n",
        "import pytest\nfrom service import request_options\n\ndef test_rejects_invalid_values_and_keeps_zero_semantics_explicit():\n    with pytest.raises(ValueError):\n        request_options({'timeout': 0})\n    with pytest.raises(ValueError):\n        request_options({'retries': -1})\n",
        "Fix the multi-file configuration service. Use defaults from settings.py, validate positive timeout and non-negative retries, and preserve the public request_options API.",
        "hard", ["service.py", "settings.py"], ["python", "multi-file", "configuration", "hard"],
        {"settings.py": "DEFAULT_TIMEOUT = 30\nDEFAULT_RETRIES = 3\n"},
    ),
    "record_deduplicator": task(
        "def deduplicate(records):\n    return list({record['id']: record for record in records}.values())\n",
        "from record_deduplicator import deduplicate\n\ndef test_keeps_last_record_with_normalized_id():\n    records = [{'id': ' A ', 'value': 1}, {'id': 'b', 'value': 2}, {'id': 'a', 'value': 3}]\n    assert deduplicate(records) == [{'id': 'a', 'value': 3}, {'id': 'b', 'value': 2}]\n",
        "import pytest\nfrom record_deduplicator import deduplicate\n\ndef test_normalizes_ids_and_rejects_missing_id():\n    assert deduplicate([{'id': ' A ', 'value': 1}, {'id': 'a', 'value': 2}]) == [{'id': 'a', 'value': 2}]\n    with pytest.raises(ValueError):\n        deduplicate([{'value': 1}])\n",
        "Fix record_deduplicator.py. Deduplicate by normalized lowercase id, keep the last record's value but first-seen id order, and reject missing/blank ids.",
        "hard", ["record_deduplicator.py"], ["python", "data-processing", "ordering", "hard"],
    ),
}


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def seed_task(task_id, spec, force=False):
    task_root = BENCHMARK_ROOT / task_id
    if task_root.exists() and not force:
        return False
    if task_root.exists():
        shutil.rmtree(task_root)
    source_name = spec["expected_files"][0]
    write(task_root / "repo" / source_name, spec["source"])
    for name, content in spec["extra_files"].items():
        write(task_root / "repo" / name, content)
    write(task_root / "public_tests" / "test_public.py", spec["public"])
    write(task_root / "hidden_tests" / "test_hidden.py", spec["hidden"])
    write(task_root / "prompt.txt", spec["prompt"] + "\n")
    metadata = {
        "id": task_id,
        "type": "bugfix",
        "difficulty": spec["difficulty"],
        "check_command": "python -m pytest -q",
        "max_steps": 16 if spec["difficulty"] != "hard" else 20,
        "max_patch_lines": 80 if spec["difficulty"] != "hard" else 120,
        "expected_files": spec["expected_files"],
        "tags": spec["tags"],
    }
    write(task_root / "metadata.json", json.dumps(metadata, indent=2) + "\n")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed the MiniCode benchmark catalog.")
    parser.add_argument("--force", action="store_true", help="Replace already generated task directories.")
    args = parser.parse_args(argv)
    created = [task_id for task_id, spec in TASKS.items() if seed_task(task_id, spec, args.force)]
    print(json.dumps({"created": created, "created_count": len(created), "catalog_total": len(TASKS) + 2}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
