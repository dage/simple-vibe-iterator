## Simple Vibe Iterator

AI-driven development system for graphical web applications using autonomous vision-guided iteration loops and open source models.

Simple Vibe Iterator extends traditional vibe coding by introducing autonomous vision-guided iteration after an initial single-shot implementation. The system targets graphical intensive web applications including special effects and games.

Current state:
- State-machine architecture: each iteration is a state transition δ producing a new `IterationNode`
- Pipeline in δ: render → screenshot/console → vision analysis → code prompt → new HTML output
- Clean separation: NiceGUI only in `src/view.py`; controller/services are framework‑agnostic
- OpenRouter-backed services for both code and vision are used throughout

UI/UX additions:
- Sticky operation status (top-right): shows current operation and services live phase with elapsed time
  - Example phases: `DevTools: Capture screenshot`, `Code: <code_model>`, `Vision: <vision_model>`
  - Idle state: green icon and “No operation running”
- Operation lock: prevents concurrent user actions (e.g., "Create root" / "Iterate")
  - If an operation is in progress, further clicks are rejected with a warning


### Setup
1. Create and activate a Python environment (Conda recommended).
2. Install Python packages:
```
pip install -r requirements.txt
```
3. Install Chrome DevTools MCP helper (required):
```
npm install -g chrome-devtools-mcp@latest
```
4. Create `.env` from `.env_template` and fill values:
   - Required: `OPENROUTER_BASE_URL` (default provided), `OPENROUTER_API_KEY`

5. The committed `config.yaml` at the project root is the default configuration for every new session. Edit it to change default models and prompts (the UI no longer allows prompt editing; restart after updating the file):
```
models:
  code: z-ai/glm-4.5-air
  vision: meta-llama/llama-3.2-90b-vision-instruct

templates:
  code: |
    ...
  vision: |
    ...
```

We automatically load `.env` and `config.yaml` at app startup. You do not need to `export` variables in your shell.

### Feedback presets
- Declarative capture/key automation lives in `feedback_presets.yaml`. The packaged presets are:
  1. *Single screenshot*: take the frame immediately after DOM ready.
  2. *Short animation*: capture three keyframes in succession without extra input.
  3. *WSAD sweep*: press W, S, A, and D in sequence and capture each resulting view.
  4. *Space press*: capture a frame before the spacebar and another 1 s into a 2 s hold.
- Selecting a preset now replaces the old manual “Input screenshots” mechanism so there’s a single capture pipeline.
- Presets begin executing as soon as the DOM ready event fires, removing the need for artificial waits.
- Input screenshots shown in the UI now display the preset action label (like “press-w” or “frame-2”) instead of just ordinal numbers.
- Set `FEEDBACK_PRESETS_PATH` to point at an alternate YAML file when iterating locally (tests use this to inject harness presets).
- Keypress actions keep the key held for the configured `duration_ms` while subsequent waits/screenshots run. Screenshots therefore happen *between* the key-down and key-up events.
- Defaults hardcode the requested models (`code: x-ai/grok-4-fast`, `vision: qwen/qwen3-vl-235b-a22b-instruct`) inside the preset file so you can re-run the same capture recipe across projects.
- Templates now receive `{auto_feedback}` summarizing the preset step descriptions (e.g., `#1: press-w, #2: press-s`) so both vision and coding models can connect each screenshot to the action that produced it.

### Run state-machine tests
Execute:
```
python integration-tests/test_state_machine.py
```
This will:
- Validate linear chains and correct parent-child links
- Verify re-running mid-chain deletes descendants and applies updated settings
- Ensure artifacts exist (screenshot, console logs, vision output)
- Verify prompt template substitution uses `{html_input}`, `{user_feedback}`, `{auto_feedback}`, `{overall_goal}`, `{vision_output}`

### Run OpenRouter integration checks
Execute:
```
python integration-tests/test_openrouter_integration.py
```
This will:
- Validate required env vars
- Call models.list()
- Run a trivial chat on the code model
- Run a simple vision check on a generated image

### Run end-to-end OpenRouter ping
Execute:
```
python integration-tests/test_openrouter_e2e_ping.py
```
This will:
- Skip if required env vars are missing
- Create a root iteration using OpenRouter-backed services
- Assert that HTML output, screenshot, console logs, and vision analysis are produced

### Run the GUI prototype
```bash
./start.sh
```
`start.sh` simply runs `python -m src.main`; the Chrome DevTools MCP server spins up automatically on demand via the launcher embedded in the app whenever a tool call occurs.

Workflow:
- Create a root node: enter the overall goal, then click "Create root" (the coding agent creates the initial HTML).
- Each node card shows: HTML Input (empty for root), HTML Output, Screenshot, Console Logs, and Vision Analysis.
- Edit settings for the next iteration (model slugs default from `config.yaml`, plus optional user feedback text).
- Click "Iterate" on any node to create its next child; descendants of that node are replaced by the new chain.
 - While an operation is running, the status shows the current phase and seconds elapsed; other actions are temporarily blocked.

### Model selector harness
Use the NiceGUI harness to exercise the `ModelSelector` with deterministic mock data before changing its columns or iconography. It now focuses solely on coding models so tool-access updates can be previewed without vision-model noise while still showing which code models accept vision input:

```bash
# interactive server (http://localhost:8060)
python experiments/model_selector_artifact.py --serve

# headless capture written to artifacts/experiments/model_selector/*.png
python experiments/model_selector_artifact.py --capture
```

The harness pre-populates the code-model selector, auto-expands the dropdown, and lists the mock capability matrix (tracking text, vision, and tool capabilities) so you can verify upcoming UI changes without hitting OpenRouter.

### JavaScript code interpreter tool
- Every code/vision model that supports tool-calling now receives the Chrome DevTools tool suite (load_html, analyze_screen, list_console_messages, press_key, evaluate_script, wait_for, performance_start_trace, performance_stop_trace).
- The tool captures `console.log` output and returns both the evaluated result and logs so model reasoning can cite concrete evidence.
- Each invocation is appended to `logs/tool_calls.jsonl` (JSON per line) so you can audit which model ran which snippet and what the tool returned.

### Logging & diagnostics
- `src/logging.py` centralizes structured logging. All JSONL logs live in `logs/` by default (override with `APP_LOG_DIR`).
- Tool executions append to `logs/tool_calls.jsonl` and truncate themselves when the file reaches 10 MB by removing the oldest 25 % of entries.
- The auto-logger captures every function call inside `src/` via `sys.setprofile` and writes the traces to `logs/auto_logger.jsonl`, trimming back to 75 % of its 100 MB budget when necessary.
- Logs are line-delimited JSON with ISO timestamps so you can grep for modules/functions and replay what happened without enabling verbose console output.
- Disable the auto-logger via `AUTO_LOGGER_DISABLED=1` or override individual paths with `TOOL_CALL_LOG` / `AUTO_LOG_FILE` when running experiments in isolated directories.
- These logs are the first stop for debugging “what happened?” questions, especially in headless or CI runs where reproducing the UI can be costly.

### Architecture overview
- `src/interfaces.py`: dataclasses and service/controller interfaces (no UI deps)
- `src/controller.py`: framework-agnostic iteration controller
- `src/services.py`: OpenRouter AI services + Chrome DevTools browser service
- `src/message_history.py`: message history dialog renderer
- `src/view.py`: NiceGUI view (UI only); reads defaults from `config.yaml` via `src/config.py`
- `src/main.py`: dependency wiring and app entry (OpenRouter-only)

Notes:
- Screenshots/HTML are written to `artifacts/` (ignored by Git).
- Configuration is read from `config.yaml`; templates are not duplicated in code.

### Model parameters
- Per‑model parameters are editable from the UI “Params” dialog and stored in `model_params.json`.
- We fetch each model’s `supported_parameters` from OpenRouter and show them as fields.
- Parameters are sent to OpenRouter via the OpenAI client’s `extra_body`, which allows provider‑specific keys (e.g., `reasoning`, `include_reasoning`).
- Enter booleans/numbers/objects as JSON to ensure correct typing. Examples:
  - `include_reasoning`: `true`
  - `reasoning`: `{ "effort": "high" }`
### Feedback preset harness
Execute:
```
python integration-tests/test_feedback_presets.py
```
This integration test spins up the Chrome DevTools browser harness against embedded HTML, runs a temporary preset (with long-duration key holds), and uses Pillow to confirm the screenshot captured the key-down state while also verifying that key-up events eventually fire via console logs.
