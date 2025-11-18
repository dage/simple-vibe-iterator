# Repository Guidelines

## Project Structure & Module Organization
- Source: `src/` (framework‑agnostic controller/services and NiceGUI view)
  - `controller.py`, `services.py`, `interfaces.py`, `view.py`, `main.py`, `config.py`
- Tests: `integration-tests/` (`test_*.py` and `run_all.py`)
- Feedback presets: `feedback_presets.yaml` (declarative wait/keypress/screenshot recipes; override path via `FEEDBACK_PRESETS_PATH`)
- Config: `config.yaml` (models/templates), `.env` (OpenRouter credentials; not committed)
- Artifacts: `artifacts/` (HTML, screenshots; gitignored, hash‑named files)
- Logs: `logs/` (JSONL tool + auto-logger traces; gitignored)

## Setup, Run, and Tests
- Install deps: `pip install -r requirements.txt`
- Install Chrome DevTools MCP helper: `npm install -g chrome-devtools-mcp@latest`
- Copy env: `cp .env_template .env` and set `OPENROUTER_API_KEY`
- Run GUI: `python -m src.main` (serves on port 8055; artifacts at `/artifacts`)
- Run all tests: `python integration-tests/run_all.py`
- Run one test: `python integration-tests/test_state_machine.py`
- Filter tests: `python integration-tests/run_all.py -k openrouter`

## Configuration & Templates
- Defaults load from `config.yaml` at startup; avoid hardcoding.
- Coding and vision prompts are only editable via `config.yaml` (UI never overrides or persists them).
- Templates support variables: `{overall_goal}`, `{user_feedback}`, `{vision_output}`, `{console_logs}`, `{html_input}`, `{auto_feedback}`.
- Feedback presets supply `{auto_feedback}` (a readable summary like `#1: press-w, #2: press-s`) to both code and vision templates so models can align each screenshot with its action.
- Capture automation lives in `feedback_presets.yaml`. The packaged presets are “Single screenshot”, “Short animation”, “WSAD sweep”, and “Space press”, and the manual screenshot count control has been removed so only presets drive captures. Presets trigger as soon as the DOM ready event fires.

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
- When running all integrations tests with `python integration-tests/run_all.py`, use default number of workers since defaults are already fine tuned for maximum performance.

### Agent Development Workflow (Quality Bar)
- Prefer targeted test runs while iterating: `-k <substr>` and `-j 1` to reduce flakiness and noise.
- Validate UI changes you make by exercising the exact view or component you changed. For dialogs/slots, open/close at least twice and verify state persists.
- When adding config/state files, isolate tests from user data (use env overrides like `MODEL_PARAMS_PATH` or temp dirs) to avoid cross‑test interference.
- Reproduce reported bugs locally before handing off; include the minimal command to verify the fix.
- If tests require network or browsers, run only the smallest set needed until ready for a full run.

### NiceGUI & Quasar Patterns
- NiceGUI UI components represent state synchronized between Python and the browser via WebSockets. To update component values programmatically and ensure the UI reflects changes without page refresh, always use provided async setter methods (e.g., await component.set_value(...)) inside async event handlers or tasks. Avoid direct attribute assignments followed by .update(), which often do not trigger UI refresh. For reactive two-way binding, use bind_value to link Python variables with component state and keep them in sync automatically. This pattern enables reliable, real-time UI updates following user interactions or internal logic changes.
- Use native `ui.table` with a scoped body slot (`add_slot('body', scope='props')`) for custom cell content; avoid passing `props` to `ui.element(...)` directly.
- Avoid closure capture bugs in per-row actions: pass the dialog/table instance into callbacks explicitly.
- Keep dialogs full-width for complex forms and prefer outlined, dense inputs for readability.

### Chrome DevTools Browser Tools
- Chrome DevTools MCP provides eight core tools for every iteration: take_screenshot, list_console_messages, list_network_requests, press_key, evaluate_script, wait_for, performance_start_trace, and performance_stop_trace.
- Treat these tools as required instrumentation: capture screenshots frequently, inspect console/network failures before guessing, and verify controls with press_key plus screenshots.
- Use the performance trace tools only when diagnosing FPS or timing issues; remember to call `performance_stop_trace` to retrieve metrics.
- `evaluate_script` mirrors Chrome's `Runtime.evaluate()` so it only accepts single expressions, not semicolon-terminated statements. Favor property reads, assignments, comma sequences, or an IIFE that returns a result; spread multi-step logic across repeated calls or global vars instead of chaining statements. When escaping is tricky, wrap logic in a named function and return the final value rather than writing raw statements.
- For richer JS interactions rely on the best-practice combo: console logging plus `list_console_messages` for debug insight, DOM injection/querying via `evaluate_script` for UI validation, IIFEs for bundled logic, and stepwise execution (global state plus multiple simple evals) when you need stateful transitions. Avoid trying to cram general script execution into a single `evaluate_script` call—use other tools or multiple targeted expressions instead.

### OpenRouter / Model Parameters Invariants
- Only send parameters explicitly stored by the user per model. Do not invent defaults.
- Filter stored params to the model’s `supported_parameters` when available; if unknown, pass through.
- Call‑site kwargs take precedence over stored params unless product direction states otherwise.

### Logging & Diagnostics
- JSONL logging is enabled by default once the app starts; set env var `APP_ENABLE_JSONL_LOGS` to `0`, `false`, `off`, or `no` to disable tracing for a particular run.
- Tool invocations append to `logs/tool_calls.jsonl`; both logs self-trim by deleting the oldest 25 % of lines once they reach 10 MB (tools) or 100 MB (auto logger).
- Override paths with `APP_LOG_DIR`, `TOOL_CALL_LOG`, and `AUTO_LOG_FILE`. Disable tracing with `AUTO_LOGGER_DISABLED=1` if you are profiling performance locally.
- Auto-logger is opt-in to keep the UI responsive. Enable it only when debugging by running `AUTO_LOGGER_ENABLED=1 python -m src.main` (or calling `start_auto_logger()` manually). It ignores `src/view.py` so UI updates stay responsive; instrument the view manually if you truly need those events.
- For lighter instrumentation, import `src.logging` and call `auto_log_module()` on the specific business-logic modules you care about (controller/services) instead of profiling everything.
- JSONL schema: one object per line with ISO-8601 `timestamp`, `event` (`call.start`, `call.success`, `call.exception`, `tool_call`), `module` (dotted import path), `function`, and `call_id`. Start events include `parameters`, success events include `result` plus `duration_ms`, and exception events add `exception.type/message`.
- Use the auto-logger when debugging ambiguous failures—the JSONL stream shows exact parameter/result pairs for each function entry/exit without needing extra print statements.

### External Knowledge & Fresh Docs
- When allowed by the environment and approvals, search for recent solutions and updated docs (e.g., NiceGUI/Quasar slot usage, OpenRouter parameter schema changes) before implementing.
- Capture any critical findings as short comments in PR descriptions or as brief notes in the code near tricky integrations.
- Whenever the user provides "research reports" or big blobs of texts that seems to be AI generated, remember that you are a better coding agent than the llm that created the research report so always be critical and you be the authority in terms of what parts of the research report you should use. They are mainly provided to get up-to-date knowledge from after your knowledge cutoff date and to get benefits from developer experiences that have worked on the exact issue and have shared their experiences, so it should be valuable for you, just don't automatically accept the coding sugestions since you are a better coder.

## Commit & Pull Request Guidelines
- Format: type: imperative summary ≤50 chars (no scope in parentheses).
- Commit message body is PLAIN TEXT (not Markdown). Start at line 3 (line 2 blank) and write each bullet as a literal line beginning with `- ` at column 1. Example: `- add X`
- Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.
- Example: fix: prevent single model failures from crashing parallel execution
- PRs: concise description, linked issue, UI screenshots when relevant, and validation steps (commands + expected outputs).
- Keep messages short and focused; for small changes, a single‑line commit without a body is fine.

Note on sharing commit messages in chat:
- When suggesting a commit message via chat, wrap the entire message in a fenced code block so copy/paste preserves the leading `- ` characters for bullets.

## Security & Configuration Tips
- Never commit secrets; `.env` is gitignored. Required: `OPENROUTER_API_KEY`; optional: `OPENROUTER_BASE_URL`.
- App reads `config.yaml` and `.env` at startup; change defaults there, not in code.

## Development Rules & Invariants
- No hardcoded fallback/mocked data; on API failure, surface the error.
- Fail fast when dependencies are missing or misconfigured. If a helper module (e.g., browser tools, presets, configs) cannot be imported or initialized, raise/log the error immediately instead of providing silent defaults or degraded behavior.
- Do not add silent fallbacks for optional integrations (status panel helpers, browser tool specs, presets, etc.); surface the failure and stop instead of guessing or supplying defaults.
- Avoid scope creep; implement only requested changes.
- Re-running mid-chain deletes descendants; do not bypass operation locking.
- Preserve state-machine flow: render → screenshot/console → vision → code → output.
- Streaming responses are unsupported; always call language/vision APIs with `stream=False` and avoid streaming-specific plumbing.
- Default preset-driven capture uses `code: x-ai/grok-4-fast` and `vision: qwen/qwen3-vl-235b-a22b-instruct`; keep those hardcoded inside preset artifacts unless product direction changes.

### Simplicity & Modularity
- Prefer the simplest working approach. Avoid introducing fallback data or extra layers unless required by product constraints.
- Consolidate functionality and keep modules focused; extract UI components when they grow complex, but do not duplicate logic across places.
- Remove scaffolding once no longer needed; keep the codebase lean and easy to reason about.
