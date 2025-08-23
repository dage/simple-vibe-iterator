## Simple Vibe Iterator

AI-driven development system for graphical web applications using autonomous vision-guided iteration loops and open source models.

Simple Vibe Iterator extends traditional vibe coding by introducing autonomous vision-guided iteration after an initial single-shot implementation. The system targets graphical intensive web applications including special effects and games.

Current state:
- State-machine architecture: each iteration is a state transition δ producing a new `IterationNode`
- Pipeline in δ: render → screenshot/console → vision analysis → code prompt → new HTML output
- Clean separation: NiceGUI only in `src/view.py`; controller/services are framework‑agnostic
- OpenRouter integration verified by tests; not required for GUI


### Setup
1. Create and activate a Python environment (Conda recommended but optional).
2. Install Python packages:
```
pip install -r requirements.txt
```
3. Install Playwright browsers (required):
```
python -m playwright install
```
4. (Optional) For OpenRouter integration tests, create `.env` from the template and fill values:
   - Required: `VIBES_API_KEY`
   - Defaults provided: `OPENROUTER_BASE_URL`, `VIBES_CODE_MODEL`, `VIBES_VISION_MODEL`

### Run state-machine tests
Execute:
```
python integration-tests/test_state_machine.py
```
This will:
- Validate linear chains and correct parent-child links
- Verify re-running mid-chain deletes descendants and applies updated settings
- Ensure artifacts exist (screenshot, console logs, vision output)
- Verify prompt template substitution uses `{html_input}`, `{code_instructions}`, `{overall_goal}`, `{vision_output}`

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

### Run the GUI prototype
```bash
python -m src.main
```
Then open `http://localhost:8080`.

Workflow:
- Create a root node: enter the overall goal, then click "Create root" (the coding agent creates the initial HTML).
- Each node card shows: HTML Input (empty for root), HTML Output, Screenshot, Console Logs, and Vision Analysis.
- Edit settings for the next iteration (model slugs default from `VIBES_CODE_MODEL`/`VIBES_VISION_MODEL`, plus instructions/templates/overall goal).
- Click "Iterate" on any node to create its next child; descendants of that node are replaced by the new chain.

### Architecture overview
- `src/interfaces.py`: dataclasses and service/controller interfaces (no UI deps)
- `src/controller.py`: framework-agnostic iteration controller
- `src/services.py`: stub AI services + Playwright browser service
- `src/view.py`: NiceGUI view (UI only)
- `src/main.py`: dependency wiring and app entry

Notes:
- Screenshots/HTML are written to `artifacts/` (ignored by Git).