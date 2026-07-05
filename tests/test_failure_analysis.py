from scripts.failure_analysis import classify_failure


def base_result(**overrides):
    result = {
        "pass_public": False,
        "pass_hidden": False,
        "final_answer": "",
        "hidden_result": {"stdout": "hidden failed", "stderr": ""},
    }
    result.update(overrides)
    return result


def trajectory(*steps):
    return {"steps": list(steps)}


def tool(action, args=None, observation="", success=None, diff=""):
    return {
        "action": action,
        "args": args or {},
        "observation": observation,
        "success": success,
        "diff": diff,
    }


def test_classify_success():
    analysis = classify_failure(base_result(pass_hidden=True), trajectory(), {})

    assert analysis["failure_type"] == "none"


def test_classify_no_test_run():
    analysis = classify_failure(base_result(), trajectory(tool("read_file", {"path": "a.py"})), {})

    assert analysis["failure_type"] == "no_test_run"


def test_classify_repeated_tool_call():
    repeated = trajectory(
        tool("run_tests", {"command": "pytest"}, "exit_code: 1", False),
        tool("read_file", {"path": "a.py"}),
        tool("read_file", {"path": "a.py"}),
    )

    analysis = classify_failure(base_result(), repeated, {"expected_files": ["a.py"]})

    assert analysis["failure_type"] == "repeated_tool_call"


def test_classify_unrelated_edit():
    training = trajectory(
        tool("run_tests", {"command": "pytest"}, "exit_code: 1", False),
        tool("read_file", {"path": "target.py"}),
        tool("patch_file", {"path": "other.py", "old_text": "a", "new_text": "b"}),
    )

    analysis = classify_failure(base_result(), training, {"expected_files": ["target.py"]})

    assert analysis["failure_type"] == "unrelated_edit"


def test_classify_hidden_test_failed():
    training = trajectory(
        tool("run_tests", {"command": "pytest"}, "exit_code: 0", True),
        tool("patch_file", {"path": "target.py", "old_text": "a", "new_text": "b"}),
    )

    analysis = classify_failure(
        base_result(pass_public=True, pass_hidden=False),
        training,
        {"expected_files": ["target.py"]},
    )

    assert analysis["failure_type"] == "hidden_test_failed"


def test_classify_patch_too_large():
    training = trajectory(
        tool("run_tests", {"command": "pytest"}, "exit_code: 1", False),
        tool("write_file", {"path": "target.py", "content": "\n".join(str(i) for i in range(10))}),
    )

    analysis = classify_failure(base_result(), training, {"expected_files": ["target.py"], "max_patch_lines": 3})

    assert analysis["failure_type"] == "patch_too_large"


def test_classify_syntax_error_before_repeated_tool_call():
    training = trajectory(
        tool("run_tests", {"command": "pytest"}, "exit_code: 1", False),
        tool("write_file", {"path": "target.py", "content": "```python"}),
        tool("write_file", {"path": "target.py", "content": "```python"}),
    )
    result = base_result(public_result={"stderr": "SyntaxError: invalid syntax"})

    analysis = classify_failure(result, training, {"expected_files": ["target.py"]})

    assert analysis["failure_type"] == "syntax_error"
    assert analysis["decision"]["next_action"] == "repair_syntax"


def test_classify_tool_protocol_error():
    training = trajectory(tool("run_tests", {"command": "pytest"}, "exit_code: 1", False))
    result = base_result(final_answer="[tool:write_file] config.py")

    analysis = classify_failure(result, training, {})

    assert analysis["failure_type"] == "tool_protocol_error"
    assert analysis["decision"]["next_action"] == "retry_structured_tool_call"


def test_controlled_retry_failure_is_not_mislabeled_as_early_stop():
    training = trajectory(tool("run_tests", {"command": "pytest"}, "exit_code: 1", False))
    result = base_result(
        final_answer="Stopped after reaching the step limit without a final answer.",
        critic_retries_used=1,
    )

    analysis = classify_failure(result, training, {"expected_files": ["target.py"]})

    assert analysis["failure_type"] == "test_failure"
