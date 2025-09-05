# Repository Guidelines

## Project Structure & Module Organization
- Source: `src/` (framework‑agnostic controller/services and NiceGUI view)
  - `controller.py`, `services.py`, `interfaces.py`, `view.py`, `main.py`, `config.py`
- Tests: `integration-tests/` (`test_*.py` and `run_all.py`)
- Config: `config.yaml` (models/templates), `.env` (OpenRouter credentials; not committed)
- Artifacts: `artifacts/` (HTML, screenshots, logs; gitignored, hash‑named files)

## Setup, Run, and Tests
- Install deps: `pip install -r requirements.txt`
- Install browsers: `python -m playwright install`
- Copy env: `cp .env_template .env` and set `OPENROUTER_API_KEY`
- Run GUI: `python -m src.main` (serves on port 8055; artifacts at `/artifacts`)
- Run all tests: `python integration-tests/run_all.py -j 2`
- Run one test: `python integration-tests/test_state_machine.py`
- Filter tests: `python integration-tests/run_all.py -k openrouter -j 4`

## Configuration & Templates
- Defaults load from `config.yaml` at startup; avoid hardcoding.
- Templates support variables: `{overall_goal}`, `{user_steering}`, `{vision_output}`, `{console_logs}`, `{html_input}`.

## Coding Style & Naming Conventions
- Language: Python, 4‑space indentation, type hints encouraged (`from __future__ import annotations` used)
- Names: files/modules `snake_case.py`; functions/vars `snake_case`; classes `PascalCase`
- Imports: prefer absolute within `src` (tests add project root to `sys.path`)
- Keep UI isolated to `src/view.py`; controller/services remain framework‑agnostic

## Testing Guidelines
- Framework: custom integration scripts (no pytest runner required)
- Location: `integration-tests/` with filenames `test_*.py`
- Expectations: cover state machine transitions, OpenRouter services, artifact creation
- CI‑like run locally with `run_all.py`; add new tests following the async pattern used in existing files

### Agent Development Workflow (Quality Bar)
- Prefer targeted test runs while iterating: `-k <substr>` and `-j 1` to reduce flakiness and noise.
- Validate UI changes you make by exercising the exact view or component you changed. For dialogs/slots, open/close at least twice and verify state persists.
- When adding config/state files, isolate tests from user data (use env overrides like `MODEL_PARAMS_PATH` or temp dirs) to avoid cross‑test interference.
- Reproduce reported bugs locally before handing off; include the minimal command to verify the fix.
- If tests require network or browsers, run only the smallest set needed until ready for a full run.

### NiceGUI & Quasar Patterns
- Use native `ui.table` with a scoped body slot (`add_slot('body', scope='props')`) for custom cell content; avoid passing `props` to `ui.element(...)` directly.
- Avoid closure capture bugs in per‑row actions: pass the dialog/table instance into callbacks explicitly.
- Keep dialogs full‑width for complex forms and prefer outlined, dense inputs for readability.

### OpenRouter / Model Parameters Invariants
- Only send parameters explicitly stored by the user per model. Do not invent defaults.
- Filter stored params to the model’s `supported_parameters` when available; if unknown, pass through.
- Call‑site kwargs take precedence over stored params unless product direction states otherwise.

### External Knowledge & Fresh Docs
- When allowed by the environment and approvals, search for recent solutions and updated docs (e.g., NiceGUI/Quasar slot usage, OpenRouter parameter schema changes) before implementing.
- Capture any critical findings as short comments in PR descriptions or as brief notes in the code near tricky integrations.

## Commit & Pull Request Guidelines
- Format: `type: imperative summary ≤50 chars` (no scope in parentheses).
- Body: start at line 3 (line 2 blank) and use Markdown bullets (`- ...`) with single‑line points.
- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- Example: `fix: prevent single model failures from crashing parallel execution`
- PRs: concise description, linked issue, UI screenshots when relevant, and validation steps (commands + expected outputs).
 - Keep messages short and focused; for small changes, a single‑line commit without a body is fine.

## Security & Configuration Tips
- Never commit secrets; `.env` is gitignored. Required: `OPENROUTER_API_KEY`; optional: `OPENROUTER_BASE_URL`.
- App reads `config.yaml` and `.env` at startup; change defaults there, not in code.

## Development Rules & Invariants
- No hardcoded fallback/mocked data; on API failure, surface the error.
- Avoid scope creep; implement only requested changes.
- Re‑running mid‑chain deletes descendants; do not bypass operation locking.
- Preserve state‑machine flow: render → screenshot/console → vision → code → output.

### Simplicity & Modularity
- Prefer the simplest working approach. Avoid introducing fallback data or extra layers unless required by product constraints.
- Consolidate functionality and keep modules focused; extract UI components when they grow complex, but do not duplicate logic across places.
- Remove scaffolding once no longer needed; keep the codebase lean and easy to reason about.
