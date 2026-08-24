# Overnight Model Testing & Benchmark Report
**Generated**: 2026-08-06 03:04:21 | **Hardware**: Apple Mac Mini M4 Pro (24 GB Unified Memory)

## 1. Executive Summary & Comparative Matrix

| Model Candidate | Params | Category | Tier 1-5 Pass Rate | Active Swap | Total Test Latency | Verdict |
|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 9B | Baseline | **5/5 (100%)** | 14,104 MB | 983.7s | **PRODUCTION CHAMPION (100% Pass)** |
| `qwen3.5:4b` | 4B | Ultra-Fast | **4/5 (80%)** | 9,352 MB | 1,194.7s | **TOP LIGHTWEIGHT MODEL (80% Pass)** |
| `hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M` | 8B | Lightweight | **1/5 (20%)** | 9,670 MB | 1,428.6s | REJECTED (20% Pass) |
| `hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q6_K` | 8B | Lightweight | **1/5 (20%)** | 9,432 MB | 1,456.4s | REJECTED (20% Pass) |
| `hf.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF:Q4_0` | 12B | Mid-Range | **1/5 (20%)** | 10,380 MB | 1,517.2s | REJECTED (20% Pass) |
| `hf.co/bartowski/google_gemma-3-12b-it-GGUF:Q4_K_M` | 12B | Mid-Range | **1/5 (20%)** | 11,962 MB | 1,549.5s | REJECTED (20% Pass) |
| `hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M` | 23B | Heavy | **1/5 (20%)** | 11,992 MB | 1,471.1s | REJECTED (20% Pass) |
| `qwen3:14b-q4_K_M` | 14B | Mid-Range | **0/5 (0%)** | 10,047 MB | 1,538.0s | REJECTED (Format Drift) |
| `qwen2.5-coder:14b-instruct` | 14B | Mid-Range | **0/5 (0%)** | 9,783 MB | 1,560.0s | REJECTED (Format Drift) |
| `hf.co/unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF:Q4_K_M` | 24B | Heavy | **0/5 (0%)** | 14,525 MB | 1,560.0s | REJECTED (Slow Latency) |
| `hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M` | 27B | Heavy | **0/5 (0%)** | 16,029 MB | 1,560.0s | REJECTED (Slow Latency) |
| `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q5_K_M` | 30B | Heavy | **0/5 (0%)** | 15,706 MB | 166.6s | REJECTED (OOM / Timeout) |

---

## 2. Detailed Per-Model Diagnostic Results

### `qwen3.5:9b` (Qwen 3.5 9B Baseline)
- **Category**: Baseline (9B)
- **Active Swap Impact**: 14104.2 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **PASS**
  - Mastery 2: **PASS**
  - Mastery 3: **PASS**
  - Mastery 4: **PASS**
  - Mastery 5: **PASS**
- **Execution Log Summary**: `Tiers: {1: 'PASS', 2: 'PASS', 3: 'PASS', 4: 'PASS', 5: 'PASS'} | Score: 0.0 | Time: 983.7s`

### `hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M` (Ministral 8B Q4)
- **Category**: Lightweight (8B)
- **Active Swap Impact**: 9670.8 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'PASS', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1428.6s`

### `hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q6_K` (Ministral 8B Q6)
- **Category**: Lightweight (8B)
- **Active Swap Impact**: 9432.9 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'PASS', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1456.4s`

### `qwen3.5:4b` (Qwen 3.5 4B)
- **Category**: Ultra-Fast (4B)
- **Active Swap Impact**: 9352.9 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **PASS**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **PASS**
  - Mastery 5: **PASS**
- **Execution Log Summary**: `Tiers: {1: 'PASS', 2: 'PASS', 3: 'FAIL', 4: 'PASS', 5: 'PASS'} | Score: 0.0 | Time: 1194.7s`

### `hf.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF:Q4_0` (Gemma 4 12B QAT)
- **Category**: Mid-Range (12B)
- **Active Swap Impact**: 10380.4 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'PASS', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1517.2s`

### `hf.co/bartowski/google_gemma-3-12b-it-GGUF:Q4_K_M` (Gemma 3 12B)
- **Category**: Mid-Range (12B)
- **Active Swap Impact**: 11962.0 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'PASS', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1549.5s`

### `qwen3:14b-q4_K_M` (Qwen 3 14B Q4)
- **Category**: Mid-Range (14B)
- **Active Swap Impact**: 10047.4 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **FAIL**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'FAIL', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1538.0s`

### `qwen2.5-coder:14b-instruct` (Qwen 2.5 Coder 14B)
- **Category**: Mid-Range (14B)
- **Active Swap Impact**: 9783.4 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **FAIL**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'FAIL', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1560.0s`

### `hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M` (GLM 4.7 Flash 23B)
- **Category**: Heavy (23B)
- **Active Swap Impact**: 11992.6 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **PASS**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'PASS', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1471.1s`

### `hf.co/unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF:Q4_K_M` (Mistral Small 3.2 24B)
- **Category**: Heavy (24B)
- **Active Swap Impact**: 14525.8 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **FAIL**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'FAIL', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1560.0s`

### `hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M` (Qwen 3.6 27B MTP)
- **Category**: Heavy (27B)
- **Active Swap Impact**: 16029.6 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **FAIL**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'FAIL', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 1560.0s`

### `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q5_K_M` (Qwen 3 Coder 30B)
- **Category**: Heavy (30B)
- **Active Swap Impact**: 15706.9 MB
- **HelgaBench Socratic Score**: **0.0/5.0**
- **Mastery Tier Probes**:
  - Mastery 1: **FAIL**
  - Mastery 2: **FAIL**
  - Mastery 3: **FAIL**
  - Mastery 4: **FAIL**
  - Mastery 5: **FAIL**
- **Execution Log Summary**: `Tiers: {1: 'FAIL', 2: 'FAIL', 3: 'FAIL', 4: 'FAIL', 5: 'FAIL'} | Score: 0.0 | Time: 166.6s`

---

## 3. Hardware & Memory Audit Notes
- **Single-Model Pinning**: `OLLAMA_MAX_LOADED_MODELS=1` enforced across all runs.
- **KV-Cache**: `q8_0` quantized cache enabled.
- **FlashAttention**: `OLLAMA_FLASH_ATTENTION=1` active.
