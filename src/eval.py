#!/usr/bin/env python3
"""
eval_v3.py — FastContext Evaluation Pipeline (matches official implementation)
Uses ripgrep for GLOB/GREP, proper LLM params for Qwen, max_turns=4 with user message injection.
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

# — Config —
LLAMA_URL = os.environ.get("LLAMA_URL", "http://localhost:8080/v1/chat/completions")
WORK_DIR = "/tmp/evonic_fastcontext"
MODEL_NAME = "fastcontext-1.0-4b-sft-q4_k_m"
MAX_TURNS = int(os.environ.get("MAX_TURNS", "8"))
MAX_COMPLETION_TOKENS = 32000
TEMPERATURE = 1.0
TOP_P = 0.95

# — Load official tool descriptions —
def _load_desc(name):
    p = Path(__file__).parent.parent / "tools" / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""

DESC_GREP = _load_desc("grep")
DESC_GLOB = _load_desc("glob")
DESC_READ = _load_desc("read")


# — Tool Schemas (OpenAI format, matching official FastContext) —
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": DESC_READ,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute path of the file to read."
                    },
                    "offset": {
                        "type": "integer",
                        "description": "The line number to start reading from. Positive values are 1-indexed. Negative values count backwards from end."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "The number of lines to read."
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": DESC_GLOB,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The glob pattern to match files or directories."
                    },
                    "directory": {
                        "type": "string",
                        "description": "The absolute path of the directory to search in. Defaults to working directory."
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": DESC_GREP,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The regular expression pattern to search for in file contents"
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (rg pattern -- PATH). Defaults to working directory."
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern for filtering files (e.g. *.py) - maps to rg --glob"
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "Output mode: content shows matching lines, files_with_matches shows file paths. Defaults to files_with_matches."
                    },
                    "-i": {
                        "type": "boolean",
                        "description": "Case insensitive search (rg -i)"
                    },
                    "-A": {
                        "type": "integer",
                        "description": "Lines to show after each match (rg -A)"
                    },
                    "-B": {
                        "type": "integer",
                        "description": "Lines to show before each match (rg -B)"
                    },
                    "-C": {
                        "type": "integer",
                        "description": "Lines to show before+after each match (rg -C)"
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "Limit output to first N lines"
                    },
                    "type": {
                        "type": "string",
                        "description": "File type to search (rg --type): py, js, rust, go, java"
                    },
                    "multiline": {
                        "type": "boolean",
                        "description": "Enable multiline mode (rg -U --multiline-dotall)"
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


# — System prompt —
def load_system_prompt():
    work_dir_ls = "\n".join(sorted(os.listdir(WORK_DIR)))
    tmpl = (Path(__file__).parent.parent / "prompts" / "default.md").read_text(encoding="utf-8")
    return tmpl.replace("${OS_KIND}", "Linux")\
               .replace("${SHELL_NAME}", os.environ.get("SHELL", "bash"))\
               .replace("${WORK_DIR}", WORK_DIR)\
               .replace("${WORK_DIR_LS}", work_dir_ls)

SYSTEM_PROMPT = load_system_prompt()

# — Tool implementations (matching official FastContext) —
MAX_LINE = 2000
MAX_LINE_LENGTH = 2000
RG_PATH = "/usr/bin/rg"

def _resolve_workspace_path(p: str) -> str:
    """Resolve path to within WORK_DIR. If absolute and outside workspace,
    try to find a matching subdirectory or file within WORK_DIR."""
    pp = Path(p)
    if not pp.is_absolute():
        candidate = Path(WORK_DIR) / pp
        if candidate.exists():
            return str(candidate)
        # Relative with no dirs: glob to find actual file
        if len(pp.parts) == 1 and pp.name:
            candidates = list(Path(WORK_DIR).rglob(pp.name))
            if candidates:
                return str(min(candidates, key=lambda c: len(str(c))))
        return str(candidate)

    # Check if within workspace
    try:
        pp.resolve().relative_to(Path(WORK_DIR).resolve())
        return str(pp)
    except ValueError:
        pass

    # Strategy 1: try suffix matching (e.g. /path/to/backend/x.py -> WORK_DIR/backend/x.py)
    parts = pp.parts
    for i in range(1, len(parts)):
        candidate = Path(WORK_DIR) / Path(*parts[i:])
        if candidate.exists():
            return str(candidate)

    # Strategy 2: glob by basename (e.g. /path/to/plugin_hooks.py -> find within WORK_DIR)
    basename = pp.name
    if basename:
        candidates = list(Path(WORK_DIR).rglob(basename))
        if len(candidates) == 1:
            return str(candidates[0])
        elif len(candidates) > 1:
            # Prefer shortest path (usually the right one)
            return str(min(candidates, key=lambda c: len(str(c))))

    # If no match, just prepend WORK_DIR (best effort)
    return str(Path(WORK_DIR) / pp.relative_to(pp.anchor) if pp.is_absolute() else pp)

def _within_workspace(p: str) -> bool:
    """Check path is within WORK_DIR."""
    pp = Path(p)
    if not pp.is_absolute():
        return True  # relative, will be resolved
    try:
        pp.resolve().relative_to(Path(WORK_DIR).resolve())
        return True
    except ValueError:
        return False

def tool_read(args: dict) -> str:
    file_path = args.get("path", "")
    offset = args.get("offset")
    limit = args.get("limit")

    if not file_path:
        return "Read Tool: file path is required."
    file_path = _resolve_workspace_path(file_path)

    p = Path(file_path)
    if not p.exists():
        return f"Read Tool: file '{file_path}' does not exist."

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)

    if offset is None or offset < 0:
        offset = 1
    end_line = len(lines)
    if limit is not None:
        end_line = min(offset + limit - 1, len(lines))

    total = end_line - offset + 1
    if total > MAX_LINE:
        end_line = offset + MAX_LINE - 1
        total = MAX_LINE

    out_lines = []
    for i in range(offset - 1, end_line):
        line = lines[i].rstrip('\n')
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "..."
        out_lines.append(f"{i+1}|{line}")

    if (end_line - offset + 1) < (limit or 0):
        out_lines.append("...")

    content = "\n".join(out_lines)
    return f"```{file_path}:{offset}-{end_line}\n{content}\n```"


def tool_glob(args: dict) -> str:
    """Glob using ripgrep --files --glob (matches official implementation)."""
    pattern = args.get("pattern", "")
    directory = args.get("directory", WORK_DIR)

    directory = _resolve_workspace_path(directory)

    p = Path(directory)
    if not p.is_dir():
        return f"Glob Tool: directory '{directory}' does not exist."

    cmd = ["rg", "--files", directory, "--glob", pattern]
    try:
        result = subprocess.run(cmd, cwd=WORK_DIR, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "Glob Tool: timed out after 10s."

    if result.returncode != 0 and result.stderr:
        return result.stderr.strip()

    files = [l for l in result.stdout.splitlines() if l.strip()]
    if not files:
        return "Glob Tool: no files found."

    limit = 100
    if len(files) > limit:
        files = files[:limit]
        files.append(f"(truncated: showing first {limit} results)")

    return "\n".join(files)

def tool_grep(args: dict) -> str:
    """Grep using ripgrep with full params (matches official implementation)."""
    pattern = args.get("pattern", "")
    path = args.get("path", WORK_DIR)
    glob = args.get("glob")
    output_mode = args.get("output_mode", "files_with_matches")
    before = args.get("-B")
    after = args.get("-A")
    context = args.get("-C")
    ignore_case = args.get("-i", False)
    filetype = args.get("type")
    head_limit = args.get("head_limit")
    multiline = args.get("multiline", False)

    path = _resolve_workspace_path(path)

    cmd = ["rg", pattern, path, "--heading", "--color", "never"]

    if glob:
        cmd += ["--glob", glob]
    if ignore_case:
        cmd.append("--ignore-case")
    if filetype:
        cmd += ["--type", filetype]
    if multiline:
        cmd += ["--multiline", "--multiline-dotall"]

    if output_mode == "content":
        if after is not None:
            cmd += ["-A", str(after)]
        if before is not None:
            cmd += ["-B", str(before)]
        if context is not None:
            cmd += ["-C", str(context)]
        cmd.append("-n")
    elif output_mode == "files_with_matches":
        cmd.append("--files-with-matches")
    elif output_mode == "count":
        cmd.append("--count-matches")

    try:
        result = subprocess.run(cmd, cwd=WORK_DIR, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "Grep Tool: timed out after 10s."

    if result.returncode == 1:
        return "Grep Tool: no matches found."

    output = result.stdout if result.stdout else result.stderr
    if not output.strip():
        return "Grep Tool: no matches found."

    limit = head_limit or 100
    lines = output.splitlines()
    if len(lines) > limit:
        lines = lines[:limit]
        lines.append(f"(truncated: showing first {limit} results)")

    return "\n".join(lines)


# — Tool executor —
TOOL_MAP = {"Read": tool_read, "Glob": tool_glob, "Grep": tool_grep}

def execute_tools(tool_calls: list) -> list:
    """Execute tool calls and return tool result messages."""
    results = []
    for tc in tool_calls:
        func_name = tc.get("function", {}).get("name", "")
        args_str = tc.get("function", {}).get("arguments", "{}")
        call_id = tc.get("id", "unknown")
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            results.append({"role": "tool", "tool_call_id": call_id,
                          "content": f"Tool '{func_name}' arguments are invalid JSON."})
            continue

        func = TOOL_MAP.get(func_name)
        if not func:
            results.append({"role": "tool", "tool_call_id": call_id,
                          "content": f"Tool '{func_name}' not found."})
            continue

        try:
            output = func(args)
        except Exception as e:
            output = f"Tool '{func_name}' failed: {str(e)}"

        results.append({"role": "tool", "tool_call_id": call_id, "content": output})

    return results

# — LLM API call —
def call_llm(messages: list, tools: list | None = None) -> dict:
    """Call llama-server with OpenAI-compatible API."""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "stream": False,
        "extra_body": {
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = Request(LLAMA_URL, data=data,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        raise RuntimeError(f"LLM API call failed: {e}")

def extract_usage(response: dict) -> dict:
    """Extract token usage from response."""
    return response.get("usage", {})


# — Main loop (matching official Agent._agent_loop) —
def run_single_query(query: dict, idx: int = 0) -> dict:
    """Run a single query through the tool-calling loop. Returns trajectory dict."""
    q_id = query["id"]
    q_text = query["query"]
    ground_truth = query.get("ground_truth", [])

    trajectory = {
        "query_id": q_id,
        "query": q_text,
        "ground_truth": ground_truth,
        "turns": [],
        "final_answer": None,
        "success": False,
        "error": None,
        "total_latency_ms": 0,
        "total_turns": 0,
        "total_tokens": {"prompt": 0, "completion": 0},
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"<query>\n{q_text}\n</query>"},
    ]

    t_start = time.time()
    n_turn = 0
    final_answer = None

    try:
        while True:
            n_turn += 1
            if n_turn > MAX_TURNS + 1:
                trajectory["error"] = f"No final answer after {MAX_TURNS} turns."
                break

            # At max_turns+1, inject user message to prompt final answer
            if n_turn == MAX_TURNS + 1:
                messages.append({
                    "role": "user",
                    "content": "Max number of turns reached. Please provide the final answer based on the information you have gathered."
                })
                tools_to_send = None  # no more tools
            else:
                tools_to_send = TOOLS

            turn_start = time.time()
            response = call_llm(messages, tools_to_send)
            turn_latency = (time.time() - turn_start) * 1000

            usage = extract_usage(response)
            trajectory["total_tokens"]["prompt"] += usage.get("prompt_tokens", 0)
            trajectory["total_tokens"]["completion"] += usage.get("completion_tokens", 0)

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

            turn_record = {
                "turn": n_turn,
                "messages_sent": len(messages),
                "request_start": datetime.now(timezone.utc).isoformat(),
                "tool_calls": [],
                "tool_results": [],
                "response_content": content,
                "finish_reason": finish_reason,
                "token_usage": usage,
                "latency_ms": round(turn_latency, 1),
                "error": None,
                "final_answer": None,
            }

            if tool_calls and n_turn <= MAX_TURNS:
                # Execute tools
                tool_results = execute_tools(tool_calls)
                turn_record["tool_calls"] = [
                    {"name": tc.get("function",{}).get("name",""),
                     "arguments": json.loads(tc.get("function",{}).get("arguments","{}"))}
                    for tc in tool_calls
                ]
                turn_record["tool_results"] = [
                    {"tool_call_id": tr["tool_call_id"],
                     "name": TOOL_MAP_REVERSE.get(tr.get("tool_call_id",""), ""),
                     "result": tr["content"]}
                    for tr in tool_results
                ]

                # Add assistant + tool messages
                messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                messages.extend(tool_results)
            else:
                # No tool calls → final answer or stop
                if n_turn > MAX_TURNS:
                    final_answer = content
                    turn_record["final_answer"] = content
                else:
                    # Check for <final_answer> in content (standard + HTML-encoded)
                    m = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                    if not m:
                        m = re.search(r"&lt;final_answer&gt;(.*?)&lt;/final_answer&gt;", content, re.DOTALL)
                    if m:
                        final_answer = m.group(0)
                        turn_record["final_answer"] = final_answer
                    elif finish_reason == "stop" and content.strip():
                        # No explicit tags but model finished — use raw content as answer
                        final_answer = content.strip()
                        turn_record["final_answer"] = final_answer

                messages.append({"role": "assistant", "content": content})
                trajectory["turns"].append(turn_record)

                if final_answer or finish_reason == "stop":
                    trajectory["final_answer"] = final_answer
                    trajectory["success"] = True
                    break

            trajectory["turns"].append(turn_record)

            if n_turn > MAX_TURNS and not tool_calls:
                # Model didn't produce tool calls on final turn, we're done
                break

    except Exception as e:
        trajectory["error"] = str(e)

    trajectory["total_latency_ms"] = round((time.time() - t_start) * 1000, 1)
    trajectory["total_turns"] = len(trajectory["turns"])

    return trajectory

# Reverse tool map for logging
TOOL_MAP_REVERSE = {"Read": "Read", "Glob": "Glob", "Grep": "Grep"}


# — Scoring —
def parse_final_answer(text: str) -> list:
    """Parse <final_answer> block to extract file:line citations.

    Handles three formats:
    1. Standard: <final_answer>...</final_answer>
    2. HTML-encoded: &lt;final_answer&gt;...&lt;/final_answer&gt;
    3. Free-form: file:line-line patterns in raw text
    """
    if not text:
        return []
    # Try standard tags, then HTML-encoded tags
    m = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
    if not m:
        m = re.search(r"&lt;final_answer&gt;(.*?)&lt;/final_answer&gt;", text, re.DOTALL)
    source = m.group(1).strip() if m else text.strip()
    citations = []
    for line in source.splitlines():
        line = line.strip()
        # Support both "file:start-end" and "file:line" formats
        match = re.match(r"([/\w._-]+):(\d+)(?:-(\d+))?", line)
        if match:
            fname = match.group(1)
            start = int(match.group(2))
            end = int(match.group(3)) if match.group(3) is not None else start
            # Normalize path: resolve /path/to/X -> actual workspace path
            fname = _resolve_workspace_path(fname)
            citations.append({"file": fname, "lines": list(range(start, end + 1))})
    return citations

def score_query(trajectory: dict, converted_gt: dict) -> dict:
    """Score a query result against ground truth. Returns file_f1, line_f1, etc."""
    predicted = parse_final_answer(trajectory.get("final_answer") or "")

    gt_files = set(converted_gt.get("files", []))
    pred_files = set()
    for c in predicted:
        norm = os.path.normpath(c["file"])
        if norm.endswith("/tmp/evonic_fastcontext/" + norm.lstrip("/")):
            norm = "/tmp/evonic_fastcontext/" + norm.lstrip("/")
        # Try to match as relative
        for gf in gt_files:
            if norm == gf or norm.endswith(gf) or gf.endswith(norm):
                pred_files.add(gf)
                c["_matched_file"] = gf
                break

    # File-level metrics
    tp_files = len(pred_files & gt_files)
    fp_files = len(pred_files - gt_files)
    fn_files = len(gt_files - pred_files)
    file_precision = tp_files / max(tp_files + fp_files, 1)
    file_recall = tp_files / max(tp_files + fn_files, 1)
    file_f1 = 2 * file_precision * file_recall / max(file_precision + file_recall, 0.001)

    # Line-level metrics
    gt_lines = set()
    for c in converted_gt.get("citations", []):
        for ln in c["lines"]:
            gt_lines.add((c["file"], ln))

    pred_lines = set()
    for c in predicted:
        mf = c.get("_matched_file", c["file"])
        for ln in c["lines"]:
            pred_lines.add((mf, ln))

    tp_lines = len(pred_lines & gt_lines)
    fp_lines = len(pred_lines - gt_lines)
    fn_lines = len(gt_lines - pred_lines)
    line_precision = tp_lines / max(tp_lines + fp_lines, 1)
    line_recall = tp_lines / max(tp_lines + fn_lines, 1)
    line_f1 = 2 * line_precision * line_recall / max(line_precision + line_recall, 0.001)

    return {
        "file_metrics": {"precision": file_precision, "recall": file_recall, "f1": file_f1,
                         "tp": tp_files, "fp": fp_files, "fn": fn_files},
        "line_metrics": {"precision": line_precision, "recall": line_recall, "f1": line_f1,
                        "tp": tp_lines, "fp": fp_lines, "fn": fn_lines},
    }


# — Baseline (no tools) —
def run_baseline(query: dict) -> dict:
    """Run query without tools."""
    q_text = query["query"]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"<query>\n{q_text}\n</query>"},
    ]
    t0 = time.time()
    response = call_llm(messages, tools=None)
    lat = (time.time() - t0) * 1000
    usage = extract_usage(response)
    content = response.get("choices", [{}])[0].get("message", {}).get("content") or ""
    return {"content": content, "latency_ms": round(lat, 1), "usage": usage}

# — Main evaluation —
def run_evaluation(queries: list, run_name: str = "eval"):
    """Run full evaluation: tool-calling + baseline + scoring."""
    os.makedirs(os.path.expanduser("~/dev/fastcontext/results"), exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_trajectories = []
    all_scores = []

    print(f"\n{'='*60}")
    print(f"FastContext Evaluation — {run_name} (v3)")
    print(f"Model: {MODEL_NAME}  |  MaxTurns: {MAX_TURNS}  |  Temp: {TEMPERATURE}")
    print(f"{'='*60}\n")

    # Tool-calling mode
    for i, q in enumerate(queries):
        print(f"[{i+1}/{len(queries)}] {q['id']}: {q['query'][:80]}...")
        traj = run_single_query(q, i)
        all_trajectories.append(traj)

        # Score
        gt_converted = {"files": [], "citations": []}
        for gt_item in q.get("ground_truth", []):
            fname = gt_item.get("file", "")
            gt_converted["files"].append(fname)
            start = gt_item.get("start_line", 0)
            end = gt_item.get("end_line", start)
            gt_converted["citations"].append({"file": fname, "lines": list(range(start, end + 1))})

        score = score_query(traj, gt_converted)
        score["query_id"] = q["id"]
        score["ground_truth"] = gt_converted
        all_scores.append(score)

        print(f"  turns={traj['total_turns']} file_f1={score['file_metrics']['f1']:.3f} "
              f"line_f1={score['line_metrics']['f1']:.3f} "
              f"lat={traj['total_latency_ms']:.0f}ms")

    # Save trajectories
    traj_file = os.path.expanduser(f"~/dev/fastcontext/results/trajectories_{run_name}.json")
    with open(traj_file, "w") as f:
        json.dump(all_trajectories, f, indent=2, default=str)

    # Save scores
    score_file = os.path.expanduser(f"~/dev/fastcontext/results/scores_{run_name}.json")
    with open(score_file, "w") as f:
        json.dump(all_scores, f, indent=2, default=str)

    # Baseline
    print("\nRunning baseline (no tools)...")
    baseline_results = []
    for i, q in enumerate(queries):
        try:
            result = run_baseline(q)
            baseline_results.append({"query_id": q["id"], **result})
        except Exception as e:
            baseline_results.append({"query_id": q["id"], "error": str(e)})

    baseline_file = os.path.expanduser(f"~/dev/fastcontext/results/baseline_{run_name}.json")
    with open(baseline_file, "w") as f:
        json.dump(baseline_results, f, indent=2, default=str)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY — Tool-Calling Mode")
    avg_file_f1 = sum(s["file_metrics"]["f1"] for s in all_scores) / max(len(all_scores), 1)
    avg_line_f1 = sum(s["line_metrics"]["f1"] for s in all_scores) / max(len(all_scores), 1)
    avg_turns = sum(t["total_turns"] for t in all_trajectories) / max(len(all_trajectories), 1)
    avg_latency = sum(t["total_latency_ms"] for t in all_trajectories) / max(len(all_trajectories), 1)
    total_prompt = sum(t["total_tokens"]["prompt"] for t in all_trajectories)
    total_compl = sum(t["total_tokens"]["completion"] for t in all_trajectories)

    print(f"  Avg File F1: {avg_file_f1:.3f}")
    print(f"  Avg Line F1: {avg_line_f1:.3f}")
    print(f"  Avg Turns:   {avg_turns:.1f}")
    print(f"  Avg Latency: {avg_latency:.0f}ms")
    print(f"  Total Prompt Tokens:  {total_prompt:,}")
    print(f"  Total Completion Tokens: {total_compl:,}")

    # Baseline summary
    b_file_f1_scores = []
    for br in baseline_results:
        predicted = parse_final_answer(br.get("content", ""))
        # Simple file match
        gt_files = set()
        for q in queries:
            if q["id"] == br["query_id"]:
                for gt in q.get("ground_truth", []):
                    gt_files.add(gt["file"])
                break
        pred_files = set(os.path.normpath(c["file"]) for c in predicted)
        tp = len(pred_files & gt_files)
        fp = len(pred_files - gt_files)
        fn = len(gt_files - pred_files)
        f1 = 2*tp / max(2*tp + fp + fn, 1)
        b_file_f1_scores.append(f1)

    avg_b_f1 = sum(b_file_f1_scores) / max(len(b_file_f1_scores), 1)
    print(f"\n  Baseline Avg File F1: {avg_b_f1:.3f}")

    return all_trajectories, all_scores, baseline_results


# — CLI —
if __name__ == "__main__":
    queries_file = sys.argv[1] if len(sys.argv) > 1 else "data/queries.jsonl"
    run_name = sys.argv[2] if len(sys.argv) > 2 else "v3"

    queries = []
    with open(queries_file, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    run_evaluation(queries, run_name)
