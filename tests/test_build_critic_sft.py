import json

from scripts import build_critic_sft


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_synthetic_rows_cover_failure_types():
    rows = build_critic_sft.synthetic_rows(2)

    assert len(rows) == len(build_critic_sft.FAILURE_TYPES) * 2
    failure_types = {row["output"]["failure_type"] for row in rows}
    assert failure_types == set(build_critic_sft.FAILURE_TYPES)
    assert all(row["instruction"] == build_critic_sft.INSTRUCTION for row in rows)
    assert all(row["label_schema"] == build_critic_sft.LABEL_SCHEMA_VERSION for row in rows)
    assert all("diagnosis" in row["output"] and "decision" in row["output"] for row in rows)
    assert all("confidence" in row["output"]["decision"] for row in rows)


def test_synthetic_rows_have_high_uniqueness():
    rows = build_critic_sft.synthetic_rows(50)
    unique = build_critic_sft.dedupe_rows(rows)
    stats = build_critic_sft.dataset_stats(unique)

    assert len(unique) >= 300
    assert stats["unique_input_output_pairs"] == len(unique)
    assert stats["failure_types"]["wrong_file"] >= 40


def test_build_critic_sft_writes_synthetic_jsonl(tmp_path):
    out = tmp_path / "critic_sft.jsonl"

    exit_code = build_critic_sft.main(
        [
            "--reports",
            str(tmp_path / "missing_reports"),
            "--synthetic-per-type",
            "1",
            "--out",
            str(out),
        ]
    )

    rows = read_jsonl(out)
    assert exit_code == 0
    assert len(rows) == len(build_critic_sft.FAILURE_TYPES)
    assert rows[0]["source"] == "synthetic_template"
    assert "Trajectory:" in rows[0]["input"]
    assert rows[0]["quality"]["keep"] is True


def test_rows_from_report_builds_benchmark_sample(tmp_path):
    workdir = tmp_path / "repo"
    trajectory_dir = workdir / "trajectories"
    trajectory_dir.mkdir(parents=True)
    training = {
        "task": "Fix parser.py",
        "steps": [
            {
                "step": 1,
                "action": "run_tests",
                "args": {"command": "python -m pytest -q"},
                "observation": "exit_code: 1\nFAILED tests/test_parser.py",
                "diff": "",
                "success": False,
            }
        ],
    }
    (trajectory_dir / "s.training.json").write_text(json.dumps(training), encoding="utf-8")
    report = {
        "benchmark_root": str(tmp_path / "benchmarks"),
        "results": [
            {
                "task_id": "parser_bug",
                "session_id": "s",
                "workdir": str(workdir),
                "training_trajectory": "trajectories/s.training.json",
                "public_result": {"stdout": "FAILED tests/test_parser.py", "stderr": ""},
                "hidden_result": {"stdout": "", "stderr": ""},
                "failure_analysis": {
                    "failure_type": "test_failure",
                    "reason": "Tests failed.",
                    "suggestion": "Inspect parser.py.",
                },
            }
        ],
    }
    report_path = tmp_path / "run.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    rows = build_critic_sft.rows_from_report(report_path)

    assert len(rows) == 1
    assert rows[0]["source"] == "benchmark"
    assert rows[0]["output"]["failure_type"] == "test_failure"
    assert rows[0]["output"]["decision"]["next_action"] == "inspect_test_failure"
    assert rows[0]["quality"]["keep"] is True
    assert "FAILED tests/test_parser.py" in rows[0]["input"]


def test_rows_from_report_skips_legacy_rows_without_training_trajectory(tmp_path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    report = {
        "benchmark_root": str(tmp_path / "benchmarks"),
        "results": [
            {
                "task_id": "legacy",
                "session_id": "s",
                "workdir": str(workdir),
                "failure_analysis": {"failure_type": "test_failure"},
            }
        ],
    }
    report_path = tmp_path / "legacy.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert build_critic_sft.rows_from_report(report_path) == []


def test_rows_from_report_filters_low_quality_missing_test_signal(tmp_path):
    workdir = tmp_path / "repo"
    trajectory_dir = workdir / "trajectories"
    trajectory_dir.mkdir(parents=True)
    training = {
        "task": "Fix parser.py",
        "steps": [
            {
                "step": 1,
                "action": "read_file",
                "args": {"path": "parser.py"},
                "observation": "def parse(x): return x",
                "diff": "",
                "success": None,
            }
        ],
    }
    (trajectory_dir / "s.training.json").write_text(json.dumps(training), encoding="utf-8")
    report = {
        "benchmark_root": str(tmp_path / "benchmarks"),
        "results": [
            {
                "task_id": "parser_bug",
                "session_id": "s",
                "workdir": str(workdir),
                "training_trajectory": "trajectories/s.training.json",
                "public_result": {"stdout": "", "stderr": ""},
                "hidden_result": {"stdout": "", "stderr": ""},
                "failure_analysis": {
                    "failure_type": "test_failure",
                    "reason": "Tests failed.",
                    "suggestion": "Inspect parser.py.",
                },
            }
        ],
    }
    report_path = tmp_path / "run.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    quality_filter = {**build_critic_sft.DEFAULT_QUALITY_FILTER, "min_quality_score": 0.8}
    filtered = build_critic_sft.rows_from_report(report_path, quality_filter=quality_filter)
    unfiltered = build_critic_sft.rows_from_report(
        report_path,
        quality_filter=quality_filter,
        apply_quality_filter=False,
    )

    assert filtered == []
    assert unfiltered[0]["quality"]["keep"] is False
    assert "missing_test_signal" in unfiltered[0]["quality"]["reasons"]


def test_rows_from_repeated_report_builds_samples(tmp_path):
    workdir = tmp_path / "repo"
    trajectory_dir = workdir / "trajectories"
    trajectory_dir.mkdir(parents=True)
    training = {
        "task": "Fix parser.py",
        "steps": [
            {
                "step": 1,
                "action": "run_tests",
                "args": {"command": "python -m pytest -q"},
                "observation": "exit_code: 1\nFAILED tests/test_parser.py",
                "diff": "",
                "success": False,
            }
        ],
    }
    (trajectory_dir / "s.training.json").write_text(json.dumps(training), encoding="utf-8")
    result = {
        "task_id": "parser_bug",
        "session_id": "s",
        "workdir": str(workdir),
        "training_trajectory": "trajectories/s.training.json",
        "public_result": {"stdout": "FAILED tests/test_parser.py", "stderr": ""},
        "hidden_result": {"stdout": "", "stderr": ""},
        "failure_analysis": {
            "failure_type": "test_failure",
            "reason": "Tests failed.",
            "suggestion": "Inspect parser.py.",
        },
    }
    report = {
        "schema": "mini-coding-agent.benchmark.v2",
        "benchmark_root": str(tmp_path / "benchmarks"),
        "runs": [{"index": 1, "results": [result]}],
    }
    report_path = tmp_path / "run.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    rows = build_critic_sft.rows_from_report(report_path)

    assert len(rows) == 1
    assert rows[0]["repeat_index"] == 1
