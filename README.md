# FastContext Research — Evaluation Pipeline

Evaluation of `microsoft/FastContext-1.0-4B-SFT` as a repository exploration
subagent using tool-calling (Read/Glob/Grep via ripgrep).

## Quick Results

| Metric | Tool-Calling | Baseline (no tools) |
|--------|-------------|---------------------|
| **File F1** | **0.900** | 0.100 |
| **Line F1** | **0.551** | 0.045 |
| **Correct Files** | **9/10** | 1/10 |

**Verdict**: 9× improvement over baseline. Highly effective at finding the right
file; line-level accuracy limited by 4B model size.

## Setup

### Prerequisites

- llama.cpp server with the GGUF model loaded
- Python 3.8+
- ripgrep (`rg`) installed
- Git (for cloning target repo)

### Model

```bash
# Q4_K_M quantized (2.4 GB)
~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf
```

### Server

```bash
tmux new -s llm
llama-server -m ~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf \
    --host 0.0.0.0 --port 8080 \
    --ctx-size 65536 --temp 1.0 \
    --n-gpu-layers 99
```

### Target Repository

```bash
git clone <repo-url> /tmp/evonic_fastcontext
```

## Usage

```bash
cd ~/dev/fastcontext

# Run evaluation (default: test_queries.jsonl, run name "v3")
python3 eval_v3.py

# Custom queries file and run name
python3 eval_v3.py test_queries.jsonl v3_64k
```

### Output

- `results/trajectories_{name}.json` — Full turn-by-turn trajectories
- `results/scores_{name}.json` — Per-query file/line F1 scores
- `results/baseline_{name}.json` — Baseline (no tools) comparison

## Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| Tools | ripgrep-based Read/Glob/Grep | Read-only, matches official FastContext |
| Temperature | 1.0 | Server-side + client-side |
| Top-P | 0.95 | |
| Max Turns | 8 | |
| Context | 65,536 (64K) | Minimum 16K for tool-calling |
| Model | Q4_K_M quant | 2.4 GB, full GPU offload |

## Files

| File | Purpose |
|------|---------|
| `eval_v3.py` | Main evaluation script — tool-calling loop + scoring |
| `system_prompt.md` | System prompt (adapted from official FastContext) |
| `read.md` / `glob.md` / `grep.md` | Tool descriptions in markdown |
| `test_queries.jsonl` | 10 evaluation queries with ground truth |
| `EVALUATION.md` | Full evaluation report with per-query breakdown |

## Key Findings

1. **64K context is optimal** — 4,096 causes 60% failure rate from tool result
   overflow. 128K+ provides no benefit over 64K for this model.
2. **Temperature 1.0** — Lower temperatures (0.1–0.3) produce deterministic but
   incorrect results. 1.0 has ~10% run-to-run variance (stochastic noise on 4B).
3. **9/10 file accuracy ceiling** — 4B model cannot reliably exceed 90%.
   A 7B+ model is needed for 95–100%.
4. **q8 (plugin_manager)**: Failed in all early runs, solved with 64K context
   (perfect 1.000/1.000).
5. **Averaging 2-3 runs** stabilizes stochastic variance between queries.

## License

Research project — see repository for details.
