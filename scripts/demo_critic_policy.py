"""Print deterministic examples of the centralized Critic policy."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.failure_analysis import classify_failure


def step(action, args=None, observation="", success=None, diff=""):
    return {
        "action": action,
        "args": args or {},
        "observation": observation,
        "success": success,
        "diff": diff,
    }


def base_result(**overrides):
    result = {
        "pass_public": False,
        "pass_hidden": False,
        "final_answer": "Stopped after a failed attempt.",
        "hidden_result": {"stdout": "FAILED tests/test_config.py", "stderr": ""},
    }
    result.update(overrides)
    return result


def main():
    metadata = {"expected_files": ["config_records.py"], "max_patch_lines": 60}
    scenarios = {
        "no_test_run": (
            base_result(),
            {"steps": [step("read_file", {"path": "config_records.py"})]},
        ),
        "repeated_tool_call": (
            base_result(),
            {
                "steps": [
                    step("run_tests", {"command": "python -m pytest -q"}, "exit_code: 1", False),
                    step("write_file", {"path": "config_records.py", "content": "same"}),
                    step("write_file", {"path": "config_records.py", "content": "same"}),
                ]
            },
        ),
        "wrong_file": (
            base_result(),
            {
                "steps": [
                    step("run_tests", {"command": "python -m pytest -q"}, "exit_code: 1", False),
                    step("read_file", {"path": "README.md"}),
                ]
            },
        ),
        "generic_test_failure": (
            base_result(),
            {
                "steps": [
                    step("run_tests", {"command": "python -m pytest -q"}, "exit_code: 1\nFAILED whitespace", False),
                    step("read_file", {"path": "config_records.py"}),
                ]
            },
        ),
        "syntax_error": (
            base_result(public_result={"stdout": "", "stderr": "SyntaxError: invalid syntax"}),
            {
                "steps": [
                    step("write_file", {"path": "config_records.py", "content": "```python"}),
                    step("run_tests", {"command": "python -m pytest -q"}, "exit_code: 1\nSyntaxError: invalid syntax", False),
                ]
            },
        ),
        "tool_protocol_error": (
            base_result(final_answer="[tool:write_file] config_records.py"),
            {"steps": [step("run_tests", {"command": "python -m pytest -q"}, "exit_code: 1", False)]},
        ),
    }

    results = {}
    for name, (result, trajectory) in scenarios.items():
        results[name] = classify_failure(result, trajectory, metadata)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
