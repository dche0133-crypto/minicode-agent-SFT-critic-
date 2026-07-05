"""Central policy for Critic diagnosis labels and retry decisions."""


CRITIC_POLICY = {
    "none": {
        "trigger": "Hidden tests pass.",
        "next_action": "final",
        "requires_target_file": False,
        "allowed_tools": [],
        "risk_level": "low",
        "abstain": False,
        "confidence": 1.0,
    },
    "no_test_run": {
        "trigger": "No dedicated run_tests action appears in the trajectory.",
        "next_action": "execute_test_command",
        "requires_target_file": False,
        "allowed_tools": ["run_tests"],
        "risk_level": "low",
        "abstain": False,
        "confidence": 0.98,
    },
    "wrong_file": {
        "trigger": "No expected target file was inspected or modified.",
        "next_action": "read_target_file",
        "requires_target_file": True,
        "allowed_tools": ["read_file", "search"],
        "risk_level": "low",
        "abstain": False,
        "confidence": 0.92,
    },
    "unrelated_edit": {
        "trigger": "The patch modified files outside the expected target set.",
        "next_action": "rollback_unrelated_edit",
        "requires_target_file": True,
        "allowed_tools": ["git_diff", "rollback", "read_file"],
        "risk_level": "high",
        "abstain": False,
        "confidence": 0.92,
    },
    "repeated_tool_call": {
        "trigger": "The same tool call was repeated with identical arguments.",
        "next_action": "inspect_new_evidence",
        "requires_target_file": True,
        "allowed_tools": ["git_diff", "read_file", "search", "run_tests"],
        "risk_level": "medium",
        "abstain": False,
        "confidence": 0.95,
    },
    "early_stop_after_test_failure": {
        "trigger": "Tests failed and the agent stopped at the step limit.",
        "next_action": "inspect_test_failure",
        "requires_target_file": True,
        "allowed_tools": ["run_tests", "read_file", "git_diff", "search"],
        "risk_level": "medium",
        "abstain": False,
        "confidence": 0.9,
    },
    "hidden_test_failed": {
        "trigger": "Public tests pass but hidden tests fail.",
        "next_action": "inspect_edge_cases",
        "requires_target_file": True,
        "allowed_tools": ["read_file", "git_diff", "search"],
        "risk_level": "medium",
        "abstain": False,
        "confidence": 0.9,
    },
    "patch_too_large": {
        "trigger": "The patch exceeds the task patch-size budget.",
        "next_action": "minimize_patch",
        "requires_target_file": True,
        "allowed_tools": ["git_diff", "rollback", "patch_file", "apply_patch"],
        "risk_level": "high",
        "abstain": False,
        "confidence": 0.95,
    },
    "test_failure": {
        "trigger": "Tests fail without matching a more specific rule.",
        "next_action": "inspect_test_failure",
        "requires_target_file": True,
        "allowed_tools": ["run_tests", "read_file", "git_diff", "search"],
        "risk_level": "medium",
        "abstain": True,
        "confidence": 0.7,
    },
    "syntax_error": {
        "trigger": "Test output reports a Python syntax, indentation, or tab error.",
        "next_action": "repair_syntax",
        "requires_target_file": True,
        "allowed_tools": ["read_file", "git_diff", "patch_file", "apply_patch", "run_tests"],
        "risk_level": "medium",
        "abstain": False,
        "confidence": 0.99,
    },
    "tool_protocol_error": {
        "trigger": "The model emitted malformed or non-executable tool-call protocol text.",
        "next_action": "retry_structured_tool_call",
        "requires_target_file": False,
        "allowed_tools": ["read_file", "search", "run_tests", "git_diff"],
        "risk_level": "low",
        "abstain": False,
        "confidence": 0.96,
    },
    "unknown": {
        "trigger": "Available evidence is insufficient for a confident diagnosis.",
        "next_action": "gather_evidence",
        "requires_target_file": False,
        "allowed_tools": ["run_tests", "read_file", "git_diff", "search"],
        "risk_level": "medium",
        "abstain": True,
        "confidence": 0.3,
    },
}

FAILURE_TYPES = [name for name in CRITIC_POLICY if name not in {"none", "unknown"}]


def target_file_from_metadata(metadata, requires_target_file):
    if not requires_target_file:
        return ""
    expected = (metadata or {}).get("expected_files", [])
    return str(expected[0]).replace("\\", "/") if expected else ""


def policy_for(failure_type):
    return CRITIC_POLICY.get(failure_type, CRITIC_POLICY["unknown"])


def build_assessment(failure_type, reason, evidence="", suggestion="", metadata=None, confidence=None):
    """Build a stable diagnosis/decision schema and compatibility aliases."""
    policy = policy_for(failure_type)
    confidence = policy["confidence"] if confidence is None else max(0.0, min(1.0, float(confidence)))
    target_file = target_file_from_metadata(metadata, policy["requires_target_file"])
    diagnosis = {
        "failure_type": failure_type if failure_type in CRITIC_POLICY else "unknown",
        "reason": reason,
        "evidence": evidence,
        "confidence": confidence,
        "trigger": policy["trigger"],
    }
    decision = {
        "next_action": policy["next_action"],
        "target_file": target_file,
        "requires_target_file": policy["requires_target_file"],
        "allowed_tools": policy["allowed_tools"],
        "risk_level": policy["risk_level"],
        "abstain": policy["abstain"],
        "confidence": confidence,
        "suggestion": suggestion,
    }
    return {
        "diagnosis": diagnosis,
        "decision": decision,
        # Flat aliases preserve report readers and the first-generation SFT schema.
        "failure_type": diagnosis["failure_type"],
        "reason": diagnosis["reason"],
        "evidence": diagnosis["evidence"],
        "confidence": diagnosis["confidence"],
        "next_action": decision["next_action"],
        "target_file": decision["target_file"],
        "requires_target_file": decision["requires_target_file"],
        "allowed_tools": decision["allowed_tools"],
        "risk_level": decision["risk_level"],
        "abstain": decision["abstain"],
        "suggestion": decision["suggestion"],
    }
