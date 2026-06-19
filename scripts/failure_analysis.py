import json
import re
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def changed_files_from_diff(diff_text):
    files = set()
    for line in str(diff_text).splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for raw in parts[2:4]:
                cleaned = raw[2:] if raw.startswith(("a/", "b/")) else raw
                if cleaned != "/dev/null":
                    files.add(cleaned.replace("\\", "/"))
    return sorted(files)


def tool_steps(training_trajectory):
    return [step for step in training_trajectory.get("steps", []) if step.get("action") not in {"final", "user", None}]


def has_repeated_tool_call(steps):
    previous = None
    for step in steps:
        current = (step.get("action"), json.dumps(step.get("args", {}), sort_keys=True))
        if current == previous:
            return True
        previous = current
    return False


def latest_run_tests_step(steps):
    for step in reversed(steps):
        if step.get("action") == "run_tests":
            return step
    return None


def patch_size(steps):
    total = 0
    for step in steps:
        if step.get("action") == "apply_patch":
            total += len(str(step.get("args", {}).get("patch", "")).splitlines())
        if step.get("action") == "patch_file":
            old_text = str(step.get("args", {}).get("old_text", ""))
            new_text = str(step.get("args", {}).get("new_text", ""))
            total += max(len(old_text.splitlines()), len(new_text.splitlines()))
        if step.get("action") == "write_file":
            total += len(str(step.get("args", {}).get("content", "")).splitlines())
    return total


def referenced_files(steps):
    files = set()
    for step in steps:
        args = step.get("args", {})
        path = args.get("path")
        if path:
            files.add(str(path).replace("\\", "/"))
        diff = step.get("diff") or ""
        files.update(changed_files_from_diff(diff))
    return sorted(files)


def classify_failure(result, training_trajectory, metadata=None):
    metadata = metadata or {}
    steps = tool_steps(training_trajectory)
    expected_files = {str(path).replace("\\", "/") for path in metadata.get("expected_files", [])}
    final_answer = str(result.get("final_answer", ""))

    if result.get("pass_hidden"):
        return {
            "failure_type": "none",
            "reason": "Hidden tests passed.",
            "evidence": "",
            "suggestion": "No failure to analyze.",
        }

    if not any(step.get("action") == "run_tests" for step in steps):
        return {
            "failure_type": "no_test_run",
            "reason": "The agent did not run the dedicated run_tests tool.",
            "evidence": ",".join(step.get("action", "") for step in steps),
            "suggestion": "Run tests before finalizing and use the test output to guide fixes.",
        }

    if has_repeated_tool_call(steps):
        return {
            "failure_type": "repeated_tool_call",
            "reason": "The agent repeated the same tool call with the same arguments.",
            "evidence": "",
            "suggestion": "Choose a different observation or return a final answer instead of repeating a stale action.",
        }

    referenced = set(referenced_files(steps))
    if expected_files and referenced and referenced.isdisjoint(expected_files):
        return {
            "failure_type": "wrong_file",
            "reason": "The agent never touched or inspected the expected target file.",
            "evidence": f"referenced={sorted(referenced)} expected={sorted(expected_files)}",
            "suggestion": "Read and patch the file named in task metadata before attempting broad changes.",
        }

    changed = set()
    for step in steps:
        if step.get("action") in {"patch_file", "write_file", "apply_patch"}:
            changed.update(referenced_files([step]))
    if expected_files and changed and changed.isdisjoint(expected_files):
        return {
            "failure_type": "unrelated_edit",
            "reason": "The agent edited files outside the expected target set.",
            "evidence": f"changed={sorted(changed)} expected={sorted(expected_files)}",
            "suggestion": "Keep edits scoped to expected files unless the task metadata allows broader changes.",
        }

    latest_test = latest_run_tests_step(steps)
    if latest_test and latest_test.get("success") is False and "Stopped after reaching the step limit" in final_answer:
        return {
            "failure_type": "early_stop_after_test_failure",
            "reason": "Tests were failing and the agent stopped due to the step limit.",
            "evidence": latest_test.get("observation", "")[:500],
            "suggestion": "Increase step budget or make the test-fix loop choose a new edit after each failed run.",
        }

    public_pass = result.get("pass_public")
    hidden_pass = result.get("pass_hidden")
    if public_pass and not hidden_pass:
        return {
            "failure_type": "hidden_test_failed",
            "reason": "Public tests passed but hidden tests failed.",
            "evidence": (result.get("hidden_result", {}).get("stdout", "") + result.get("hidden_result", {}).get("stderr", ""))[:500],
            "suggestion": "Add edge-case reasoning and avoid overfitting to public tests.",
        }

    max_patch_lines = int(metadata.get("max_patch_lines", 80))
    total_patch_lines = patch_size(steps)
    if total_patch_lines > max_patch_lines:
        return {
            "failure_type": "patch_too_large",
            "reason": f"Patch size {total_patch_lines} lines exceeded limit {max_patch_lines}.",
            "evidence": "",
            "suggestion": "Prefer minimal targeted edits for benchmark tasks.",
        }

    return {
        "failure_type": "test_failure",
        "reason": "Tests failed without matching a more specific rule.",
        "evidence": (result.get("hidden_result", {}).get("stdout", "") + result.get("hidden_result", {}).get("stderr", ""))[:500],
        "suggestion": "Inspect hidden/public failure output and produce a more targeted fix.",
    }


def attach_failure_analysis(report, benchmark_root=None):
    benchmark_root = Path(benchmark_root or report.get("benchmark_root", "."))
    for result in report.get("results", []):
        metadata = {}
        for metadata_path in benchmark_root.rglob("metadata.json"):
            candidate = read_json(metadata_path)
            if candidate.get("id") == result.get("task_id"):
                metadata = candidate
                break
        trajectory_path = Path(result.get("workdir", ".")) / result.get("training_trajectory", "")
        training = read_json(trajectory_path) if trajectory_path.exists() else {"steps": []}
        result["failure_analysis"] = classify_failure(result, training, metadata)

    counts = {}
    for result in report.get("results", []):
        failure_type = result.get("failure_analysis", {}).get("failure_type", "unknown")
        counts[failure_type] = counts.get(failure_type, 0) + 1
    report.setdefault("summary", {})["failure_types"] = counts
    return report
