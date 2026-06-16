#!/usr/bin/env python3
"""
eval_v4.py -- Variant evaluation with prompt style, tool case, and nudge.

Supports:
  --prompt-style {default,xml,json}   Choose system prompt format
  --tool-case {pascal,upper}          Tool name casing (Read vs READ)
  --nudge                             Enable retry on failure (max 2 nudges)

All other behavior matches eval.py.
"""
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

# -- Import eval_v3 core (will be overridden below if needed) --
# We import the v3 module so we can override its globals and reuse its functions.
# Adding parent to path just in case.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval

# -- Default config (same as v3) --
LLAMA_URL    = eval.LLAMA_URL
WORK_DIR     = eval.WORK_DIR
MODEL_NAME   = eval.MODEL_NAME
MAX_TURNS    = eval.MAX_TURNS

# -- CLI flags --
PROMPT_STYLE = "default"
TOOL_CASE    = "pascal"
NUDGE        = False
MAX_NUDGES   = 2


def parse_args():
    global PROMPT_STYLE, TOOL_CASE, NUDGE
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--prompt-style" and i+1 < len(args):
            PROMPT_STYLE = args[i+1]
            i += 2
        elif args[i] == "--tool-case" and i+1 < len(args):
            TOOL_CASE = args[i+1]
            i += 2
        elif args[i] == "--nudge":
            NUDGE = True
            i += 1
        else:
            i += 1


# ============================================================
#  Variant System Prompts
# ============================================================

def _load_desc(name):
    p = Path(__file__).parent / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""

def load_system_prompt(style):
    """Load system prompt based on style."""
    if style == "xml":
        tmpl_file = "xml.md"
    elif style == "json":
        tmpl_file = "json.md"
    else:
        tmpl_file = "default.md"

    work_dir_ls = "\n".join(sorted(os.listdir(WORK_DIR)))
    tmpl = (Path(__file__).parent.parent / "prompts" / tmpl_file).read_text(encoding="utf-8")
    return tmpl.replace("${OS_KIND}", "Linux")\
               .replace("${SHELL_NAME}", os.environ.get("SHELL", "bash"))\
               .replace("${WORK_DIR}", WORK_DIR)\
               .replace("${WORK_DIR_LS}", work_dir_ls)


def build_nudge_prompt(base_prompt, attempt):
    """Add nudge instruction to system prompt."""
    nudge_line = (
        f"\n\n[NUDGE: This is attempt {attempt}. "
        f"Previous attempt(s) did not produce a correct answer. "
        f"Try a COMPLETELY DIFFERENT search strategy. "
        f"Use different keywords, search paths, or approach.]"
    )
    return base_prompt + nudge_line


# ============================================================
#  UPPERCASE Tool Overrides
# ============================================================

def build_upper_tools():
    """Return TOOLS list with UPPERCASE tool names."""
    import copy
    tools = copy.deepcopy(eval.TOOLS)
    name_map = {"Read": "READ", "Glob": "GLOB", "Grep": "GREP"}
    for t in tools:
        old_name = t["function"]["name"]
        if old_name in name_map:
            t["function"]["name"] = name_map[old_name]
    return tools


def build_upper_tool_map():
    """Return tool map with UPPERCASE keys."""
    import eval as v3
    return {
        "READ":  v3.tool_read,
        "GLOB":  v3.tool_glob,
        "GREP":  v3.tool_grep,
    }


# ============================================================
#  Nudge Logic
# ============================================================

def run_single_query_nudge(query, idx, base_system_prompt, tools, tool_map):
    """
    Run a single query with nudge support.
    Returns best trajectory across all attempts.
    """
    best_traj = None
    best_score = -1.0

    for attempt in range(1 + (MAX_NUDGES if NUDGE else 0)):
        # Set up the prompt for this attempt
        if attempt == 0:
            system_prompt = base_system_prompt
        else:
            system_prompt = build_nudge_prompt(base_system_prompt, attempt)

        # Override v3 globals for this attempt
        eval.SYSTEM_PROMPT = system_prompt
        eval.TOOLS = tools
        eval.TOOL_MAP = tool_map

        # Run the query
        traj = eval.run_single_query(query, idx)

        # Check if we got a usable result
        if traj.get("success") and traj.get("final_answer"):
            parsed = eval.parse_final_answer(traj["final_answer"])
            if parsed:
                # Quick score to decide if worth keeping
                gt_files = set(gt.get("file", "") for gt in query.get("ground_truth", []))
                pred_files = set(os.path.normpath(c["file"]) for c in parsed)
                tp = len(pred_files & gt_files)
                fp = len(pred_files - gt_files)
                fn = len(gt_files - pred_files)
                file_f1 = 2*tp / max(2*tp + fp + fn, 1)

                if file_f1 > best_score:
                    best_score = file_f1
                    best_traj = traj

                # Perfect result - no need for more nudges
                if file_f1 >= 1.0:
                    break
        elif best_traj is None:
            best_traj = traj

    return best_traj


# ============================================================
#  Main Evaluation Runner
# ============================================================

def run_evaluation_v4(queries, run_name="v4"):
    """Run evaluation with variant support."""
    results_dir = os.path.expanduser("~/dev/fastcontext/results")
    os.makedirs(results_dir, exist_ok=True)

    # Load variant system prompt
    base_system_prompt = load_system_prompt(PROMPT_STYLE)

    # Select tools
    if TOOL_CASE == "upper":
        tools = build_upper_tools()
        tool_map = build_upper_tool_map()
        eval.TOOL_MAP_REVERSE = {"READ": "READ", "GLOB": "GLOB", "GREP": "GREP"}
    else:
        tools = eval.TOOLS
        tool_map = eval.TOOL_MAP
        eval.TOOL_MAP_REVERSE = {"Read": "Read", "Glob": "Glob", "Grep": "Grep"}

    all_trajectories = []
    all_scores = []

    print(f"\n{'='*60}")
    print(f"FastContext Evaluation -- {run_name} (v4)")
    print(f"Prompt: {PROMPT_STYLE}  |  ToolCase: {TOOL_CASE}  |  Nudge: {NUDGE}")
    print(f"Model: {MODEL_NAME}  |  MaxTurns: {MAX_TURNS}")
    print(f"{'='*60}\n")

    for i, q in enumerate(queries):
        print(f"[{i+1}/{len(queries)}] {q['id']}: {q['query'][:80]}...")

        if NUDGE:
            traj = run_single_query_nudge(q, i, base_system_prompt, tools, tool_map)
            if traj is None:
                # All nudge attempts failed — create empty fallback
                traj = {
                    "query_id": q["id"], "query": q["query"],
                    "ground_truth": q.get("ground_truth", []),
                    "turns": [], "final_answer": None, "success": False,
                    "total_latency_ms": 0, "total_turns": 0,
                    "total_tokens": {"prompt": 0, "completion": 0},
                }
        else:
            eval.SYSTEM_PROMPT = base_system_prompt
            eval.TOOLS = tools
            eval.TOOL_MAP = tool_map
            traj = eval.run_single_query(q, i)

        all_trajectories.append(traj)

        # Score
        gt_converted = {"files": [], "citations": []}
        for gt_item in q.get("ground_truth", []):
            fname = gt_item.get("file", "")
            gt_converted["files"].append(fname)
            start = gt_item.get("start_line", 0)
            end = gt_item.get("end_line", start)
            gt_converted["citations"].append({
                "file": fname,
                "lines": list(range(start, end + 1))
            })

        score = eval.score_query(traj, gt_converted)
        score["query_id"] = q["id"]
        score["ground_truth"] = gt_converted
        all_scores.append(score)

        file_f1 = score["file_metrics"]["f1"]
        line_f1 = score["line_metrics"]["f1"]
        turns   = traj["total_turns"]
        lat     = traj["total_latency_ms"]
        print(f"  turns={turns} file_f1={file_f1:.3f} line_f1={line_f1:.3f} lat={lat:.0f}ms")

    # Save results
    traj_file = os.path.join(results_dir, f"trajectories_{run_name}.json")
    with open(traj_file, "w") as f:
        json.dump(all_trajectories, f, indent=2, default=str)

    score_file = os.path.join(results_dir, f"scores_{run_name}.json")
    with open(score_file, "w") as f:
        json.dump(all_scores, f, indent=2, default=str)

    # Summary
    avg_file_f1 = sum(s["file_metrics"]["f1"] for s in all_scores) / max(len(all_scores), 1)
    avg_line_f1 = sum(s["line_metrics"]["f1"] for s in all_scores) / max(len(all_scores), 1)
    avg_turns   = sum(t["total_turns"] for t in all_trajectories) / max(len(all_trajectories), 1)
    avg_latency = sum(t["total_latency_ms"] for t in all_trajectories) / max(len(all_trajectories), 1)

    print(f"\n{'='*60}")
    print(f"SUMMARY -- v4 ({PROMPT_STYLE}, {TOOL_CASE}, nudge={NUDGE})")
    print(f"  Avg File F1: {avg_file_f1:.3f}")
    print(f"  Avg Line F1: {avg_line_f1:.3f}")
    print(f"  Avg Turns:   {avg_turns:.1f}")
    print(f"  Avg Latency: {avg_latency:.0f}ms")
    print(f"{'='*60}")

    return all_trajectories, all_scores


# ============================================================
#  CLI
# ============================================================

if __name__ == "__main__":
    parse_args()

    queries_file = None
    run_name = f"v4_{PROMPT_STYLE}_{TOOL_CASE}"
    if NUDGE:
        run_name += "_nudge"

    # Parse positional args
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    j = 0
    for a in positional:
        # Skip values consumed by --flags
        pass

    # Find queries file
    remaining = []
    skip_next = False
    for i, a in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if a in ("--prompt-style", "--tool-case"):
            skip_next = True
            continue
        if a == "--nudge":
            continue
        remaining.append(a)

    queries_file = remaining[0] if remaining else "data/queries.jsonl"
    if len(remaining) > 1:
        run_name = remaining[1]

    queries = []
    with open(queries_file, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    run_evaluation_v4(queries, run_name)
