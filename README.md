# debug-agent-ai

AI-powered root cause analysis for stack traces, powered by [Claude](https://anthropic.com) (`claude-sonnet-4-6`).

The agent reads a stack trace, inspects the relevant source files, searches for definitions, and produces a structured root cause report with a suggested fix.

## Install

```bash
pip install debug-agent-ai
# or from source:
pip install -e .
```

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Quick start

### Analyse from a file

```bash
debug-agent analyze --trace error.txt --src ./src
```

### Pipe mode

```bash
python myapp.py 2>&1 | debug-agent analyze --src ./myapp
```

### Verbose mode (see every tool call)

```bash
debug-agent analyze -t crash.txt -s ./myapp -v
```

### Python API

```python
from debug_agent import DebugAgent

agent = DebugAgent()          # reads ANTHROPIC_API_KEY from environment
report = agent.analyze(
    trace=open("error.txt").read(),
    src_dir="./src",
    verbose=True,
)
print(report)
```

## How it works

The agent runs an agentic loop with Claude `claude-sonnet-4-6` and three tools:

| Tool | Purpose |
|---|---|
| `read_file(path, start_line?, end_line?)` | Read source files (with optional line range) |
| `search_code(pattern, directory)` | Grep source files for a regex pattern |
| `fetch_error_docs(error_type)` | Look up built-in docs for known error types |

Loop steps:
1. **Parse trace** — identify the error class and every file/line reference.
2. **Fetch error docs** — ground the analysis in error semantics.
3. **Read relevant files** — inspect the failing lines and surrounding context.
4. **Search definitions** — locate function/class definitions if needed.
5. **Synthesise** — write a final report once enough evidence is gathered.

## Output format

```markdown
## Root Cause
The `user_data` dictionary is constructed from an API response that omits the
`email` key when the user has not confirmed their address, causing the KeyError.

## Evidence
- `auth/views.py:47`: `return user_data["email"]` — unconditional access
- `api/client.py:112`: email field is only set when `confirmed=True`

## Suggested Fix
Replace the direct access with `user_data.get("email", "")` or add an
explicit check: `if "email" not in user_data: raise ValueError(...)`

## Confidence
High — the missing key and its conditional insertion are directly visible.
```

## Development

```bash
git clone https://github.com/example/debug-agent
cd debug-agent
pip install -e .[dev]

# run tests
pytest

# lint
ruff check debug_agent/
```

## License

MIT
