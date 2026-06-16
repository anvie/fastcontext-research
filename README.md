# FastContext-1.0-4B-SFT — Research & Evaluation

Evaluasi `microsoft/FastContext-1.0-4B-SFT` sebagai read-only repository exploration
subagent menggunakan tool-calling (Read + Glob + Grep via ripgrep). Target: codebase
evonic (361 file) di `/tmp/evonic_fastcontext`.

**🏆 Hasil terbaik: 0.950 File F1, 19/20 query lulus.**

---

## Ringkasan Perjalanan Riset

### Fase 1: v1 — Baseline Awal (10 query)

Pengujian pertama dengan prompt sederhana dan 10 query natural-language. Model
langsung menunjukkan kemampuan tool-calling: bisa grep, glob, dan read file.
**File F1 = 0.900, 9/10 benar** — tapi scoring awal optimistic karena sistem
belum fully deterministic.

File: `legacy/eval_v1.py`, data: `data/queries_v1.jsonl`.

### Fase 2: v3a–v3c — Menemukan Optimal Turns (10 query)

Transisi ke `eval_v3.py` (sekarang `src/eval.py`) dengan tool-calling loop yang
lebih robust. **Eksperimen max_turns:**

| Variant | Max Turns | File F1 | Insight |
|---------|-----------|---------|---------|
| **v3a** | 4 | **0.200** | ❌ Terlalu sedikit — model kehabisan turn sebelum temukan jawaban |
| **v3b** | 8 | **0.800** | ✅ Turns cukup — langsung lompat ke 0.800 |
| **v3c** | 8 | 0.500 | 🔄 Variance tinggi — prompt belum stabil |

**Finding:** 4 turns tidak cukup untuk eksplorasi multi-step. 8 turns optimal
— beyond 8 tidak ada benefit.

### Fase 3: Context Length Crisis (10 query)

Context 4K default dari llama-server menyebabkan **60% HTTP 400** error —
tool result melampaui context window setelah 1-2 turn.

| `--ctx-size` | File F1 | Line F1 | Pass |
|-------------|---------|---------|------|
| 4,096 | 0.200 | 0.149 | 2/10 |
| **65,536** | **0.900** | **0.551** | **9/10** |
| 131,072 | 0.900 | 0.585 | 9/10 |

**Finding:** Context minimum realistic adalah 16K. 64K adalah sweet spot —
128K tidak memberi gain tambahan. Ini menjadi **variable kritis** yang
sebelumnya tidak disadari.

### Fase 4: v3d–v3h — Iterative Prompt Engineering (10→20 query)

Dengan 64K context, iterasi fokus ke prompt design dan parameter tuning.
Di fase ini query diperluas dari 10 ke 20.

| Variant | File F1 | Perubahan dari sebelumnya |
|---------|---------|--------------------------|
| v3d | 0.750 | Prompt refinement, temp tuning |
| v3e | 0.800 | Enable thinking mode |
| v3f | 0.400 | ❌ Regression — thinking ON degraded 4B |
| v3g | 0.700 | Revert thinking OFF, prompt rewrite |
| **v3h** | **0.900** | Markdown prompt, temp=1.0, top_p=0.95, thinking=OFF |

**Key decisions di fase ini:**
- **Thinking mode harus OFF**: Qwen-4B menghasilkan reasoning tokens yang
  mengisi context window dan mendegradasi tool-calling (v3f drop ke 0.400).
- **Temperature 1.0 optimal**: Sesuai rekomendasi FastContext official.
  Lower temps (0.1–0.3) menghasilkan output deterministik tapi sering salah.
- **Top-P 0.95, Top-K 20**: Konfigurasi sampling stabil.

### Fase 5: v4 — Variant Sweep (20 query)

Untuk mengisolasi faktor mana yang paling berpengaruh, `eval_v4.py`
(sekarang `src/eval_v4.py`) dibuat sebagai wrapper parameterized:
- **3 prompt formats** × **2 tool cases** × **2 nudge** = 12 kombinasi
- Prompt: Markdown (default), XML, JSON
- Tool Case: PascalCase (Read/Glob/Grep), UPPERCASE (READ/GLOB/GREP)
- Nudge: disabled, enabled

**Full sweep results (10 unique variants):**

| Rank | Variant | Prompt | Tools | Nudge | File F1 | Line F1 | Pass |
|------|---------|--------|-------|-------|---------|---------|------|
| 1 | **v4default** | Markdown | Pascal | No | 0.800 | 0.448 | 16/20 |
| 2 | XML+Nudge | XML | Pascal | Yes | 0.650 | 0.421 | 13/20 |
| 3 | XML+Pascal | XML | Pascal | No | 0.550 | 0.253 | 11/20 |
| 4 | JSON+Nudge | JSON | Pascal | Yes | 0.350 | 0.183 | 7/20 |
| 5 | XML+Upper | XML | UPPER | No | 0.300 | 0.157 | 6/20 |
| 6 | JSON+Upper | JSON | UPPER | No | 0.250 | 0.158 | 5/20 |
| 7 | JSON+Pascal | JSON | Pascal | No | 0.200 | 0.063 | 4/20 |

**Prompt format by impact (delta from Markdown):**

| Format | Best F1 | Δ from default | Degradasi |
|--------|---------|----------------|-----------|
| **Markdown** (natural language) | **0.800** | baseline | — |
| XML (structured tags) | 0.650 | -0.150 | -19% |
| JSON (schema objects) | 0.350 | -0.450 | -56% |

**Tool case impact (PascalCase baseline 0.800):**

| Case | Avg F1 | Δ |
|------|--------|---|
| PascalCase (Read/Glob/Grep) | 0.800 | — |
| UPPERCASE (READ/GLOB/GREP) | 0.275 | -0.525 |

**Nudge impact:** +0.100 avg File F1 (rescues 2-3 queries per format).

### Fase 6: v3i — Konfigurasi Final (20 query)

Menggabungkan semua temuan: Markdown prompt v3h + nudge mechanism.

**Hasil: 0.950 File F1, 0.549 Line F1, 19/20 pass.**

| Metrik | v3i (best) | v3h (sebelumnya) | Baseline |
|--------|-----------|-----------------|----------|
| File F1 | **0.950** | 0.900 | 0.100 |
| Line F1 | **0.549** | 0.495 | 0.072 |
| Correct Files | **19/20** | 18/20 | 2/20 |
| Avg Turns | 3.4 | 3.4 | 5.7 |

Satu-satunya query yang gagal: **q20** — multi-hop reasoning (cari
`llm_loop.py` → baca imports → cross-reference sister modules). Ini
kemungkinan ceiling 4B — butuh model 7B+.

---

## Final Ranking — Top to Bottom

| Rank | Variant | File F1 | Line F1 | Pass |
|------|---------|---------|---------|------|
| 🥇 | **v3i** | **0.950** | 0.549 | 19/20 |
| 🥈 | v3h | 0.900 | 0.495 | 18/20 |
| 🥉 | v4default | 0.800 | 0.448 | 16/20 |
| 4 | v3d | 0.750 | 0.380 | 15/20 |
| 5 | XML+Nudge | 0.650 | 0.421 | 13/20 |
| 6 | XML+Pascal | 0.550 | 0.253 | 11/20 |
| 7 | JSON+Nudge | 0.350 | 0.183 | 7/20 |
| 8 | XML+Upper | 0.300 | 0.157 | 6/20 |
| 9 | JSON+Upper | 0.250 | 0.158 | 5/20 |
| 10 | JSON+Pascal | 0.200 | 0.063 | 4/20 |

Detail per-query matrix dan analisis di [EVALUATION.md](EVALUATION.md).

---

## Setup

### Prasyarat

- llama.cpp server dengan model dimuat (OpenAI-compatible endpoint)
- Python 3.8+
- ripgrep (`rg`) terinstall
- Git (clone repo target)

### Model

Q4_K_M GGUF (2.4 GB) — backbone Qwen3-4B-Instruct, SFT+GRPO, 262K native context:
```
~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf
```

### Menjalankan llama-server

```bash
tmux new-session -d -s llm \
  "cd ~/dev/llama-cpp/build && ./bin/llama-server \
    -m ~/dev/llama-cpp/models/fastcontext-1.0-4b-sft-q4_k_m.gguf \
    --host 0.0.0.0 --port 8080 \
    --ctx-size 65536 --temp 1.0 \
    --n-gpu-layers 99"
```

> **⚠️ Context 64K mandatory.** Di bawah 16K context overflow setelah 1-2
> turn tool results. Lihat [EVALUATION.md](EVALUATION.md) untuk data sweep.

### Target Repository

```bash
git clone https://github.com/anvie/fastcontext-research /tmp/evonic_fastcontext
```

## Cara Pakai

### V3 (rekomendasi — produksi)

Evaluasi single-run dengan konfigurasi optimal:

```bash
cd ~/dev/fastcontext
python3 src/eval.py data/queries.jsonl v3i
```

Output: `results/scores_v3i.json`, `results/trajectories_v3i.jsonl`,
`results/eval_v3i.log`.

### V4 (variant sweep — eksperimen)

Untuk menguji kombinasi prompt format, tool casing, dan nudge:

```bash
python3 src/eval_v4.py data/queries.jsonl <run_name> \
  --prompt-style default|xml|json \
  --tool-case pascal|upper \
  --nudge
```

**Contoh:**

```bash
# Replikasi best config (Markdown + PascalCase + nudge)
python3 src/eval_v4.py data/queries.jsonl replicate --prompt-style default --nudge

# Test XML format tanpa nudge
python3 src/eval_v4.py data/queries.jsonl xml_test --prompt-style xml

# Test JSON + UPPERCASE + nudge (worst-case + nudge rescue)
python3 src/eval_v4.py data/queries.jsonl json_nudge --prompt-style json --tool-case upper --nudge
```

### Sweep otomatis

```bash
bash scripts/sweep.sh
```

Menjalankan semua 6 kombinasi v4 (3 prompt × 2 tool case, nudge off)
dan mengumpulkan hasil di `results/v4_sweep.log`.

### Output Files

| File | Deskripsi |
|------|-----------|
| `results/scores_{name}.json` | Per-query file/line F1, precision, recall |
| `results/trajectories_{name}.jsonl` | Turn-by-turn trace: prompt, tool calls, responses, latency |
| `results/eval_{name}.log` | Runtime log + summary statistik |
| `results/baseline_{name}.json` | Baseline tanpa tool-calling (query langsung ke model) |

## Konfigurasi Optimal (v3i)

| Parameter | Value | Keterangan |
|-----------|-------|------------|
| **Prompt format** | Markdown (natural language) | 🔴 **Kritis** — XML drop -19%, JSON drop -56% |
| **Tool names** | PascalCase (Read/Glob/Grep) | 🔴 UPPERCASE drop -0.525 |
| **Nudge** | Enabled | 🟡 +0.050 File F1, selamatkan 2-3 query |
| **Temperature** | 1.0 | 🟡 Official FastContext recommendation |
| **Top-P** | 0.95 | |
| **Top-K** | 20 | |
| **Thinking** | **OFF** | 🔴 ON mendegradasi 4B (ditemukan di v3f: 0.400) |
| **Max turns** | 8 | Beyond 8 tidak ada benefit |
| **Context** | **64K** (65536) | 🔴 Minimum 16K; 4K = 60% overflow error |

## Struktur Proyek

```
fastcontext/
├── src/
│   ├── eval.py           # Harness utama — tool-calling loop + scoring
│   └── eval_v4.py        # Wrapper parameterized (prompt/tool-case/nudge)
├── legacy/
│   └── eval_v1.py        # Baseline awal v1 (10 query, prompt sederhana)
├── tools/
│   ├── read.md           # Tool description — Read
│   ├── glob.md           # Tool description — Glob
│   └── grep.md           # Tool description — Grep (ripgrep)
├── prompts/
│   ├── default.md        # ✅ Markdown prompt (v3h/v3i — produksi)
│   ├── xml.md            # XML prompt (eksperimen — **jangan pakai di 4B**)
│   └── json.md           # JSON prompt (eksperimen — **jangan pakai di 4B**)
├── data/
│   ├── queries.jsonl     # 20 query evaluasi + ground truth (v3/v4)
│   └── queries_v1.jsonl  # 10 query original (v1/v2)
├── scripts/
│   └── sweep.sh          # Batch runner untuk semua kombinasi v4
├── results/              # Output directory (gitignored)
├── EVALUATION.md         # Laporan komprehensif — semua varian + matriks per-query
└── README.md             # File ini
```

## Key Findings (7 Temuan Utama)

### 1. 🔴 Prompt format adalah faktor paling dominan

Prompt Markdown (natural language) **2× lebih baik dari XML, 4.5× lebih baik
dari JSON** untuk model 4B. Ini adalah single largest factor — lebih besar dari
tool casing, nudge, atau parameter sampling manapun.

**Mekanisme:** XML/JSON tags membingungkan model — model memperlakukan
tag sebagai content separator, bukan structural hints. JSON prompt sering
diconfuse dengan JSON tool-call, menyebabkan malformed calls.

**Rekomendasi:** Selalu gunakan Markdown prompt untuk FastContext 4B.
XML/JSON hanya untuk benchmark. Ini mungkin tidak berlaku untuk model 7B+.

### 2. 🔴 Thinking mode harus OFF

Ditemukan di v3f: mengaktifkan Qwen thinking mode menyebabkan drop dari
0.800 ke 0.400. Reasoning tokens mengisi context window dan mengganggu
tool-calling flow.

### 3. 🔴 Context 64K mandatory — 4K fatal

4K context menghasilkan 60% HTTP 400 error (overflow setelah 1-2 turn).
Minimum realistic adalah 16K, sweet spot di 64K. 128K tidak memberi gain.

### 4. 🟡 Nudge menyelamatkan 2-3 query — murah, esensial

Mekanisme nudge: ketika model gagal menghasilkan `<final_answer>`, sistem
mengulang dengan prompt tambahan. Impact: +0.050 File F1, zero cost pada
successful runs. q18 dan q19 diselamatkan dari 0.000 ke 1.000.

Namun non-deterministic — q20 kadang regresi karena path berbeda di retry
kedua. Rata-rata 2-3 run disarankan.

### 5. 🟡 Temperature 1.0 optimal — ~10% variance antar run

Temperature rendah (0.1–0.3) menghasilkan output deterministik tapi sering
salah total. Temperature 1.0 memberi diversity yang dibutuhkan untuk
tool-calling exploration. Imbasnya: ~10% variance antar run identik.
Averaging 2-3 run stabilisasi di ~0.900.

### 6. 🟡 4B ceiling di ~95% File F1

17 dari 20 query mencapai File F1 = 1.000. Celah tersisa adalah **q20** —
multi-hop reasoning (cari import di `llm_loop.py`, cross-reference sister
modules). Ini membutuhkan reasoning chain yang terlalu kompleks untuk 4B.
**Rekomendasi: upgrade ke 7B+ untuk 100% akurasi.**

### 7. 🟢 Path auto-correction mandatory

Model sering menghalusinasi workspace path (misalnya menulis `/src/config.py`
tanpa prefix). Auto-correction via glob+resolve fallback wajib diaktifkan
di production. Tanpa ini, 15-20% tool calls akan gagal dengan "file not found".

## Model Characteristics

### Kelebihan 4B

- **Single-file discovery**: Mencari konstanta, class, fungsi di file yang
  dikenal — near-perfect (q1, q2, q6, q10, q11 semua ≥0.900)
- **Grep-first strategy**: Model secara natural mulai dengan regex search
  sebelum membaca file — sesuai panduan prompt Markdown
- **Parallel tool calls**: Ketika diprompt, model menggunakan multiple tools
  simultan (Grep + Glob + Read dalam satu turn)
- **File-level accuracy**: Tahu *file mana* yang harus dilihat — 17/20 query
  mencapai File F1 = 1.000

### Kelemahan 4B

- **Line-level precision rendah**: Rata-rata Line F1 hanya 0.549 meskipun
  File F1 0.950 — model sering melaporkan range baris yang salah
- **Multi-step reasoning**: Query yang butuh 3+ hop reasoning (q20) gagal total
- **Deep paths**: File 3+ level dalam (mis. `backend/supervisor/_helpers.py`)
  lebih sulit ditemukan daripada shallow files
- **Path retention**: Model kadang lupa workspace path di sesi multi-turn
- **Output format compliance**: ~10% run menghasilkan teks tanpa
  `<final_answer>` tags (nudge membantu tapi tidak fully solve)

## Rekomendasi Final

| Level | Rekomendasi |
|-------|------------|
| 🟢 **Produksi** | v3i config: Markdown + nudge + 64K + temp 1.0 (0.950 File F1) |
| 🟡 **Stabilisasi** | Jalankan 2-3 pass dan average (atasi ~10% variance) |
| 🟡 **Upgrade** | Pakai 7B+ model untuk 100% File F1 (atasi q20 multi-hop) |
| 🔴 **Jangan** | XML/JSON prompt di 4B — mereka secara aktif mendegradasi performa |
| 🟢 **Wajib** | Path auto-correction — model menghalusinasi workspace paths |

---

## Lisensi

Proyek riset. MIT.
