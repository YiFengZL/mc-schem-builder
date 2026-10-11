# mc-schem-builder

**English** | [简体中文](README.zh-CN.md)

Generate Minecraft Java 1.21.1 Litematica blueprints (`.schem`) from natural-language building descriptions — with machine-checked verification and an iterative acceptance loop. Also a TRAE Agent Skill: drop it into your workspace and describe a building in chat.

## What It Does

- Turns a sentence into a placeable Litematica blueprint — `.schem` plus preview images, auto-chunked to 256³, targeting Java 1.21.1
- Ships 9 styles, so most requests need only spec edits and no geometry code
- Covers everything else with a declarative geometry layer: lines, circles, ellipses, rectangles, parallelograms, spheres, boxes, half-spaces, function surfaces, and space curves (helix / Bézier / torus knot), composed with boolean ops
- Gates micro-edits with a voxel-level diff that proves everything outside the intended bbox is bit-identical
- Runs a full acceptance loop: static audit plus four-view multimodal review, feeding errors back with coordinates until it passes

## Why It's Useful

- **Nothing fails silently.** Unknown block names are caught before generation against a 1062-block registry extracted from the game jar — Litematica would otherwise swap them for air without a word.
- **The spec is the source of truth.** Iterating means editing fields, not rewriting scripts; the build script reads the spec at runtime.
- **Deliverables come with evidence** — assertion reports, audit numbers against thresholds, and preview images, not "looks fine to me."
- **Dependencies are minimal**: three pure-Python packages, and rendering needs neither PIL nor matplotlib.

## How it Works

mc-schem-builder closes that loop:

1. **Describe the building** — five elements: type, size, materials, key parts, constraints
2. **Declare a spec** — `spec.json` is the single source of truth; iterating means editing fields, not rewriting scripts
3. **Generate** — Track A: preset styles, zero geometry code; Track B: scaffold a script and fill in one `build()` function with declarative geometry
4. **Verify** — per-piece format/completeness/spec assertions, plus a voxel-level diff gate that proves "everything else is bit-identical"
5. **Acceptance loop** — static audit (floating blocks, connectivity, component bboxes, symmetry) plus multimodal vision review of four rendered views; every error comes back with coordinates, type, and severity, and iteration continues until it passes or hits the round cap

## Quick Start

### Prerequisites

- Python 3.10+
- `pip install mcschematic nbtlib numpy`

That's it. Rendering needs no PIL or matplotlib — previews are pure numpy + zlib writing PNG directly.

### Install

```bash
# Copy the whole directory into your TRAE workspace (standard Agent Skills layout)
# <project-root>/.trae/skills/mc-schem-builder/
```

The block registry (`references/block_registry_1_21_1.json` — 1062 blocks and their legal blockstates, extracted from the game client jar) is bundled. This is what stops "silently becomes air". To regenerate it or switch MC versions:

```bash
python scripts/extract_block_registry.py --version 1.21.1
```

### Verify the install

```bash
python scripts/validate_spec.py --list        # 11 styles
python scripts/block_check.py --selfcheck     # 10/10 positives, 9/9 negatives
python scripts/geom_lib.py --selfcheck        # geometry layer regression
python scripts/build_lib.py --selfcheck       # primitives/transforms/materials assertions
```

### End-to-end demo

```bash
python scripts/validate_spec.py --new castle --name demo --out spec.json
python scripts/validate_spec.py --check spec.json
python scripts/gen_schem.py --spec spec.json --out-dir ./out
python scripts/verify_schem.py --assert ./out/demo.schem
python scripts/render_preview.py --schem ./out/demo.schem
```

Expected: all assertions green, plus `demo.schem`, three preview images, and `demo.report.json`.

### Load it in game

1. Copy the `.schem` to **`.minecraft/schematics/`** (not `config/litematica/schematics/`; with version-isolated launchers: `.minecraft/versions/<instance>/schematics/`)
2. In game press `M` → Schematic Manager → Load → Paste Schematic
3. Easy Place is built into Litematica but off by default; full-auto printing needs a third-party Printer mod

> ⚠️ Using Printer / full-auto placement on public servers may violate server rules. This project only generates blueprint files — it never connects to servers or modifies game files.

## Two Tracks

| | **Track A: preset styles** | **Track B: custom geometry** |
|---|---|---|
| When | The build maps to an existing style | Shapes no style covers |
| Entry | `python scripts/gen_schem.py --spec spec.json` | `python scripts/scaffold.py --spec spec.json --out build_x.py` |
| Who writes geometry | Nobody — just edit spec fields | You fill in the `build()` body only (semantic primitives + declarative geometry); never touch the mcschematic API |

Nine styles ship with builders: `castle`, `wall`, `tower`, `bridge`, `modern_villa`, `modern_house`, `spiral_tower`, `curved_roof`, `slant_house`. Two more (`gangxia_north`, `fuzhou_railway_station`) are parameter-only declarations that require Track B. Inspect any style with `python scripts/validate_spec.py --show <style>`.

Don't generate until `validate_spec.py --check` passes — out-of-range parameters, cross-field contradictions, and invalid block names are all caught here.

## Acceptance Loop

One round runs the whole pipeline:

```bash
python scripts/loop_check.py --spec spec.json --round 1
```

| Step | What | Output |
|---|---|---|
| ① Generate + run | `build_<name>.py` (Track A: `gen_schem.py`) | `.schem` + `report.json` |
| ② Assertions | `verify_schem.py --assert` per piece | 8 format/completeness/spec checks |
| ③ Vision | front/side/top/iso views + report template | `<out>/audit/round_N/views/` |
| ④ Static audit | `audit_schem.py` (four dimensions) | `audit.json` structured error list |
| ⑤ State machine | consecutive-pass count / termination | `<out>/.audit_state.json` |

### Static audit

Every metric prints measured value / threshold / verdict:

| Dimension | Metric | Default |
|---|---|---|
| Floating blocks | `floating_ratio`: air below and ≤ 1 solid 26-neighbor | ≤ 0.5% |
| Connectivity | blocks outside the largest 26-connected component | ≤ 0.5% |
| Component bboxes | declared vs actual bbox overruns (`spec.audit.components`) | 0 |
| Symmetry | mirror difference about the declared axis plane | ≤ 2% |

Connectivity uses 26-adjacency — diagonal contact is normal in MC (45° stairs, slanted walls). Strict 6-adjacency counts are reported for reference only.

### Vision review

The four views go to a multimodal model (`scripts/vision_review.py`), which returns a structured report; findings merge into the same error list. Three backends:

```bash
# ① Any OpenAI-compatible gateway (OpenAI / Qwen / Doubao / vLLM / LM Studio / one-api ...)
export VISION_BASE_URL=https://<your-gateway>
export VISION_API_KEY=<key>
export VISION_MODEL=<vision-model>        # e.g. gpt-4o-mini / qwen-vl-max
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto

# ② Local Ollama
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto \
    --vision-backend ollama --vision-model llava:13b

# ③ No model available: export the prompt, review the views manually, ingest the reply
python scripts/vision_review.py --views <views> --bbox=-200,-59,-200,199,-4,199 \
    --prompt-out <views>/review_prompt.txt
```

Without a configured model, `--vision-auto` does **not** silently pass: the round's vision dimension is marked `NOT_RUN` (overall FAIL) and the prompt is exported for backfill. Test the link without a real model: `python scripts/vision_link_test.py --views <views>` (local mock endpoint, 13 assertions).

### Termination

- **2 consecutive rounds** with all dimensions green → `PASS_TERMINATED`
- **5 rounds** without passing → `MAX_ROUNDS_TERMINATED`, printing the best round + unresolved error list

Every error carries a coordinate range, type, and severity. Vague "nope, try again" feedback is rejected by design.

## Iteration Protocol

"Raise the tower 3 blocks" works like this: change spec fields → regenerate into a new version dir → run the diff gate:

```bash
python scripts/verify_schem.py --diff v1/x.schem v2/x.schem --expect-bbox="-3,18,-3,62,24,62"
```

Any change outside the expected bbox fails the gate. Both tracks only edit `spec.json` — Track B scripts read the spec at runtime, so your `build()` geometry is never overwritten.

> Arguments containing negative coordinates must use the `=` form (`--expect-bbox="-3,..."`), or argparse treats them as options.

## Configuration

| Variable | Description |
|---|---|
| `VISION_BASE_URL` | OpenAI-compatible endpoint for vision review |
| `VISION_API_KEY` | API key (not needed for Ollama) |
| `VISION_MODEL` | Vision model name |

Audit thresholds travel with the spec (versioned alongside the build):

```json
"audit": {"symmetry_axis": "x", "float_max": 0.005,
          "components": [{"name": "main hall", "bbox": [-48,0,-14,48,55,57], "tol": 2}]}
```

## Project Layout

```
├── SKILL.md                 # Agent Skill entry point (full workflow + triggers)
├── assets/template_spec.json
├── scripts/
│   ├── gen_schem.py         # Track A: preset styles
│   ├── scaffold.py          # Track B: build() skeleton generator
│   ├── geom_lib.py          # declarative geometry (10 primitives + boolean ops)
│   ├── build_lib.py         # voxel primitives/transforms/materials + chunking
│   ├── validate_spec.py     # spec validation CLI
│   ├── block_check.py       # block name / blockstate static checks
│   ├── verify_schem.py      # assertions + voxel diff gate
│   ├── render_preview.py    # pure-numpy renderer (iso/top/side/vision pack)
│   ├── loop_check.py        # acceptance loop driver (steps ①–⑤)
│   ├── audit_schem.py       # static audit (four dimensions) + state machine
│   ├── vision_review.py     # vision backends (OpenAI-compatible / Ollama / manual ingest)
│   ├── vision_link_test.py  # vision link self-test (mock endpoint)
│   ├── palette_1_21_1.py    # version constants + facing constructors
│   └── extract_block_registry.py
├── references/
│   ├── styles/              # 11 style definitions (params + cross-field constraints)
│   ├── pitfalls.md          # field-tested pitfalls log
│   ├── spec_schema.md       # spec.json field reference
│   ├── api_mcschematic.md   # tested mcschematic API signatures
│   ├── block_ids_1_21_1.md  # common blocks + facing cheatsheet
│   ├── block_registry_1_21_1.json
│   └── trae_install.md      # TRAE install & environment notes
└── mc_builds/               # regression builds used to test the pipeline end-to-end
```

## Hard Constraints

Breaking these fails silently:

1. **Block names must exist in the target version** — run `block_check.py` before generating.
2. **Stairs/slabs/walls/trapdoors/doors need explicit facing** — use the `p_stairs / p_slab / p_wall / p_door / p_trapdoor` constructors in `palette_1_21_1.py`.
3. **Entities can't go into `.schem`** (armor stands, paintings, item frames, displays).
4. **Chunking is judged by the air-inclusive bounding box** (default cap 256³ per piece), not block count.
5. **`.schem` is read-only in Litematica** — changes mean regenerating from scripts.

## Switching MC Versions

Version-specific code lives in exactly two places: `scripts/palette_<version>.py` (copy the 1.21.1 one, change `MC_VERSION` / `DATA_VERSION`) and `references/block_registry_<version>.json` (re-extract). Confirm the target Litematica still reads Sponge `.schem`.

## Documentation

- [SKILL.md](SKILL.md) — full agent workflow, triggers, delivery checklist
- [references/pitfalls.md](references/pitfalls.md) — pitfalls log (silent failures, loop & vision details)
- [references/trae_install.md](references/trae_install.md) — install, environment notes, in-game loading
- [references/spec_schema.md](references/spec_schema.md) — spec fields and validation rules

## Stack

| Layer | Technology |
|---|---|
| Generation | mcschematic (Sponge `.schem`) |
| Reading back | nbtlib |
| Voxel math & rendering | numpy (hand-rolled PNG via zlib — no PIL) |
| Vision | any OpenAI-compatible multimodal endpoint, or Ollama |
| Target | Minecraft Java 1.21.1 + Litematica |
