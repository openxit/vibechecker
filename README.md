# vibecheck

**Zero-dependency static analyzer for detecting phantom stubs, cloned logic, swallowed errors, unverified imports, and dead code.**

`vibecheck` uses Python's `ast` and `tokenize` modules alongside a lexical JS/TS scanner to catch common structural defects and incomplete code patterns—locally, deterministically, and without any external runtime dependencies.

## Features

- **Desktop App & CLI (Windows & Linux):**
  - **Windows:** Download `vibecheck-windows-x64.zip` from [GitHub Releases](../../releases) (recommended for instant startup) or the standalone `vibecheck.exe`, and launch the GUI without a terminal window or Python installation.
  - **Linux:** Download `vibecheck-linux-x64.tar.gz` from [GitHub Releases](../../releases), extract it, and run `./vibecheck` for the GUI or `./vibecheck /path/to/repo` for the CLI.
  - **CLI via pip (any OS):** `pip install git+https://github.com/openxit/vibechecker.git` (install `python3-tk` on Debian/Ubuntu if you also launch `vibecheck gui`).
- **Direct GitHub Repository Scanning:** Paste any GitHub URL (e.g. `https://github.com/owner/repo`) or `owner/repo` shorthand into the GUI or CLI to fetch and scan the archive without cloning manually.
- **Projects Sidebar & Background Monitor:** Save local folders or GitHub repositories in the left sidebar with configurable check intervals (5, 15, 30, or 60 minutes). Background checks stay silent when clean and trigger a Windows notification when issues are detected.
- **Live Scan Progress:** Modal progress dialog with determinate progress bar, file counter, current file path, and cancel support.
- **Repair Prompt Generator & Score:** Color-coded Slop Score (`0` clean, `1–25` minor, `>25` high) paired with a heuristic Vibe Signal (`Low`, `Medium`, `High`). Click **Fix Prompt...** in the GUI (or pass `--format prompt`) to generate a structured repair prompt with file paths, line numbers, and snippets.

## Rules

| Rule | Name | Severity | What it catches |
| --- | --- | --- | --- |
| `VC001` | `phantom-stub` | `error` | Empty functions (`pass`, `...`, `{}`), `NotImplementedError`, or functions returning dummy `[]` / `{}` / `None` while ignoring parameters (skips `@abstractmethod`, `@overload`, `Protocol`). |
| `VC002` | `placeholder-comment` | `warning` | Unfinished `TODO`/`FIXME`/`PLACEHOLDER` markers or deferral phrases (`# implement this later`, `# rest of implementation`, `# for brevity`). Never triggers on string literals. |
| `VC003` | `cloned-logic` | `warning` | Structurally identical functions duplicated across files under different variable/function names (AST-normalized). |
| `VC004` | `swallowed-exception` | `error` | Broad `except:` / `except Exception:` or `catch` blocks that silently discard or only `print`/`console.log` errors. |
| `VC005` | `unverified-import` | `error` | Imports missing from stdlib, local project modules, `pyproject.toml`, `requirements*.txt`, or `package.json`. |
| `VC006` | `mock-data-in-source` | `warning` | `mock_*`/`dummy_*`/`fake_*` variables or placeholder API keys left outside `tests/`. |
| `VC007` | `narrating-comment` | `info` | Redundant comments that merely restate obvious syntax on the next line. |
| `VC008` | `dead-helper` | `warning` | Top-level functions that are never called or referenced anywhere in the scanned project. |
| `VC009` | `boilerplate-placeholder` | `warning` | Generated placeholder entities (`John Doe`, `example.com`, `Lorem ipsum`) left in non-test source files. |

## Quickstart

```bash
# Launch the desktop GUI
vibecheck gui

# Scan a local directory or GitHub repository
python -m vibecheck .
python -m vibecheck https://github.com/owner/repo

# Fail CI if any error is found or slop score exceeds 15
vibecheck . --fail-on error --max-score 15

# Output structured JSON, GitHub Actions annotations, or a repair prompt
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
