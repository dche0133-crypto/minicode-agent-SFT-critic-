import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.critic_policy import FAILURE_TYPES, build_assessment

INSTRUCTION = (
    "You are a coding-agent critic. Given a task, agent trajectory, test output, "
    "and diff, return JSON with separate diagnosis and decision objects."
)

LABEL_SCHEMA_VERSION = "critic_diagnosis_decision.v2"

DEFAULT_QUALITY_FILTER = {
    "min_steps": 1,
    "min_quality_score": 0.75,
    "min_confidence": 0.3,
    "max_input_chars": 12000,
    "require_test_signal": True,
    "require_diff_for_edit_decisions": True,
    "exclude_failure_types": [],
}

EDIT_DECISION_ACTIONS = {
    "repair_syntax",
    "minimize_patch",
    "rollback_unrelated_edit",
}


TARGETS = [
    ("bubble_sort.py", "sorting", "comparison operator sorts descending instead of ascending"),
    ("parser.py", "parsing", "empty lines are parsed as invalid records"),
    ("config_loader.py", "configuration", "missing keys are not assigned defaults"),
    ("calculator.py", "arithmetic", "negative numbers are handled incorrectly"),
    ("slugify.py", "text normalization", "unicode punctuation is not stripped"),
    ("date_utils.py", "date handling", "leap-day ranges are off by one"),
    ("cache.py", "caching", "expired entries are returned instead of evicted"),
    ("csv_reader.py", "CSV parsing", "quoted commas split fields incorrectly"),
    ("validator.py", "validation", "None values are accepted for required fields"),
    ("path_utils.py", "path handling", "absolute paths bypass workspace checks"),
]

WRONG_FILES = [
    "README.md",
    "docs/usage.md",
    "tests/test_unrelated.py",
    "pyproject.toml",
    "examples/demo.py",
    "CHANGELOG.md",
    "scripts/dev_notes.py",
]

TEST_FAILURES = [
    ("tests/test_sorting.py::test_orders_values", "E AssertionError: assert [3, 2, 1] == [1, 2, 3]"),
    ("tests/test_parser.py::test_empty_line_is_skipped", "E ValueError: empty line"),
    ("tests/test_config.py::test_missing_timeout_uses_default", "E KeyError: 'timeout'"),
    ("tests/test_calculator.py::test_negative_numbers", "E AssertionError: assert 3 == -3"),
    ("tests/test_slugify.py::test_unicode_punctuation", "E AssertionError: 'hello—world' != 'hello-world'"),
    ("tests/test_dates.py::test_leap_day_range", "E AssertionError: assert 28 == 29"),
    ("tests/test_cache.py::test_expired_item_evicted", "E AssertionError: stale value returned"),
    ("tests/test_csv_reader.py::test_quoted_comma", "E AssertionError: ['a', '\"b', 'c\"'] != ['a', 'b,c']"),
    ("tests/test_validator.py::test_required_none_rejected", "E Failed: DID NOT RAISE <class 'ValueError'>"),
    ("tests/test_paths.py::test_absolute_path_rejected", "E AssertionError: path escaped workspace"),
]

TEST_COMMANDS = [
    "python -m pytest -q",
    "python -m pytest tests -q",
    "python -m pytest tests/test_public.py -q",
    "pytest -q",
]

SOURCE_SNIPPETS = [
    "def solve(value):\n    return value",
    "def parse(row):\n    return row.split(',')",
    "def load(config):\n    return config",
    "def validate(value):\n    return True",
    "def normalize(text):\n    return text.lower()",
]

SUGGESTION_STYLES = {
    "no_test_run": [
        "Run the benchmark test command before finalizing.",
        "Use run_tests first so the next action is grounded in observed failures.",
        "Do not claim completion until public tests have been executed.",
    ],
    "wrong_file": [
        "Inspect the expected target file and ignore unrelated documentation.",
        "Read the implementation named by the task metadata before editing.",
        "Switch from the unrelated file to the failing source file.",
    ],
    "unrelated_edit": [
        "Rollback unrelated edits and make a targeted change in the expected file.",
        "Keep the patch scoped to the implementation under test.",
        "Undo the unrelated modification before applying the actual fix.",
    ],
    "repeated_tool_call": [
        "Choose a different tool or edit after repeated identical observations.",
        "Stop repeating the same read and use the available evidence to patch.",
        "Use search, git_diff, or a targeted edit instead of repeating the call.",
    ],
    "early_stop_after_test_failure": [
        "Use the failing pytest output to choose a concrete edit and rerun tests.",
        "Do not stop after a failed run; inspect the failure and patch the target.",
        "Continue the test-fix loop until success or a clearly explained blocker.",
    ],
    "hidden_test_failed": [
        "Reason about edge cases beyond public tests before finalizing.",
        "Add a fix that generalizes to hidden cases instead of overfitting.",
        "Review boundary cases implied by the task and hidden failure.",
    ],
    "patch_too_large": [
        "Replace the broad rewrite with a minimal localized patch.",
        "Minimize the diff to the failing behavior only.",
        "Avoid rewriting the whole file for a small bugfix.",
    ],
    "test_failure": [
        "Inspect the failing assertion and the latest diff before choosing another edit.",
        "Read the target implementation and use the test output to make a minimal fix.",
        "Gather more evidence from the failing test before changing unrelated code.",
    ],
    "syntax_error": [
        "Remove invalid syntax, then rerun the smallest relevant test command.",
        "Inspect the target file for Markdown fences or indentation mistakes before editing again.",
    ],
    "tool_protocol_error": [
        "Issue one valid structured tool call instead of returning protocol text as an answer.",
        "Retry with the declared XML or JSON tool-call format and gather fresh evidence.",
    ],
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact_text(text, limit=3000):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def compact_steps(training):
    lines = []
    for step in training.get("steps", []):
        action = step.get("action", "")
        args = json.dumps(step.get("args", {}), ensure_ascii=False, sort_keys=True)
        observation = compact_text(step.get("observation", ""), 700).replace("\n", "\\n")
        success = step.get("success")
        lines.append(f"step={step.get('step')} action={action} success={success} args={args} observation={observation}")
    return "\n".join(lines) or "(empty)"


def latest_diff(training):
    for step in reversed(training.get("steps", [])):
        diff = step.get("diff")
        if diff:
            return compact_text(diff, 2500)
    return ""


def output_label(analysis, metadata=None):
    label = build_assessment(
        analysis.get("failure_type", "test_failure"),
        analysis.get("reason", ""),
        evidence=analysis.get("evidence", ""),
        suggestion=analysis.get("suggestion", ""),
        metadata=metadata,
        confidence=analysis.get("confidence"),
    )
    label["schema"] = LABEL_SCHEMA_VERSION
    return label


def sample_input(task, training, public_result=None, hidden_result=None):
    public_result = public_result or {}
    hidden_result = hidden_result or {}
    return "\n\n".join(
        [
            "Task:\n" + compact_text(task, 1000),
            "Trajectory:\n" + compact_steps(training),
            "Public test output:\n" + compact_text(public_result.get("stdout", "") + public_result.get("stderr", ""), 2000),
            "Hidden test output:\n" + compact_text(hidden_result.get("stdout", "") + hidden_result.get("stderr", ""), 2000),
            "Diff:\n" + (latest_diff(training) or "(empty)"),
        ]
    )


def metadata_for_task(benchmark_root, task_id):
    benchmark_root = Path(benchmark_root)
    for path in benchmark_root.rglob("metadata.json"):
        metadata = read_json(path)
        if metadata.get("id") == task_id:
            return metadata
    return {}


def report_results(report):
    if "results" in report:
        return report.get("results", [])
    results = []
    for run in report.get("runs", []):
        for result in run.get("results", []):
            item = dict(result)
            item["repeat_index"] = run.get("index")
            results.append(item)
    return results


def has_test_signal(training, public_result, hidden_result):
    public_text = str(public_result.get("stdout", "")) + str(public_result.get("stderr", ""))
    hidden_text = str(hidden_result.get("stdout", "")) + str(hidden_result.get("stderr", ""))
    if public_text.strip() or hidden_text.strip():
        return True
    for step in training.get("steps", []):
        if step.get("action") == "run_tests" or "pytest" in str(step.get("observation", "")).lower():
            return True
    return False


def quality_assessment(row_input, training, output, public_result, hidden_result, quality_filter=None):
    quality_filter = quality_filter or DEFAULT_QUALITY_FILTER
    steps = training.get("steps", [])
    failure_type = output.get("diagnosis", {}).get("failure_type", output.get("failure_type", "unknown"))
    decision = output.get("decision", {})
    confidence = float(output.get("diagnosis", {}).get("confidence", output.get("confidence", 0)))
    diff = latest_diff(training)
    signals = {
        "steps": len(steps),
        "input_chars": len(row_input),
        "has_test_signal": has_test_signal(training, public_result, hidden_result),
        "has_diff": bool(diff.strip()),
        "confidence": confidence,
        "failure_type": failure_type,
        "next_action": decision.get("next_action", ""),
    }
    reasons = []
    score = 1.0
    if failure_type in set(quality_filter.get("exclude_failure_types", [])):
        reasons.append("excluded_failure_type")
        score -= 1.0
    if len(steps) < int(quality_filter.get("min_steps", 1)):
        reasons.append("too_few_steps")
        score -= 0.35
    if len(row_input) > int(quality_filter.get("max_input_chars", 12000)):
        reasons.append("input_too_long")
        score -= 0.2
    if quality_filter.get("require_test_signal", True) and not signals["has_test_signal"]:
        reasons.append("missing_test_signal")
        score -= 0.35
    if confidence < float(quality_filter.get("min_confidence", 0.3)):
        reasons.append("low_confidence")
        score -= 0.3
    requires_diff = (
        quality_filter.get("require_diff_for_edit_decisions", True)
        and decision.get("next_action") in EDIT_DECISION_ACTIONS
    )
    if requires_diff and not signals["has_diff"]:
        reasons.append("missing_diff_for_edit_decision")
        score -= 0.35
    score = round(max(0.0, min(1.0, score)), 3)
    keep = score >= float(quality_filter.get("min_quality_score", 0.6))
    return {
        "keep": keep,
        "score": score,
        "reasons": reasons or ["ok"],
        "signals": signals,
    }


def build_row(result, training, metadata, quality_filter=None):
    public_result = result.get("public_result", {})
    hidden_result = result.get("hidden_result", {})
    analysis = result.get("failure_analysis", {})
    row_input = sample_input(
        training.get("task") or result.get("final_answer", ""),
        training,
        public_result,
        hidden_result,
    )
    output = output_label(analysis, metadata)
    quality = quality_assessment(row_input, training, output, public_result, hidden_result, quality_filter)
    row = {
        "id": f"{result.get('task_id')}_{result.get('session_id')}",
        "source": "benchmark",
        "task_id": result.get("task_id"),
        "instruction": INSTRUCTION,
        "label_schema": LABEL_SCHEMA_VERSION,
        "input": row_input,
        "output": output,
        "quality": quality,
    }
    if result.get("repeat_index") is not None:
        row["repeat_index"] = result.get("repeat_index")
    return row


def rows_from_report(report_path, include_success=False, quality_filter=None, apply_quality_filter=True):
    report = read_json(report_path)
    benchmark_root = report.get("benchmark_root", ROOT / "benchmarks")
    rows = []
    for result in report_results(report):
        analysis = result.get("failure_analysis", {})
        if analysis.get("failure_type") == "none" and not include_success:
            continue
        workdir = Path(result.get("workdir", "."))
        training_rel = result.get("training_trajectory")
        if not training_rel:
            continue
        training_path = workdir / training_rel
        if not training_path.is_file():
            continue
        training = read_json(training_path)
        metadata = metadata_for_task(benchmark_root, result.get("task_id"))
        row = build_row(result, training, metadata, quality_filter)
        if not apply_quality_filter or row["quality"]["keep"]:
            rows.append(row)
    return rows


def synthetic_case(failure_type, index):
    target, domain, bug = TARGETS[index % len(TARGETS)]
    wrong = WRONG_FILES[(index // len(TARGETS)) % len(WRONG_FILES)]
    test_name, assertion = TEST_FAILURES[(index + len(failure_type)) % len(TEST_FAILURES)]
    command = TEST_COMMANDS[(index + len(target)) % len(TEST_COMMANDS)]
    snippet = SOURCE_SNIPPETS[(index + len(wrong)) % len(SOURCE_SNIPPETS)]
    variant = index // max(1, len(TARGETS))
    task_templates = [
        f"Fix the {domain} bug in {target}: {bug}. Validate with pytest.",
        f"The public tests fail for {target}. Make the smallest correct fix and rerun tests.",
        f"Repair {target} without changing the public API. The issue is: {bug}.",
        f"Debug the failing benchmark task. Expected target file: {target}.",
    ]
    task = task_templates[index % len(task_templates)]
    base_test = f"FAILED {test_name}\n{assertion}\ncase_id={index}"
    hidden_test = f"FAILED hidden/{test_name}\n{assertion}\nhidden_case={index}"
    diff = (
        f"diff --git a/{wrong} b/{wrong}\n"
        f"--- a/{wrong}\n"
        f"+++ b/{wrong}\n"
        "@@ -1 +1 @@\n"
        f"-old_{index}\n"
        f"+new_{index}\n"
    )
    target_diff = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1 +1 @@\n"
        f"-buggy_{index}\n"
        f"+fixed_{index}\n"
    )
    suggestion = SUGGESTION_STYLES[failure_type][index % len(SUGGESTION_STYLES[failure_type])]
    cases = {
        "no_test_run": {
            "steps": [
                {"step": 1, "action": "read_file", "args": {"path": target}, "observation": snippet, "diff": "", "success": None},
                {"step": 2, "action": "final", "args": {}, "observation": f"Done after inspecting {target}.", "diff": "", "success": None},
            ],
            "analysis": {
                "failure_type": "no_test_run",
                "reason": f"The agent finalized after reading {target} but never executed {command}.",
                "suggestion": suggestion,
            },
        },
        "wrong_file": {
            "steps": [
                {"step": 1, "action": "read_file", "args": {"path": wrong}, "observation": f"contents from {wrong}", "diff": "", "success": None},
                {"step": 2, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\n" + base_test, "diff": "", "success": False},
            ],
            "analysis": {
                "failure_type": "wrong_file",
                "reason": f"The agent inspected {wrong} instead of the expected target {target}.",
                "suggestion": suggestion,
            },
        },
        "unrelated_edit": {
            "steps": [
                {"step": 1, "action": "read_file", "args": {"path": target}, "observation": snippet, "diff": "", "success": None},
                {"step": 2, "action": "patch_file", "args": {"path": wrong, "old_text": f"old_{index}", "new_text": f"new_{index}"}, "observation": f"patched {wrong}", "diff": diff, "success": None},
            ],
            "analysis": {
                "failure_type": "unrelated_edit",
                "reason": f"The agent edited {wrong}, which is unrelated to the expected target {target}.",
                "suggestion": suggestion,
            },
        },
        "repeated_tool_call": {
            "steps": [
                {"step": 1, "action": "read_file", "args": {"path": target}, "observation": snippet, "diff": "", "success": None},
                {"step": 2, "action": "read_file", "args": {"path": target}, "observation": snippet + f"\nrepeat={variant}", "diff": "", "success": None},
            ],
            "analysis": {
                "failure_type": "repeated_tool_call",
                "reason": f"The agent repeated read_file on {target} without gaining new evidence.",
                "suggestion": suggestion,
            },
        },
        "early_stop_after_test_failure": {
            "steps": [
                {"step": 1, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\n" + base_test, "diff": "", "success": False},
                {"step": 2, "action": "final", "args": {}, "observation": f"Stopped after reaching the step limit while {target} still failed.", "diff": "", "success": False},
            ],
            "analysis": {
                "failure_type": "early_stop_after_test_failure",
                "reason": f"Tests for {target} were failing and the agent stopped instead of making another targeted fix.",
                "suggestion": suggestion,
            },
        },
        "hidden_test_failed": {
            "steps": [
                {"step": 1, "action": "patch_file", "args": {"path": target, "old_text": f"buggy_{index}", "new_text": f"fixed_{index}"}, "observation": f"patched {target}", "diff": target_diff, "success": None},
                {"step": 2, "action": "run_tests", "args": {"command": command}, "observation": f"exit_code: 0\n{2 + (index % 4)} passed", "diff": target_diff, "success": True},
                {"step": 3, "action": "final", "args": {}, "observation": f"Public tests pass for {target}.", "diff": target_diff, "success": True},
            ],
            "analysis": {
                "failure_type": "hidden_test_failed",
                "reason": f"Public tests passed but hidden edge-case test failed: {hidden_test.splitlines()[0]}.",
                "suggestion": suggestion,
            },
        },
        "patch_too_large": {
            "steps": [
                {"step": 1, "action": "write_file", "args": {"path": target, "content": "\n".join(f"line_{index}_{i}" for i in range(90 + index % 40))}, "observation": f"rewrote {target}", "diff": "large diff for " + target, "success": None},
                {"step": 2, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\n" + base_test, "diff": "large diff for " + target, "success": False},
            ],
            "analysis": {
                "failure_type": "patch_too_large",
                "reason": f"The patch rewrote too much of {target} for a small {domain} bugfix.",
                "suggestion": suggestion,
            },
        },
        "test_failure": {
            "steps": [
                {"step": 1, "action": "read_file", "args": {"path": target}, "observation": snippet, "diff": "", "success": None},
                {"step": 2, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\n" + base_test, "diff": target_diff, "success": False},
            ],
            "analysis": {
                "failure_type": "test_failure",
                "reason": f"Tests for {target} failed without a more specific trajectory rule match.",
                "suggestion": suggestion,
            },
        },
        "syntax_error": {
            "steps": [
                {"step": 1, "action": "write_file", "args": {"path": target, "content": "```python\\n" + snippet}, "observation": f"wrote {target}", "diff": target_diff, "success": None},
                {"step": 2, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\\nSyntaxError: invalid syntax", "diff": target_diff, "success": False},
            ],
            "analysis": {
                "failure_type": "syntax_error",
                "reason": f"The generated edit made {target} syntactically invalid.",
                "suggestion": suggestion,
            },
        },
        "tool_protocol_error": {
            "steps": [
                {"step": 1, "action": "run_tests", "args": {"command": command}, "observation": "exit_code: 1\\n" + base_test, "diff": "", "success": False},
                {"step": 2, "action": "final", "args": {}, "observation": "[tool:write_file] " + target, "diff": "", "success": False},
            ],
            "analysis": {
                "failure_type": "tool_protocol_error",
                "reason": "The model emitted tool protocol text instead of a valid executable tool call.",
                "suggestion": suggestion,
            },
        },
    }
    case = cases[failure_type]
    training = {"task": task, "steps": case["steps"]}
    row_input = sample_input(task, training, {"stdout": base_test, "stderr": ""}, {"stdout": hidden_test, "stderr": ""})
    output = output_label(case["analysis"], {"expected_files": [target]})
    quality = quality_assessment(
        row_input,
        training,
        output,
        {"stdout": base_test, "stderr": ""},
        {"stdout": hidden_test, "stderr": ""},
    )
    return {
        "id": f"synthetic_{failure_type}_{index:04d}",
        "source": "synthetic_template",
        "task_id": f"synthetic_{failure_type}",
        "instruction": INSTRUCTION,
        "label_schema": LABEL_SCHEMA_VERSION,
        "input": row_input,
        "output": output,
        "quality": quality,
    }


def synthetic_rows(per_type):
    rows = []
    for failure_type in FAILURE_TYPES:
        for index in range(per_type):
            rows.append(synthetic_case(failure_type, index))
    return rows


def dedupe_rows(rows):
    seen = set()
    unique = []
    for row in rows:
        key = (
            row.get("instruction", ""),
            row.get("input", ""),
            json.dumps(row.get("output", {}), ensure_ascii=False, sort_keys=True),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def dataset_stats(rows):
    failure_counts = {}
    decision_counts = {}
    quality_reasons = {}
    sources = {}
    unique_inputs = set()
    unique_pairs = set()
    quality_scores = []
    for row in rows:
        output = row.get("output", {})
        failure_type = output.get("diagnosis", {}).get("failure_type", output.get("failure_type", "unknown"))
        failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
        next_action = output.get("decision", {}).get("next_action", output.get("next_action", "unknown"))
        decision_counts[next_action] = decision_counts.get(next_action, 0) + 1
        source = row.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        unique_inputs.add(row.get("input", ""))
        unique_pairs.add((row.get("input", ""), json.dumps(row.get("output", {}), ensure_ascii=False, sort_keys=True)))
        quality = row.get("quality", {})
        quality_scores.append(float(quality.get("score", 0)))
        for reason in quality.get("reasons", []):
            quality_reasons[reason] = quality_reasons.get(reason, 0) + 1
    return {
        "rows": len(rows),
        "unique_inputs": len(unique_inputs),
        "unique_input_output_pairs": len(unique_pairs),
        "sources": sources,
        "failure_types": failure_counts,
        "decision_actions": decision_counts,
        "avg_quality_score": round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else 0,
        "quality_reasons": quality_reasons,
        "label_schema": LABEL_SCHEMA_VERSION,
    }


def balance_rows(rows, max_per_failure_type=None):
    if not max_per_failure_type:
        return rows
    counts = {}
    balanced = []
    for row in rows:
        failure_type = row.get("output", {}).get("diagnosis", {}).get(
            "failure_type",
            row.get("output", {}).get("failure_type", "unknown"),
        )
        if counts.get(failure_type, 0) >= max_per_failure_type:
            continue
        counts[failure_type] = counts.get(failure_type, 0) + 1
        balanced.append(row)
    return balanced


def report_paths(path):
    path = Path(path)
    if path.is_file():
        return [path]
    return sorted(path.glob("run_*.json"))


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Build Critic SFT JSONL data from benchmark results and templates.")
    parser.add_argument("--reports", default="benchmark_results", help="Benchmark result file or directory.")
    parser.add_argument("--out", default="datasets/critic_sft.jsonl", help="Output JSONL path.")
    parser.add_argument("--include-success", action="store_true", help="Include successful benchmark rows.")
    parser.add_argument("--synthetic-per-type", type=int, default=0, help="Number of synthetic rows per failure type.")
    parser.add_argument("--no-quality-filter", action="store_true", help="Keep rows even when quality checks fail.")
    parser.add_argument("--min-quality-score", type=float, default=0.75, help="Minimum quality score for benchmark rows.")
    parser.add_argument("--min-confidence", type=float, default=0.3, help="Minimum label confidence for benchmark rows.")
    parser.add_argument("--max-input-chars", type=int, default=12000, help="Drop benchmark rows whose input prompt is too long.")
    parser.add_argument("--max-per-failure-type", type=int, default=0, help="Optional cap per failure type after filtering and dedupe.")
    parser.add_argument("--no-dedupe", action="store_true", help="Disable exact input/output deduplication.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    reports = Path(args.reports)
    if not reports.is_absolute():
        reports = ROOT / reports
    rows = []
    quality_filter = {
        **DEFAULT_QUALITY_FILTER,
        "min_quality_score": args.min_quality_score,
        "min_confidence": args.min_confidence,
        "max_input_chars": args.max_input_chars,
    }
    if reports.exists():
        for report in report_paths(reports):
            rows.extend(
                rows_from_report(
                    report,
                    include_success=args.include_success,
                    quality_filter=quality_filter,
                    apply_quality_filter=not args.no_quality_filter,
                )
            )
    if args.synthetic_per_type:
        rows.extend(synthetic_rows(args.synthetic_per_type))
    before = len(rows)
    if not args.no_dedupe:
        rows = dedupe_rows(rows)
    before_balance = len(rows)
    rows = balance_rows(rows, args.max_per_failure_type or None)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    write_jsonl(out, rows)
    stats = dataset_stats(rows)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    if before != len(rows):
        print(f"removed {before - len(rows)} rows by dedupe/balancing")
    if before_balance != len(rows):
        print(f"balanced away {before_balance - len(rows)} rows")
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
