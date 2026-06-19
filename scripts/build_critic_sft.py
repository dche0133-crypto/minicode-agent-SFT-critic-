import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTRUCTION = (
    "You are a coding-agent critic. Given a task, agent trajectory, test output, "
    "and diff, diagnose the failure and suggest the next action. Return only JSON."
)


FAILURE_TYPES = [
    "no_test_run",
    "wrong_file",
    "unrelated_edit",
    "repeated_tool_call",
    "early_stop_after_test_failure",
    "hidden_test_failed",
    "patch_too_large",
]

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
    metadata = metadata or {}
    failure_type = analysis.get("failure_type", "test_failure")
    expected = metadata.get("expected_files", [])
    target = expected[0] if expected else ""
    mapping = {
        "none": ("final", target),
        "no_test_run": ("run_tests", ""),
        "wrong_file": ("read_file", target),
        "unrelated_edit": ("rollback", target),
        "repeated_tool_call": ("choose_different_tool", target),
        "early_stop_after_test_failure": ("edit_file", target),
        "hidden_test_failed": ("edit_file", target),
        "patch_too_large": ("minimize_patch", target),
        "test_failure": ("inspect_failure", target),
    }
    next_action, mapped_target = mapping.get(failure_type, ("inspect_failure", target))
    return {
        "failure_type": failure_type,
        "reason": analysis.get("reason", ""),
        "next_action": next_action,
        "target_file": mapped_target,
        "suggestion": analysis.get("suggestion", ""),
    }


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


def rows_from_report(report_path, include_success=False):
    report = read_json(report_path)
    benchmark_root = report.get("benchmark_root", ROOT / "benchmarks")
    rows = []
    for result in report.get("results", []):
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
        rows.append(
            {
                "id": f"{result.get('task_id')}_{result.get('session_id')}",
                "source": "benchmark",
                "task_id": result.get("task_id"),
                "instruction": INSTRUCTION,
                "input": sample_input(
                    training.get("task") or result.get("final_answer", ""),
                    training,
                    result.get("public_result", {}),
                    result.get("hidden_result", {}),
                ),
                "output": output_label(analysis, metadata),
            }
        )
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
    }
    case = cases[failure_type]
    training = {"task": task, "steps": case["steps"]}
    return {
        "id": f"synthetic_{failure_type}_{index:04d}",
        "source": "synthetic_template",
        "task_id": f"synthetic_{failure_type}",
        "instruction": INSTRUCTION,
        "input": sample_input(task, training, {"stdout": base_test, "stderr": ""}, {"stdout": hidden_test, "stderr": ""}),
        "output": output_label(case["analysis"], {"expected_files": [target]}),
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
    sources = {}
    unique_inputs = set()
    unique_pairs = set()
    for row in rows:
        failure_type = row.get("output", {}).get("failure_type", "unknown")
        failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
        source = row.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        unique_inputs.add(row.get("input", ""))
        unique_pairs.add((row.get("input", ""), json.dumps(row.get("output", {}), ensure_ascii=False, sort_keys=True)))
    return {
        "rows": len(rows),
        "unique_inputs": len(unique_inputs),
        "unique_input_output_pairs": len(unique_pairs),
        "sources": sources,
        "failure_types": failure_counts,
    }


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
    parser.add_argument("--no-dedupe", action="store_true", help="Disable exact input/output deduplication.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    reports = Path(args.reports)
    if not reports.is_absolute():
        reports = ROOT / reports
    rows = []
    if reports.exists():
        for report in report_paths(reports):
            rows.extend(rows_from_report(report, include_success=args.include_success))
    if args.synthetic_per_type:
        rows.extend(synthetic_rows(args.synthetic_per_type))
    before = len(rows)
    if not args.no_dedupe:
        rows = dedupe_rows(rows)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    write_jsonl(out, rows)
    stats = dataset_stats(rows)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    if before != len(rows):
        print(f"deduped {before - len(rows)} duplicate rows")
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
