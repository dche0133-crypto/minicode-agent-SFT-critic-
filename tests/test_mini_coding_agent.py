import json
import os
import pytest
from unittest.mock import patch

from mini_coding_agent import (
    DeepSeekModelClient,
    FakeModelClient,
    MiniAgent,
    OllamaModelClient,
    SessionStore,
    WorkspaceContext,
    build_welcome,
    load_env_file,
)


def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".mini-coding-agent" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


def test_agent_runs_tool_then_final(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Read the file successfully.</final>",
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Read the file successfully."
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])
    assert "hello.txt" in agent.session["memory"]["files"]


def test_agent_retries_after_empty_model_output(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            "<final>Recovered after retry.</final>",
        ],
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after retry."
    notices = [item["content"] for item in agent.session["history"] if item["role"] == "assistant"]
    assert any("empty response" in item for item in notices)


def test_agent_retries_after_malformed_tool_payload(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":"bad"}</tool>',
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
            "<final>Recovered after malformed tool output.</final>",
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Recovered after malformed tool output."
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])
    notices = [item["content"] for item in agent.session["history"] if item["role"] == "assistant"]
    assert any("valid <tool> call" in item for item in notices)


def test_agent_accepts_xml_write_file_tool(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool name="write_file" path="hello.py"><content>print("hi")\n</content></tool>',
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Create hello.py")

    assert answer == "Done."
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hi")\n'


def test_write_file_strips_outer_markdown_code_fence(tmp_path):
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "write_file",
        {"path": "hello.py", "content": "```python\nprint('hi')\n```"},
    )

    assert result.startswith("wrote hello.py")
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hi')"


def test_temporary_tool_whitelist_blocks_unapproved_tool(tmp_path):
    agent = build_agent(
        tmp_path,
        ['<tool>{"name":"write_file","args":{"path":"blocked.py","content":"x"}}</tool>'],
    )

    result = agent.ask_with_allowed_tools(
        "Run only tests.",
        ["run_tests"],
        max_steps=1,
    )

    assert "step limit" in result
    tool_event = next(item for item in agent.session["history"] if item["role"] == "tool")
    assert "blocked by the current retry policy" in tool_event["content"]
    assert not (tmp_path / "blocked.py").exists()
    assert agent.active_allowed_tools is None


def test_required_tool_retries_after_a_prose_response(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>I would run tests next.</final>",
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
        ],
    )
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")

    agent.ask_with_allowed_tools("Inspect hello.txt.", ["read_file"], max_steps=1, require_tool=True)

    assert any(item.get("role") == "tool" and item.get("name") == "read_file" for item in agent.session["history"])


def test_retries_do_not_consume_the_whole_budget(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            "",
            "<final>Recovered after several retries.</final>",
        ],
        max_steps=1,
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after several retries."


def test_agent_saves_and_resumes_session(tmp_path):
    agent = build_agent(tmp_path, ["<final>First pass.</final>"])
    assert agent.ask("Start a session") == "First pass."

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.session["history"][0]["content"] == "Start a session"
    assert resumed.ask("Continue") == "Resumed."


def test_session_title_is_created_from_first_user_message(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])

    agent.ask("请总结 README 并说明工具调用")

    assert agent.session["title"] == "请总结 README 并说明工具调用"
    assert "请总结 README" in agent.session_store.path(agent.session["id"]).read_text(encoding="utf-8")


def test_history_and_last_display_recorded_events(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    agent.ask("Inspect README")

    history = agent.history_display_text()
    last = agent.last_text()

    assert "History: showing" in history
    assert "user: Inspect README" in history
    assert "tool:read_file" in history
    assert "Last:" in last
    assert "assistant: Done." in last


def test_sessions_text_and_runtime_resume(tmp_path):
    first = build_agent(tmp_path, ["<final>First.</final>"])
    first.ask("First task")
    first_id = first.session["id"]

    second = build_agent(tmp_path, ["<final>Second.</final>"])
    second.ask("Second task")

    listing = second.sessions_text()
    assert first_id in listing
    assert "First task" in listing
    assert "*" in listing

    result = second.resume_session(first_id)

    assert result.startswith(f"resumed {first_id}")
    assert second.session["title"] == "First task"
    assert second.session["history"][0]["content"] == "First task"


def test_compact_moves_history_summary_to_memory_and_clears_history(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )
    agent.ask("Inspect README")

    result = agent.compact()

    assert "compacted" in result
    assert "read_file x1" in result
    assert agent.session["history"] == []
    assert any("compacted" in note for note in agent.session["memory"]["notes"])


def test_delegate_uses_child_agent(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"delegate","args":{"task":"inspect README","max_steps":2}}</tool>',
            "<final>Child result.</final>",
            "<final>Parent incorporated the child result.</final>",
        ],
    )

    answer = agent.ask("Use delegation")

    assert answer == "Parent incorporated the child result."
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "delegate"
    assert "delegate_result" in tool_events[0]["content"]


def test_patch_file_replaces_exact_match(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "patch_file",
        {
            "path": "sample.txt",
            "old_text": "world",
            "new_text": "agent",
        },
    )

    assert result == "patched sample.txt"
    assert file_path.read_text(encoding="utf-8") == "hello agent\n"


def test_run_tests_tool_runs_test_command(tmp_path):
    agent = build_agent(tmp_path, [])

    class FakeCompleted:
        returncode = 0
        stdout = "passed"
        stderr = ""

    with patch("subprocess.run", return_value=FakeCompleted()) as mock_run:
        result = agent.run_tool("run_tests", {"command": "python -m pytest -q", "timeout": 120})

    assert "exit_code: 0" in result
    assert "passed" in result
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["cwd"] == agent.root
    assert mock_run.call_args.kwargs["timeout"] == 120


def test_git_diff_tool_runs_git_diff_for_path(tmp_path):
    agent = build_agent(tmp_path, [])

    class FakeCompleted:
        returncode = 0
        stdout = "diff --git a/README.md b/README.md"
        stderr = ""

    with patch("subprocess.run", return_value=FakeCompleted()) as mock_run:
        result = agent.run_tool("git_diff", {"path": "README.md"})

    assert "diff --git" in result
    assert mock_run.call_args.args[0] == ["git", "diff", "--", "README.md"]


def test_rollback_tool_uses_git_restore(tmp_path):
    agent = build_agent(tmp_path, [])

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch("subprocess.run", return_value=FakeCompleted()) as mock_run:
        result = agent.run_tool("rollback", {"path": "README.md"})

    assert "exit_code: 0" in result
    assert mock_run.call_args.args[0] == ["git", "restore", "--", "README.md"]


def test_apply_patch_tool_uses_git_apply(tmp_path):
    agent = build_agent(tmp_path, [])
    patch_text = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-demo\n+demo updated\n"

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch("subprocess.run", return_value=FakeCompleted()) as mock_run:
        result = agent.run_tool("apply_patch", {"patch": patch_text})

    assert "exit_code: 0" in result
    assert mock_run.call_args.args[0] == ["git", "apply", "--whitespace=nowarn", "-"]
    assert mock_run.call_args.kwargs["input"] == patch_text


def test_apply_patch_rejects_parent_escape(tmp_path):
    agent = build_agent(tmp_path, [])
    patch_text = "diff --git a/../evil.py b/../evil.py\n--- a/../evil.py\n+++ b/../evil.py\n@@ -1 +1 @@\n-a\n+b\n"

    result = agent.run_tool("apply_patch", {"patch": patch_text})

    assert result.startswith("error: invalid arguments for apply_patch")
    assert "escapes workspace" in result


def test_critic_json_reports_latest_test_failure(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.record(
        {
            "role": "tool",
            "name": "run_tests",
            "args": {"command": "python -m pytest -q"},
            "content": "exit_code: 1\nstdout:\nFAILED tests/test_demo.py::test_x\nE AssertionError\nstderr:\n(empty)",
            "created_at": "1",
        }
    )

    data = json.loads(agent.critic_json())

    assert data["failure_type"] == "assertion_failure"
    assert data["next_action"] == "edit_file"
    assert data["target_file"] == "tests/test_demo.py"


def test_test_fix_prompt_contains_fixed_workflow():
    prompt = MiniAgent.test_fix_prompt("python -m pytest tests -q")

    assert "Use run_tests with command: python -m pytest tests -q" in prompt
    assert "Apply the smallest fix" in prompt
    assert "Use git_diff" in prompt


def test_export_trajectory_writes_redacted_dataset(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")
    agent.record({"role": "user", "content": "Use secret-test-key carefully", "created_at": "1"})
    agent.record(
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": ".env"},
            "content": "DEEPSEEK_API_KEY=secret-test-key",
            "created_at": "2",
        }
    )

    result = agent.export_trajectory()

    assert result.startswith("exported trajectory to trajectories/")
    exported = tmp_path / "trajectories" / f"{agent.session['id']}.json"
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["schema"] == "mini-coding-agent.trajectory.v1"
    assert data["steps"][0]["content"] == "Use [REDACTED:DEEPSEEK_API_KEY] carefully"
    assert "secret-test-key" not in exported.read_text(encoding="utf-8")


def test_export_training_trajectory_normalizes_steps(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.session["memory"]["task"] = "Fix tests"
    agent.record({"role": "user", "content": "Fix tests", "created_at": "1"})
    agent.record(
        {
            "role": "tool",
            "name": "run_tests",
            "args": {"command": "python -m pytest -q"},
            "content": "exit_code: 1\nstdout:\nFAILED tests/test_demo.py::test_x\nstderr:\n(empty)",
            "created_at": "2",
        }
    )
    agent.record(
        {
            "role": "tool",
            "name": "git_diff",
            "args": {"path": "."},
            "content": "diff --git a/demo.py b/demo.py",
            "created_at": "3",
        }
    )
    agent.record({"role": "assistant", "content": "Need a fix.", "created_at": "4"})

    result = agent.export_training_trajectory()

    assert result.startswith("exported training trajectory to trajectories/")
    exported = tmp_path / "trajectories" / f"{agent.session['id']}.training.json"
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["schema"] == "mini-coding-agent.training-trajectory.v1"
    assert data["task"] == "Fix tests"
    assert data["steps"][0]["action"] == "run_tests"
    assert data["steps"][0]["success"] is False
    assert data["steps"][1]["action"] == "git_diff"
    assert data["steps"][1]["diff"] == "diff --git a/demo.py b/demo.py"
    assert data["steps"][2]["action"] == "final"


def test_invalid_risky_tool_does_not_prompt_for_approval(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="ask")

    with patch("builtins.input") as mock_input:
        result = agent.run_tool("write_file", {})

    assert result.startswith("error: invalid arguments for write_file: 'path'")
    assert 'example: <tool name="write_file"' in result
    mock_input.assert_not_called()


def test_list_files_hides_internal_agent_state(tmp_path):
    agent = build_agent(tmp_path, [])
    (tmp_path / ".mini-coding-agent").mkdir(exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")

    result = agent.run_tool("list_files", {})

    assert ".mini-coding-agent" not in result
    assert ".git" not in result
    assert "[F] hello.txt" in result


def test_tools_text_lists_tools_and_risk_levels(tmp_path):
    agent = build_agent(tmp_path, [])

    result = agent.tools_text()

    assert "- list_files(" in result
    assert "- write_file(" in result
    assert "[safe]" in result
    assert "[approval required]" in result


def test_path_rejects_parent_escape(tmp_path):
    agent = build_agent(tmp_path, [])

    with pytest.raises(ValueError, match="path escapes workspace"):
        agent.path("../outside.txt")


def test_path_rejects_symlink_escape(tmp_path):
    agent = build_agent(tmp_path, [])
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this environment")

    with pytest.raises(ValueError, match="path escapes workspace"):
        agent.path("outside-link/secret.txt")


def test_path_accepts_case_variant_on_case_insensitive_filesystems(tmp_path):
    project_root = tmp_path / "Proj"
    project_root.mkdir()
    agent = build_agent(project_root, [])
    variant = project_root.parent / project_root.name.lower() / "README.md"

    if not variant.exists():
        pytest.skip("case-sensitive filesystem")

    resolved = agent.path(str(variant))

    assert resolved.samefile(project_root / "README.md")


def test_repeated_identical_tool_call_is_rejected(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "1"})
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "2"})

    result = agent.run_tool("list_files", {})

    assert result == "error: repeated identical tool call for list_files; choose a different tool or return a final answer"


def test_welcome_screen_keeps_box_shape_for_long_paths(tmp_path):
    deep = tmp_path / "very" / "long" / "path" / "for" / "the" / "mini" / "agent" / "welcome" / "screen"
    deep.mkdir(parents=True)
    agent = build_agent(deep, [])

    welcome = build_welcome(agent, model="qwen3.5:4b", host="http://127.0.0.1:11434")
    lines = welcome.splitlines()

    assert len(lines) >= 5
    assert len({len(line) for line in lines}) == 1
    assert "..." in welcome
    assert "O   O" in welcome
    assert "MINI-CODING-AGENT" not in welcome
    assert "MINI CODING AGENT" in welcome
    assert "// READY" not in welcome
    assert "SLASH" not in welcome
    assert "READY      " not in welcome
    assert "commands: Commands:" not in welcome


def test_prompt_top_level_sections_stay_flush_left_with_multiline_content(tmp_path):
    workspace = WorkspaceContext(
        cwd=str(tmp_path),
        repo_root=str(tmp_path),
        branch="fix/prompt-indentation",
        default_branch="main",
        status=" M mini_coding_agent.py\n?? tests/test_prompt.py",
        recent_commits=["abc123 first commit", "def456 second commit"],
        project_docs={"README.md": "line1\nline2"},
    )
    store = SessionStore(tmp_path / ".mini-coding-agent" / "sessions")
    agent = MiniAgent(
        model_client=FakeModelClient([]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )
    agent.session["memory"] = {
        "task": "verify prompt formatting",
        "files": ["mini_coding_agent.py"],
        "notes": ["saw inconsistent indentation", "need regression coverage"],
    }
    agent.record({"role": "user", "content": "inspect prompt()", "created_at": "1"})
    agent.record(
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "mini_coding_agent.py"},
            "content": "    def prompt(self, user_message):\n        ...",
            "created_at": "2",
        }
    )

    prompt = agent.prompt("is this issue legit?")
    lines = prompt.splitlines()

    for label in ["Rules:", "Tools:", "Valid response examples:", "Workspace:", "Memory:", "Transcript:", "Current user request:"]:
        assert label in lines
        assert f"            {label}" not in prompt


def _make_filler(i):
    return {"role": "tool", "name": "list_files", "args": {}, "content": "", "created_at": str(i)}


def test_history_text_deduplicates_reads_but_not_after_write(tmp_path):
    """read_file deduplication must not skip a read that follows a write.

    Realistic prior-turn history (non-recent window):
        user: "update config"
        assistant: <tool>read_file config</tool>
        tool:   config v1 (content: setting=true)
        assistant: <tool>write_file config</tool>
        tool:   wrote
        assistant: <tool>read_file config</tool>
        tool:   config v2 (content: setting=false)   <- MUST NOT be skipped

    Without fix: seen_reads={"config"} after first read; write does NOT clear it;
                 second read is wrongly skipped (LLM sees stale content).
    With fix: write clears seen_reads, second read is correctly shown.
    """
    agent = build_agent(tmp_path, [])

    # Simulate a prior turn with read->write->read on the same file
    # history_length=13, recent_start=7 (indices 0-6 non-recent, 7-12 recent)
    agent.record({"role": "user", "content": "update config", "created_at": "0"})        # index 0
    agent.record({"role": "assistant", "content": '<tool>{"name":"read_file","args":{"path":"config.txt"}}</tool>', "created_at": "1"})
    agent.record({"role": "tool", "name": "read_file", "args": {"path": "config.txt"}, "content": "# config.txt\n   1: setting=true\n", "created_at": "2"})  # index 2, non-recent, ADDED
    agent.record({"role": "assistant", "content": '<tool>{"name":"write_file","args":{"path":"config.txt","content":"setting=false\n"}}</tool>', "created_at": "3"})
    agent.record({"role": "tool", "name": "write_file", "args": {"path": "config.txt", "content": "setting=false\n"}, "content": "wrote config.txt", "created_at": "4"})  # index 4, non-recent
    agent.record({"role": "assistant", "content": '<tool>{"name":"read_file","args":{"path":"config.txt"}}</tool>', "created_at": "5"})
    agent.record({"role": "tool", "name": "read_file", "args": {"path": "config.txt"}, "content": "# config.txt\n   1: setting=false\n", "created_at": "6"})  # index 6, non-recent, ADDED (write cleared dedup)
    # recent entries
    for i in range(7, 13):
        agent.record(_make_filler(i))

    history = agent.history_text()

    # Both read contents appear exactly once (check full line to avoid JSON false positives)
    assert "# config.txt\n   1: setting=true\n" in history
    assert "# config.txt\n   1: setting=false\n" in history
    # Also verify duplicate read (setting=true, same path) does NOT appear twice
    assert history.count("setting=true") == 1


def test_history_text_deduplicates_unchanged_repeated_reads(tmp_path):
    """read_file deduplication should still skip repeated reads with no write in between."""
    agent = build_agent(tmp_path, [])

    # Realistic: two identical reads with no write between them
    # history_length=10, recent_start=4 (indices 0-3 non-recent, 4-9 recent)
    agent.record({"role": "user", "content": "check logs", "created_at": "0"})  # index 0
    agent.record({"role": "assistant", "content": '<tool>{"name":"read_file","args":{"path":"log.txt"}}</tool>', "created_at": "1"})
    agent.record({"role": "tool", "name": "read_file", "args": {"path": "log.txt"}, "content": "# log.txt\n   1: stable\n", "created_at": "2"})  # index 2, non-recent, ADDED
    agent.record({"role": "assistant", "content": '<tool>{"name":"read_file","args":{"path":"log.txt"}}</tool>', "created_at": "3"})  # index 3, non-recent, SKIPPED (dup)
    for i in range(4, 10):
        agent.record(_make_filler(i))  # indices 4-9, recent

    history = agent.history_text()

    # Only first read should appear; duplicates must be skipped
    assert history.count("stable") == 1


def test_ollama_client_posts_expected_payload():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"response": "<final>ok</final>"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OllamaModelClient(
        model="qwen3.5:4b",
        host="http://127.0.0.1:11434",
        temperature=0.2,
        top_p=0.9,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["timeout"] == 30
    assert captured["body"]["model"] == "qwen3.5:4b"
    assert captured["body"]["prompt"] == "hello"
    assert captured["body"]["stream"] is False
    assert captured["body"]["raw"] is False
    assert captured["body"]["think"] is False
    assert captured["body"]["options"]["num_predict"] == 42


def test_deepseek_client_posts_expected_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            body = {"choices": [{"message": {"content": "<final>ok</final>"}}]}
            return json.dumps(body).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = DeepSeekModelClient(
        model="deepseek-v4-pro",
        host="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        temperature=0.2,
        top_p=0.9,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_tokens"] == 42
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_load_env_file_sets_missing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# local secrets",
                "DEEPSEEK_API_KEY=from-dotenv",
                "export MINI_AGENT_TEST_VALUE='quoted value'",
                "IGNORED_LINE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINI_AGENT_TEST_VALUE", raising=False)

    loaded = load_env_file(env_file)

    assert loaded == 2
    assert os.environ["DEEPSEEK_API_KEY"] == "from-dotenv"
    assert os.environ["MINI_AGENT_TEST_VALUE"] == "quoted value"


def test_load_env_file_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-shell")

    loaded = load_env_file(env_file)

    assert loaded == 0
    assert os.environ["DEEPSEEK_API_KEY"] == "from-shell"
