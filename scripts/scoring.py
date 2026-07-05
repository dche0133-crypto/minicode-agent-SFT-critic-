SCORE_WEIGHTS = {
    "public_correctness": 25,
    "hidden_correctness": 30,
    "test_driven": 15,
    "tool_efficiency": 10,
    "edit_discipline": 10,
    "completion_or_recovery": 10,
}

DISCIPLINE_FAILURES = {"wrong_file", "unrelated_edit", "patch_too_large", "repeated_tool_call"}
FAILURE_PENALTIES = {
    "syntax_error": -15,
    "tool_protocol_error": -18,
    "no_test_run": -8,
    "wrong_file": -12,
    "unrelated_edit": -12,
    "repeated_tool_call": -10,
    "early_stop_after_test_failure": -6,
    "patch_too_large": -8,
    "hidden_test_failed": -3,
}


def score_result(result, metadata=None):
    """Return an explainable 100-point score for one benchmark task result."""
    metadata = metadata or {}
    weights = SCORE_WEIGHTS
    tool_calls = int(result.get("tool_calls", 0))
    tool_budget = max(1, int(metadata.get("score_tool_budget", metadata.get("max_steps", 16))))
    tools = set(result.get("tools", []))
    failure_type = result.get("failure_analysis", {}).get("failure_type", "unknown")
    pass_public = bool(result.get("pass_public"))
    pass_hidden = bool(result.get("pass_hidden"))
    retries = int(result.get("critic_retries_used", 0))

    public_correctness = weights["public_correctness"] if pass_public else 0
    hidden_correctness = weights["hidden_correctness"] if pass_hidden else 0
    test_driven = weights["test_driven"] if "run_tests" in tools else 0
    efficiency_ratio = max(0.0, min(1.0, (tool_budget - tool_calls) / max(1, tool_budget - 1)))
    tool_efficiency = round(weights["tool_efficiency"] * efficiency_ratio, 2)
    edit_discipline = 0 if failure_type in DISCIPLINE_FAILURES else weights["edit_discipline"]
    completion_or_recovery = weights["completion_or_recovery"] if pass_hidden else 0

    components = {
        "public_correctness": public_correctness,
        "hidden_correctness": hidden_correctness,
        "test_driven": test_driven,
        "tool_efficiency": tool_efficiency,
        "edit_discipline": edit_discipline,
        "completion_or_recovery": completion_or_recovery,
    }
    penalties = {}
    if failure_type in FAILURE_PENALTIES:
        penalties[f"failure:{failure_type}"] = FAILURE_PENALTIES[failure_type]
    raw_total = round(sum(components.values()) + sum(penalties.values()), 2)
    return {
        "total": max(0.0, min(100.0, raw_total)),
        "raw_total": raw_total,
        "weights": weights,
        "components": components,
        "penalties": penalties,
        "context": {
            "tool_calls": tool_calls,
            "tool_budget": tool_budget,
            "critic_retries_used": retries,
            "failure_type": failure_type,
            "recovery_status": "recovered" if retries and pass_hidden else "first_pass" if pass_hidden else "failed",
        },
    }
