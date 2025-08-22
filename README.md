## Simple Vibe Iterator

AI-driven development system for graphical web applications using autonomous vision-guided iteration loops and open source models.

Simple Vibe Iterator extends traditional vibe coding by introducing autonomous vision-guided iteration after an initial single-shot implementation. The system targets graphical intensive web applications including special effects and games.

Current state:
- Iteration loop working: stub AI services + Playwright render/screenshot/console logs
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

### Run integration checks
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
- Enter an initial prompt at the top; it disappears after first use.
- Each iteration renders a card with HTML, Screenshot (expand to view), Console Logs, and Vision Analysis.
- Feedback is optional. Click "Show complete prompt" to preview the combined prompt sent on Iterate.
- Click "Iterate" to create the next iteration using the combined prompt.

### Architecture overview
- `src/interfaces.py`: dataclasses and service/controller interfaces (no UI deps)
- `src/controller.py`: framework-agnostic iteration controller
- `src/services.py`: stub AI services + Playwright browser service
- `src/view.py`: NiceGUI view (UI only)
- `src/main.py`: dependency wiring and app entry

Notes:
- Screenshots/HTML are written to `artifacts/` (ignored by Git).