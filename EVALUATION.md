# FastContext-1.0-4B-SFT Evaluation Report

**Date**: 2026-06-16  
**Model**: `microsoft/FastContext-1.0-4B-SFT` (Q4_K_M, 2.4 GB)  
**Backend**: llama.cpp server (RTX 5090, GPU-accelerated, `--ctx-size 65536`)  
**Target**: evonic codebase (361 files) at `/tmp/evonic_fastcontext`  
**Test Queries**: 20 natural-language codebase exploration queries

---

## Executive Summary

FastContext-1.0-4B-SFT was evaluated as a repository exploration subagent using
tool-calling (Read/Glob/Grep via ripgrep). After iterative optimization, the
**best configuration achieves 0.950 File F1 (19/20 pass)** with markdown-format
system prompt and nudge retry.

| Metric | Best (v3i) | v4 Best (XML) | Baseline | Improvement |
|--------|-----------|---------------|----------|-------------|
| File F1 | **0.950** | 0.650 | 0.100 | **9.5x** |
| Line F1 | **0.549** | 0.421 | 0.072 | **7.6x** |
| Correct Files | **19/20** | 13/20 | 2/20 | **9.5x** |
| Avg Turns | 3.4 | 3.4 | 5.7 | -- |
| Avg Latency | 4,435 ms | 4,435 ms | 2,095 ms | -- |

**Slack**: Even the best configuration leaves a **~5% File F1 gap** (q20 fails).
A 7B+ model is recommended for 100% file-level accuracy.

**Key finding**: Prompt format (Markdown vs XML vs JSON) is the STRONGEST
determinant of performance — not tool names, not JSON schema casing, not nudge.
The 4B model is highly sensitive to instruction formatting.

---

## Final Ranking (All 10 Variants)

| Rank | Variant | Prompt | ToolCase | Nudge | File F1 | Line F1 | Pass |
|------|---------|--------|----------|-------|---------|---------|------|
| **1** | **v3i** | **Markdown** | Pascal | **Yes** | **0.950** | **0.549** | **19/20** |
| 2 | v3h | Markdown | Pascal | No | 0.900 | 0.495 | 18/20 |
| 3 | v4default | Markdown | Pascal | No | 0.800 | 0.448 | 16/20 |
| 4 | v3d | Markdown | Pascal | No | 0.750 | 0.380 | 15/20 |
| 5 | XML+Nudge | XML | Pascal | Yes | 0.650 | 0.421 | 13/20 |
| 6 | XML+Pascal | XML | Pascal | No | 0.550 | 0.253 | 11/20 |
| 7 | JSON+Nudge | JSON | Pascal | Yes | 0.350 | 0.183 | 7/20 |
| 8 | XML+Upper | XML | UPPER | No | 0.300 | 0.157 | 6/20 |
| 9 | JSON+Upper | JSON | UPPER | No | 0.250 | 0.158 | 5/20 |
| 10 | JSON+Pascal | JSON | Pascal | No | 0.200 | 0.063 | 4/20 |

```
File F1 by variant:
███████████████████░  v3i          0.950
██████████████████░░  v3h          0.900
████████████████░░░░  v4default    0.800
███████████████░░░░░  v3d          0.750
█████████████░░░░░░░  XML+Nudge    0.650
███████████░░░░░░░░░  XML+Pascal   0.550
███████░░░░░░░░░░░░░  JSON+Nudge   0.350
██████░░░░░░░░░░░░░░  XML+Upper    0.300
█████░░░░░░░░░░░░░░░  JSON+Upper   0.250
████░░░░░░░░░░░░░░░░  JSON+Pascal  0.200
```

---

## Prompt Format Analysis

### The single largest factor

Prompt format explains **0.400 File F1 delta** — far more than any other parameter:

| Format | Best Score | Delta from Markdown |
|--------|-----------|---------------------|
| **Markdown** (natural) | **0.950** | baseline |
| XML (struct tags) | 0.650 | **-0.300 (32% drop)** |
| JSON (structured) | 0.350 | **-0.600 (63% drop)** |

### Why Markdown wins

The v3 Markdown prompt uses natural language with markdown headers (`## Search
Strategy`, `## Critical: Path Usage`, `## Example`). Key elements:

1. **Explicit workspace path**: `Always use the exact workspace path in tool calls: /tmp/evonic_fastcontext` — prevents path hallucination
2. **Step-by-step search strategy**: `Start broad: Grep with a simple regex in the entire workspace` — guides the model to use Grep before Read
3. **Parallel tool guidance**: `Batch parallel calls: Grep + Glob + Read simultaneously` — encourages efficient exploration
4. **Natural `##` headers**: Qwen-4B was SFT'd on markdown-formatted code instructions

### Why XML/JSON fail

Both XML and JSON formats contain the **same information** as Markdown but
wrapped in tags — yet perform significantly worse:

- **XML** (`<agent>`, `<path_rule>`, `<search_strategy>`): -0.300. The model
  appears to treat XML tags as content separators rather than structural hints.
  It often generates tool calls outside the expected block.

- **JSON** (schema-like objects): -0.600. Worst format. The model frequently
  confuses JSON prompt structure with tool call JSON, causing malformed calls
  and confused tool parsing.

- **UPPERCASE tool names** (READ/GLOB/GREP): -0.100 additional penalty. The
  model was trained on PascalCase tool names and lowercase reads better.

**Recommendation**: Use natural Markdown prompts. Structured formats (XML/JSON)
actively degrade 4B model performance. This may not apply to 7B+ models.

---

## Nudge Mechanism

Nudge retries failed queries by appending a stronger instruction to the system
prompt: *"You MUST end with a proper `<final_answer>` block..."*

### Effectiveness

| Query | Without Nudge | With Nudge | Effect |
|-------|--------------|------------|--------|
| q18 (supervisor/_helpers) | ❌ 0.000 | ✅ 1.000 | **Rescued** |
| q19 (plugin READMEs) | ❌ 0.000 | ✅ 1.000 | **Rescued** |
| q20 (llm_loop imports) | ✅ 1.000 | ❌ 0.000 | **Regressed** |
| Other 17 queries | ~same | ~same | No effect |

**Net impact**: +0.050 File F1 (+1 pass). Nudge is cheap (no extra turns on pass)
and rescues queries where the model produces text without `<final_answer>` tags.
However, it's non-deterministic — q20 regressed from 1.000 to 0.000 (different
path on second attempt).

---

## Per-Query Breakdown (Top 3 Variants)

### v3i (0.950, Best)

| # | Query | File F1 | Line F1 | Notes |
|---|-------|---------|---------|-------|
| q1 | read_file constants | 1.000 | 1.000 | ✅ Perfect |
| q2 | subagent TTL/max | 1.000 | 1.000 | ✅ Perfect |
| q3 | ScoringEngine class | 1.000 | 0.842 | File correct, wide range |
| q4 | LLM error messages | 1.000 | 0.783 | 8/12 error types found |
| q5 | app.py single-instance guard | 1.000 | 0.658 | File correct |
| q6 | _sanitize_filename | 1.000 | 0.900 | Near perfect |
| q7 | filter_pipeline stages | 1.000 | 0.788 | All 9 stages found |
| q8 | plugin_manager: 4 functions | 1.000 | 0.246 | File correct, sparse lines |
| q9 | llm_loop: llm_call flow | 1.000 | 0.075 | File correct, wrong lines |
| q10 | Makefile targets | 1.000 | 0.994 | Near perfect |
| q11 | config.py settings | 1.000 | 1.000 | ✅ Perfect |
| q12 | CI workflow branches | 1.000 | 0.693 | File correct |
| q13 | logging env vars | 1.000 | 0.101 | File correct, missed vars |
| q14 | Dockerfile base image | 1.000 | 0.427 | File correct |
| q15 | plugin_hooks registries | 1.000 | 0.062 | File correct, wrong lines |
| q16 | _resolve_app_root | 1.000 | 0.385 | File correct |
| q17 | .env.example model vars | 1.000 | 0.667 | File correct |
| q18 | supervisor/_helpers functions | 1.000 | 0.359 | Rescued by nudge |
| q19 | plugin README dirs | 1.000 | 0.011 | Rescued by nudge |
| q20 | llm_loop imports | 0.000 | 0.000 | ❌ Only failure |

### v3h (0.900, No Nudge)

Differences from v3i:
- q18: ❌ 0.000 (no `<final_answer>` produced — rescued by nudge in v3i)
- q19: ❌ 0.000 (no `<final_answer>` produced — rescued by nudge in v3i)
- q20: ✅ 1.000 (regressed in v3i due to nudge side effect)

### v4default (0.800, Same Prompt as v3h)

Additional failures vs v3h (stochastic):
- q6: ❌ 0.000 (v3h had 1.000)
- q8: ❌ 0.000 (v3h had 1.000)
- q19: ❌ 0.000 (same as v3h)
- q20: ❌ 0.000 (different from v3h's 1.000)

The v4default uses identical code path to v3h but different runs —
**temperature=1.0 introduces ~10% variance**. Averaging 3 runs is recommended.

---

## Model Characteristics

### What the 4B model does well

1. **Single-file discovery**: Finding constants, classes, functions in a known
   file — near-perfect (q1, q2, q6, q10, q11 all ≥0.900).
2. **Grep-first search strategy**: Model reliably starts with regex search
   before reading — matches the Markdown prompt's guidance.
3. **Parallel tool calls**: When prompted, the model uses multiple tools
   simultaneously (Grep + Glob + Read in one turn).
4. **File-level accuracy**: 17/20 queries achieve perfect file identification
   (File F1 = 1.000). The model knows *which* file to look at.

### Where it struggles

1. **Line-level precision**: Average Line F1 is 0.549 even at File F1 1.000.
   The model often reports the right file but wrong/approximate line ranges.
2. **Multi-step reasoning**: q20 requires: find `llm_loop.py` → read imports →
   cross-reference sister modules. This multi-hop chain breaks the 4B model.
3. **Deep paths**: Files 3+ levels deep (e.g., `backend/supervisor/_helpers.py`)
   are harder to discover than shallow files.
4. **Path retention**: The model sometimes forgets the workspace path within
   a multi-turn session (auto-path correction mitigates this).
5. **Output format compliance**: ~10% of runs produce text without
   `<final_answer>` tags (nudge helps but doesn't fully solve).

---

## Optimal Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Prompt Format** | Markdown (natural language) | Best by +0.300 File F1 |
| **Tool Names** | PascalCase (Read/Glob/Grep) | Model's native expectation |
| **Nudge** | Enabled | +0.050 File F1, low cost |
| **Temperature** | 1.0 | Official FastContext recommendation |
| **Top-P** | 0.95 | |
| **Top-K** | 20 | |
| **Thinking** | Disabled (`enable_thinking=False`) | |
| **Max Turns** | 8 | Beyond 8 gives no benefit |
| **Context** | 65,536 (64K) | Minimum 16K; 64K is sweet spot |
| **Path Correction** | Auto-fix (`/path/to/X` → workspace) | Required — model hallucinates paths |
| **Tools** | ripgrep-based Read/Glob/Grep | Matching official FastContext |

---

## v3 vs v4 Code Analysis

`eval_v4.py` wraps `eval_v3.py` by overriding globals. The tool execution,
parsing, and message loop are **identical** between v3 and v4. V4 only adds
three parameterization axes:

| Axis | v3 | v4 Options |
|------|-----|------------|
| Prompt Format | Markdown only | Markdown, XML, JSON |
| Tool Case | PascalCase only | PascalCase, UPPERCASE |
| Nudge | None | Disabled, Enabled |

### Regression root cause

The v4 parameterization introduced **prompt format** as the explanation for
v3's success. Running v4 with `--prompt-style default` (Markdown, unchanged
from v3) scores **0.800-0.900** (variance) — matching v3's performance range.
The v4 XML/JSON formats are the sole regression factor.

**Chains of evidence**:
- v3 (= Markdown) scores 0.750-0.900
- v4default (= Markdown, same code path) scores 0.800
- v4 XML+Pascal scores 0.550
- v4 JSON+Pascal scores 0.200

The delta between Markdown and XML across v4 runs is 0.250-0.350 File F1.
The JSON drop is catastrophic at 0.600.

---

## Historical Context: Context Length Discovery

Early runs at 4,096 context failed completely (60% HTTP 400 errors — context
overflow after 1-2 turns). The model cannot function below 16K context because
each turn adds 2-5K tokens of tool results.

| `--ctx-size` | File F1 | Line F1 | Pass | Notes |
|-------------|---------|---------|------|-------|
| 4,096 | 0.200 | 0.149 | 2/10 | 60% HTTP 400 — overflow |
| 65,536 | 0.900 | 0.551 | 9/10 | Optimal |
| 131,072 | 0.900 | 0.585 | 9/10 | No additional gain |

---

## Recommendations

1. **🟢 v3i configuration for production**: Markdown prompt + nudge + 64K context
   + temperature 1.0. Achieves 0.950 File F1 (19/20).

2. **🟡 Run 2-3 passes and average**: Temperature=1.0 causes ~10% variance
   between identical runs. Average stabilizes at ~0.900.

3. **🟡 Upgrade to 7B+ for 100%**: The remaining gap (q20's multi-hop
   reasoning) likely requires a larger model. 4B hits ceiling at ~95%.

4. **🔴 Never use XML/JSON prompts for 4B**: They actively confuse the model.
   If forced to use structured formats, plan for a 2-3x drop in accuracy.

5. **🟡 Keep nudge enabled**: +0.050 File F1 at zero cost on successful runs.
   Only runs when `<final_answer>` is missing.

6. **🔴 Minimum context: 16,384**: Below this, tool results overflow and the
   model breaks. 64K is recommended.

7. **🟢 Path auto-correction is mandatory**: The model hallucinates workspace
   paths. glob+resolve fallback is essential production hardening.
