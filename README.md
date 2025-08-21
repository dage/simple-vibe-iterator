## Simple Vibe Iterator

AI-driven development system for graphical web applications using autonomous vision-guided iteration loops and open source models.

Simple Vibe Iterator extends traditional vibe coding by introducing autonomous vision-guided iteration after an initial single-shot implementation. The system targets graphical intensive web applications including special effects and games.

Minimal scaffold focused on:
- OpenRouter client (`src/or_client.py`)
- Playwright screenshot helper (`src/playwright_browser.py`)
- Integration test (`integration-tests/test_openrouter_integration.py`)


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
4. Create your `.env` from the committed template and fill values:
```
cp .env_template .env
```
- Required to fill: `VIBES_API_KEY`
- Defaults provided in the template (you may keep these): `OPENROUTER_BASE_URL`, `VIBES_CODE_MODEL`, `VIBES_VISION_MODEL`

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