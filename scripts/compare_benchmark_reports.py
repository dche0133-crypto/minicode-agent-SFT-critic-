import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def report_results(report):
    if "results" in report:
        return report.get("results", [])
    results = []
    for run in report.get("runs", []):
        for result in run.get("results", []):
            item = dict(result)
            item["repeat_index"] = run.get("index")
            results.append(item)
    return results


def failure_type(result):
    return result.get("failure_analysis", {}).get("failure_type", "unknown")


def score_total(result):
    return float(result.get("score", {}).get("total", 0))


def group_by_task(results):
    grouped = defaultdict(list)
    for result in results:
        grouped[result.get("task_id", "unknown")].append(result)
    return dict(grouped)


def aggregate_task(task_results):
    total = len(task_results)
    failures = Counter(failure_type(result) for result in task_results)
    retry_statuses = Counter(
        retry.get("status", "")
        for result in task_results
        for retry in result.get("critic_retries", [])
        if retry.get("status")
    )
    return {
        "task_id": task_results[0].get("task_id", "unknown"),
        "difficulty": task_results[0].get("difficulty", ""),
        "total": total,
        "public_pass": sum(1 for result in task_results if result.get("pass_public")),
        "hidden_pass": sum(1 for result in task_results if result.get("pass_hidden")),
        "avg_score": round(sum(score_total(result) for result in task_results) / total, 2) if total else 0,
        "avg_tool_calls": round(sum(float(result.get("tool_calls", 0)) for result in task_results) / total, 2) if total else 0,
        "avg_patch_lines": round(
            sum(float(result.get("patch_quality", {}).get("changed_lines", 0)) for result in task_results) / total,
            2,
        ) if total else 0,
        "avg_wall_time": round(
            sum(float(result.get("cost", {}).get("total_wall_time_sec", 0)) for result in task_results) / total,
            2,
        ) if total else 0,
        "failure_type": failures.most_common(1)[0][0] if failures else "unknown",
        "retry_status": retry_statuses.most_common(1)[0][0] if retry_statuses else "-",
    }


def aggregate_tasks(report):
    return {
        task_id: aggregate_task(task_results)
        for task_id, task_results in sorted(group_by_task(report_results(report)).items())
    }


def pct(value):
    return f"{float(value) * 100:.1f}%"


def metric_value(summary, name):
    if f"mean_{name}" in summary:
        return summary[f"mean_{name}"]
    return summary.get(name, 0)


def metric_std(summary, name):
    return summary.get(f"std_{name}", 0)


def overall_rows(base, candidate):
    metrics = [
        ("public_pass_rate", "Public Pass Rate", True),
        ("hidden_pass_rate", "Hidden Pass Rate", True),
        ("avg_score", "Avg Score", False),
        ("avg_tool_calls", "Avg Tool Calls", False),
        ("avg_total_wall_time_sec", "Avg Wall Time Sec", False),
        ("avg_patch_changed_lines", "Avg Patch Lines", False),
        ("avg_patch_changed_files", "Avg Patch Files", False),
    ]
    rows = []
    base_summary = base.get("summary", {})
    candidate_summary = candidate.get("summary", {})
    for key, label, is_rate in metrics:
        base_value = metric_value(base_summary, key)
        candidate_value = metric_value(candidate_summary, key)
        delta = candidate_value - base_value
        base_std = metric_std(base_summary, key)
        candidate_std = metric_std(candidate_summary, key)
        if is_rate:
            rows.append(
                [
                    label,
                    pct(base_value),
                    pct(candidate_value),
                    f"{delta * 100:+.1f} pp",
                    pct(base_std),
                    pct(candidate_std),
                ]
            )
        else:
            rows.append(
                [
                    label,
                    f"{base_value:.2f}",
                    f"{candidate_value:.2f}",
                    f"{delta:+.2f}",
                    f"{base_std:.2f}",
                    f"{candidate_std:.2f}",
                ]
            )
    return rows


def transition_matrix(base_tasks, candidate_tasks):
    transitions = Counter()
    for task_id in sorted(set(base_tasks) & set(candidate_tasks)):
        transitions[(base_tasks[task_id]["failure_type"], candidate_tasks[task_id]["failure_type"])] += 1
    return transitions


def difficulty_summary(report):
    groups = defaultdict(list)
    for result in report_results(report):
        groups[result.get("difficulty") or "unknown"].append(result)
    rows = {}
    for difficulty, items in groups.items():
        total = len(items)
        rows[difficulty] = {
            "total": total,
            "public_pass_rate": sum(1 for item in items if item.get("pass_public")) / total if total else 0,
            "hidden_pass_rate": sum(1 for item in items if item.get("pass_hidden")) / total if total else 0,
            "avg_score": round(sum(score_total(item) for item in items) / total, 2) if total else 0,
            "avg_tool_calls": round(sum(float(item.get("tool_calls", 0)) for item in items) / total, 2) if total else 0,
            "avg_patch_lines": round(sum(float(item.get("patch_quality", {}).get("changed_lines", 0)) for item in items) / total, 2) if total else 0,
            "avg_wall_time": round(sum(float(item.get("cost", {}).get("total_wall_time_sec", 0)) for item in items) / total, 2) if total else 0,
        }
    return rows


def task_delta_rows(base_tasks, candidate_tasks):
    rows = []
    for task_id in sorted(set(base_tasks) | set(candidate_tasks)):
        base = base_tasks.get(task_id)
        candidate = candidate_tasks.get(task_id)
        if not base or not candidate:
            continue
        rows.append(
            {
                "task_id": task_id,
                "difficulty": candidate.get("difficulty") or base.get("difficulty") or "-",
                "base_hidden": f"{base['hidden_pass']}/{base['total']}",
                "candidate_hidden": f"{candidate['hidden_pass']}/{candidate['total']}",
                "hidden_delta": candidate["hidden_pass"] - base["hidden_pass"],
                "base_public": f"{base['public_pass']}/{base['total']}",
                "candidate_public": f"{candidate['public_pass']}/{candidate['total']}",
                "score_delta": round(candidate["avg_score"] - base["avg_score"], 2),
                "tool_delta": round(candidate["avg_tool_calls"] - base["avg_tool_calls"], 2),
                "patch_delta": round(candidate["avg_patch_lines"] - base["avg_patch_lines"], 2),
                "wall_time_delta": round(candidate["avg_wall_time"] - base["avg_wall_time"], 2),
                "base_failure": base["failure_type"],
                "candidate_failure": candidate["failure_type"],
                "retry_status": candidate.get("retry_status", "-"),
            }
        )
    return sorted(rows, key=lambda row: (-row["hidden_delta"], -row["score_delta"], row["task_id"]))


def markdown_table(headers, rows):
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def bar(value, width=20):
    value = max(0.0, min(1.0, float(value)))
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def simple_visual_rows(base_report, candidate_report):
    metrics = [
        ("public_pass_rate", "Public", True),
        ("hidden_pass_rate", "Hidden", True),
    ]
    rows = []
    for key, label, _ in metrics:
        base_value = metric_value(base_report.get("summary", {}), key)
        candidate_value = metric_value(candidate_report.get("summary", {}), key)
        rows.append([label, bar(base_value), pct(base_value), bar(candidate_value), pct(candidate_value)])
    return rows


def hard_case_rows(task_rows):
    return [
        row for row in task_rows
        if row["difficulty"] == "hard"
    ]


def generate_markdown(base_report, candidate_report, base_name="baseline", candidate_name="candidate"):
    base_tasks = aggregate_tasks(base_report)
    candidate_tasks = aggregate_tasks(candidate_report)
    transitions = transition_matrix(base_tasks, candidate_tasks)
    base_difficulty = difficulty_summary(base_report)
    candidate_difficulty = difficulty_summary(candidate_report)
    task_rows = task_delta_rows(base_tasks, candidate_tasks)

    lines = [
        "# Benchmark Experiment Report",
        "",
        f"- baseline: `{base_name}`",
        f"- candidate: `{candidate_name}`",
        f"- model: `{candidate_report.get('provider')}` / `{candidate_report.get('model')}`",
        "",
        "## Overall Metrics",
        "",
    ]
    lines.extend(
        markdown_table(
            ["Metric", "Baseline", "Candidate", "Delta", "Baseline Std", "Candidate Std"],
            overall_rows(base_report, candidate_report),
        )
    )

    lines.extend(["", "## Visual Summary", ""])
    lines.extend(
        markdown_table(
            ["Metric", "Baseline Bar", "Baseline", "Candidate Bar", "Candidate"],
            simple_visual_rows(base_report, candidate_report),
        )
    )

    lines.extend(["", "## Failure Type Transition Matrix", ""])
    transition_rows = [
        [before, after, count]
        for (before, after), count in sorted(transitions.items(), key=lambda item: (-item[1], item[0]))
    ]
    lines.extend(markdown_table(["Baseline Failure", "Candidate Failure", "Tasks"], transition_rows))

    lines.extend(["", "## Difficulty Breakdown", ""])
    difficulty_rows = []
    for difficulty in sorted(set(base_difficulty) | set(candidate_difficulty)):
        base = base_difficulty.get(difficulty, {})
        candidate = candidate_difficulty.get(difficulty, {})
        difficulty_rows.append(
            [
                difficulty,
                pct(base.get("hidden_pass_rate", 0)),
                pct(candidate.get("hidden_pass_rate", 0)),
                f"{(candidate.get('hidden_pass_rate', 0) - base.get('hidden_pass_rate', 0)) * 100:+.1f} pp",
                f"{base.get('avg_score', 0):.2f}",
                f"{candidate.get('avg_score', 0):.2f}",
                f"{candidate.get('avg_tool_calls', 0):.2f}",
                f"{candidate.get('avg_patch_lines', 0):.2f}",
                f"{candidate.get('avg_wall_time', 0):.2f}",
            ]
        )
    lines.extend(
        markdown_table(
            [
                "Difficulty",
                "Baseline Hidden",
                "Candidate Hidden",
                "Delta",
                "Baseline Score",
                "Candidate Score",
                "Candidate Tools",
                "Candidate Patch Lines",
                "Candidate Wall Time",
            ],
            difficulty_rows,
        )
    )

    lines.extend(["", "## Per-Task Changes", ""])
    lines.extend(
        markdown_table(
            [
                "Task",
                "Difficulty",
                "Public",
                "Hidden",
                "Score Delta",
                "Tool Delta",
                "Patch Delta",
                "Failure Transition",
                "Retry Status",
            ],
            [
                [
                    row["task_id"],
                    row["difficulty"],
                    f"{row['base_public']} -> {row['candidate_public']}",
                    f"{row['base_hidden']} -> {row['candidate_hidden']}",
                    f"{row['score_delta']:+.2f}",
                    f"{row['tool_delta']:+.2f}",
                    f"{row['patch_delta']:+.2f}",
                    f"{row['base_failure']} -> {row['candidate_failure']}",
                    row["retry_status"],
                ]
                for row in task_rows
            ],
        )
    )

    hard_rows = hard_case_rows(task_rows)
    if hard_rows:
        lines.extend(["", "## Hard Task Case Study", ""])
        lines.extend(
            markdown_table(
                [
                    "Task",
                    "Hidden",
                    "Score Delta",
                    "Tool Delta",
                    "Patch Delta",
                    "Wall Time Delta",
                    "Failure Transition",
                    "Retry Status",
                ],
                [
                    [
                        row["task_id"],
                        f"{row['base_hidden']} -> {row['candidate_hidden']}",
                        f"{row['score_delta']:+.2f}",
                        f"{row['tool_delta']:+.2f}",
                        f"{row['patch_delta']:+.2f}",
                        f"{row['wall_time_delta']:+.2f}",
                        f"{row['base_failure']} -> {row['candidate_failure']}",
                        row["retry_status"],
                    ]
                    for row in hard_rows
                ],
            )
        )

    improved = [row for row in task_rows if row["hidden_delta"] > 0]
    regressed = [row for row in task_rows if row["hidden_delta"] < 0]
    no_hidden_gain = [row for row in task_rows if row["hidden_delta"] == 0 and row["score_delta"] > 10]
    lines.extend(["", "## Reading Notes", ""])
    lines.append(f"- Hidden-pass improved tasks: {len(improved)}")
    lines.append(f"- Hidden-pass regressed tasks: {len(regressed)}")
    lines.append(f"- Score improved without hidden-pass gain: {len(no_hidden_gain)}")
    lines.append("- Treat this as an evaluation report, not proof of model capability by itself.")
    return "\n".join(lines) + "\n"


def write_task_csv(base_report, candidate_report, path):
    base_tasks = aggregate_tasks(base_report)
    candidate_tasks = aggregate_tasks(candidate_report)
    rows = task_delta_rows(base_tasks, candidate_tasks)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "task_id",
                "difficulty",
                "base_public",
                "candidate_public",
                "base_hidden",
                "candidate_hidden",
                "hidden_delta",
                "score_delta",
                "tool_delta",
                "patch_delta",
                "wall_time_delta",
                "base_failure",
                "candidate_failure",
                "retry_status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Compare two benchmark reports and write a Markdown experiment report.")
    parser.add_argument("--baseline", required=True, help="Baseline benchmark JSON report.")
    parser.add_argument("--candidate", required=True, help="Candidate/retry benchmark JSON report.")
    parser.add_argument("--out", default="EXPERIMENT_REPORT.md", help="Output Markdown report path.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    base_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    out_path = Path(args.out)
    markdown = generate_markdown(
        read_json(base_path),
        read_json(candidate_path),
        base_name=str(base_path),
        candidate_name=str(candidate_path),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    csv_path = out_path.with_suffix(".tasks.csv")
    write_task_csv(read_json(base_path), read_json(candidate_path), csv_path)
    print(f"wrote {out_path}")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
