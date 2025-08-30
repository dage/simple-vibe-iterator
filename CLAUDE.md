# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Simple Vibe Iterator is an AI-driven development system for graphical web applications that uses autonomous vision-guided iteration loops with open source models. The system implements a state-machine architecture where each iteration is a state transition δ producing a new `IterationNode`.

## Development Commands

### Environment Setup
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers (required)
python -m playwright install

# Setup environment variables
cp .env_template .env  # Fill in OPENROUTER_BASE_URL and OPENROUTER_API_KEY
```

### Running the Application
```bash
# Start the GUI application
python -m src.main
# Access at http://localhost:8080
```

### Testing
```bash
# Run all integration tests
python integration-tests/run_all.py

# Run specific test categories
python integration-tests/test_state_machine.py         # State machine validation
python integration-tests/test_openrouter_integration.py  # OpenRouter API tests
python integration-tests/test_openrouter_e2e_ping.py    # End-to-end workflow test

# Run tests with parallelization
python integration-tests/run_all.py -j 4  # 4 parallel jobs
```

## Architecture

### Core Components
- **`src/interfaces.py`**: Data structures (`IterationNode`, `TransitionSettings`, `TransitionArtifacts`) and service protocols (`AICodeService`, `BrowserService`, `VisionService`)
- **`src/controller.py`**: Framework-agnostic iteration controller with pure transition function δ
- **`src/services.py`**: OpenRouter AI services and Playwright browser service implementations
- **`src/view.py`**: NiceGUI-based UI layer (only UI code)
- **`src/main.py`**: Dependency injection and application entry point

### State Machine Flow
Each iteration follows: `render → screenshot/console → vision analysis → code prompt → new HTML output`

1. Optional input rendering when `html_input` exists
2. Vision analysis of input screenshot using configurable templates
3. Code generation with context from vision analysis and console logs
4. Output rendering to produce final artifacts

### Configuration System
- **`config.yaml`**: Default models and prompt templates loaded at startup
- Templates support variable substitution: `{overall_goal}`, `{user_steering}`, `{vision_output}`, `{console_logs}`, `{html_input}`
- Models configurable per iteration via UI settings

### Service Architecture
- Clean protocol separation allows service swapping (currently OpenRouter-only)
- Browser service handles HTML rendering and screenshot capture via Playwright
- AI services handle code generation and vision analysis
- Operation status tracking with phase indicators

### Data Flow
- Artifacts stored in `artifacts/` directory (Git ignored)
- Screenshots and HTML files named by content hash
- Console logs captured and formatted for AI analysis
- Vision analysis feeds into code generation context

## Git Commit Messages

When asked to suggest a commit message, always:

1. **First analyze recent commit history**: Run `git log -5` (not --oneline) to examine the last 5 full commit messages (including multiline bodies) and understand the existing style, tone, and level of detail used in this repository.

2. **Follow this repository's simplified conventional format**:
   ```
   type: imperative summary ≤50 characters
   
   Optional body with bullet points explaining what was done.
   Wrap at 72 characters per line.
   ```
   
   **Important**: Do NOT use scope in parentheses (no `type(scope):`). Use simple `type:` format.

3. **Use these commit types**:
   - `feat`: new feature
   - `fix`: bug fix  
   - `docs`: documentation changes
   - `style`: formatting, missing semicolons, etc (no code change)
   - `refactor`: code change that neither fixes bug nor adds feature
   - `perf`: performance improvement
   - `test`: adding/updating tests
   - `build`: build system or external dependencies
   - `ci`: CI configuration changes
   - `chore`: maintenance tasks
   - `revert`: reverting previous commit

4. **One logical change per commit**: Each commit should represent a single, cohesive change.

## Development Notes

- Framework-agnostic controller design enables easy UI framework swapping
- State machine ensures reproducible iteration chains with proper parent-child relationships
- Re-running mid-chain operations automatically delete descendants
- Operation locking prevents concurrent user actions during processing
- Comprehensive integration test suite validates state machine behavior and OpenRouter connectivity

## Critical Development Rules

- **NEVER add hardcoded fallback data or mock data** - always use real API responses. If API fails after all retries, throw an error instead of returning fallback values (including empty data).
- **NEVER add fields or functionality that wasn't explicitly requested** - avoid scope creep.
- **Do what has been asked; nothing more, nothing less.**