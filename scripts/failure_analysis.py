import json
import re
from pathlib import Path

from scripts.critic_policy import build_assessment


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


def combined_test_output(result):
    chunks = []
    for key in ("public_result", "hidden_result"):
        test_result = result.get(key, {}) or {}
        chunks.extend([str(test_result.get("stdout", "")), str(test_result.get("stderr", ""))])
    return "\n".join(chunk for chunk in chunks if chunk)


def has_syntax_error(result):
    return bool(re.search(r"\b(?:SyntaxError|IndentationError|TabError)\b", combined_test_output(result)))


def has_tool_protocol_error(result):
    final_answer = str(result.get("final_answer", "")).lower()
    protocol_markers = ("[tool:", "malformed tool", "valid <tool> call", "tool protocol error")
    return any(marker in final_answer for marker in protocol_markers)


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
        return build_assessment("none", "Hidden tests passed.", suggestion="No failure to analyze.", metadata=metadata)

    if has_tool_protocol_error(result):
        return build_assessment(
            "tool_protocol_error",
            "The model output contained malformed tool-call protocol text.",
            evidence=final_answer[:500],
            suggestion="Retry with one valid structured tool call instead of emitting protocol text as a final answer.",
            metadata=metadata,
        )

    if has_syntax_error(result):
        return build_assessment(
            "syntax_error",
            "Test output indicates that the edited code is not syntactically valid.",
            evidence=combined_test_output(result)[:500],
            suggestion="Read the target file, remove invalid syntax such as Markdown fences, then rerun tests.",
            metadata=metadata,
        )

    if not any(step.get("action") == "run_tests" for step in steps):
        return build_assessment(
            "no_test_run",
            "The agent did not run the dedicated run_tests tool.",
            evidence=",".join(step.get("action", "") for step in steps),
            suggestion="Run tests before finalizing and use the test output to guide fixes.",
            metadata=metadata,
        )

    if has_repeated_tool_call(steps):
        return build_assessment(
            "repeated_tool_call",
            "The agent repeated the same tool call with the same arguments.",
            suggestion="Choose a different observation or return a final answer instead of repeating a stale action.",
            metadata=metadata,
        )

    referenced = set(referenced_files(steps))
    if expected_files and referenced and referenced.isdisjoint(expected_files):
        return build_assessment(
            "wrong_file",
            "The agent never touched or inspected the expected target file.",
            evidence=f"referenced={sorted(referenced)} expected={sorted(expected_files)}",
            suggestion="Read and patch the file named in task metadata before attempting broad changes.",
            metadata=metadata,
        )

    changed = set()
    for step in steps:
        if step.get("action") in {"patch_file", "write_file", "apply_patch"}:
            changed.update(referenced_files([step]))
    if expected_files and changed and changed.isdisjoint(expected_files):
        return build_assessment(
            "unrelated_edit",
            "The agent edited files outside the expected target set.",
            evidence=f"changed={sorted(changed)} expected={sorted(expected_files)}",
            suggestion="Keep edits scoped to expected files unless the task metadata allows broader changes.",
            metadata=metadata,
        )

    latest_test = latest_run_tests_step(steps)
    if (
        latest_test
        and latest_test.get("success") is False
        and "Stopped after reaching the step limit" in final_answer
        and not result.get("critic_retries_used")
    ):
        return build_assessment(
            "early_stop_after_test_failure",
            "Tests were failing and the agent stopped due to the step limit.",
            evidence=latest_test.get("observation", "")[:500],
            suggestion="Increase step budget or make the test-fix loop choose a new edit after each failed run.",
            metadata=metadata,
        )

    public_pass = result.get("pass_public")
    hidden_pass = result.get("pass_hidden")
    if public_pass and not hidden_pass:
        return build_assessment(
            "hidden_test_failed",
            "Public tests passed but hidden tests failed.",
            evidence=(result.get("hidden_result", {}).get("stdout", "") + result.get("hidden_result", {}).get("stderr", ""))[:500],
            suggestion="Add edge-case reasoning and avoid overfitting to public tests.",
            metadata=metadata,
        )

    max_patch_lines = int(metadata.get("max_patch_lines", 80))
    total_patch_lines = patch_size(steps)
    if total_patch_lines > max_patch_lines:
        return build_assessment(
            "patch_too_large",
            f"Patch size {total_patch_lines} lines exceeded limit {max_patch_lines}.",
            suggestion="Prefer minimal targeted edits for benchmark tasks.",
            metadata=metadata,
        )

    return build_assessment(
        "test_failure",
        "Tests failed without matching a more specific rule.",
        evidence=(result.get("hidden_result", {}).get("stdout", "") + result.get("hidden_result", {}).get("stderr", ""))[:500],
        suggestion="Inspect hidden/public failure output and produce a more targeted fix.",
        metadata=metadata,
    )


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
