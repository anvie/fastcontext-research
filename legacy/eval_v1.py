#!/usr/bin/env python3
"""
FastContext Evaluation Pipeline
Evaluates microsoft/FastContext-1.0-4B-SFT as a repository exploration subagent.
"""

import json
import os
import re
import subprocess
import sys
import time
import fnmatch
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ─── Configuration ───────────────────────────────────────────────────────
LLAMA_SERVER = os.environ.get("LLAMA_SERVER", "http://localhost:8080")
MODEL_NAME = os.environ.get("MODEL_NAME", "fastcontext-1.0-4b-sft-q4_k_m.gguf")
WORK_DIR = os.environ.get("WORK_DIR", "/tmp/evonic_fastcontext")
SYSTEM_PROMPT_FILE = os.environ.get(
    "SYSTEM_PROMPT_FILE", os.path.expanduser("~/dev/fastcontext/system_prompt.md")
)
MAX_TURNS = int(os.environ.get("MAX_TURNS", "6"))
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "120"))
TOOL_TIMEOUT = int(os.environ.get("TOOL_TIMEOUT", "30"))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.expanduser("~/dev/fastcontext/results"))
QUERIES_FILE = os.environ.get(
    "QUERIES_FILE", os.path.expanduser("~/dev/fastcontext/test_queries.jsonl")
)

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─── Tool Executor ───────────────────────────────────────────────────────

def tool_read(path: str, offset: int = 1, limit: int = 50) -> str:
    """Read a file with offset and line limit."""
    full_path = os.path.join(WORK_DIR, path.lstrip("/"))
    full_path = os.path.normpath(full_path)
    if not full_path.startswith(os.path.normpath(WORK_DIR)):
        return "Error: path escapes workspace"
    try:
        with open(full_path, "r", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except IsADirectoryError:
        return f"Error: {path} is a directory"
    except PermissionError:
        return f"Error: permission denied: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"

    total = len(lines)
    start = max(0, offset - 1)
    end = min(start + limit, total)
    result = []
    for i in range(start, end):
        result.append(f"{i + 1}: {lines[i].rstrip()}")
    header = f"File: {path} | Lines: {start + 1}-{end} of {total}"
    return header + "\n" + "\n".join(result)


def tool_glob(pattern: str) -> str:
    """Find files matching a glob pattern."""
    # Search from workspace root
    matches = []
    for root, dirs, files in os.walk(WORK_DIR):
        # Skip .git
        if ".git" in root.split(os.sep):
            continue
        rel_root = os.path.relpath(root, WORK_DIR)
        if rel_root == ".":
            rel_root = ""
        for name in files + dirs:
            rel_path = os.path.join(rel_root, name) if rel_root else name
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern):
                matches.append(rel_path)
    matches.sort()
    if not matches:
        return f"No files matching '{pattern}'"
    if len(matches) > 100:
        return f"Found {len(matches)} files matching '{pattern}':\n" + "\n".join(matches[:100]) + f"\n... and {len(matches) - 100} more"
    return f"Found {len(matches)} files matching '{pattern}':\n" + "\n".join(matches)


def tool_grep(pattern: str, path_filter: str = ".") -> str:
    """Search file contents with regex (ripgrep-style via grep)."""
    search_dir = os.path.join(WORK_DIR, path_filter.lstrip("/"))
    search_dir = os.path.normpath(search_dir)
    if not search_dir.startswith(os.path.normpath(WORK_DIR)):
        return "Error: path escapes workspace"
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.go",
             "--include=*.md", "--include=*.json", "--include=*.toml", "--include=*.html",
             "--include=*.css", "--include=*.rs", "--include=*.ts",
             "-I", pattern, search_dir],
            capture_output=True, text=True, timeout=TOOL_TIMEOUT, cwd=WORK_DIR
        )
        output = result.stdout.strip()
        if not output:
            return f"No matches for '{pattern}' in {path_filter}"
        lines = output.split("\n")
        if len(lines) > 50:
            return "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: grep timed out for pattern '{pattern}'"
    except Exception as e:
        return f"Error running grep: {e}"


def execute_tool(tool_name: str, args: Dict[str, Any]) -> str:
    """Execute a tool and return the result string."""
    tool_name = tool_name.strip().upper()
    try:
        if tool_name == "READ":
            return tool_read(
                path=args.get("path", args.get("file_path", "")),
                offset=int(args.get("offset", 1)),
                limit=int(args.get("limit", 50)),
            )
        elif tool_name == "GLOB":
            return tool_glob(pattern=args.get("pattern", "*"))
        elif tool_name == "GREP":
            return tool_grep(
                pattern=args.get("pattern", ""),
                path_filter=args.get("path", "."),
            )
        else:
            return f"Error: unknown tool '{tool_name}'. Available: READ, GLOB, GREP"
    except Exception as e:
        return f"Error executing {tool_name}: {e}"


# ─── Tool Call Parser ────────────────────────────────────────────────────

def parse_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse Qwen-style tool call blocks from model response."""
    calls = []
    # Pattern: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'
    matches = re.findall(pattern, content, re.DOTALL)
    for match in matches:
        try:
            call_data = json.loads(match)
            if isinstance(call_data, dict):
                name = call_data.get("name", call_data.get("function", ""))
                args = call_data.get("arguments", call_data.get("parameters", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append({"name": name, "arguments": args})
        except json.JSONDecodeError:
            # Try to extract name/args manually
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', match)
            args_str = re.search(r'"arguments"\s*:\s*(.*?)(?:\}|$)', match, re.DOTALL)
            if name_match:
                name = name_match.group(1)
                args = {}
                if args_str:
                    try:
                        args = json.loads(args_str.group(1) + "}")
                    except json.JSONDecodeError:
                        pass
                calls.append({"name": name, "arguments": args})
    return calls


def parse_final_answer(content: str) -> Optional[str]:
    """Extract final_answer block from response."""
    pattern = r'<final_answer>\s*(.*?)\s*</final_answer>'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


# ─── API Client ──────────────────────────────────────────────────────────

def chat_completion(messages: List[Dict], max_tokens: int = 4096) -> Dict[str, Any]:
    """Send chat completion request to llama-server."""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        resp = requests.post(
            f"{LLAMA_SERVER}/v1/chat/completions",
            json=payload,
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        return {"error": "timeout", "message": f"Request timed out after {API_TIMEOUT}s"}
    except requests.RequestException as e:
        return {"error": "request_failed", "message": str(e)}


# ─── Main Evaluation Loop ────────────────────────────────────────────────

def run_single_query(query: Dict, query_idx: int) -> Dict[str, Any]:
    """Run a single query through the tool-calling loop."""
    query_text = query["query"]
    query_id = query.get("id", f"q{query_idx:03d}")

    system_prompt = ""
    try:
        with open(SYSTEM_PROMPT_FILE, "r") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        print(f"WARNING: System prompt file not found: {SYSTEM_PROMPT_FILE}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"<query>\n{query_text}\n</query>"},
    ]

    trajectory = {
        "query_id": query_id,
        "query": query_text,
        "ground_truth": query.get("ground_truth", {}),
        "turns": [],
        "final_answer": None,
        "success": False,
        "error": None,
    }

    total_tokens = {"prompt": 0, "completion": 0}
    turn_start_time = time.time()

    for turn_idx in range(MAX_TURNS):
        turn_data = {
            "turn": turn_idx + 1,
            "messages_sent": len(messages),
            "request_start": datetime.now().isoformat(),
            "tool_calls": [],
            "tool_results": [],
            "response_content": None,
            "response_full": None,
            "token_usage": None,
            "latency_ms": 0,
            "error": None,
            "final_answer": None,
        }

        t0 = time.time()
        response = chat_completion(messages)
        latency = (time.time() - t0) * 1000
        turn_data["latency_ms"] = round(latency, 1)

        if "error" in response:
            turn_data["error"] = response
            trajectory["turns"].append(turn_data)
            trajectory["error"] = f"API error at turn {turn_idx + 1}: {response}"
            break

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        turn_data["response_content"] = content
        turn_data["token_usage"] = response.get("usage", {})
        if "usage" in response:
            total_tokens["prompt"] += response["usage"].get("prompt_tokens", 0)
            total_tokens["completion"] += response["usage"].get("completion_tokens", 0)

        if not content:
            turn_data["error"] = "Empty response from model"
            trajectory["turns"].append(turn_data)
            trajectory["error"] = f"Empty response at turn {turn_idx + 1}"
            break

        # Check for final answer
        final = parse_final_answer(content)
        if final:
            turn_data["final_answer"] = final
            trajectory["final_answer"] = final
            trajectory["success"] = True
            trajectory["turns"].append(turn_data)
            break

        # Parse tool calls
        tool_calls = parse_tool_calls(content)
        if not tool_calls:
            # No tool calls and no final answer — treat as final answer attempt
            turn_data["final_answer"] = content.strip()
            trajectory["final_answer"] = content.strip()
            trajectory["success"] = True
            trajectory["turns"].append(turn_data)
            break

        # Execute tools
        tool_results = []
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            result = execute_tool(tool_name, tool_args)
            turn_data["tool_calls"].append({
                "name": tool_name,
                "arguments": tool_args,
            })
            tool_results.append({
                "tool_call_id": f"call_{turn_idx}_{len(tool_results)}",
                "name": tool_name,
                "result": result,
            })
        turn_data["tool_results"] = tool_results

        # Append assistant message
        messages.append({"role": "assistant", "content": content})

        # Append tool results as a single tool message
        tool_result_text = ""
        for tr in tool_results:
            tool_result_text += f"\n[Tool: {tr['name']}]\n{tr['result']}\n"
        messages.append({"role": "user", "content": tool_result_text})

        trajectory["turns"].append(turn_data)

    trajectory["total_latency_ms"] = round((time.time() - turn_start_time) * 1000, 1)
    trajectory["total_turns"] = len(trajectory["turns"])
    trajectory["total_tokens"] = total_tokens

    return trajectory


# ─── Scoring ─────────────────────────────────────────────────────────────

def parse_citations(final_answer: str) -> List[Dict[str, Any]]:
    """Parse citation lines from final_answer.
    Format: /path/to/file.py:10-15 or /path/to/file.py:10-15 (reason)
    """
    citations = []
    if not final_answer:
        return citations
    lines = final_answer.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Match: /path:start-end or /path:line
        match = re.match(r'(/\S+):(\d+)(?:-(\d+))?', line)
        if match:
            filepath = match.group(1)
            start = int(match.group(2))
            end = match.group(3)
            end = int(end) if end else start
            citations.append({
                "file": filepath,
                "start": start,
                "end": end,
                "raw": line,
            })
    return citations


def score_query(trajectory: Dict, ground_truth: Dict) -> Dict[str, Any]:
    """Score a trajectory against ground truth."""
    gt_files = set(ground_truth.get("files", []))
    gt_file_lines = {}
    for gt_item in ground_truth.get("citations", []):
        fname = gt_item.get("file", "")
        gt_files.add(fname)
        gt_file_lines[fname] = gt_item.get("lines", [])

    final_answer = trajectory.get("final_answer", "")
    citations = parse_citations(final_answer)

    pred_files = set()
    pred_file_lines = {}
    for cit in citations:
        fname = cit["file"]
        pred_files.add(fname)
        if fname not in pred_file_lines:
            pred_file_lines[fname] = []
        for line in range(cit["start"], cit["end"] + 1):
            pred_file_lines[fname].append(line)

    # File-level metrics
    if gt_files:
        file_tp = len(gt_files & pred_files)
        file_fp = len(pred_files - gt_files)
        file_fn = len(gt_files - pred_files)
        file_precision = file_tp / (file_tp + file_fp) if (file_tp + file_fp) > 0 else 0.0
        file_recall = file_tp / (file_tp + file_fn) if (file_tp + file_fn) > 0 else 0.0
        file_f1 = 2 * file_precision * file_recall / (file_precision + file_recall) if (file_precision + file_recall) > 0 else 0.0
    else:
        file_precision = file_recall = file_f1 = 1.0 if not pred_files else 0.0

    # Line-level metrics
    gt_lines_all = set()
    for fname, lines in gt_file_lines.items():
        if fname in pred_files or fname in gt_files:
            for l in lines:
                gt_lines_all.add((fname, l))
    pred_lines_all = set()
    for fname, lines in pred_file_lines.items():
        for l in lines:
            pred_lines_all.add((fname, l))

    if gt_lines_all:
        line_tp = len(gt_lines_all & pred_lines_all)
        line_fp = len(pred_lines_all - gt_lines_all)
        line_fn = len(gt_lines_all - pred_lines_all)
        line_precision = line_tp / (line_tp + line_fp) if (line_tp + line_fp) > 0 else 0.0
        line_recall = line_tp / (line_tp + line_fn) if (line_tp + line_fn) > 0 else 0.0
        line_f1 = 2 * line_precision * line_recall / (line_precision + line_recall) if (line_precision + line_recall) > 0 else 0.0
    else:
        line_precision = line_recall = line_f1 = 1.0 if not pred_lines_all else 0.0

    return {
        "query_id": trajectory.get("query_id", ""),
        "success": trajectory.get("success", False),
        "file_metrics": {
            "precision": round(file_precision, 4),
            "recall": round(file_recall, 4),
            "f1": round(file_f1, 4),
            "tp": file_tp,
            "fp": file_fp,
            "fn": file_fn,
        },
        "line_metrics": {
            "precision": round(line_precision, 4),
            "recall": round(line_recall, 4),
            "f1": round(line_f1, 4),
            "tp": line_tp,
            "fp": line_fp,
            "fn": line_fn,
        },
        "gt_files": sorted(gt_files),
        "pred_files": sorted(pred_files),
        "gt_citations": ground_truth.get("citations", []),
        "pred_citations": citations,
    }


# ─── Baseline (direct query, no tools) ───────────────────────────────────

def run_baseline(query: Dict) -> Dict[str, Any]:
    """Run a query directly without tool-calling loop."""
    query_text = query["query"]
    query_id = query.get("id", "baseline")

    system_prompt = ""
    try:
        with open(SYSTEM_PROMPT_FILE, "r") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        pass

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"<query>\n{query_text}\n</query>"},
    ]

    t0 = time.time()
    response = chat_completion(messages, max_tokens=2048)
    latency_ms = (time.time() - t0) * 1000

    content = ""
    if "choices" in response:
        content = response["choices"][0].get("message", {}).get("content", "")

    return {
        "query_id": query_id,
        "query": query_text,
        "response": content,
        "latency_ms": round(latency_ms, 1),
        "token_usage": response.get("usage", {}),
        "final_answer": parse_final_answer(content) or content,
    }


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    print(f"FastContext Evaluation Pipeline")
    print(f"Server: {LLAMA_SERVER}")
    print(f"Model: {MODEL_NAME}")
    print(f"Workspace: {WORK_DIR}")
    print(f"Max turns: {MAX_TURNS}")
    print()

    # Health check
    try:
        h = requests.get(f"{LLAMA_SERVER}/health", timeout=10)
        print(f"Server health: {h.json()}")
    except Exception as e:
        print(f"ERROR: Cannot reach server: {e}")
        sys.exit(1)

    # Load queries (JSONL format)
    queries = []
    with open(QUERIES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    print(f"Loaded {len(queries)} test queries\n")

    # Run tool-calling evaluation
    all_trajectories = []
    all_scores = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    traj_file = os.path.join(OUTPUT_DIR, f"trajectories_{timestamp}.jsonl")
    scores_file = os.path.join(OUTPUT_DIR, f"scores_{timestamp}.json")

    with open(traj_file, "w") as tjf:
        for i, q in enumerate(queries):
            qid = q.get("id", f"q{i:03d}")
            print(f"[{i + 1}/{len(queries)}] Running: {qid} — {q['query'][:80]}...")
            traj = run_single_query(q, i)
            all_trajectories.append(traj)

            # Convert ground_truth list format to score_query format
            gt_raw = q.get("ground_truth", [])
            gt_converted = {"files": [], "citations": []}
            for gt_item in gt_raw:
                fname = gt_item.get("file", "")
                gt_converted["files"].append(fname)
                start = gt_item.get("start_line", gt_item.get("start", 0))
                end = gt_item.get("end_line", gt_item.get("end", start))
                lines = list(range(start, end + 1))
                gt_converted["citations"].append({"file": fname, "lines": lines})

            score = score_query(traj, gt_converted)
            all_scores.append(score)

            # Write trajectory
            tjf.write(json.dumps(traj) + "\n")
            tjf.flush()

            # Print quick summary
            turns = traj["total_turns"]
            f1_file = score["file_metrics"]["f1"]
            f1_line = score["line_metrics"]["f1"]
            status = "✓" if traj["success"] else "✗"
            print(f"  {status} turns={turns} | file_f1={f1_file:.3f} | line_f1={f1_line:.3f} | latency={traj['total_latency_ms']}ms")
            print()

    # Save scores
    with open(scores_file, "w") as sf:
        json.dump(all_scores, sf, indent=2)

    # ─── Summary ─────────────────────────────────────────────────────────
    avg_file_f1 = sum(s["file_metrics"]["f1"] for s in all_scores) / len(all_scores)
    avg_line_f1 = sum(s["line_metrics"]["f1"] for s in all_scores) / len(all_scores)
    avg_turns = sum(t["total_turns"] for t in all_trajectories) / len(all_trajectories)
    avg_latency = sum(t["total_latency_ms"] for t in all_trajectories) / len(all_trajectories)
    success_rate = sum(1 for t in all_trajectories if t["success"]) / len(all_trajectories)
    total_prompt_tokens = sum(t["total_tokens"]["prompt"] for t in all_trajectories)
    total_completion_tokens = sum(t["total_tokens"]["completion"] for t in all_trajectories)

    # Run baseline
    print("─" * 60)
    print("Running baseline (direct queries, no tools)...")
    baseline_results = []
    for i, q in enumerate(queries):
        qid = q.get("id", f"q{i:03d}")
        bl = run_baseline(q)
        baseline_results.append(bl)
        gt_bl = {"files": [], "citations": []}
        for gt_item in q.get("ground_truth", []):
            fname = gt_item.get("file", "")
            gt_bl["files"].append(fname)
            start = gt_item.get("start_line", gt_item.get("start", 0))
            end = gt_item.get("end_line", gt_item.get("end", start))
            gt_bl["citations"].append({"file": fname, "lines": list(range(start, end + 1))})
        score_bl = score_query(bl, gt_bl)
        bl["score"] = score_bl
        print(f"  [{i + 1}/{len(queries)}] {qid}: f1_file={score_bl['file_metrics']['f1']:.3f} | latency={bl['latency_ms']}ms")
    print()

    avg_bl_file_f1 = sum(r["score"]["file_metrics"]["f1"] for r in baseline_results) / len(baseline_results)
    avg_bl_line_f1 = sum(r["score"]["line_metrics"]["f1"] for r in baseline_results) / len(baseline_results)
    avg_bl_latency = sum(r["latency_ms"] for r in baseline_results) / len(baseline_results)
    bl_total_tokens = sum(r["token_usage"].get("total_tokens", 0) for r in baseline_results)

    # Save baseline
    bl_file = os.path.join(OUTPUT_DIR, f"baseline_{timestamp}.json")
    with open(bl_file, "w") as bf:
        json.dump(baseline_results, bf, indent=2)

    # ─── Print Final Summary ─────────────────────────────────────────────
    print("=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Queries:         {len(queries)}")
    print(f"Success rate:    {success_rate:.1%}")
    print(f"Avg turns:       {avg_turns:.1f}")
    print()
    print("TOOL-CALLING MODE:")
    print(f"  Avg latency:   {avg_latency:.0f}ms")
    print(f"  File F1:       {avg_file_f1:.3f}")
    print(f"  Line F1:       {avg_line_f1:.3f}")
    print(f"  Prompt tokens: {total_prompt_tokens}")
    print(f"  Compl tokens:  {total_completion_tokens}")
    print()
    print("BASELINE (no tools):")
    print(f"  Avg latency:   {avg_bl_latency:.0f}ms")
    print(f"  File F1:       {avg_bl_file_f1:.3f}")
    print(f"  Line F1:       {avg_bl_line_f1:.3f}")
    print(f"  Total tokens:  {bl_total_tokens}")
    print()
    print(f"Results saved to: {OUTPUT_DIR}/")
    print(f"  Trajectories: trajectories_{timestamp}.jsonl")
    print(f"  Scores:       scores_{timestamp}.json")
    print(f"  Baseline:     baseline_{timestamp}.json")

    # Return summary dict for EVALUATION.md
    return {
        "timestamp": timestamp,
        "num_queries": len(queries),
        "success_rate": success_rate,
        "avg_turns": avg_turns,
        "tool_calling": {
            "avg_latency_ms": avg_latency,
            "file_f1": avg_file_f1,
            "line_f1": avg_line_f1,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
        },
        "baseline": {
            "avg_latency_ms": avg_bl_latency,
            "file_f1": avg_bl_file_f1,
            "line_f1": avg_bl_line_f1,
            "total_tokens": bl_total_tokens,
        },
        "per_query_scores": all_scores,
        "output_dir": OUTPUT_DIR,
        "traj_file": traj_file,
        "scores_file": scores_file,
        "bl_file": bl_file,
    }


if __name__ == "__main__":
    main()
