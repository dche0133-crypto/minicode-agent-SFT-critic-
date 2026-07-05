import argparse
import json
import re
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
from scripts.scoring import score_result  # noqa: E402


def now_id():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_RETRY_POLICY = {
    "accept_if": [
        "public_tests_passed",
        "fewer_public_test_failures",
        "fewer_public_test_errors",
    ],
    "reject_if": [
        "introduced_syntax_error",
        "introduced_collection_error",
        "no_public_test_improvement",
        "edit_phase_invalid",
        "verify_phase_invalid",
    ],
    "rollback_on_reject": True,
    "diagnose_max_steps": 1,
    "verify_max_steps": 1,
    "edit_max_steps": None,
    "require_harness_evidence_after_attempt": 2,
    "require_harness_evidence_for": [
        "test_failure",
        "assertion_failure",
        "early_stop_after_test_failure",
    ],
}


def load_retry_policy(path=None):
    policy = dict(DEFAULT_RETRY_POLICY)
    if not path:
        return policy
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    user_policy = read_json(config_path)
    for key, value in user_policy.items():
        policy[key] = value
    return policy


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


SNAPSHOT_IGNORES = {".git", ".mini-coding-agent", "trajectories", "__pycache__", ".pytest_cache"}


def create_retry_snapshot(repo, snapshot):
    """Copy mutable task files before an edit phase, excluding agent/git bookkeeping."""
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo, snapshot, ignore=shutil.ignore_patterns(*SNAPSHOT_IGNORES))
    return snapshot


def restore_retry_snapshot(snapshot, repo):
    """Restore task files after a retry candidate fails to improve public tests."""
    if not snapshot.is_dir() or snapshot.parent.name != "retry_snapshots" or repo.name != "repo":
        raise ValueError("Refusing to restore an invalid retry snapshot path.")
    for item in repo.iterdir():
        if item.name in SNAPSHOT_IGNORES:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    copy_tree_contents(snapshot, repo)


def test_result_metrics(result):
    """Extract a small, deterministic quality signal from one pytest result."""
    output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"

    def count(pattern):
        matches = re.findall(pattern, output, flags=re.IGNORECASE)
        return sum(int(value) for value in matches)

    return {
        "passed": result.get("exit_code") == 0,
        "failed": count(r"(\d+)\s+failed"),
        "errors": count(r"(\d+)\s+errors?"),
        "collection_errors": output.lower().count("error collecting"),
        "syntax_errors": len(re.findall(r"\b(?:syntaxerror|indentationerror|taberror)\b", output, flags=re.IGNORECASE)),
    }


def assess_candidate(before, candidate, retry_policy=None):
    """Accept only a retry whose public-test outcome strictly improves."""
    retry_policy = retry_policy or DEFAULT_RETRY_POLICY
    before_metrics = test_result_metrics(before)
    candidate_metrics = test_result_metrics(candidate)
    if candidate_metrics["passed"]:
        reason = "public_tests_passed"
    elif candidate_metrics["syntax_errors"] > before_metrics["syntax_errors"]:
        reason = "introduced_syntax_error"
    elif candidate_metrics["collection_errors"] > before_metrics["collection_errors"]:
        reason = "introduced_collection_error"
    elif candidate_metrics["failed"] < before_metrics["failed"]:
        reason = "fewer_public_test_failures"
    elif candidate_metrics["errors"] < before_metrics["errors"]:
        reason = "fewer_public_test_errors"
    else:
        reason = "no_public_test_improvement"
    if reason in retry_policy.get("accept_if", []):
        accepted = True
    elif reason in retry_policy.get("reject_if", []):
        accepted = False
    else:
        accepted = False
    return {
        "accepted": accepted,
        "reason": reason,
        "before_metrics": before_metrics,
        "candidate_metrics": candidate_metrics,
    }


def candidate_patch_score(candidate_decision, patch, retry_duration_sec=0):
    """Score a retry candidate using tests, patch discipline, and cost signals."""
    patch = patch or {}
    flags = set(patch.get("flags", []))
    changed_lines = int(patch.get("changed_lines", 0))
    changed_files = int(patch.get("changed_files", 0))
    reason = candidate_decision.get("reason", "")
    score = 0
    reasons = []

    if reason == "public_tests_passed":
        score += 70
        reasons.append("public_tests_passed:+70")
    elif reason in {"fewer_public_test_failures", "fewer_public_test_errors"}:
        score += 45
        reasons.append(f"{reason}:+45")
    elif reason == "no_public_test_improvement":
        score -= 25
        reasons.append("no_public_test_improvement:-25")
    elif reason in {"introduced_syntax_error", "introduced_collection_error"}:
        score -= 45
        reasons.append(f"{reason}:-45")
    elif reason in {"edit_phase_invalid", "verify_phase_invalid"}:
        score -= 35
        reasons.append(f"{reason}:-35")

    if patch.get("size_label") == "small":
        score += 10
        reasons.append("small_patch:+10")
    elif patch.get("size_label") == "medium":
        score += 2
        reasons.append("medium_patch:+2")
    elif patch.get("size_label") == "large":
        score -= 12
        reasons.append("large_patch:-12")

    if "tests_modified" in flags:
        score -= 25
        reasons.append("tests_modified:-25")
    if "unexpected_files" in flags:
        score -= 20
        reasons.append("unexpected_files:-20")
    if "no_patch" in flags:
        score -= 15
        reasons.append("no_patch:-15")

    if changed_files > 2:
        penalty = min(15, (changed_files - 2) * 5)
        score -= penalty
        reasons.append(f"many_files:-{penalty}")
    if changed_lines > 40:
        penalty = min(20, (changed_lines - 40) // 10 + 5)
        score -= penalty
        reasons.append(f"many_lines:-{penalty}")
    if retry_duration_sec and retry_duration_sec > 20:
        score -= 5
        reasons.append("slow_retry:-5")

    return {
        "score": max(0, min(100, score)),
        "raw_score": score,
        "reasons": reasons,
        "patch": patch,
    }


def run_command(command, cwd, timeout):
    if command.startswith("python ") and not shutil.which("python"):
        command = f'"{sys.executable}" {command[len("python "):]}'
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


def parse_numstat(output):
    files = []
    total_added = 0
    total_deleted = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path = parts[0], parts[1], parts[-1]
        added = int(added_raw) if added_raw.isdigit() else 0
        deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
        total_added += added
        total_deleted += deleted
        files.append({"path": path, "added": added, "deleted": deleted})
    return files, total_added, total_deleted


def patch_quality(repo, metadata=None):
    """Summarize patch size and whether edits stayed inside expected source files."""
    metadata = metadata or {}
    expected_files = set(metadata.get("expected_files", []))
    numstat = run_command("git diff --numstat", repo, 20)
    name_only = run_command("git diff --name-only", repo, 20)
    files, added, deleted = parse_numstat(numstat.get("stdout", ""))
    changed_files = [line.strip() for line in name_only.get("stdout", "").splitlines() if line.strip()]
    test_files = [
        path for path in changed_files
        if path.startswith("tests/") or "/tests/" in path or Path(path).name.startswith("test_")
    ]
    unexpected_files = [
        path for path in changed_files
        if expected_files and path not in expected_files and path not in test_files
    ]
    changed_lines = added + deleted
    if changed_lines == 0:
        size_label = "none"
    elif changed_lines <= 20 and len(changed_files) <= 2:
        size_label = "small"
    elif changed_lines <= 80 and len(changed_files) <= 4:
        size_label = "medium"
    else:
        size_label = "large"
    flags = []
    if test_files:
        flags.append("tests_modified")
    if unexpected_files:
        flags.append("unexpected_files")
    if size_label == "large":
        flags.append("large_patch")
    if not changed_files:
        flags.append("no_patch")
    return {
        "changed_files": len(changed_files),
        "changed_lines": changed_lines,
        "added_lines": added,
        "deleted_lines": deleted,
        "size_label": size_label,
        "files": files,
        "expected_files": sorted(expected_files),
        "unexpected_files": unexpected_files,
        "test_files_modified": test_files,
        "flags": flags,
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
                '<tool>{"name":"patch_file","args":{"path":"bubble_sort.py","old_text":"if result[j] < result[j + 1]:","new_text":"if result[j] > result[j + 1]:"}}</tool>',
                '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
                '<tool>{"name":"git_diff","args":{"path":"."}}</tool>',
                "<final>Fixed bubble_sort ordering and verified tests.</final>",
            ]
        )
    if args.fake_agent_retry_success:
        return FakeModelClient(
            [
                "<final>I inspected the task and believe it is complete.</final>",
                '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
                '<tool>{"name":"patch_file","args":{"path":"bubble_sort.py","old_text":"if result[j] < result[j + 1]:","new_text":"if result[j] > result[j + 1]:"}}</tool>',
                '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
                "<final>Fixed bubble_sort ordering after critic feedback and verified tests.</final>",
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


def interim_result(task_id, metadata, public_result, final_answer, agent, repo, critic_retries_used=0):
    tool_calls = [item for item in agent.session["history"] if item.get("role") == "tool"]
    return {
        "task_id": task_id,
        "type": metadata.get("type", ""),
        "difficulty": metadata.get("difficulty", ""),
        "tags": metadata.get("tags", []),
        "pass_public": public_result["exit_code"] == 0,
        "pass_hidden": False,
        "public_result": public_result,
        "hidden_result": public_result,
        "tool_calls": len(tool_calls),
        "tools": [item.get("name") for item in tool_calls],
        "final_answer": final_answer,
        "critic_retries_used": critic_retries_used,
        "session_id": agent.session["id"],
        "workdir": str(repo),
    }


def critic_retry_prompt(analysis, public_result, attempt, max_retries):
    decision = analysis.get("decision", {})
    return "\n".join(
        [
            "Critic feedback for an online retry.",
            f"Retry attempt: {attempt}/{max_retries}",
            "The previous fix attempt did not pass the public test command.",
            "Use the diagnosis below to continue the same task. Prefer the suggested next action, keep edits minimal, and rerun tests.",
            "",
            "Diagnosis JSON:",
            json.dumps(analysis, indent=2, ensure_ascii=False),
            "",
            "Decision policy:",
            f"next_action={decision.get('next_action', analysis.get('next_action'))}",
            f"target_file={decision.get('target_file', analysis.get('target_file')) or '(not required)'}",
            f"allowed_tools={', '.join(decision.get('allowed_tools', analysis.get('allowed_tools', []))) or '(none)'}",
            f"risk_level={decision.get('risk_level', analysis.get('risk_level', 'unknown'))}",
            f"abstain={decision.get('abstain', analysis.get('abstain', False))}",
            "Important: next_action is a semantic decision label, never a tool name. "
            "Use the exact names listed in allowed_tools; execute tests with run_tests.",
            "",
            "Latest public test output:",
            (public_result.get("stdout", "") + public_result.get("stderr", ""))[:4000] or "(empty)",
        ]
    )


def retry_phase_prompt(feedback, phase, allowed_tools, required_tools=None):
    required_tools = list(required_tools or [])
    if phase == "diagnose":
        instruction = (
            "You are in the DIAGNOSIS phase. Do not make source-code edits in this phase. "
            "Use only the allowed tools to collect the specific evidence requested by the Critic."
        )
    elif phase == "edit":
        instruction = (
            "You are in the EDIT phase. Make exactly one minimal source-code edit based on the diagnosis. "
            "Do not run tests or provide a prose final answer in this phase."
        )
    else:
        instruction = (
            "You are in the VERIFY phase. Run the public test command exactly once. "
            "Do not edit code in this phase; the next transition depends on this observation."
        )
    return "\n".join(
        [
            feedback,
            "",
            f"Controlled retry phase: {phase}",
            instruction,
            f"Hard allowed_tools whitelist: {', '.join(allowed_tools) or '(none)'}",
            f"Required tools for this phase: {', '.join(required_tools) or '(none beyond the whitelist)'}",
        ]
    )


def phase_tool_names(agent, start_index):
    return [
        item.get("name")
        for item in agent.session["history"][start_index:]
        if item.get("role") == "tool"
    ]


def run_retry_phase(agent, phase, prompt, allowed_tools, max_steps, require_edit=False, required_tools=None):
    history_start = len(agent.session["history"])
    started = time.monotonic()
    final_answer = agent.ask_with_allowed_tools(
        prompt,
        allowed_tools,
        max_steps=max_steps,
        require_tool=bool(allowed_tools),
        require_edit=require_edit,
        required_tools=required_tools or set(),
    )
    tool_calls = phase_tool_names(agent, history_start)
    valid_tool_calls = [name for name in tool_calls if name in allowed_tools]
    tool_requirement_met = bool(valid_tool_calls) if allowed_tools else True
    edit_requirement_met = not require_edit or bool(set(valid_tool_calls).intersection({"write_file", "patch_file", "apply_patch"}))
    required_tools = set(required_tools or [])
    required_tools_met = not required_tools or required_tools.issubset(valid_tool_calls)
    test_requirement_met = "run_tests" not in required_tools or "run_tests" in valid_tool_calls
    return {
        "phase": phase,
        "allowed_tools": list(allowed_tools),
        "tool_calls": tool_calls,
        "valid_tool_calls": valid_tool_calls,
        "tool_requirement_met": tool_requirement_met,
        "edit_requirement_met": edit_requirement_met,
        "required_tools_met": required_tools_met,
        "test_requirement_met": test_requirement_met,
        "phase_valid": tool_requirement_met and edit_requirement_met and required_tools_met,
        "final_answer": final_answer,
        "duration_sec": round(time.monotonic() - started, 3),
    }


def collect_harness_evidence(agent, metadata):
    """Record deterministic code/diff evidence without relying on model tool selection."""
    expected_files = list(metadata.get("expected_files", []))
    tool_requests = []
    if expected_files:
        tool_requests.append(("read_file", {"path": expected_files[0], "start": 1, "end": 240}))
    tool_requests.append(("git_diff", {"path": "."}))

    collected = []
    for name, args in tool_requests:
        result = agent.run_tool(name, args)
        agent.record(
            {
                "role": "tool",
                "name": name,
                "args": args,
                "content": result,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "harness",
            }
        )
        agent.note_tool(name, args, result)
        collected.append({"name": name, "args": args, "content": result})

    tool_calls = [item["name"] for item in collected]
    return {
        "phase": "diagnose",
        "source": "harness",
        "allowed_tools": tool_calls,
        "tool_calls": tool_calls,
        "valid_tool_calls": tool_calls,
        "tool_requirement_met": bool(tool_calls),
        "edit_requirement_met": True,
        "required_tools_met": {"read_file", "git_diff"}.issubset(tool_calls),
        "test_requirement_met": True,
        "phase_valid": {"read_file", "git_diff"}.issubset(tool_calls),
        "evidence": collected,
        "final_answer": "Harness collected target-file and diff evidence.",
        "duration_sec": 0.0,
    }


def diagnosis_tools_for(analysis):
    """Keep diagnosis read-only even when a broad policy also permits repairs."""
    decision = analysis.get("decision", {})
    allowed = decision.get("allowed_tools", analysis.get("allowed_tools", []))
    safe_tools = {"list_files", "read_file", "search", "git_diff", "run_tests"}
    return [name for name in allowed if name in safe_tools]


def retry_strategy_for(analysis, attempt, retry_policy=None):
    """Convert a diagnosis into an explicit retry strategy for reporting and control."""
    retry_policy = retry_policy or DEFAULT_RETRY_POLICY
    decision = analysis.get("decision", {})
    failure_type = analysis.get("failure_type", "unknown")
    diagnose_tools = diagnosis_tools_for(analysis)
    required_diagnosis_tools = sorted(diagnosis_required_tools_for(analysis, attempt, retry_policy))
    evidence_gathered = bool(required_diagnosis_tools)
    edit_tools = edit_tools_for(analysis, evidence_gathered=evidence_gathered)
    return {
        "failure_type": failure_type,
        "next_action": decision.get("next_action", analysis.get("next_action", "gather_evidence")),
        "risk_level": decision.get("risk_level", analysis.get("risk_level", "medium")),
        "abstain": bool(decision.get("abstain", analysis.get("abstain", False))),
        "diagnose_tools": diagnose_tools,
        "required_diagnosis_tools": required_diagnosis_tools,
        "edit_tools": edit_tools,
        "verify_tools": ["run_tests"],
        "rollback_on_reject": bool(retry_policy.get("rollback_on_reject", True)),
        "accept_if": list(retry_policy.get("accept_if", [])),
        "reject_if": list(retry_policy.get("reject_if", [])),
    }


def diagnosis_required_tools_for(analysis, attempt, retry_policy=None):
    """Require a concrete code/diff comparison after a failed controlled retry."""
    retry_policy = retry_policy or DEFAULT_RETRY_POLICY
    failure_type = analysis.get("failure_type", "")
    min_attempt = int(retry_policy.get("require_harness_evidence_after_attempt", 2))
    evidence_failures = set(retry_policy.get("require_harness_evidence_for", []))
    if attempt >= min_attempt and failure_type in evidence_failures:
        return {"read_file", "git_diff"}
    return set()


def edit_tools_for(analysis, evidence_gathered=False):
    decision = analysis.get("decision", {})
    failure_type = analysis.get("failure_type", "")
    if decision.get("abstain", analysis.get("abstain", False)) and not (
        evidence_gathered and failure_type == "test_failure"
    ):
        return []
    return ["patch_file", "apply_patch", "write_file"]


def run_task(task_root, args, run_root):
    retry_policy = getattr(args, "retry_policy_config", DEFAULT_RETRY_POLICY)
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
    critic_retries = []
    for attempt in range(1, args.critic_retries + 1):
        if public_result["exit_code"] == 0:
            break
        partial = interim_result(
            task_id,
            metadata,
            public_result,
            final_answer,
            agent,
            repo,
            critic_retries_used=len(critic_retries),
        )
        analysis = classify_failure(partial, agent.training_trajectory_data(), metadata)
        strategy = retry_strategy_for(analysis, attempt, retry_policy)
        feedback = critic_retry_prompt(analysis, public_result, attempt, args.critic_retries)
        diagnose_tools = strategy["diagnose_tools"]
        diagnose_required_tools = set(strategy["required_diagnosis_tools"])
        if diagnose_required_tools:
            phases = [collect_harness_evidence(agent, metadata)]
            evidence_text = "\n\n".join(
                f"{item['name']} {json.dumps(item['args'])}:\n{item['content']}"
                for item in phases[0]["evidence"]
            )
            feedback += "\n\nHarness-collected evidence for this follow-up:\n" + evidence_text[:6000]
        else:
            phases = [
                run_retry_phase(
                    agent,
                    "diagnose",
                    retry_phase_prompt(feedback, "diagnose", diagnose_tools, diagnose_required_tools),
                    diagnose_tools,
                    max_steps=int(retry_policy.get("diagnose_max_steps", 1)),
                    required_tools=diagnose_required_tools,
                )
            ]
        status = "diagnosis_failed"
        candidate = None
        if not phases[0]["phase_valid"]:
            final_answer = phases[-1]["final_answer"]
        else:
            public_result = run_command(check_command, repo, args.test_timeout)
        if phases[0]["phase_valid"] and public_result["exit_code"] != 0:
            edit_tools = strategy["edit_tools"]
            if edit_tools:
                before_edit_result = public_result
                snapshot = create_retry_snapshot(
                    repo,
                    workdir / "retry_snapshots" / f"attempt_{attempt:02d}",
                )
                phases.append(
                    run_retry_phase(
                        agent,
                        "edit",
                        retry_phase_prompt(feedback, "edit", edit_tools),
                        edit_tools,
                        max_steps=int(
                            retry_policy.get("edit_max_steps")
                            or min(agent.max_steps, max(1, len(metadata.get("expected_files", []))))
                        ),
                        require_edit=True,
                    )
                )
                final_answer = phases[-1]["final_answer"]
                if not phases[-1]["phase_valid"]:
                    candidate_patch = patch_quality(repo, metadata)
                    if retry_policy.get("rollback_on_reject", True):
                        restore_retry_snapshot(snapshot, repo)
                    candidate = {
                        "accepted": False,
                        "reason": "edit_phase_invalid",
                        "rollback_applied": bool(retry_policy.get("rollback_on_reject", True)),
                        "snapshot": str(snapshot.relative_to(workdir)).replace("\\", "/"),
                        "before_public_result": before_edit_result,
                        "candidate_public_result": None,
                    }
                    candidate["patch_quality"] = candidate_patch
                    candidate["patch_score"] = candidate_patch_score(candidate, candidate_patch, phases[-1]["duration_sec"])
                    public_result = before_edit_result
                    status = "edit_failed"
                else:
                    phases.append(
                        run_retry_phase(
                            agent,
                            "verify",
                            retry_phase_prompt(feedback, "verify", ["run_tests"], {"run_tests"}),
                            ["run_tests"],
                            max_steps=int(retry_policy.get("verify_max_steps", 1)),
                            required_tools={"run_tests"},
                        )
                    )
                    final_answer = phases[-1]["final_answer"]
                    candidate_public_result = run_command(check_command, repo, args.test_timeout)
                    candidate = assess_candidate(before_edit_result, candidate_public_result, retry_policy)
                    candidate.update(
                        {
                            "rollback_applied": False,
                            "snapshot": str(snapshot.relative_to(workdir)).replace("\\", "/"),
                            "before_public_result": before_edit_result,
                            "candidate_public_result": candidate_public_result,
                        }
                    )
                    if not phases[-1]["phase_valid"]:
                        candidate["accepted"] = False
                        candidate["reason"] = "verify_phase_invalid"
                    candidate_patch = patch_quality(repo, metadata)
                    candidate["patch_quality"] = candidate_patch
                    candidate["patch_score"] = candidate_patch_score(candidate, candidate_patch, phases[-1]["duration_sec"])
                    if candidate["accepted"]:
                        public_result = candidate_public_result
                        status = "completed" if public_result["exit_code"] == 0 else "candidate_improved"
                    else:
                        if retry_policy.get("rollback_on_reject", True):
                            restore_retry_snapshot(snapshot, repo)
                        candidate["rollback_applied"] = bool(retry_policy.get("rollback_on_reject", True))
                        public_result = before_edit_result
                        status = "verify_failed" if not phases[-1]["phase_valid"] else "candidate_rejected"
            else:
                final_answer = phases[-1]["final_answer"]
                status = "edit_abstained"
        else:
            final_answer = phases[-1]["final_answer"]
            if phases[0]["phase_valid"]:
                status = "recovered_in_diagnosis"
        retry_duration = round(sum(phase["duration_sec"] for phase in phases), 3)
        critic_retries.append(
            {
                "attempt": attempt,
                "failure_analysis": analysis,
                "strategy": strategy,
                "phases": phases,
                "status": status,
                "public_result_after_retry": public_result,
                "candidate": candidate,
                "agent_duration_sec": retry_duration,
            }
        )
        if status not in {"completed", "candidate_improved", "candidate_rejected"}:
            break
    agent_duration = round(time.monotonic() - started, 3)
    copy_tree_contents(task_root / "hidden_tests", repo / "tests")
    hidden_result = run_command(check_command, repo, args.test_timeout)
    patch = patch_quality(repo, metadata)

    trajectory_path = repo / "trajectories" / f"{agent.session['id']}.json"
    agent.export_trajectory(str(trajectory_path.relative_to(repo)))
    training_trajectory_path = repo / "trajectories" / f"{agent.session['id']}.training.json"
    agent.export_training_trajectory(str(training_trajectory_path.relative_to(repo)))
    tool_calls = [item for item in agent.session["history"] if item.get("role") == "tool"]
    result = {
        "task_id": task_id,
        "type": metadata.get("type", ""),
        "difficulty": metadata.get("difficulty", ""),
        "tags": metadata.get("tags", []),
        "git_initialized": git_initialized,
        "pass_public": public_result["exit_code"] == 0,
        "pass_hidden": hidden_result["exit_code"] == 0,
        "public_result": public_result,
        "hidden_result": hidden_result,
        "tool_calls": len(tool_calls),
        "tools": [item.get("name") for item in tool_calls],
        "agent_duration_sec": agent_duration,
        "cost": {
            "tool_calls": len(tool_calls),
            "critic_retries_used": len(critic_retries),
            "agent_duration_sec": agent_duration,
            "public_test_duration_sec": public_result.get("duration_sec", 0),
            "hidden_test_duration_sec": hidden_result.get("duration_sec", 0),
            "retry_duration_sec": round(sum(item.get("agent_duration_sec", 0) for item in critic_retries), 3),
            "total_wall_time_sec": round(
                agent_duration
                + float(public_result.get("duration_sec", 0))
                + float(hidden_result.get("duration_sec", 0)),
                3,
            ),
        },
        "patch_quality": patch,
        "critic_retries_used": len(critic_retries),
        "critic_retries": critic_retries,
        "final_answer": final_answer,
        "session_id": agent.session["id"],
        "trajectory": str(trajectory_path.relative_to(repo)).replace("\\", "/"),
        "training_trajectory": str(training_trajectory_path.relative_to(repo)).replace("\\", "/"),
        "workdir": str(repo),
    }
    result["failure_analysis"] = classify_failure(result, agent.training_trajectory_data(), metadata)
    result["score"] = score_result(result, metadata)

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
        "avg_agent_duration_sec": round(sum(item.get("cost", {}).get("agent_duration_sec", 0) for item in results) / total, 2) if total else 0,
        "avg_total_wall_time_sec": round(sum(item.get("cost", {}).get("total_wall_time_sec", 0) for item in results) / total, 2) if total else 0,
        "avg_patch_changed_files": round(sum(item.get("patch_quality", {}).get("changed_files", 0) for item in results) / total, 2) if total else 0,
        "avg_patch_changed_lines": round(sum(item.get("patch_quality", {}).get("changed_lines", 0) for item in results) / total, 2) if total else 0,
        "avg_score": round(sum(item.get("score", {}).get("total", 0) for item in results) / total, 2) if total else 0,
        "failure_types": failure_counts(results),
    }


def failure_counts(results):
    counts = {}
    for item in results:
        failure_type = item.get("failure_analysis", {}).get("failure_type", "unknown")
        counts[failure_type] = counts.get(failure_type, 0) + 1
    return counts


def mean_and_std(values):
    values = [float(value) for value in values]
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(mean, 4), round(variance**0.5, 4)


def summarize_repeated_runs(runs):
    """Aggregate repeated benchmark runs without hiding their per-run variation."""
    summaries = [run["summary"] for run in runs]
    aggregate = {"repeat": len(summaries), "tasks_per_run": summaries[0]["total"] if summaries else 0}
    for field in (
        "public_pass_rate",
        "hidden_pass_rate",
        "avg_tool_calls",
        "avg_agent_duration_sec",
        "avg_total_wall_time_sec",
        "avg_patch_changed_files",
        "avg_patch_changed_lines",
        "avg_score",
    ):
        mean, std = mean_and_std([summary[field] for summary in summaries])
        aggregate[f"mean_{field}"] = mean
        aggregate[f"std_{field}"] = std
    return aggregate


def most_common_label(counts):
    if not counts:
        return "none"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def retry_statuses(result):
    statuses = [item.get("status", "") for item in result.get("critic_retries", [])]
    return ", ".join(status for status in statuses if status) or "-"


def summarize_task_across_runs(task_results):
    failure_counts_by_type = {}
    retry_status_counts = {}
    for result in task_results:
        failure_type = result.get("failure_analysis", {}).get("failure_type", "unknown")
        failure_counts_by_type[failure_type] = failure_counts_by_type.get(failure_type, 0) + 1
        for status in retry_statuses(result).split(", "):
            if status and status != "-":
                retry_status_counts[status] = retry_status_counts.get(status, 0) + 1
    total = len(task_results)
    return {
        "task_id": task_results[0]["task_id"],
        "difficulty": task_results[0].get("difficulty", ""),
        "public_pass": sum(1 for item in task_results if item.get("pass_public")),
        "hidden_pass": sum(1 for item in task_results if item.get("pass_hidden")),
        "total": total,
        "avg_score": round(sum(item.get("score", {}).get("total", 0) for item in task_results) / total, 2) if total else 0,
        "avg_tool_calls": round(sum(item.get("tool_calls", 0) for item in task_results) / total, 2) if total else 0,
        "avg_patch_lines": round(sum(item.get("patch_quality", {}).get("changed_lines", 0) for item in task_results) / total, 2) if total else 0,
        "avg_wall_time": round(sum(item.get("cost", {}).get("total_wall_time_sec", 0) for item in task_results) / total, 2) if total else 0,
        "failure_type": most_common_label(failure_counts_by_type),
        "retry_status": most_common_label(retry_status_counts) if retry_status_counts else "-",
    }


def per_task_rows(report):
    if report["schema"] == "mini-coding-agent.benchmark.v2":
        grouped = {}
        for run in report.get("runs", []):
            for result in run.get("results", []):
                grouped.setdefault(result["task_id"], []).append(result)
        return [summarize_task_across_runs(items) for _, items in sorted(grouped.items())]
    rows = []
    for result in report.get("results", []):
        rows.append(
            {
                "task_id": result["task_id"],
                "difficulty": result.get("difficulty", ""),
                "public_pass": 1 if result.get("pass_public") else 0,
                "hidden_pass": 1 if result.get("pass_hidden") else 0,
                "total": 1,
                "avg_score": result.get("score", {}).get("total", 0),
                "avg_tool_calls": result.get("tool_calls", 0),
                "avg_patch_lines": result.get("patch_quality", {}).get("changed_lines", 0),
                "avg_wall_time": result.get("cost", {}).get("total_wall_time_sec", 0),
                "failure_type": result.get("failure_analysis", {}).get("failure_type", "unknown"),
                "retry_status": retry_statuses(result),
            }
        )
    return sorted(rows, key=lambda item: item["task_id"])


def write_per_task_report(report, path):
    rows = per_task_rows(report)
    summary = report.get("summary", {})
    lines = [
        "# Benchmark Per-Task Diagnosis",
        "",
        f"- schema: `{report.get('schema')}`",
        f"- provider/model: `{report.get('provider')}` / `{report.get('model')}`",
        f"- created_at: `{report.get('created_at')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## Per-Task Table",
            "",
            "| Task | Difficulty | Public | Hidden | Avg Score | Avg Tools | Patch Lines | Wall Time | Failure Type | Retry Status |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        public = f"{row['public_pass']}/{row['total']}"
        hidden = f"{row['hidden_pass']}/{row['total']}"
        lines.append(
            "| {task_id} | {difficulty} | {public} | {hidden} | {avg_score} | {avg_tool_calls} | {avg_patch_lines} | {avg_wall_time} | {failure_type} | {retry_status} |".format(
                task_id=row["task_id"],
                difficulty=row["difficulty"] or "-",
                public=public,
                hidden=hidden,
                avg_score=row["avg_score"],
                avg_tool_calls=row["avg_tool_calls"],
                avg_patch_lines=row["avg_patch_lines"],
                avg_wall_time=row["avg_wall_time"],
                failure_type=row["failure_type"],
                retry_status=row["retry_status"],
            )
        )
    hard_rows = [row for row in rows if row["difficulty"] == "hard"]
    if hard_rows:
        lines.extend(["", "## Hard Task Case Study", ""])
        for row in sorted(hard_rows, key=lambda item: (item["hidden_pass"] / item["total"], item["avg_score"])):
            lines.append(
                "- `{}`: hidden {}/{}, score {}, failure `{}`, retry `{}`, patch lines {}, wall time {}s".format(
                    row["task_id"],
                    row["hidden_pass"],
                    row["total"],
                    row["avg_score"],
                    row["failure_type"],
                    row["retry_status"],
                    row["avg_patch_lines"],
                    row["avg_wall_time"],
                )
            )
    weak_rows = [
        row for row in rows
        if row["hidden_pass"] < row["total"] or row["failure_type"] not in {"none", "-"}
    ]
    if weak_rows:
        lines.extend(["", "## Tasks To Inspect First", ""])
        for row in sorted(weak_rows, key=lambda item: (item["hidden_pass"] / item["total"], item["avg_score"])):
            lines.append(
                "- `{}`: hidden {}/{}, failure `{}`, retry `{}`".format(
                    row["task_id"],
                    row["hidden_pass"],
                    row["total"],
                    row["failure_type"],
                    row["retry_status"],
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    parser.add_argument("--fake-agent-retry-success", action="store_true", help="Use a fake model that only succeeds after critic retry feedback.")
    parser.add_argument("--critic-retries", type=int, default=0, help="Number of public-test failure retries guided by critic feedback.")
    parser.add_argument("--retry-policy", default=None, help="Optional JSON file overriding retry accept/reject/rollback policy.")
    parser.add_argument("--repeat", type=int, default=5, help="Repeat the full task set and report mean/std metrics.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1.")
    args.retry_policy_config = load_retry_policy(args.retry_policy)
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

    provider = "fake" if args.fake_agent_success or args.fake_agent_retry_success else args.provider
    if args.repeat == 1:
        results = [run_task(task, args, run_root) for task in tasks]
        report = {
            "schema": "mini-coding-agent.benchmark.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_root": str(benchmark_root),
            "workdir": str(run_root),
            "provider": provider,
            "model": args.model,
            "summary": summarize(results),
            "results": results,
        }
    else:
        runs = []
        for index in range(1, args.repeat + 1):
            repeat_root = run_root / f"repeat_{index:02d}"
            results = [run_task(task, args, repeat_root) for task in tasks]
            runs.append(
                {
                    "index": index,
                    "workdir": str(repeat_root),
                    "summary": summarize(results),
                    "results": results,
                }
            )
        report = {
            "schema": "mini-coding-agent.benchmark.v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_root": str(benchmark_root),
            "workdir": str(run_root),
            "provider": provider,
            "model": args.model,
            "summary": summarize_repeated_runs(runs),
            "runs": runs,
        }
    report_path = out_dir / f"run_{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    per_task_report_path = report_path.with_suffix(".per_task.md")
    write_per_task_report(report, per_task_report_path)

    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"wrote {report_path}")
    print(f"wrote {per_task_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
