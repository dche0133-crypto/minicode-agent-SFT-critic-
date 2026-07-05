from scripts.scoring import SCORE_WEIGHTS, score_result


def test_score_result_rewards_successful_test_driven_run():
    result = {
        "pass_public": True,
        "pass_hidden": True,
        "tool_calls": 4,
        "tools": ["read_file", "patch_file", "run_tests", "git_diff"],
        "critic_retries_used": 0,
        "failure_analysis": {"failure_type": "none"},
    }

    score = score_result(result, {"max_steps": 16})

    assert score["total"] == 98.0
    assert score["components"]["public_correctness"] == SCORE_WEIGHTS["public_correctness"]
    assert score["components"]["hidden_correctness"] == SCORE_WEIGHTS["hidden_correctness"]
    assert score["components"]["test_driven"] == SCORE_WEIGHTS["test_driven"]
    assert score["context"]["recovery_status"] == "first_pass"


def test_score_result_penalizes_untested_repeated_failure():
    result = {
        "pass_public": False,
        "pass_hidden": False,
        "tool_calls": 15,
        "tools": ["read_file", "write_file"],
        "critic_retries_used": 1,
        "failure_analysis": {"failure_type": "repeated_tool_call"},
    }

    score = score_result(result, {"max_steps": 16})

    assert score["total"] == 0.0
    assert score["components"]["test_driven"] == 0
    assert score["components"]["edit_discipline"] == 0
    assert score["penalties"]["failure:repeated_tool_call"] == -10
    assert score["context"]["recovery_status"] == "failed"
