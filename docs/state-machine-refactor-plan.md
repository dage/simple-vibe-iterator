# State Machine Architecture Refactor (v3.1)

## Overview

Adopt a state-machine model where each iteration is a **state transition**. An iteration consumes one HTML blob as input, applies internal processing (render → screenshot → vision → code generation), and produces one HTML blob as output. All external dependencies (model slugs, instructions, and templates) live within the iteration node.

All changes will be implemented on a new feature branch: `feature/state-machine-refactor`.

## Terminology and Principles

- **IterationNode**: Canonical record for one iteration.
  - `html_input`: HTML string consumed by this iteration.
  - `html_output`: HTML string produced by this iteration.
  - `settings`: All external dependencies, instructions, and templates for this iteration:
    - `code_model`: slug (default from `VIBES_CODE_MODEL` env var)
    - `code_instructions`: short freeform instructions for the code model
    - `vision_model`: slug (default from `VIBES_VISION_MODEL` env var)
    - `vision_instructions`: short freeform instructions for the vision model
    - `overall_goal`: high-level goal text provided before iterations begin
    - `code_template`: templated string to build the final code-model prompt. Supports placeholders like `{html_input}`, `{code_instructions}`, `{overall_goal}`, `{vision_output}`
    - `vision_template`: templated string to build the final vision-model prompt. Supports placeholders like `{html_input}`, `{vision_instructions}`, `{overall_goal}`
  - `artifacts`: Outputs belonging to this iteration:
    - `screenshot_filename`: filename of the rendered screenshot
    - `console_logs`: list of strings
    - `vision_output`: textual analysis from vision model
  - `parent_id`: ID of the parent iteration or `None` for the root.

- **State Transition** (`δ`): Pure async function mapping `(html_input, settings)` → `(html_output, artifacts)`.
  - Contract: No hidden state. Exactly one new `IterationNode` per transition.

- **No State Duplication**: All information for an iteration is stored only in its `IterationNode`. No parallel or implicit caches.

- **Linear Re-run Behavior**: Re-running a non-leaf node deletes its descendants and creates a new chain from that point (no branching in this phase).

- **Minimalistic & DRY**: Avoid repetitive code and extensive defensive checks. Keep core pipeline concise.

## Refactor Plan

### 1. Data Model

Define `IterationNode` in `src/interfaces.py`:

```
@dataclass
class IterationNode:
    id: str
    parent_id: str | None
    html_input: str
    html_output: str
    settings: TransitionSettings
    artifacts: TransitionArtifacts
```

Define helper dataclasses:

```
@dataclass
class TransitionSettings:
    code_model: str
    code_instructions: str
    vision_model: str
    vision_instructions: str
    overall_goal: str
    code_template: str
    vision_template: str

@dataclass
class TransitionArtifacts:
    screenshot_filename: str
    console_logs: List[str]
    vision_output: str
```

### 2. Controller Refactor

In `src/controller.py`:

- Rename `SessionController` → `IterationController`.
- Rename `SessionData` → `IterationNode`.
- Implement `apply_transition(from_node_id: str, settings: TransitionSettings) -> str`:
  1. Delete all descendants of `from_node_id`.
  2. Fetch `html_input` from the target node.
  3. Call δ(html_input, settings) → `(html_output, artifacts)`.
  4. Persist a new `IterationNode` with `parent_id = from_node_id` and return its `id`.
- Add methods:
  - `get_node(id: str) -> IterationNode`
  - `get_children(id: str) -> List[IterationNode]`

### 3. Pure Transition Function

Extract existing pipeline into a reusable function in `src/controller.py`:

```
async def δ(
    html_input: str,
    settings: TransitionSettings
) -> tuple[str, TransitionArtifacts]:
    # 1. Render html_input via Playwright → screenshot_filename, console_logs
    # 2. Build vision prompt from settings.vision_template using {html_input}, {vision_instructions}, {overall_goal}; call vision model (settings.vision_model) → vision_output
    # 3. Build code prompt from settings.code_template using {html_input}, {code_instructions}, {overall_goal}, {vision_output}
    # 4. Call code model (settings.code_model) with the built prompt → html_output
    return html_output, TransitionArtifacts(
        screenshot_filename=screenshot_filename,
        console_logs=console_logs,
        vision_output=vision_output,
    )
```

### 4. UI Updates

In `src/view.py`:

- Default `code_model` and `vision_model` fields in the settings panel to the environment variables `VIBES_CODE_MODEL` and `VIBES_VISION_MODEL`.
- Allow users to edit these slugs, instructions, templates, and `overall_goal` before clicking **Iterate**.
- On **Iterate** from any card:
  1. Read the current node’s settings; apply any edits the user made.
  2. Call `apply_transition(node.id, updated_settings)`.
  3. Replace any existing descendants in the UI with the newly generated chain.

### 5. Acceptance Tests

Create simple end-to-end tests in `integration-tests/`:

1. **Linear Chain**:
   - Create root node via initial `html_input` and `overall_goal`.
   - Iterate twice.
   - Assert there are 3 nodes with correct parent-child links.

2. **Re-run Mid-Chain**:
   - Build a chain of length 3.
   - Re-run the second node with modified `code_model`.
   - Assert its descendants were deleted and one new child exists.

3. **Artifact Presence**:
   - For each node, assert `screenshot_filename` exists in `artifacts/`.
   - Assert `console_logs` and `vision_output` are non-empty strings.

4. **Settings Propagation**:
   - Change `code_model` or `code_instructions` in the UI settings for a mid-chain node.
   - After iteration, assert the new node’s corresponding setting equals the edited value.

## Non-Goals (v3)

- No temperature or token-limit parameters.
- No graph branching or multi-branch UI.
- No rendering settings in iteration settings.
- No defensive boilerplate; trust data validity.

---

*All changes on branch `feature/state-machine-refactor`.*