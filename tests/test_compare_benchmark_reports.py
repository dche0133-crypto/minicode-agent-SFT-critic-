from scripts import compare_benchmark_reports as compare


def result(task_id, difficulty, public, hidden, failure_type, score=50, tools=2, retry_status=None):
    retries = []
    if retry_status:
        retries.append({"status": retry_status})
    return {
        "task_id": task_id,
        "difficulty": difficulty,
        "pass_public": public,
        "pass_hidden": hidden,
        "tool_calls": tools,
        "critic_retries": retries,
        "failure_analysis": {"failure_type": failure_type},
        "score": {"total": score},
    }


def test_generate_markdown_contains_overall_transitions_and_difficulty():
    baseline = {
        "schema": "mini-coding-agent.benchmark.v2",
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "summary": {
            "repeat": 2,
            "mean_public_pass_rate": 0.25,
            "std_public_pass_rate": 0.0,
            "mean_hidden_pass_rate": 0.0,
            "std_hidden_pass_rate": 0.0,
            "mean_avg_score": 20.0,
            "std_avg_score": 1.0,
            "mean_avg_tool_calls": 2.0,
            "std_avg_tool_calls": 0.0,
        },
        "runs": [
            {
                "index": 1,
                "results": [
                    result("easy_task", "easy", False, False, "no_test_run", 10),
                    result("hard_task", "hard", False, False, "syntax_error", 20),
                ],
            },
            {
                "index": 2,
                "results": [
                    result("easy_task", "easy", False, False, "no_test_run", 10),
                    result("hard_task", "hard", False, False, "syntax_error", 20),
                ],
            },
        ],
    }
    candidate = {
        "schema": "mini-coding-agent.benchmark.v2",
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "summary": {
            "repeat": 2,
            "mean_public_pass_rate": 0.5,
            "std_public_pass_rate": 0.1,
            "mean_hidden_pass_rate": 0.25,
            "std_hidden_pass_rate": 0.1,
            "mean_avg_score": 45.0,
            "std_avg_score": 2.0,
            "mean_avg_tool_calls": 4.0,
            "std_avg_tool_calls": 0.2,
        },
        "runs": [
            {
                "index": 1,
                "results": [
                    result("easy_task", "easy", True, True, "none", 90, retry_status="completed"),
                    result("hard_task", "hard", False, False, "test_failure", 35, retry_status="candidate_rejected"),
                ],
            },
            {
                "index": 2,
                "results": [
                    result("easy_task", "easy", True, False, "hidden_test_failed", 70, retry_status="completed"),
                    result("hard_task", "hard", False, False, "test_failure", 35, retry_status="candidate_rejected"),
                ],
            },
        ],
    }

    markdown = compare.generate_markdown(baseline, candidate)

    assert "## Overall Metrics" in markdown
    assert "Public Pass Rate" in markdown
    assert "+25.0 pp" in markdown
    assert "## Failure Type Transition Matrix" in markdown
    assert "## Visual Summary" in markdown
    assert "no_test_run" in markdown
    assert "none" in markdown
    assert "syntax_error" in markdown
    assert "test_failure" in markdown
    assert "## Difficulty Breakdown" in markdown
    assert "easy" in markdown
    assert "hard" in markdown
    assert "## Per-Task Changes" in markdown
    assert "## Hard Task Case Study" in markdown
    assert "Tool Delta" in markdown
    assert "Patch Delta" in markdown
    assert "easy_task" in markdown
    assert "candidate_rejected" in markdown


def test_main_writes_report(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    out_path = tmp_path / "report.md"
    baseline_path.write_text(
        '{"schema":"mini-coding-agent.benchmark.v1","summary":{"public_pass_rate":0,"hidden_pass_rate":0,"avg_score":0,"avg_tool_calls":1},"results":[]}',
        encoding="utf-8",
    )
    candidate_path.write_text(
        '{"schema":"mini-coding-agent.benchmark.v1","summary":{"public_pass_rate":1,"hidden_pass_rate":1,"avg_score":90,"avg_tool_calls":2},"results":[]}',
        encoding="utf-8",
    )

    exit_code = compare.main(["--baseline", str(baseline_path), "--candidate", str(candidate_path), "--out", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()
    assert out_path.with_suffix(".tasks.csv").exists()
    assert "Benchmark Experiment Report" in out_path.read_text(encoding="utf-8")
