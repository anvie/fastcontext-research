# FastContext-1.0-4B-SFT — Evaluation Pipeline

Systematic evaluation of `microsoft/FastContext-1.0-4B-SFT` as a read-only
repository exploration subagent using tool-calling (Read + Glob + Grep via ripgrep).

**Best result: 0.950 File F1, 19/20 queries passed.**

## Results

| Variant | File F1 | Line F1 | Pass | Key Feature |
|---------|---------|---------|------|-------------|
| **v3i** | **0.950** | **0.549** | **19/20** | Markdown prompt + nudge |
| v3h | 0.900 | 0.495 | 18/20 | Markdown prompt, no nudge |
| v4default | 0.800 | 0.448 | 16/20 | Same prompt, stochastic variance |
| XML+Nudge | 0.650 | 0.421 | 13/20 | Best XML variant |
| XML+Pascal | 0.550 | 0.253 | 11/20 | XML, no nudge |
| JSON+Pascal | 0.200 | 0.063 | 4/20 | Worst format |

Full variant matrix (10 variants) and per-query analysis in [EVALUATION.md](EVALUATION.md).

## Setup

### Prerequisites

- llama.cpp server with model loaded (OpenAI-compatible endpoint)
- Python 3.8+
- ripgrep (`rg`) installed
- Git (clone target repo)

### Model

Q4_K_M GGUF (2.4 GB):
```
~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf
```

### Server

```bash
tmux new-session -d -s llm \
  "cd ~/dev/llama-cpp/build && ./bin/llama-server \
    -m ~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf \
    --host 0.0.0.0 --port 8080 \
    --ctx-size 65536 --temp 1.0 \
    --n-gpu-layers 99"
```

### Target Repository

```bash
git clone https://github.com/anvie/fastcontext-research /tmp/evonic_fastcontext
```

## Usage

### Quick evaluation (v3, best config)

```bash
cd ~/dev/fastcontext
python3 src/eval.py data/queries.jsonl v3i
```

### Variant sweep (v4)

```bash
python3 src/eval_v4.py data/queries.jsonl <run_name> \
  --prompt-style default|xml|json \
  --tool-case pascal|upper \
  --nudge
```

### Examples

```bash
# Best config (Markdown + PascalCase + nudge)
python3 src/eval_v4.py data/queries.jsonl v3i --prompt-style default --nudge

# XML format, no nudge
python3 src/eval_v4.py data/queries.jsonl xml_test --prompt-style xml

# JSON format, UPPERCASE tools, with nudge
python3 src/eval_v4.py data/queries.jsonl json_test --prompt-style json --tool-case upper --nudge
```

### Output

| File | Description |
|------|-------------|
| `results/scores_{name}.json` | Per-query file/line F1 scores |
| `results/trajectories_{name}.jsonl` | Turn-by-turn tool-call traces |
| `results/eval_{name}.log` | Runtime log with summary |

## Optimal Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| Prompt format | Markdown (natural language) | **Critical**: XML/JSON cause 25-63% F1 drop |
| Tool names | PascalCase (Read/Glob/Grep) | UPPERCASE degrades by 0.100+ |
| Nudge | Enabled | +0.050 File F1, rescues border cases |
| Temperature | 1.0 | Server-side, matches FastContext recommendation |
| Top-P | 0.95 | |
| Top-K | 20 | |
| Max turns | 8 | Beyond 8 gives no benefit |
| Context | 64K (65536) | Minimum 16K; overflow below |

## Files

| File | Purpose |
|------|---------|
| `src/eval.py` | Main evaluation harness — tool-calling loop + scoring |
| `src/eval_v4.py` | Parameterizable variant runner (wraps eval) |
| `prompts/default.md` | Winning prompt — Markdown format |
| `prompts/xml.md` | XML format prompt (benchmark only — **do not use with 4B**) |
| `prompts/json.md` | JSON format prompt (benchmark only — **do not use with 4B**) |
| `tools/read.md` / `tools/glob.md` / `tools/grep.md` | Tool descriptions in Markdown |
| `data/queries.jsonl` | 20 evaluation queries with ground truth |
| `EVALUATION.md` | Comprehensive report — all variants, per-query matrix, analysis |

## Key Findings

1. **Prompt format is the strongest factor**: Markdown 2× better than XML, 4.5× better than JSON for the 4B model. XML/JSON tags confuse the model's instruction-following.

2. **Nudge rescues 2 queries** (q18, q19) with zero cost on successful runs (+0.050 File F1).

3. **64K context is the sweet spot**: 4K context causes 60% HTTP 400 errors (overflow). 128K provides no additional benefit.

4. **Temperature 1.0** is optimal: lower temps (0.1–0.3) produce deterministic but wrong results. ~10% run-to-run variance, which averaging 2-3 runs stabilizes.

5. **4B ceiling at ~95% File F1**: The remaining gap (q20 — multi-hop sister-module imports) likely requires a 7B+ model.

6. **UPPERCASE tool names degrade performance**: The model expects PascalCase from its training data.

7. **Path auto-correction is mandatory**: The model hallucinates workspace paths; glob+resolve fallback fixes this.

## License

Research project. MIT.
