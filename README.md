# vibecheck

**Zero-dependency static analyzer that catches AI-generated phantom stubs, cloned logic, swallowed errors, and hallucinated imports.**

Traditional linters check syntax and formatting. `vibecheck` uses Python's `ast`/`tokenize` modules and a lexical JS/TS scanner to catch the failure modes introduced by LLM-assisted coding—locally, deterministically, and without any API keys or cloud calls.

## Features

- **Desktop App & CLI in one (`vibecheck.exe`):** Double-click `vibecheck.exe` (from [GitHub Releases](../../releases)) to open the native Windows GUI without any terminal window or Python installation. Pass arguments to run it as a CLI.
- **Live Scan Progress:** Shows a progress dialog with a determinate progress bar, file counter, current file path, and cancel support.
- **Dynamic AI Fix Prompt:** When issues are found (especially on a high/red Slop Score), click **Fix Prompt...** in the GUI (or run `--format prompt` in the CLI) to generate a structured repair prompt with exact file paths, line numbers, code snippets, and rule-specific fix instructions ready to paste into your IDE or AI assistant.
- **Color-Coded Slop Score (0–100):** `0` (clean, green), `1–25` (minor slop, amber), `>25` (high slop, red).

## Rules

| Rule | Name | Severity | What it catches |
| --- | --- | --- | --- |
| `VC001` | `phantom-stub` | `error` | Empty functions (`pass`, `...`, `{}`), `NotImplementedError`, or functions returning dummy `[]` / `{}` / `None` while ignoring parameters (skips `@abstractmethod`, `@overload`, `Protocol`). |
| `VC002` | `ai-placeholder-comment` | `warning` | Unfinished `TODO`/`FIXME`/`PLACEHOLDER` markers or LLM deferral phrases (`# implement this later`, `# rest of implementation`, `# for brevity`). Never triggers on string literals. |
| `VC003` | `cloned-logic` | `warning` | Structurally identical functions duplicated across files under different variable/function names (AST-normalized). |
| `VC004` | `swallowed-exception` | `error` | Broad `except:` / `except Exception:` or `catch` blocks that silently discard or only `print`/`console.log` errors. |
| `VC005` | `unverified-import` | `error` | Imports missing from stdlib, local project modules, `pyproject.toml`, `requirements*.txt`, or `package.json`. |
| `VC006` | `mock-data-in-source` | `warning` | `mock_*`/`dummy_*`/`fake_*` variables or placeholder API keys left outside `tests/`. |

## Quickstart

```bash
# Launch the desktop GUI
vibecheck gui

# Scan from the CLI
python -m vibecheck .

# Fail CI if any error is found or slop score exceeds 15
vibecheck . --fail-on error --max-score 15

# Output structured JSON, GitHub Actions annotations, or an AI fix prompt
vibecheck . --format json
vibecheck . --format github
vibecheck . --format prompt

# List rules or generate .vibecheck.toml
vibecheck rules
vibecheck init
```

## Inline Suppressions

Suppress a rule on the same line or the line above:

```python
def intentionally_empty(event: dict) -> None:  # vibecheck: ignore[VC001]
    pass
```

## Configuration

`vibecheck` reads `.vibecheck.toml` or `[tool.vibecheck]` in `pyproject.toml`:

```toml
[tool.vibecheck]
disable = ["VC002"]
ignore = ["legacy/**"]
fail_on = "error"
max_score = 15
```
