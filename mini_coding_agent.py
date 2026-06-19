import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


DOC_NAMES = ("AGENTS.md", "README.md", "pyproject.toml", "package.json")
HELP_TEXT = "/help, /tools, /history, /last, /sessions, /resume, /compact, /critic, /test-fix, /export-trajectory, /export-training-trajectory, /memory, /session, /reset, /exit"
WELCOME_ART = (
    "/\\     /\\\\",
    "{  `---'  }",
    "{  O   O  }",
    "~~>  V  <~~",
    "\\\\  \\|/  /",
    "`-----'__",
)
HELP_DETAILS = "\n".join(
    [
        "Commands:",
        "/help    Show this help message.",
        "/tools   Show the available tools and approval requirements.",
        "/history Show recent recorded session history.",
        "/last    Show the most recent recorded event.",
        "/sessions List saved sessions for this workspace.",
        "/resume  Resume a saved session by id, or use `/resume latest`.",
        "/compact Compact history into memory notes and clear history.",
        "/critic  Print a JSON diagnosis from the latest test result.",
        "/test-fix Run a fixed test-fix workflow; optional command after it.",
        "/export-trajectory Export the current session as trajectory JSON.",
        "/export-training-trajectory Export normalized training trajectory JSON.",
        "/memory  Show the agent's distilled working memory.",
        "/session Show the path to the saved session file.",
        "/reset   Clear the current session history and memory.",
        "/exit    Exit the agent.",
    ]
)
MAX_TOOL_OUTPUT = 4000
MAX_HISTORY = 12000
IGNORED_PATH_NAMES = {".git", ".mini-coding-agent", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}

##############################
#### Six Agent Components ####
##############################
# 1) Live Repo Context -> WorkspaceContext
# 2) Prompt Shape And Cache Reuse -> build_prefix, memory_text, prompt
# 3) Structured Tools, Validation, And Permissions -> build_tools, run_tool, validate_tool, approve, parse, path, tool_*
# 4) Context Reduction And Output Management -> clip, history_text
# 5) Transcripts, Memory, And Resumption -> SessionStore, record, note_tool, ask, reset
# 6) Delegation And Bounded Subagents -> tool_delegate


def now():
    return datetime.now(timezone.utc).isoformat()


# Supporting helper for component 4 (context reduction and output management).
def clip(text, limit=MAX_TOOL_OUTPUT):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def middle(text, limit):
    text = str(text).replace("\n", " ")
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    left = (limit - 3) // 2
    right = limit - 3 - left
    return text[:left] + "..." + text[-right:]


def load_env_file(path):
    path = Path(path)
    if not path.exists():
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("\"'")
        os.environ[key] = value
        loaded += 1
    return loaded


##############################
#### 1) Live Repo Context ####
##############################
class WorkspaceContext:
    def __init__(self, cwd, repo_root, branch, default_branch, status, recent_commits, project_docs):
        self.cwd = cwd
        self.repo_root = repo_root
        self.branch = branch
        self.default_branch = default_branch
        self.status = status
        self.recent_commits = recent_commits
        self.project_docs = project_docs

    @classmethod
    def build(cls, cwd):
        cwd = Path(cwd).resolve()

        def git(args, fallback=""):
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                return result.stdout.strip() or fallback
            except Exception:
                return fallback

        repo_root = Path(git(["rev-parse", "--show-toplevel"], str(cwd))).resolve()
        docs = {}
        for base in (repo_root, cwd):
            for name in DOC_NAMES:
                path = base / name
                if not path.exists():
                    continue
                key = str(path.relative_to(repo_root))
                if key in docs:
                    continue
                docs[key] = clip(path.read_text(encoding="utf-8", errors="replace"), 1200)

        return cls(
            cwd=str(cwd),
            repo_root=str(repo_root),
            branch=git(["branch", "--show-current"], "-") or "-",
            default_branch=(git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], "origin/main") or "origin/main").removeprefix("origin/"),
            status=clip(git(["status", "--short"], "clean") or "clean", 1500),
            recent_commits=[line for line in git(["log", "--oneline", "-5"]).splitlines() if line],
            project_docs=docs,
        )

    def text(self):
        commits = "\n".join(f"- {line}" for line in self.recent_commits) or "- none"
        docs = "\n".join(f"- {path}\n{snippet}" for path, snippet in self.project_docs.items()) or "- none"
        return "\n".join([
            "Workspace:",
            f"- cwd: {self.cwd}",
            f"- repo_root: {self.repo_root}",
            f"- branch: {self.branch}",
            f"- default_branch: {self.default_branch}",
            "- status:",
            self.status,
            "- recent_commits:",
            commits,
            "- project_docs:",
            docs,
        ])


##############################
#### 5) Session Memory #######
##############################
class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        return self.root / f"{session_id}.json"

    def save(self, session):
        path = self.path(session["id"])
        path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, session_id):
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def latest(self):
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None

    def list_sessions(self):
        return sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


class FakeModelClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def complete(self, prompt, max_new_tokens):
        self.prompts.append(prompt)
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        return self.outputs.pop(0)


class OllamaModelClient:
    def __init__(self, model, host, temperature, top_p, timeout):
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.top_p = top_p
        self.timeout = timeout

    def complete(self, prompt, max_new_tokens):
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "raw": False,
            "think": False,
            "options": {
                "num_predict": max_new_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }
        request = urllib.request.Request(
            self.host + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Could not reach Ollama.\n"
                "Make sure `ollama serve` is running and the model is available.\n"
                f"Host: {self.host}\n"
                f"Model: {self.model}"
            ) from exc

        if data.get("error"):
            raise RuntimeError(f"Ollama error: {data['error']}")
        return data.get("response", "")


class DeepSeekModelClient:
    def __init__(self, model, host, api_key_env, temperature, top_p, timeout):
        self.model = model
        self.host = host.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.top_p = top_p
        self.timeout = timeout

    def complete(self, prompt, max_new_tokens):
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing DeepSeek API key. Set the {self.api_key_env} environment variable."
            )

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": max_new_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "thinking": {"type": "disabled"},
        }
        request = urllib.request.Request(
            self.host + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Could not reach DeepSeek.\n"
                f"Host: {self.host}\n"
                f"Model: {self.model}"
            ) from exc

        if data.get("error"):
            raise RuntimeError(f"DeepSeek error: {data['error']}")
        try:
            return data["choices"][0]["message"].get("content", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected DeepSeek response: {data}") from exc


class MiniAgent:
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        approval_policy="ask",
        max_steps=8,
        max_new_tokens=512,
        depth=0,
        max_depth=1,
        read_only=False,
    ):
        self.model_client = model_client
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.session_store = session_store
        self.approval_policy = approval_policy
        self.max_steps = max_steps
        self.max_new_tokens = max_new_tokens
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "title": "Untitled session",
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": {"task": "", "files": [], "notes": []},
        }
        self.ensure_session_defaults()
        self.tools = self.build_tools()
        self.prefix = self.build_prefix()
        self.session_path = self.session_store.save(self.session)

    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=session_store.load(session_id),
            **kwargs,
        )

    @staticmethod
    def remember(bucket, item, limit):
        if not item:
            return
        if item in bucket:
            bucket.remove(item)
        bucket.append(item)
        del bucket[:-limit]

    def ensure_session_defaults(self):
        self.session.setdefault("title", "Untitled session")
        self.session.setdefault("history", [])
        self.session.setdefault("memory", {"task": "", "files": [], "notes": []})
        self.session["memory"].setdefault("task", "")
        self.session["memory"].setdefault("files", [])
        self.session["memory"].setdefault("notes", [])

    @staticmethod
    def title_from_message(message):
        title = " ".join(str(message).split())
        return middle(title, 80) or "Untitled session"

    ###############################################
    #### 3) Structured Tools And Permissions ######
    ###############################################
    def build_tools(self):
        tools = {
            "list_files": {
                "schema": {"path": "str='.'"},
                "risky": False,
                "description": "List files in the workspace.",
                "run": self.tool_list_files,
            },
            "read_file": {
                "schema": {"path": "str", "start": "int=1", "end": "int=200"},
                "risky": False,
                "description": "Read a UTF-8 file by line range.",
                "run": self.tool_read_file,
            },
            "search": {
                "schema": {"pattern": "str", "path": "str='.'"},
                "risky": False,
                "description": "Search the workspace with rg or a simple fallback.",
                "run": self.tool_search,
            },
            "run_shell": {
                "schema": {"command": "str", "timeout": "int=20"},
                "risky": True,
                "description": "Run a shell command in the repo root.",
                "run": self.tool_run_shell,
            },
            "run_tests": {
                "schema": {"command": "str='python -m pytest -q'", "timeout": "int=120"},
                "risky": True,
                "description": "Run the test command in the repo root.",
                "run": self.tool_run_tests,
            },
            "git_diff": {
                "schema": {"path": "str='.'"},
                "risky": False,
                "description": "Show git diff for the workspace or a path.",
                "run": self.tool_git_diff,
            },
            "write_file": {
                "schema": {"path": "str", "content": "str"},
                "risky": True,
                "description": "Write a text file.",
                "run": self.tool_write_file,
            },
            "patch_file": {
                "schema": {"path": "str", "old_text": "str", "new_text": "str"},
                "risky": True,
                "description": "Replace one exact text block in a file.",
                "run": self.tool_patch_file,
            },
            "apply_patch": {
                "schema": {"patch": "str"},
                "risky": True,
                "description": "Apply a unified diff patch in the workspace.",
                "run": self.tool_apply_patch,
            },
            "rollback": {
                "schema": {"path": "str"},
                "risky": True,
                "description": "Rollback changes to a tracked file or directory with git restore.",
                "run": self.tool_rollback,
            },
        }
        if self.depth < self.max_depth:
            tools["delegate"] = {
                "schema": {"task": "str", "max_steps": "int=3"},
                "risky": False,
                "description": "Ask a bounded read-only child agent to investigate.",
                "run": self.tool_delegate,
            }
        return tools

    ############################################
    #### 2) Prompt Shape And Cache Reuse #######
    ############################################
    def build_prefix(self):
        tool_lines = []
        for name, tool in self.tools.items():
            fields = ", ".join(f"{key}: {value}" for key, value in tool["schema"].items())
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        tool_text = "\n".join(tool_lines)
        examples = "\n".join(
            [
                '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
                '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
                '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
                '<tool name="apply_patch"><patch>diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n</patch></tool>',
                '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
                '<tool>{"name":"git_diff","args":{"path":"."}}</tool>',
                '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
                "<final>Done.</final>",
            ]
        )
        rules = "\n".join([
            "- Use tools instead of guessing about the workspace.",
            "- Return exactly one <tool>...</tool> or one <final>...</final>.",
            "- Tool calls must look like:",
            '  <tool>{"name":"tool_name","args":{...}}</tool>',
            "- For write_file and patch_file with multi-line text, prefer XML style:",
            '  <tool name="write_file" path="file.py"><content>...</content></tool>',
            "- Final answers must look like:",
            "  <final>your answer</final>",
            "- Never invent tool results.",
            "- Keep answers concise and concrete.",
            "- If the user asks you to create or update a specific file and the path is clear, use write_file or patch_file instead of repeatedly listing files.",
            "- Before writing tests for existing code, read the implementation first.",
            "- When writing tests, match the current implementation unless the user explicitly asked you to change the code.",
            "- Prefer run_tests over run_shell when running tests.",
            "- Prefer git_diff over run_shell when inspecting code changes.",
            "- Prefer apply_patch for unified diff changes that touch multiple lines or files.",
            "- Use rollback only when the user asks to revert changes or a test-fix attempt clearly needs to be abandoned.",
            "- New files should be complete and runnable, including obvious imports.",
            "- Do not repeat the same tool call with the same arguments if it did not help. Choose a different tool or return a final answer.",
            "- Required tool arguments must not be empty. Do not call read_file, write_file, patch_file, run_shell, or delegate with args={}.",
        ])
        return "\n\n".join([
            "You are Mini-Coding-Agent, a small coding agent running through a model provider.",
            "Rules:\n" + rules,
            "Tools:\n" + tool_text,
            "Valid response examples:\n" + examples,
            self.workspace.text(),
        ])

    def memory_text(self):
        memory = self.session["memory"]
        notes = "\n".join(f"- {note}" for note in memory["notes"]) or "- none"
        return "\n".join([
            "Memory:",
            f"- task: {memory['task'] or '-'}",
            f"- files: {', '.join(memory['files']) or '-'}",
            "- notes:",
            notes,
        ])

    def tools_text(self):
        lines = ["Tools:"]
        for name, tool in self.tools.items():
            fields = ", ".join(f"{key}: {value}" for key, value in tool["schema"].items())
            risk = "approval required" if tool["risky"] else "safe"
            lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        return "\n".join(lines)

    @staticmethod
    def history_item_summary(item):
        role = item.get("role", "-")
        if role == "tool":
            name = item.get("name", "-")
            args = json.dumps(item.get("args", {}), ensure_ascii=False, sort_keys=True)
            content = clip(str(item.get("content", "")).replace("\n", " "), 180)
            return f"tool:{name} {args}\n  {content}"
        content = clip(str(item.get("content", "")).replace("\n", " "), 220)
        return f"{role}: {content}"

    def history_display_text(self, limit=20):
        history = self.session["history"]
        if not history:
            return "History: empty"
        recent = history[-limit:]
        offset = len(history) - len(recent)
        lines = [f"History: showing {len(recent)} of {len(history)} entries"]
        for index, item in enumerate(recent, start=offset + 1):
            lines.append(f"{index}. {self.history_item_summary(item)}")
        return "\n".join(lines)

    def last_text(self):
        if not self.session["history"]:
            return "Last: empty"
        return "Last:\n" + self.history_item_summary(self.session["history"][-1])

    def sessions_text(self, limit=20):
        paths = self.session_store.list_sessions()[:limit]
        if not paths:
            return "Sessions: none"
        lines = ["Sessions:"]
        for path in paths:
            try:
                session = self.session_store.load(path.stem)
                marker = "*" if session.get("id") == self.session.get("id") else " "
                title = session.get("title") or session.get("memory", {}).get("task") or "Untitled session"
                created = session.get("created_at", "-")
                count = len(session.get("history", []))
                lines.append(f"{marker} {path.stem}  {created}  {count} entries  {middle(title, 70)}")
            except Exception as exc:
                lines.append(f"  {path.stem}  unreadable: {exc}")
        return "\n".join(lines)

    #####################################################
    #### 4) Context Reduction And Output Management #####
    #####################################################
    def history_text(self):
        history = self.session["history"]
        if not history:
            return "- empty"

        lines = []
        seen_reads = set()
        recent_start = max(0, len(history) - 6)
        for index, item in enumerate(history):
            recent = index >= recent_start
            if item["role"] == "tool" and item["name"] in ("write_file", "patch_file"):
                path = str(item["args"].get("path", ""))
                seen_reads.discard(path)
            if item["role"] == "tool" and item["name"] == "read_file" and not recent:
                path = str(item["args"].get("path", ""))
                if path in seen_reads:
                    continue
                seen_reads.add(path)

            if item["role"] == "tool":
                limit = 900 if recent else 180
                lines.append(f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}")
                lines.append(clip(item["content"], limit))
            else:
                limit = 900 if recent else 220
                lines.append(f"[{item['role']}] {clip(item['content'], limit)}")

        return clip("\n".join(lines), MAX_HISTORY)

    ########################################################
    #### 2) Prompt Shape And Cache Reuse (Continued) #######
    ########################################################
    def prompt(self, user_message):
        return "\n\n".join([
            self.prefix,
            self.memory_text(),
            "Transcript:\n" + self.history_text(),
            "Current user request:\n" + user_message,
        ])

    ###############################################
    #### 5) Session Memory (Continued) ###########
    ###############################################
    def record(self, item):
        self.session["history"].append(item)
        self.session_path = self.session_store.save(self.session)

    def note_tool(self, name, args, result):
        memory = self.session["memory"]
        path = args.get("path")
        if name in {"read_file", "write_file", "patch_file"} and path:
            self.remember(memory["files"], str(path), 8)
        note = f"{name}: {clip(str(result).replace(chr(10), ' '), 220)}"
        self.remember(memory["notes"], note, 5)

    def ask(self, user_message):
        memory = self.session["memory"]
        if not memory["task"]:
            memory["task"] = clip(user_message.strip(), 300)
        if self.session.get("title") == "Untitled session":
            self.session["title"] = self.title_from_message(user_message)
        self.record({"role": "user", "content": user_message, "created_at": now()})

        tool_steps = 0
        attempts = 0
        max_attempts = max(self.max_steps * 3, self.max_steps + 4)

        while tool_steps < self.max_steps and attempts < max_attempts:
            attempts += 1
            raw = self.model_client.complete(self.prompt(user_message), self.max_new_tokens)
            kind, payload = self.parse(raw)

            if kind == "tool":
                tool_steps += 1
                name = payload.get("name", "")
                args = payload.get("args", {})
                result = self.run_tool(name, args)
                self.record(
                    {
                        "role": "tool",
                        "name": name,
                        "args": args,
                        "content": result,
                        "created_at": now(),
                    }
                )
                self.note_tool(name, args, result)
                continue

            if kind == "retry":
                self.record({"role": "assistant", "content": payload, "created_at": now()})
                continue

            final = (payload or raw).strip()
            self.record({"role": "assistant", "content": final, "created_at": now()})
            self.remember(memory["notes"], clip(final, 220), 5)
            return final

        if attempts >= max_attempts and tool_steps < self.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
        else:
            final = "Stopped after reaching the step limit without a final answer."
        self.record({"role": "assistant", "content": final, "created_at": now()})
        return final

    #############################################################
    #### 3) Structured Tools, Validation, And Permissions #######
    #############################################################
    def run_tool(self, name, args):
        tool = self.tools.get(name)
        if tool is None:
            return f"error: unknown tool '{name}'"
        try:
            self.validate_tool(name, args)
        except Exception as exc:
            example = self.tool_example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            return message
        if self.repeated_tool_call(name, args):
            return f"error: repeated identical tool call for {name}; choose a different tool or return a final answer"
        if tool["risky"] and not self.approve(name, args):
            return f"error: approval denied for {name}"
        try:
            return clip(tool["run"](args))
        except Exception as exc:
            return f"error: tool {name} failed: {exc}"

    def repeated_tool_call(self, name, args):
        tool_events = [item for item in self.session["history"] if item["role"] == "tool"]
        if len(tool_events) < 2:
            return False
        recent = tool_events[-2:]
        return all(item["name"] == name and item["args"] == args for item in recent)

    def tool_example(self, name):
        examples = {
            "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
            "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
            "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
            "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
            "run_tests": '<tool>{"name":"run_tests","args":{"command":"python -m pytest -q","timeout":120}}</tool>',
            "git_diff": '<tool>{"name":"git_diff","args":{"path":"."}}</tool>',
            "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
            "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
            "apply_patch": '<tool name="apply_patch"><patch>diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n</patch></tool>',
            "rollback": '<tool>{"name":"rollback","args":{"path":"binary_search.py"}}</tool>',
            "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
        }
        return examples.get(name, "")

    def validate_tool(self, name, args):
        args = args or {}

        if name == "list_files":
            path = self.path(args.get("path", "."))
            if not path.is_dir():
                raise ValueError("path is not a directory")
            return

        if name == "read_file":
            path = self.path(args["path"])
            if not path.is_file():
                raise ValueError("path is not a file")
            start = int(args.get("start", 1))
            end = int(args.get("end", 200))
            if start < 1 or end < start:
                raise ValueError("invalid line range")
            return

        if name == "search":
            pattern = str(args.get("pattern", "")).strip()
            if not pattern:
                raise ValueError("pattern must not be empty")
            self.path(args.get("path", "."))
            return

        if name == "run_shell":
            command = str(args.get("command", "")).strip()
            if not command:
                raise ValueError("command must not be empty")
            timeout = int(args.get("timeout", 20))
            if timeout < 1 or timeout > 120:
                raise ValueError("timeout must be in [1, 120]")
            return

        if name == "run_tests":
            command = str(args.get("command", "python -m pytest -q")).strip()
            if not command:
                raise ValueError("command must not be empty")
            timeout = int(args.get("timeout", 120))
            if timeout < 1 or timeout > 300:
                raise ValueError("timeout must be in [1, 300]")
            return

        if name == "git_diff":
            self.path(args.get("path", "."))
            return

        if name == "write_file":
            path = self.path(args["path"])
            if path.exists() and path.is_dir():
                raise ValueError("path is a directory")
            if "content" not in args:
                raise ValueError("missing content")
            return

        if name == "patch_file":
            path = self.path(args["path"])
            if not path.is_file():
                raise ValueError("path is not a file")
            old_text = str(args.get("old_text", ""))
            if not old_text:
                raise ValueError("old_text must not be empty")
            if "new_text" not in args:
                raise ValueError("missing new_text")
            text = path.read_text(encoding="utf-8")
            count = text.count(old_text)
            if count != 1:
                raise ValueError(f"old_text must occur exactly once, found {count}")
            return

        if name == "apply_patch":
            patch = str(args.get("patch", ""))
            if not patch.strip():
                raise ValueError("patch must not be empty")
            if "--- " not in patch or "+++ " not in patch:
                raise ValueError("patch must be a unified diff")
            self.validate_patch_paths(patch)
            return

        if name == "rollback":
            path = self.path(args["path"])
            if path == self.root:
                raise ValueError("path must not be the workspace root")
            return

        if name == "delegate":
            if self.depth >= self.max_depth:
                raise ValueError("delegate depth exceeded")
            task = str(args.get("task", "")).strip()
            if not task:
                raise ValueError("task must not be empty")
            return

    def validate_patch_paths(self, patch):
        paths = set()
        for line in str(patch).splitlines():
            if line.startswith(("--- ", "+++ ")):
                raw = line[4:].split("\t", 1)[0].strip()
            elif line.startswith("diff --git "):
                parts = line.split()
                for raw in parts[2:4]:
                    if raw != "/dev/null":
                        paths.add(raw)
                continue
            else:
                continue
            if raw != "/dev/null":
                paths.add(raw)

        if not paths:
            raise ValueError("patch has no file paths")

        for raw in paths:
            cleaned = raw
            if cleaned.startswith(("a/", "b/")):
                cleaned = cleaned[2:]
            if not cleaned or cleaned == "/dev/null":
                continue
            if Path(cleaned).is_absolute() or ".." in Path(cleaned).parts:
                raise ValueError(f"patch path escapes workspace: {raw}")
            self.path(cleaned)

    def approve(self, name, args):
        if self.read_only:
            return False
        if self.approval_policy == "auto":
            return True
        if self.approval_policy == "never":
            return False
        try:
            answer = input(f"approve {name} {json.dumps(args, ensure_ascii=True)}? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    @staticmethod
    def parse(raw):
        raw = str(raw)
        if "<tool>" in raw and ("<final>" not in raw or raw.find("<tool>") < raw.find("<final>")):
            body = MiniAgent.extract(raw, "tool")
            try:
                payload = json.loads(body)
            except Exception:
                return "retry", MiniAgent.retry_notice("model returned malformed tool JSON")
            if not isinstance(payload, dict):
                return "retry", MiniAgent.retry_notice("tool payload must be a JSON object")
            if not str(payload.get("name", "")).strip():
                return "retry", MiniAgent.retry_notice("tool payload is missing a tool name")
            args = payload.get("args", {})
            if args is None:
                payload["args"] = {}
            elif not isinstance(args, dict):
                return "retry", MiniAgent.retry_notice()
            return "tool", payload
        if "<tool" in raw and ("<final>" not in raw or raw.find("<tool") < raw.find("<final>")):
            payload = MiniAgent.parse_xml_tool(raw)
            if payload is not None:
                return "tool", payload
            return "retry", MiniAgent.retry_notice()
        if "<final>" in raw:
            final = MiniAgent.extract(raw, "final").strip()
            if final:
                return "final", final
            return "retry", MiniAgent.retry_notice("model returned an empty <final> answer")
        raw = raw.strip()
        if raw:
            return "final", raw
        return "retry", MiniAgent.retry_notice("model returned an empty response")

    @staticmethod
    def retry_notice(problem=None):
        prefix = "Runtime notice"
        if problem:
            prefix += f": {problem}"
        else:
            prefix += ": model returned malformed tool output"
        return (
            f"{prefix}. Reply with a valid <tool> call or a non-empty <final> answer. "
            'For multi-line files, prefer <tool name="write_file" path="file.py"><content>...</content></tool>.'
        )

    @staticmethod
    def parse_xml_tool(raw):
        match = re.search(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", raw, re.S)
        if not match:
            return None
        attrs = MiniAgent.parse_attrs(match.group("attrs"))
        name = str(attrs.pop("name", "")).strip()
        if not name:
            return None

        body = match.group("body")
        args = dict(attrs)
        for key in ("content", "old_text", "new_text", "command", "task", "pattern", "path", "patch"):
            if f"<{key}>" in body:
                args[key] = MiniAgent.extract_raw(body, key)

        body_text = body.strip("\n")
        if name == "write_file" and "content" not in args and body_text:
            args["content"] = body_text
        if name == "delegate" and "task" not in args and body_text:
            args["task"] = body_text.strip()
        return {"name": name, "args": args}

    @staticmethod
    def parse_attrs(text):
        attrs = {}
        for match in re.finditer(r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", text):
            attrs[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
        return attrs

    @staticmethod
    def extract(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:].strip()
        return text[start:end].strip()

    @staticmethod
    def extract_raw(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:]
        return text[start:end]

    def reset(self):
        self.session["history"] = []
        self.session["memory"] = {"task": "", "files": [], "notes": []}
        self.session_store.save(self.session)

    def resume_session(self, session_id):
        if session_id == "latest":
            session_id = self.session_store.latest()
        if not session_id:
            return "error: no saved sessions"
        self.session = self.session_store.load(session_id)
        self.ensure_session_defaults()
        self.prefix = self.build_prefix()
        self.session_path = self.session_store.save(self.session)
        return f"resumed {self.session['id']}: {self.session.get('title', 'Untitled session')}"

    def compact(self):
        history = self.session["history"]
        if not history:
            return "history already empty"

        tool_counts = {}
        last_assistant = ""
        for item in history:
            if item.get("role") == "tool":
                name = item.get("name", "-")
                tool_counts[name] = tool_counts.get(name, 0) + 1
            if item.get("role") == "assistant":
                last_assistant = str(item.get("content", ""))

        memory = self.session["memory"]
        tools = ", ".join(f"{name} x{count}" for name, count in sorted(tool_counts.items())) or "none"
        files = ", ".join(memory.get("files", [])) or "none"
        summary = (
            f"compacted {len(history)} history entries; "
            f"task={memory.get('task') or '-'}; files={files}; tools={tools}"
        )
        if last_assistant:
            summary += f"; last_answer={clip(last_assistant.replace(chr(10), ' '), 220)}"

        self.remember(memory["notes"], summary, 5)
        self.session["history"] = []
        self.session_path = self.session_store.save(self.session)
        return summary

    def latest_tool_event(self, name):
        for item in reversed(self.session["history"]):
            if item.get("role") == "tool" and item.get("name") == name:
                return item
        return None

    @staticmethod
    def exit_code_from_result(text):
        match = re.search(r"exit_code:\s*(-?\d+)", str(text))
        return int(match.group(1)) if match else None

    @staticmethod
    def infer_target_file(text):
        patterns = [
            r"([A-Za-z0-9_./\\-]+\.py):\d+",
            r"File \"([^\"]+\.py)\"",
            r"FAILED\s+([A-Za-z0-9_./\\-]+\.py)",
        ]
        for pattern in patterns:
            match = re.search(pattern, str(text))
            if match:
                return match.group(1).replace("\\", "/")
        return ""

    def critic_data(self):
        test_event = self.latest_tool_event("run_tests")
        diff_event = self.latest_tool_event("git_diff")
        if not test_event:
            return {
                "failure_type": "no_test_run",
                "reason": "No run_tests result is available in the current session history.",
                "next_action": "run_tests",
                "target_file": "",
                "suggestion": "Run the test suite before deciding what to fix.",
            }

        result = str(test_event.get("content", ""))
        exit_code = self.exit_code_from_result(result)
        target = self.infer_target_file(result)
        if exit_code == 0:
            return {
                "failure_type": "none",
                "reason": "The latest test command exited successfully.",
                "next_action": "final",
                "target_file": target,
                "suggestion": "Summarize the successful test result and any relevant diff.",
            }

        failure_type = "test_failure"
        lower = result.lower()
        if "syntaxerror" in lower:
            failure_type = "syntax_error"
        elif "assert" in lower or "failed" in lower:
            failure_type = "assertion_failure"
        elif exit_code is None:
            failure_type = "unknown_test_state"

        suggestion = "Inspect the failing test output, read the target implementation, patch the smallest relevant change, then rerun tests."
        if diff_event:
            suggestion += " Review git_diff before finalizing."
        return {
            "failure_type": failure_type,
            "reason": clip(result.replace("\n", " "), 300),
            "next_action": "edit_file",
            "target_file": target,
            "suggestion": suggestion,
        }

    def critic_json(self):
        return json.dumps(self.critic_data(), indent=2, ensure_ascii=False)

    @staticmethod
    def test_fix_prompt(command):
        command = str(command or "python -m pytest -q").strip() or "python -m pytest -q"
        return "\n".join(
            [
                "Run the fixed test-fix workflow for this repository.",
                f"1. Use run_tests with command: {command}",
                "2. If tests fail, inspect the relevant files with read_file/search.",
                "3. Diagnose the failure like a critic: failure_type, reason, target_file, next_action.",
                "4. Apply the smallest fix, preferring apply_patch for unified diffs or patch_file for exact replacements.",
                "5. Use git_diff to review changes.",
                "6. Rerun run_tests.",
                "7. Stop after success or after two fix attempts, then return a concise final answer.",
            ]
        )

    @staticmethod
    def redact_secrets(text):
        redacted = str(text)
        for key, value in os.environ.items():
            key_upper = key.upper()
            if not any(marker in key_upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                continue
            if value and len(value) >= 8:
                redacted = redacted.replace(value, f"[REDACTED:{key}]")
        return redacted

    def trajectory_data(self):
        steps = []
        for index, item in enumerate(self.session["history"], start=1):
            role = item.get("role")
            base = {
                "index": index,
                "role": role,
                "created_at": item.get("created_at"),
            }
            if role == "tool":
                base.update(
                    {
                        "type": "tool",
                        "name": item.get("name"),
                        "args": item.get("args", {}),
                        "result": self.redact_secrets(item.get("content", "")),
                    }
                )
            else:
                base.update(
                    {
                        "type": role,
                        "content": self.redact_secrets(item.get("content", "")),
                    }
                )
            steps.append(base)

        return {
            "schema": "mini-coding-agent.trajectory.v1",
            "session_id": self.session["id"],
            "title": self.session.get("title", "Untitled session"),
            "created_at": self.session.get("created_at"),
            "workspace_root": self.session.get("workspace_root"),
            "memory": self.session.get("memory", {}),
            "steps": steps,
        }

    def training_trajectory_data(self):
        steps = []
        latest_diff = ""
        last_success = None
        for index, item in enumerate(self.session["history"], start=1):
            role = item.get("role")
            if role == "tool":
                name = item.get("name", "")
                observation = self.redact_secrets(item.get("content", ""))
                if name == "git_diff":
                    latest_diff = observation
                success = None
                if name in {"run_tests", "run_shell"}:
                    exit_code = self.exit_code_from_result(observation)
                    if exit_code is not None:
                        success = exit_code == 0
                        last_success = success
                steps.append(
                    {
                        "step": index,
                        "thought": "",
                        "action": name,
                        "args": item.get("args", {}),
                        "observation": observation,
                        "diff": latest_diff if name != "git_diff" else observation,
                        "success": success,
                        "created_at": item.get("created_at"),
                    }
                )
            elif role == "assistant":
                content = self.redact_secrets(item.get("content", ""))
                steps.append(
                    {
                        "step": index,
                        "thought": "",
                        "action": "final",
                        "args": {},
                        "observation": content,
                        "diff": latest_diff,
                        "success": last_success,
                        "created_at": item.get("created_at"),
                    }
                )

        return {
            "schema": "mini-coding-agent.training-trajectory.v1",
            "session_id": self.session["id"],
            "title": self.session.get("title", "Untitled session"),
            "task": self.session.get("memory", {}).get("task", ""),
            "workspace_root": self.session.get("workspace_root"),
            "steps": steps,
        }

    def export_trajectory(self, output_path=None):
        if output_path:
            path = self.path(output_path)
            if path.exists() and path.is_dir():
                path = path / f"{self.session['id']}.json"
            elif str(output_path).endswith(("/", "\\")):
                path.mkdir(parents=True, exist_ok=True)
                path = path / f"{self.session['id']}.json"
        else:
            path = self.root / "trajectories" / f"{self.session['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.trajectory_data(), indent=2, ensure_ascii=False), encoding="utf-8")
        return f"exported trajectory to {path.relative_to(self.root).as_posix()}"

    def export_training_trajectory(self, output_path=None):
        if output_path:
            path = self.path(output_path)
            if path.exists() and path.is_dir():
                path = path / f"{self.session['id']}.training.json"
            elif str(output_path).endswith(("/", "\\")):
                path.mkdir(parents=True, exist_ok=True)
                path = path / f"{self.session['id']}.training.json"
        else:
            path = self.root / "trajectories" / f"{self.session['id']}.training.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.training_trajectory_data(), indent=2, ensure_ascii=False), encoding="utf-8")
        return f"exported training trajectory to {path.relative_to(self.root).as_posix()}"

    def path_is_within_root(self, resolved):
        probe = resolved
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        for candidate in (probe, *probe.parents):
            try:
                if candidate.samefile(self.root):
                    return True
            except OSError:
                continue
        return False

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        if not self.path_is_within_root(resolved):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved

    def tool_list_files(self, args):
        path = self.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        entries = [
            item for item in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            if item.name not in IGNORED_PATH_NAMES
        ]
        lines = []
        for entry in entries[:200]:
            kind = "[D]" if entry.is_dir() else "[F]"
            lines.append(f"{kind} {entry.relative_to(self.root)}")
        return "\n".join(lines) or "(empty)"

    def tool_read_file(self, args):
        path = self.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        body = "\n".join(f"{number:>4}: {line}" for number, line in enumerate(lines[start - 1:end], start=start))
        return f"# {path.relative_to(self.root)}\n{body}"

    def tool_search(self, args):
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        path = self.path(args.get("path", "."))

        if shutil.which("rg"):
            result = subprocess.run(
                ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
                cwd=self.root,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() or result.stderr.strip() or "(no matches)"

        matches = []
        files = [path] if path.is_file() else [
            item for item in path.rglob("*")
            if item.is_file() and not any(part in IGNORED_PATH_NAMES for part in item.relative_to(self.root).parts)
        ]
        for file_path in files:
            for number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if pattern.lower() in line.lower():
                    matches.append(f"{file_path.relative_to(self.root)}:{number}:{line}")
                    if len(matches) >= 200:
                        return "\n".join(matches)
        return "\n".join(matches) or "(no matches)"

    def tool_run_shell(self, args):
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        result = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return "\n".join(
            [
                f"exit_code: {result.returncode}",
                "stdout:",
                result.stdout.strip() or "(empty)",
                "stderr:",
                result.stderr.strip() or "(empty)",
            ]
        )

    def command_result_text(self, result):
        return "\n".join(
            [
                f"exit_code: {result.returncode}",
                "stdout:",
                result.stdout.strip() or "(empty)",
                "stderr:",
                result.stderr.strip() or "(empty)",
            ]
        )

    def tool_run_tests(self, args):
        command = str(args.get("command", "python -m pytest -q")).strip()
        timeout = int(args.get("timeout", 120))
        result = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return self.command_result_text(result)

    def tool_git_diff(self, args):
        path = self.path(args.get("path", "."))
        rel = "." if path == self.root else str(path.relative_to(self.root))
        result = subprocess.run(
            ["git", "diff", "--", rel],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = self.command_result_text(result)
        if result.returncode == 0 and not result.stdout.strip():
            return "exit_code: 0\n(no diff)"
        return text

    def tool_rollback(self, args):
        path = self.path(args["path"])
        rel = str(path.relative_to(self.root))
        result = subprocess.run(
            ["git", "restore", "--", rel],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return self.command_result_text(result)

    def tool_apply_patch(self, args):
        patch = str(args["patch"])
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return self.command_result_text(result)

    def tool_write_file(self, args):
        path = self.path(args["path"])
        content = str(args["content"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {path.relative_to(self.root)} ({len(content)} chars)"

    def tool_patch_file(self, args):
        path = self.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
        return f"patched {path.relative_to(self.root)}"

    ###################################################
    #### 6) Delegation And Bounded Subagents ##########
    ###################################################
    def tool_delegate(self, args):
        if self.depth >= self.max_depth:
            raise ValueError("delegate depth exceeded")
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        child = MiniAgent(
            model_client=self.model_client,
            workspace=self.workspace,
            session_store=self.session_store,
            approval_policy="never",
            max_steps=int(args.get("max_steps", 3)),
            max_new_tokens=self.max_new_tokens,
            depth=self.depth + 1,
            max_depth=self.max_depth,
            read_only=True,
        )
        child.session["memory"]["task"] = task
        child.session["memory"]["notes"] = [clip(self.history_text(), 300)]
        return "delegate_result:\n" + child.ask(task)


def build_welcome(agent, model, host):
    width = max(68, min(shutil.get_terminal_size((80, 20)).columns, 84))
    inner = width - 4
    gap = 3
    left_width = (inner - gap) // 2
    right_width = inner - gap - left_width

    def row(text):
        body = middle(text, width - 4)
        return f"| {body.ljust(width - 4)} |"

    def divider(char="-"):
        return "+" + char * (width - 2) + "+"

    def center(text):
        body = middle(text, inner)
        return f"| {body.center(inner)} |"

    def cell(label, value, size):
        body = middle(f"{label:<9} {value}", size)
        return body.ljust(size)

    def pair(left_label, left_value, right_label, right_value):
        left = cell(left_label, left_value, left_width)
        right = cell(right_label, right_value, right_width)
        return f"| {left}{' ' * gap}{right} |"

    line = divider("=")
    rows = [center(text) for text in WELCOME_ART]
    rows.extend(
        [
            center("MINI CODING AGENT"),
            divider("-"),
            row(""),
            row("WORKSPACE  " + middle(agent.workspace.cwd, inner - 11)),
            pair("MODEL", model, "BRANCH", agent.workspace.branch),
            pair("APPROVAL", agent.approval_policy, "SESSION", agent.session["id"]),
            row(""),
        ]
    )
    return "\n".join([line, *rows, line])


def build_agent(args):
    workspace = WorkspaceContext.build(args.cwd)
    env_file = Path(args.env_file)
    if not env_file.is_absolute():
        env_file = Path(workspace.repo_root) / env_file
    load_env_file(env_file)
    store = SessionStore(Path(workspace.repo_root) / ".mini-coding-agent" / "sessions")
    if args.provider == "ollama":
        model = OllamaModelClient(
            model=args.model,
            host=args.host or "http://127.0.0.1:11434",
            temperature=args.temperature,
            top_p=args.top_p,
            timeout=args.timeout,
        )
    else:
        model = DeepSeekModelClient(
            model=args.model,
            host=args.host or "https://api.deepseek.com",
            api_key_env=args.api_key_env,
            temperature=args.temperature,
            top_p=args.top_p,
            timeout=args.timeout,
        )
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        return MiniAgent.from_session(
            model_client=model,
            workspace=workspace,
            session_store=store,
            session_id=session_id,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )
    return MiniAgent(
        model_client=model,
        workspace=workspace,
        session_store=store,
        approval_policy=args.approval,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
    )


def build_arg_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Minimal coding agent for DeepSeek or Ollama models.",
    )
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt.")
    parser.add_argument("--cwd", default=".", help="Workspace directory.")
    parser.add_argument("--provider", choices=("deepseek", "ollama"), default="deepseek", help="Model provider.")
    parser.add_argument("--model", default="deepseek-v4-pro", help="Model name.")
    parser.add_argument("--host", default=None, help="Model API host URL.")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY", help="Environment variable containing the DeepSeek API key.")
    parser.add_argument("--env-file", default=".env", help="Path to a dotenv file loaded before creating the model client.")
    parser.add_argument("--timeout", type=int, default=300, help="Model request timeout in seconds.")
    parser.add_argument("--ollama-timeout", dest="timeout", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--resume", default=None, help="Session id to resume or 'latest'.")
    parser.add_argument(
        "--approval",
        choices=("ask", "auto", "never"),
        default="ask",
        help="Approval policy for risky tools; auto grants the model arbitrary command execution and file writes.",
    )
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum tool/model iterations per request.")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="Maximum model output tokens per step.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature sent to the model provider.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling value sent to the model provider.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    agent = build_agent(args)

    print(build_welcome(agent, model=args.model, host=getattr(agent.model_client, "host", args.host)))

    if args.prompt:
        prompt = " ".join(args.prompt).strip()
        if prompt:
            print()
            try:
                print(agent.ask(prompt))
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return 0

    while True:
        try:
            user_input = input("\nmini-coding-agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print(HELP_DETAILS)
            continue
        if user_input == "/tools":
            print(agent.tools_text())
            continue
        if user_input == "/history":
            print(agent.history_display_text())
            continue
        if user_input == "/last":
            print(agent.last_text())
            continue
        if user_input == "/sessions":
            print(agent.sessions_text())
            continue
        if user_input.startswith("/resume"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                print("usage: /resume latest|SESSION_ID")
            else:
                try:
                    print(agent.resume_session(parts[1].strip()))
                except FileNotFoundError:
                    print(f"session not found: {parts[1].strip()}")
                except json.JSONDecodeError as exc:
                    print(f"could not read session: {exc}")
            continue
        if user_input == "/compact":
            print(agent.compact())
            continue
        if user_input == "/critic":
            print(agent.critic_json())
            continue
        if user_input.startswith("/test-fix"):
            parts = user_input.split(maxsplit=1)
            command = parts[1].strip() if len(parts) > 1 else "python -m pytest -q"
            print()
            try:
                print(agent.ask(agent.test_fix_prompt(command)))
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
            continue
        if user_input.startswith("/export-trajectory"):
            parts = user_input.split(maxsplit=1)
            output_path = parts[1].strip() if len(parts) > 1 else None
            try:
                print(agent.export_trajectory(output_path))
            except Exception as exc:
                print(f"could not export trajectory: {exc}")
            continue
        if user_input.startswith("/export-training-trajectory"):
            parts = user_input.split(maxsplit=1)
            output_path = parts[1].strip() if len(parts) > 1 else None
            try:
                print(agent.export_training_trajectory(output_path))
            except Exception as exc:
                print(f"could not export training trajectory: {exc}")
            continue
        if user_input == "/memory":
            print(agent.memory_text())
            continue
        if user_input == "/session":
            print(agent.session_path)
            continue
        if user_input == "/reset":
            agent.reset()
            print("session reset")
            continue

        print()
        try:
            print(agent.ask(user_input))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
