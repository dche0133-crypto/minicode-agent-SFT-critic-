import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mini_coding_agent import (  # noqa: E402
    DeepSeekModelClient,
    FakeModelClient,
    MiniAgent,
    OllamaModelClient,
    SessionStore,
    WorkspaceContext,
    load_env_file,
)
from scripts.failure_analysis import classify_failure  # noqa: E402


def now_id():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def copy_tree_contents(src, dst):
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def run_command(command, cwd, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_sec": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nTimed out after {timeout}s",
            "duration_sec": round(time.monotonic() - started, 3),
        }


def init_git_repo(repo):
    if not shutil.which("git"):
        return False
    commands = [
        "git init",
        "git add .",
        'git -c user.name="Mini Agent Benchmark" -c user.email="benchmark@example.com" commit -m initial',
    ]
    for command in commands:
        result = run_command(command, repo, 20)
        if result["exit_code"] != 0:
            return False
    return True


def task_dirs(root):
    return sorted(path for path in root.rglob("metadata.json") if path.parent.is_dir())


def build_model(args):
    if args.fake_agent_success:
        return FakeModelClient(
            [
                '<tool name="patch_file" path="bubble_sort.py"><old_text>if result[j] < result[j + 1]:</old_text><new_text>if result[j] > result[j + 1]:</new_text></tool>',
                '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
                '<tool>{"name":"git_diff","args":{"path":"."}}</tool>',
                "<final>Fixed bubble_sort ordering and verified tests.</final>",
            ]
        )
    if args.provider == "ollama":
        return OllamaModelClient(
            model=args.model,
            host=args.host or "http://127.0.0.1:11434",
            temperature=args.temperature,
            top_p=args.top_p,
            timeout=args.timeout,
        )
    return DeepSeekModelClient(
        model=args.model,
        host=args.host or "https://api.deepseek.com",
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout=args.timeout,
    )


def run_task(task_root, args, run_root):
    metadata = read_json(task_root / "metadata.json")
    prompt = (task_root / "prompt.txt").read_text(encoding="utf-8").strip()
    task_id = metadata["id"]
    workdir = run_root / task_id
    repo = workdir / "repo"
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(task_root / "repo", repo)
    copy_tree_contents(task_root / "public_tests", repo / "tests")
    git_initialized = init_git_repo(repo)

    workspace = WorkspaceContext.build(repo)
    store = SessionStore(repo / ".mini-coding-agent" / "sessions")
    model = build_model(args)
    agent = MiniAgent(
        model_client=model,
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        max_steps=int(metadata.get("max_steps", args.max_steps)),
        max_new_tokens=args.max_new_tokens,
    )

    started = time.monotonic()
    final_answer = agent.ask(prompt)
    agent_duration = round(time.monotonic() - started, 3)

    check_command = metadata.get("check_command", "python -m pytest -q")
    public_result = run_command(check_command, repo, args.test_timeout)
    copy_tree_contents(task_root / "hidden_tests", repo / "tests")
    hidden_result = run_command(check_command, repo, args.test_timeout)

    trajectory_path = repo / "trajectories" / f"{agent.session['id']}.json"
    agent.export_trajectory(str(trajectory_path.relative_to(repo)))
    training_trajectory_path = repo / "trajectories" / f"{agent.session['id']}.training.json"
    agent.export_training_trajectory(str(training_trajectory_path.relative_to(repo)))
    tool_calls = [item for item in agent.session["history"] if item.get("role") == "tool"]
    result = {
        "task_id": task_id,
        "type": metadata.get("type", ""),
        "tags": metadata.get("tags", []),
        "git_initialized": git_initialized,
        "pass_public": public_result["exit_code"] == 0,
        "pass_hidden": hidden_result["exit_code"] == 0,
        "public_result": public_result,
        "hidden_result": hidden_result,
        "tool_calls": len(tool_calls),
        "tools": [item.get("name") for item in tool_calls],
        "agent_duration_sec": agent_duration,
        "final_answer": final_answer,
        "session_id": agent.session["id"],
        "trajectory": str(trajectory_path.relative_to(repo)).replace("\\", "/"),
        "training_trajectory": str(training_trajectory_path.relative_to(repo)).replace("\\", "/"),
        "workdir": str(repo),
    }
    result["failure_analysis"] = classify_failure(result, agent.training_trajectory_data(), metadata)

    return result


def summarize(results):
    total = len(results)
    public_pass = sum(1 for item in results if item["pass_public"])
    hidden_pass = sum(1 for item in results if item["pass_hidden"])
    return {
        "total": total,
        "public_pass": public_pass,
        "hidden_pass": hidden_pass,
        "public_pass_rate": public_pass / total if total else 0,
        "hidden_pass_rate": hidden_pass / total if total else 0,
        "avg_tool_calls": sum(item["tool_calls"] for item in results) / total if total else 0,
        "failure_types": failure_counts(results),
    }


def failure_counts(results):
    counts = {}
    for item in results:
        failure_type = item.get("failure_analysis", {}).get("failure_type", "unknown")
        counts[failure_type] = counts.get(failure_type, 0) + 1
    return counts


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run Mini-Coding-Agent benchmark tasks.")
    parser.add_argument("--benchmarks", default="benchmarks", help="Benchmark root directory.")
    parser.add_argument("--task", default=None, help="Run one task id only.")
    parser.add_argument("--out", default="benchmark_results", help="Directory for result JSON files.")
    parser.add_argument("--workdir", default=None, help="Directory for copied benchmark repos; defaults to benchmark_runs/run_<timestamp>.")
    parser.add_argument("--provider", choices=("deepseek", "ollama"), default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--host", default=None)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--test-timeout", type=int, default=120)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--fake-agent-success", action="store_true", help="Use a fake model to smoke-test the benchmark runner without API calls.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    env_file = Path(args.env_file)
    if not env_file.is_absolute():
        env_file = ROOT / env_file
    load_env_file(env_file)

    benchmark_root = Path(args.benchmarks)
    if not benchmark_root.is_absolute():
        benchmark_root = ROOT / benchmark_root
    tasks = []
    for metadata_path in task_dirs(benchmark_root):
        metadata = read_json(metadata_path)
        if args.task and metadata.get("id") != args.task:
            continue
        tasks.append(metadata_path.parent)
    if not tasks:
        raise SystemExit("No benchmark tasks found.")

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = now_id()
    if args.workdir:
        run_root = Path(args.workdir)
        if not run_root.is_absolute():
            run_root = ROOT / run_root
        run_root.mkdir(parents=True, exist_ok=True)
    else:
        run_root = ROOT / "benchmark_runs" / f"run_{run_id}"
        run_root.mkdir(parents=True, exist_ok=True)

    results = [run_task(task, args, run_root) for task in tasks]
    report = {
        "schema": "mini-coding-agent.benchmark.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": str(benchmark_root),
        "workdir": str(run_root),
        "provider": "fake" if args.fake_agent_success else args.provider,
        "model": args.model,
        "summary": summarize(results),
        "results": results,
    }
    report_path = out_dir / f"run_{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
