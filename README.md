&nbsp;
# Mini-Coding-Agent

This folder contains a small standalone coding agent:

- code: `mini_coding_agent.py`
- CLI: `mini-coding-agent`

It is a minimal local agent loop with:

- workspace snapshot collection
- stable prompt plus turn state
- structured tools
- approval handling for risky tools
- transcript and memory persistence
- bounded delegation
- dedicated test, diff, rollback, and trajectory-export workflows
- fixed test-fix and critic/reflection commands for evaluation data collection

The default model backend is DeepSeek, with Ollama still available as an optional local provider.

<a href="https://magazine.sebastianraschka.com/p/components-of-a-coding-agent">
  <img src="https://substack-post-media.s3.amazonaws.com/public/images/49b97718-57f4-4977-99c8-8ad5c4d32af3_1548x862.png" width="500px">
</a>

<br>

**[The detailed tutorial: Components of a Coding Agent](https://magazine.sebastianraschka.com/p/components-of-a-coding-agent)**


&nbsp;
## Six Core Components

<a href="https://magazine.sebastianraschka.com/p/components-of-a-coding-agent">
  <img alt="Six core components of a coding agent" src="https://sebastianraschka.com/images/github/mini-coding-agent/six-components.webp" width="500px">
</a>

This coding harness is organized around six practical building blocks:

1. **Live repo context**  
   The agent collects stable workspace facts upfront, such as repo layout, instructions, and git state.
2. **Prompt shape and cache reuse**  
   A stable prompt prefix, which is separate from the changing request, transcript, and memory so repeated model calls can reuse the static parts efficiently.
3. **Structured tools, validation, and permissions**  
   The model works through named tools with checked inputs, workspace path validation, and approval gates instead of free-form arbitrary actions.
4. **Context reduction and output management**  
   Long outputs are clipped, repeated reads are deduplicated, and older transcript entries are compressed to keep prompt size under control.
5. **Transcripts, memory, and resumption**  
   The runtime keeps both a full durable transcript and a smaller working memory so sessions can be resumed while preserving important state via working memory.
6. **Delegation and bounded subagents**  
   Scoped subtasks can be delegated to helper agents that inherit enough context to help (but operate within limits).

&nbsp;
## Requirements

You need:

- Python 3.10+
- A DeepSeek API key in `DEEPSEEK_API_KEY`

Optional:

- `uv` for environment management and the `mini-coding-agent` CLI entry point
- Ollama installed with a local model if you want to use `--provider ollama`

This project has no Python runtime dependency beyond the standard library, so you can run it directly with `python mini_coding_agent.py` if you do not want to use `uv`.

&nbsp;
## Configure DeepSeek

Create a `.env` file in the project root:

```text
DEEPSEEK_API_KEY=your-api-key
```

Do not commit `.env` to version control.

You can also set the key directly in your shell:

```bash
set DEEPSEEK_API_KEY=your-api-key
```

PowerShell:

```powershell
$env:DEEPSEEK_API_KEY="your-api-key"
```

The default model is `deepseek-v4-pro`.

&nbsp;
## Optional: Install Ollama

Install Ollama on your machine so the `ollama` command is available in your shell.

Official installation link: [ollama.com/download](https://ollama.com/download)

Then verify:

```bash
ollama --help
```

Start the server:

```bash
ollama serve
```

In another terminal, pull a model. Example:

```bash
ollama pull qwen3.5:4b
```

Qwen 3.5 model library:

- [ollama.com/library/qwen3.5](https://ollama.com/library/qwen3.5)

When using `--provider ollama`, you can choose a local model such as `qwen3.5:4b`. If you have sufficient memory, it is worth trying a larger model such as `qwen3.5:9b` or another larger Qwen 3.5 variant. In Ollama mode, the agent sends prompts to Ollama's `/api/generate` endpoint.

&nbsp;
## Project Setup

Clone the repo or your fork and change into it:

```bash
git clone https://github.com/rasbt/mini-coding-agent.git
cd mini-coding-agent
```

If you forked it first, use your fork URL instead:

```bash
git clone https://github.com/<your-github-user>/mini-coding-agent.git
cd mini-coding-agent
```



&nbsp;
## Basic Usage

Start the agent:

```bash
cd mini-coding-agent
uv run mini-coding-agent
```

Without `uv`, run the script directly:

```bash
cd mini-coding-agent
python mini_coding_agent.py
```

By default it uses:

- provider: `deepseek`
- model: `deepseek-v4-pro`
- approval: `ask`

For a concrete usage example, see [EXAMPLE.md](EXAMPLE.md).

&nbsp;
## Approval Modes

Risky tools such as shell commands and file writes are gated by approval.

- `--approval ask`
  prompts before risky actions (default and recommended)
- `--approval auto`
  allows risky actions automatically, including arbitrary command execution and file writes by the model; use only with trusted prompts and trusted repositories
- `--approval never`
  denies risky actions

Example:

```bash
uv run mini-coding-agent --approval auto
```



&nbsp;
## Resume Sessions

The agent saves sessions under the target workspace root in:

```text
.mini-coding-agent/sessions/
```

Resume the latest session:

```bash
uv run mini-coding-agent --resume latest
```


Resume a specific session:

```bash
uv run mini-coding-agent --resume 20260401-144025-2dd0aa
```


&nbsp;
## Interactive Commands

Inside the REPL, slash commands are handled directly by the agent instead of
being sent to the model as a normal task.

- `/help`
  shows the list of available interactive commands
- `/tools`
  prints the available tools and whether each one requires approval
- `/history`
  prints recent recorded user, tool, and assistant events
- `/last`
  prints the most recent recorded event
- `/sessions`
  lists saved sessions for the current workspace
- `/resume <id>`
  resumes a saved session by id; use `/resume latest` for the newest session
- `/compact`
  summarizes the current history into memory notes and clears the detailed history
- `/critic`
  prints a JSON diagnosis from the latest `run_tests` result
- `/test-fix [command]`
  runs a fixed test-fix workflow using `run_tests`, file inspection, patching, `git_diff`, and rerun
- `/export-trajectory [path]`
  exports the current session as trajectory JSON for evaluation or training data
- `/export-training-trajectory [path]`
  exports a normalized training trajectory with `step`, `thought`, `action`, `args`, `observation`, `diff`, and `success`
- `/memory`
  prints the distilled session memory, including the current task, tracked files, and notes
- `/session`
  prints the path to the current saved session JSON file
- `/reset`
  clears the current session history and distilled memory but keeps you in the REPL
- `/exit`
  exits the interactive session
- `/quit`
  exits the interactive session; alias for `/exit`

&nbsp;
## Benchmark Runner

This fork includes a small benchmark harness for evaluating agent behavior on
isolated coding tasks.

Smoke-test the benchmark runner without calling a model:

```bash
python scripts/run_benchmark.py --fake-agent-success
```

Run one benchmark task with the configured model provider:

```bash
python scripts/run_benchmark.py --task bubble_sort_order
```

Each benchmark task lives under `benchmarks/` and contains:

- `prompt.txt`
- `repo/`
- `public_tests/`
- `hidden_tests/`
- `metadata.json`

Results are written to `benchmark_results/`, while copied task repos and their
trajectory files are written to `benchmark_runs/run_<timestamp>/`. Reports
include public/hidden test status, tool-call counts, final answer text, the raw
trajectory path, and the normalized training trajectory path. Each result also includes a rule-based
`failure_analysis` field with coarse failure types such as `no_test_run`,
`wrong_file`, `unrelated_edit`, `repeated_tool_call`,
`early_stop_after_test_failure`, `hidden_test_failed`, and `patch_too_large`.

Re-analyze an existing benchmark report:

```bash
python scripts/analyze_failures.py benchmark_results/run_YYYYMMDD-HHMMSS.json
```

Build Critic SFT data from benchmark reports:

```bash
python scripts/build_critic_sft.py --reports benchmark_results --out datasets/critic_sft_from_benchmark.jsonl
```

Generate synthetic Critic SFT data from templates:

```bash
python scripts/build_critic_sft.py --reports missing_reports --synthetic-per-type 50 --out datasets/critic_sft_synthetic.jsonl
```

The builder deduplicates exact input/output pairs by default and prints dataset
statistics, including source counts and failure-type distribution.

Combine benchmark-derived and synthetic data:

```bash
python scripts/build_critic_sft.py --reports benchmark_results --include-success --synthetic-per-type 50 --out datasets/critic_sft.jsonl
```

&nbsp;
## Main CLI Flags

```bash
uv run mini-coding-agent --help
```

Without `uv`:

```bash
python mini_coding_agent.py --help
```

CLI flags are passed before the agent starts. Use them to choose the workspace,
model connection, resume behavior, approval mode, and generation limits.

Important flags:

- `--cwd`
  sets the workspace directory the agent should inspect and modify; default: `.`
- `--model`
  selects the model name; default: `deepseek-v4-pro`
- `--provider`
  selects the model provider, either `deepseek` or `ollama`; default: `deepseek`
- `--host`
  points the agent at the provider API URL; DeepSeek default: `https://api.deepseek.com`; Ollama default: `http://127.0.0.1:11434`
- `--api-key-env`
  names the environment variable containing the DeepSeek API key; default: `DEEPSEEK_API_KEY`
- `--env-file`
  loads environment variables from a dotenv file before creating the model client; default: `.env`
- `--timeout`
  controls how long the client waits for a model response; default: `300` seconds
- `--resume`
  resumes a saved session by id or uses `latest`; default: start a new session
- `--approval`
  controls how risky tools are handled: `ask`, `auto`, or `never`; default: `ask`
- `--max-steps`
  limits how many model and tool turns are allowed for one user request; default: `8`
- `--max-new-tokens`
  caps the model output length for each step; default: `512`
- `--temperature`
  controls sampling randomness; default: `0.2`
- `--top-p`
  controls nucleus sampling for generation; default: `0.9`

&nbsp;
## Example

See [EXAMPLE.md](EXAMPLE.md)

&nbsp;
## Notes & Tips

- The agent expects the model to emit either `<tool>...</tool>` or `<final>...</final>`.
- Different Ollama models will follow those instructions with different reliability.
- If the model does not follow the format well, use a stronger instruction-following model.
- The agent is intentionally small and optimized for readability, not robustness.
