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
  - Example phases: `Playwright: Capture screenshot`, `Code: <code_model>`, `Vision: <vision_model>`
  - Idle state: green icon and “No operation running”
- Operation lock: prevents concurrent user actions (e.g., "Create root" / "Iterate")
  - If an operation is in progress, further clicks are rejected with a warning


### Setup
1. Create and activate a Python environment (Conda recommended).
2. Install Python packages:
```
pip install -r requirements.txt
```
3. Install Playwright browsers (required):
```
python -m playwright install
```
4. Create `.env` from `.env_template` and fill values:
   - Required: `OPENROUTER_BASE_URL` (default provided), `OPENROUTER_API_KEY`

5. The committed `config.yaml` at the project root is the default configuration for every new session. Edit it to change default models and templates:
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

### Run state-machine tests
Execute:
```
python integration-tests/test_state_machine.py
```
This will:
- Validate linear chains and correct parent-child links
- Verify re-running mid-chain deletes descendants and applies updated settings
- Ensure artifacts exist (screenshot, console logs, vision output)
- Verify prompt template substitution uses `{html_input}`, `{user_steering}`, `{overall_goal}`, `{vision_output}`

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
python -m src.main
```
Then open `http://localhost:8055`.

Workflow:
- Create a root node: enter the overall goal, then click "Create root" (the coding agent creates the initial HTML).
- Each node card shows: HTML Input (empty for root), HTML Output, Screenshot, Console Logs, and Vision Analysis.
- Edit settings for the next iteration (model slugs default from `config.yaml`, plus instructions/templates/overall goal).
- Click "Iterate" on any node to create its next child; descendants of that node are replaced by the new chain.
 - While an operation is running, the status shows the current phase and seconds elapsed; other actions are temporarily blocked.

### Architecture overview
- `src/interfaces.py`: dataclasses and service/controller interfaces (no UI deps)
- `src/controller.py`: framework-agnostic iteration controller
- `src/services.py`: OpenRouter AI services + Playwright browser service
- `src/view.py`: NiceGUI view (UI only); reads defaults from `config.yaml` via `src/config.py`
- `src/main.py`: dependency wiring and app entry (OpenRouter-only)

Notes:
- Screenshots/HTML are written to `artifacts/` (ignored by Git).
- Configuration is read from `config.yaml`; templates are not duplicated in code.
