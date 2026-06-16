#!/usr/bin/env bash
# Batch runner for FastContext v4 variant evaluation
# Runs all prompt-style × tool-case × nudge combinations

set -e
cd ~/dev/fastcontext
QUERIES="data/queries.jsonl"

echo "===== FastContext v4 Variant Sweep ====="
echo "Queries: $QUERIES"
echo "Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

VARIANTS=(
    "XML+Pascal:   --prompt-style xml   --tool-case pascal"
    "XML+Upper:    --prompt-style xml   --tool-case upper"
    "JSON+Pascal:  --prompt-style json  --tool-case pascal"
    "JSON+Upper:   --prompt-style json  --tool-case upper"
    "XML+Pascal+Nudge: --prompt-style xml --tool-case pascal --nudge"
    "JSON+Pascal+Nudge: --prompt-style json --tool-case pascal --nudge"
)

for variant_line in "${VARIANTS[@]}"; do
    label="${variant_line%%:*}"
    flags="${variant_line#*:}"
    echo ""
    echo "===== $label ====="
    echo "Flags: $flags"
    echo "Started: $(date '+%H:%M:%S')"
    PYTHONUNBUFFERED=1 python3 -u src/eval_v4.py $flags "$QUERIES" "$label"
    echo "Finished: $(date '+%H:%M:%S')"
    sleep 2
done

echo ""
echo "===== ALL DONE ====="
echo "Finished at: $(date '+%Y-%m-%d %H:%M:%S')"

# Print summary table
echo ""
echo "===== SCORE SUMMARY ====="
for f in results/scores_*.json; do
    if [ -f "$f" ]; then
        name=$(basename "$f" .json)
        avg=$(python3 -c "
import json
with open('$f') as fh:
    data = json.load(fh)
if data:
    ff1 = sum(s['file_metrics']['f1'] for s in data)/len(data)
    lf1 = sum(s['line_metrics']['f1'] for s in data)/len(data)
    print(f'{ff1:.3f}  {lf1:.3f}')
")
        printf "%-40s  FileF1: %s\n" "$name" "$avg"
    fi
done
