import json

from scripts import run_benchmark


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
        ]
    )

    assert exit_code == 0
    reports = list(out_dir.glob("run_*.json"))
    assert len(reports) == 1

    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["schema"] == "mini-coding-agent.benchmark.v1"
    assert report["summary"]["total"] == 1
    assert report["summary"]["public_pass"] == 1
    assert report["summary"]["hidden_pass"] == 1
    assert report["summary"]["failure_types"] == {"none": 1}

    result = report["results"][0]
    assert result["task_id"] == "bubble_sort_order"
    assert result["pass_public"] is True
    assert result["pass_hidden"] is True
    assert result["failure_analysis"]["failure_type"] == "none"
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
        ]
    )

    assert exit_code == 0
    report = json.loads(next(out_dir.glob("run_*.json")).read_text(encoding="utf-8"))
    assert "benchmark_runs" in report["workdir"]
    assert "mini-agent-benchmark-" not in report["workdir"]
