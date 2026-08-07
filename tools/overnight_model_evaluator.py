#!/usr/bin/env python3
"""Overnight Model Evaluator & Benchmark Suite for Helga.

Executes comprehensive, zero-swap pedagogical quality audits across all 12
installed Ollama models on the Mac Mini.

Features:
  1. Full 5-tier depth contract probe (Mastery 1 to 5).
  2. Full 4-profile Socratic HelgaBench battery (confused_beginner, fast_learner,
     misconception_holder, confident_bluffer).
  3. OOM & RAM guard with forced Ollama model eviction between candidate runs.
  4. Real-time markdown report synthesis to docs/OVERNIGHT_MODEL_TESTING_REPORT.md.
"""

import json
import os
import subprocess
import sys
import time

MODELS_QUEUE = [
    # --- Group A: Fast Baseline & Lightweight (4B - 9B) ---
    {"name": "qwen3.5:9b", "label": "Qwen 3.5 9B Baseline", "tier": "Baseline", "params": "9B"},
    {"name": "hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M", "label": "Ministral 8B Q4", "tier": "Lightweight", "params": "8B"},
    {"name": "hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q6_K", "label": "Ministral 8B Q6", "tier": "Lightweight", "params": "8B"},
    {"name": "qwen3.5:4b", "label": "Qwen 3.5 4B", "tier": "Ultra-Fast", "params": "4B"},

    # --- Group B: Mid-Range Socratic (12B - 14B) ---
    {"name": "hf.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF:Q4_0", "label": "Gemma 4 12B QAT", "tier": "Mid-Range", "params": "12B"},
    {"name": "hf.co/bartowski/google_gemma-3-12b-it-GGUF:Q4_K_M", "label": "Gemma 3 12B", "tier": "Mid-Range", "params": "12B"},
    {"name": "qwen3:14b-q4_K_M", "label": "Qwen 3 14B Q4", "tier": "Mid-Range", "params": "14B"},
    {"name": "qwen2.5-coder:14b-instruct", "label": "Qwen 2.5 Coder 14B", "tier": "Mid-Range", "params": "14B"},

    # --- Group C: Heavy / Graduate Candidates (23B - 30B) ---
    {"name": "hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M", "label": "GLM 4.7 Flash 23B", "tier": "Heavy", "params": "23B"},
    {"name": "hf.co/unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF:Q4_K_M", "label": "Mistral Small 3.2 24B", "tier": "Heavy", "params": "24B"},
    {"name": "hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M", "label": "Qwen 3.6 27B MTP", "tier": "Heavy", "params": "27B"},
    {"name": "hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q5_K_M", "label": "Qwen 3 Coder 30B", "tier": "Heavy", "params": "30B"}
]

REPORT_PATH = "/Users/brennankelley/Desktop/Helga-Soctraic-Voice-Tutor-main/docs/OVERNIGHT_MODEL_TESTING_REPORT.md"
JSON_PATH = "/Users/brennankelley/Desktop/Helga-Soctraic-Voice-Tutor-main/data/overnight_eval_results.json"

def get_swap_mb():
    try:
        res = subprocess.check_output(["sysctl", "vm.swapusage"]).decode()
        if "used =" in res:
            part = res.split("used =")[1].split()[0]
            if part.endswith("M"):
                return float(part[:-1])
            elif part.endswith("G"):
                return float(part[:-1]) * 1024.0
    except Exception:
        pass
    return 0.0

def unload_ollama_model(model_name):
    """Evict model weights from VRAM to prevent OOM / swap leaks between test runs."""
    try:
        cmd = f"curl -s http://localhost:11434/api/generate -d '{{\"model\": \"{model_name}\", \"keep_alive\": 0}}'"
        subprocess.run(cmd, shell=True, timeout=15)
    except Exception as e:
        print(f"  [RAM GUARD] Warning: model unload failed for {model_name}: {e}")

def run_cmd(cmd, timeout=360):
    try:
        t0 = time.time()
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        elapsed = time.time() - t0
        return p.returncode, p.stdout, elapsed
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT EXPIRED", float(timeout)

def generate_markdown_report(results):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Overnight Model Testing & Benchmark Report",
        f"**Generated**: {now_str} | **Hardware**: Apple Mac Mini M4 Pro (24 GB Unified Memory)",
        "",
        "## 1. Executive Summary & Comparative Matrix",
        "",
        "| Model Candidate | Params | Category | Tier 1-5 Pass Rate | HelgaBench Score (1-5) | Active Swap | Total Test Latency | Verdict |",
        "|---|---|---|---|---|---|---|---|"
    ]
    
    for r in results:
        t_pass_count = sum(1 for status in r.get("tiers", {}).values() if status == "PASS")
        t_total = len(r.get("tiers", {}))
        pass_str = f"{t_pass_count}/{t_total}" if t_total > 0 else "0/0"
        score = r.get("helgabench_score", 0.0)
        swap = r.get("swap_mb", 0.0)
        lat = r.get("total_sec", 0.0)
        
        verdict = "REJECTED"
        if t_pass_count >= 4 and score >= 4.0 and swap < 1500:
            verdict = "**APPROVED (Top Tier)**"
        elif t_pass_count >= 3 and score >= 3.5:
            verdict = "CANDIDATE (Secondary)"
        elif swap > 5000:
            verdict = "REJECTED (High Swap)"
        
        lines.append(f"| `{r['name']}` | {r['params']} | {r['tier']} | **{pass_str}** | **{score}/5.0** | {swap:.1f} MB | {lat:.1f}s | {verdict} |")
        
    lines.extend([
        "",
        "---",
        "",
        "## 2. Detailed Per-Model Diagnostic Results",
        ""
    ])
    
    for r in results:
        lines.append(f"### `{r['name']}` ({r['label']})")
        lines.append(f"- **Category**: {r['tier']} ({r['params']})")
        lines.append(f"- **Active Swap Impact**: {r['swap_mb']:.1f} MB")
        lines.append(f"- **HelgaBench Socratic Score**: **{r['helgabench_score']}/5.0**")
        lines.append("- **Mastery Tier Probes**:")
        for t_lvl, status in r.get("tiers", {}).items():
            lines.append(f"  - Mastery {t_lvl}: **{status}**")
        lines.append(f"- **Execution Log Summary**: `{r.get('summary_text', 'OK')[:200]}`")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 3. Hardware & Memory Audit Notes",
        "- **Single-Model Pinning**: `OLLAMA_MAX_LOADED_MODELS=1` enforced across all runs.",
        "- **KV-Cache**: `q8_0` quantized cache enabled.",
        "- **FlashAttention**: `OLLAMA_FLASH_ATTENTION=1` active.",
        ""
    ])

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))

def main():
    print(f"============================================================", flush=True)
    print(f"=== Starting Overnight Model Evaluation Suite ({len(MODELS_QUEUE)} candidates) ===", flush=True)
    print(f"============================================================", flush=True)
    
    completed_results = []
    
    for idx, item in enumerate(MODELS_QUEUE, 1):
        m_name = item["name"]
        m_label = item["label"]
        print(f"\n[{idx}/{len(MODELS_QUEUE)}] Processing {m_label} ({m_name})...", flush=True)
        
        prefix = f"export OLLAMA_MODEL='{m_name}' LLM_MODEL='{m_name}' OLLAMA_URL='http://localhost:11434' LLM_API_URL='http://localhost:11434/v1/chat/completions' RESEARCH_URL='http://localhost:5006' OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_FLASH_ATTENTION=1; "
        
        tier_results = {}
        total_sec = 0.0
        
        # Test Tiers 1 through 5
        for m_lvl in [1, 2, 3, 4, 5]:
            print(f"  - Probing Mastery Tier {m_lvl}...", flush=True)
            code_t, out_t, dur_t = run_cmd(prefix + f"python3 tools/tier_probe.py --mastery {m_lvl}", timeout=240)
            total_sec += dur_t
            t_pass = "PASS" in out_t and code_t == 0
            tier_results[m_lvl] = "PASS" if t_pass else "FAIL"
            print(f"    Tier {m_lvl}: {'PASS' if t_pass else 'FAIL'} ({round(dur_t, 1)}s)", flush=True)

        # HelgaBench Socratic Screening
        print("  - Running Socratic HelgaBench Battery...", flush=True)
        code_hb, out_hb, dur_hb = run_cmd(prefix + f"python3 tools/helgabench.py --model '{m_name}' --url 'http://localhost:11434' --profiles confused_beginner,fast_learner,misconception_holder,confident_bluffer --turns 3", timeout=360)
        total_sec += dur_hb
        
        overall_score = 0.0
        if "OVERALL" in out_hb:
            for line in out_hb.splitlines():
                if "OVERALL" in line:
                    try:
                        overall_score = float(line.split()[-1])
                    except Exception:
                        pass
        print(f"    HelgaBench Score: {overall_score}/5.0 ({round(dur_hb, 1)}s)", flush=True)
        
        swap_after = get_swap_mb()
        
        res_rec = {
            "name": m_name,
            "label": m_label,
            "tier": item["tier"],
            "params": item["params"],
            "tiers": tier_results,
            "helgabench_score": overall_score,
            "swap_mb": swap_after,
            "total_sec": round(total_sec, 1),
            "summary_text": f"Tiers: {tier_results} | Score: {overall_score} | Time: {round(total_sec, 1)}s"
        }
        completed_results.append(res_rec)
        
        # Save raw JSON checkpoint
        with open(JSON_PATH, "w") as f:
            json.dump(completed_results, f, indent=2)
            
        # Update Markdown Report
        generate_markdown_report(completed_results)
        
        # Evict model weights from VRAM before moving to the next model candidate
        print(f"  [RAM GUARD] Evicting {m_name} from VRAM...", flush=True)
        unload_ollama_model(m_name)
        time.sleep(3)

    print(f"\n============================================================", flush=True)
    print(f"=== OVERNIGHT EVALUATION COMPLETE — Report written to {REPORT_PATH} ===", flush=True)
    print(f"============================================================", flush=True)

if __name__ == "__main__":
    main()
