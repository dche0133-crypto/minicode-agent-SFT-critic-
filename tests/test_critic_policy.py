from scripts.critic_policy import CRITIC_POLICY, build_assessment


def test_policy_defines_all_required_decision_fields():
    for policy in CRITIC_POLICY.values():
        assert {"trigger", "next_action", "requires_target_file", "allowed_tools", "risk_level", "abstain", "confidence"} <= set(policy)


def test_assessment_separates_diagnosis_from_decision():
    assessment = build_assessment(
        "no_test_run",
        "No test tool call was recorded.",
        metadata={"expected_files": ["config_records.py"]},
    )

    assert assessment["diagnosis"]["failure_type"] == "no_test_run"
    assert assessment["decision"]["next_action"] == "execute_test_command"
    assert assessment["decision"]["target_file"] == ""
    assert assessment["decision"]["allowed_tools"] == ["run_tests"]
    assert assessment["decision"]["abstain"] is False


def test_low_confidence_test_failure_abstains_before_editing():
    assessment = build_assessment(
        "test_failure",
        "The test failed without a specific rule match.",
        metadata={"expected_files": ["config_records.py"]},
    )

    assert assessment["diagnosis"]["confidence"] == 0.7
    assert assessment["decision"]["abstain"] is True
    assert assessment["decision"]["next_action"] == "inspect_test_failure"
