from scripts.seed_benchmark_suite import TASKS


def test_catalog_adds_eighteen_tasks_for_twenty_total_tasks():
    assert len(TASKS) == 18
    difficulties = [spec["difficulty"] for spec in TASKS.values()]
    assert difficulties.count("easy") == 6
    assert difficulties.count("medium") == 7
    assert difficulties.count("hard") == 5


def test_catalog_tasks_have_required_benchmark_artifacts():
    for spec in TASKS.values():
        assert spec["source"].strip()
        assert spec["public"].strip()
        assert spec["hidden"].strip()
        assert spec["prompt"].strip()
        assert spec["expected_files"]
