import json

from scripts import run_benchmark
from scripts.critic_policy import build_assessment


def test_run_benchmark_fake_agent_success(tmp_path):
    out_dir = tmp_path / "results"
    workdir = tmp_path / "work"

    exit_code = run_benchmark.main(
        [
            "--fake-agent-success",
            "--task",
            "bubble_sort_order",
            "--out",
            str(out_dir),
            "--workdir",
            str(workdir),
            "--repeat",
            "1",
        ]
    )

    assert exit_code == 0
    reports = list(out_dir.glob("run_*.json"))
    assert len(reports) == 1

    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["schema"] == "mini-coding-agent.benchmark.v1"
    assert reports[0].with_suffix(".per_task.md").exists()
    assert report["summary"]["total"] == 1
    assert report["summary"]["public_pass"] == 1
    assert report["summary"]["hidden_pass"] == 1
    assert report["summary"]["avg_score"] > 0
    assert report["summary"]["failure_types"] == {"none": 1}

    result = report["results"][0]
    assert result["task_id"] == "bubble_sort_order"
    assert result["pass_public"] is True
    assert result["pass_hidden"] is True
    assert result["failure_analysis"]["failure_type"] == "none"
    assert result["score"]["total"] > 0
    assert result["patch_quality"]["changed_lines"] > 0
    assert result["cost"]["tool_calls"] == result["tool_calls"]
    assert result["cost"]["total_wall_time_sec"] >= result["cost"]["agent_duration_sec"]
    assert "patch_file" in result["tools"]
    assert "run_tests" in result["tools"]
    assert (workdir / "bubble_sort_order" / "repo" / result["trajectory"]).exists()
    assert result["training_trajectory"].endswith(".training.json")
    assert (workdir / "bubble_sort_order" / "repo" / result["training_trajectory"]).exists()


def test_run_benchmark_default_workdir_is_under_project_root(tmp_path):
    out_dir = tmp_path / "results"

    exit_code = run_benchmark.main(
        [
            "--fake-agent-success",
            "--task",
            "bubble_sort_order",
            "--out",
            str(out_dir),
            "--repeat",
            "1",
        ]
    )

    assert exit_code == 0
    report = json.loads(next(out_dir.glob("run_*.json")).read_text(encoding="utf-8"))
    assert "benchmark_runs" in report["workdir"]
    assert "mini-agent-benchmark-" not in report["workdir"]


def test_run_benchmark_critic_retry_can_recover_public_failure(tmp_path):
    out_dir = tmp_path / "results"
    workdir = tmp_path / "work"

    exit_code = run_benchmark.main(
        [
            "--fake-agent-retry-success",
            "--critic-retries",
            "1",
            "--task",
            "bubble_sort_order",
            "--out",
            str(out_dir),
            "--workdir",
            str(workdir),
            "--repeat",
            "1",
        ]
    )

    assert exit_code == 0
    report = json.loads(next(out_dir.glob("run_*.json")).read_text(encoding="utf-8"))
    result = report["results"][0]

    assert result["pass_public"] is True
    assert result["pass_hidden"] is True
    assert result["critic_retries_used"] == 1
    assert result["critic_retries"][0]["failure_analysis"]["failure_type"] == "no_test_run"
    assert result["critic_retries"][0]["strategy"]["next_action"] == "execute_test_command"
    assert result["critic_retries"][0]["strategy"]["verify_tools"] == ["run_tests"]
    assert result["critic_retries"][0]["candidate"]["patch_score"]["score"] > 0
    assert "patch_quality" in result["critic_retries"][0]["candidate"]
    phases = result["critic_retries"][0]["phases"]
    assert [phase["phase"] for phase in phases] == ["diagnose", "edit", "verify"]
    assert phases[0]["allowed_tools"] == ["run_tests"]
    assert phases[0]["tool_requirement_met"] is True
    assert phases[0]["phase_valid"] is True
    assert phases[1]["tool_requirement_met"] is True
    assert phases[1]["edit_requirement_met"] is True
    assert phases[1]["phase_valid"] is True
    assert phases[2]["test_requirement_met"] is True
    assert phases[2]["phase_valid"] is True
    assert phases[1]["allowed_tools"] == ["patch_file", "apply_patch", "write_file"]
    assert phases[2]["allowed_tools"] == ["run_tests"]
    assert phases[2]["valid_tool_calls"] == ["run_tests"]
    assert "patch_file" in result["tools"]
    assert "run_tests" in result["tools"]
    assert result["patch_quality"]["size_label"] in {"small", "medium", "large"}


def test_second_retry_requires_code_and_diff_comparison():
    analysis = build_assessment("test_failure", "A public assertion still fails.")

    assert run_benchmark.diagnosis_required_tools_for(analysis, attempt=1) == set()
    assert run_benchmark.diagnosis_required_tools_for(analysis, attempt=2) == {"read_file", "git_diff"}
    assert run_benchmark.edit_tools_for(analysis) == []
    assert run_benchmark.edit_tools_for(analysis, evidence_gathered=True) == ["patch_file", "apply_patch", "write_file"]


def test_retry_policy_can_override_harness_evidence_threshold(tmp_path):
    policy_path = tmp_path / "retry_policy.json"
    policy_path.write_text(
        json.dumps({"require_harness_evidence_after_attempt": 3}),
        encoding="utf-8",
    )
    policy = run_benchmark.load_retry_policy(policy_path)
    analysis = build_assessment("test_failure", "A public assertion still fails.")

    assert run_benchmark.diagnosis_required_tools_for(analysis, attempt=2, retry_policy=policy) == set()
    assert run_benchmark.diagnosis_required_tools_for(analysis, attempt=3, retry_policy=policy) == {"read_file", "git_diff"}


def test_retry_policy_can_override_candidate_acceptance():
    before = {"exit_code": 1, "stdout": "1 failed in 0.01s", "stderr": ""}
    passing = {"exit_code": 0, "stdout": "1 passed in 0.01s", "stderr": ""}
    policy = {**run_benchmark.DEFAULT_RETRY_POLICY, "accept_if": []}

    decision = run_benchmark.assess_candidate(before, passing, policy)

    assert decision["reason"] == "public_tests_passed"
    assert decision["accepted"] is False


def test_harness_evidence_records_target_and_diff():
    class Agent:
        def __init__(self):
            self.events = []

        def run_tool(self, name, args):
            return f"{name} result for {args}"

        def record(self, event):
            self.events.append(event)

        def note_tool(self, name, args, result):
            pass

    agent = Agent()
    phase = run_benchmark.collect_harness_evidence(agent, {"expected_files": ["target.py"]})

    assert phase["phase_valid"] is True
    assert phase["source"] == "harness"
    assert phase["valid_tool_calls"] == ["read_file", "git_diff"]
    assert [event["source"] for event in agent.events] == ["harness", "harness"]


def test_candidate_assessment_rejects_non_improving_or_syntax_breaking_patch():
    before = {"exit_code": 1, "stdout": "1 failed in 0.01s", "stderr": ""}
    same_failure = {"exit_code": 1, "stdout": "1 failed in 0.01s", "stderr": ""}
    syntax_failure = {"exit_code": 1, "stdout": "", "stderr": "SyntaxError: invalid syntax"}
    improved = {"exit_code": 1, "stdout": "", "stderr": "1 failed, 1 passed"}

    assert run_benchmark.assess_candidate(before, same_failure)["accepted"] is False
    assert run_benchmark.assess_candidate(before, syntax_failure)["reason"] == "introduced_syntax_error"
    assert run_benchmark.assess_candidate(
        {"exit_code": 1, "stdout": "2 failed", "stderr": ""}, improved
    )["accepted"] is True


def test_candidate_patch_score_rewards_tests_and_penalizes_risky_patch():
    passing = {"reason": "public_tests_passed"}
    clean_patch = {"changed_files": 1, "changed_lines": 4, "size_label": "small", "flags": []}
    risky_patch = {
        "changed_files": 3,
        "changed_lines": 80,
        "size_label": "large",
        "flags": ["tests_modified", "unexpected_files"],
    }

    clean = run_benchmark.candidate_patch_score(passing, clean_patch)
    risky = run_benchmark.candidate_patch_score(passing, risky_patch)

    assert clean["score"] > risky["score"]
    assert "public_tests_passed:+70" in clean["reasons"]
    assert "tests_modified:-25" in risky["reasons"]


def test_retry_snapshot_restores_candidate_files(tmp_path):
    repo = tmp_path / "task" / "repo"
    repo.mkdir(parents=True)
    (repo / "source.py").write_text("value = 1\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "keep").write_text("metadata", encoding="utf-8")
    snapshot = tmp_path / "task" / "retry_snapshots" / "attempt_01"

    run_benchmark.create_retry_snapshot(repo, snapshot)
    (repo / "source.py").write_text("value = broken\n", encoding="utf-8")
    (repo / "extra.py").write_text("temporary\n", encoding="utf-8")
    run_benchmark.restore_retry_snapshot(snapshot, repo)

    assert (repo / "source.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (repo / "extra.py").exists()
    assert (repo / ".git" / "keep").read_text(encoding="utf-8") == "metadata"


def test_run_benchmark_repeat_reports_mean_and_std(tmp_path):
    out_dir = tmp_path / "results"
    workdir = tmp_path / "work"

    exit_code = run_benchmark.main(
        [
            "--fake-agent-success",
            "--task",
            "bubble_sort_order",
            "--repeat",
            "2",
            "--out",
            str(out_dir),
            "--workdir",
            str(workdir),
        ]
    )

    assert exit_code == 0
    report = json.loads(next(out_dir.glob("run_*.json")).read_text(encoding="utf-8"))
    assert report["schema"] == "mini-coding-agent.benchmark.v2"
    assert report["summary"]["repeat"] == 2
    assert report["summary"]["mean_hidden_pass_rate"] == 1.0
    assert report["summary"]["std_hidden_pass_rate"] == 0.0
    assert report["summary"]["mean_avg_patch_changed_lines"] > 0
    assert report["summary"]["mean_avg_total_wall_time_sec"] > 0
    assert [run["index"] for run in report["runs"]] == [1, 2]
    per_task_report = next(out_dir.glob("run_*.per_task.md"))
    text = per_task_report.read_text(encoding="utf-8")
    assert "Benchmark Per-Task Diagnosis" in text
    assert "Patch Lines" in text
    assert "bubble_sort_order" in text


def test_default_repeat_is_five_for_more_stable_evaluation():
    parser = run_benchmark.build_arg_parser()
    args = parser.parse_args([])

    assert args.repeat == 5
