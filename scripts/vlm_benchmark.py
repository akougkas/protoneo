#!/usr/bin/env python3
"""VLM Benchmark Harness for ProtoNeo figure description.

Loads one model at a time via Ollama, runs the same 3 test images
through each, collects outputs, and saves results to JSON for analysis.

Usage:
    python scripts/vlm_benchmark.py

Models are tested sequentially. Each model is loaded, tested on 3 images,
then unloaded before the next model starts.
"""

import base64
import json
import time
from pathlib import Path

import httpx

OLLAMA_URL = "http://192.168.86.141:11434"
LLAMA_SERVER_URL = "http://192.168.86.141:8081"
OUTPUT_FILE = Path("scripts/vlm_benchmark_results.json")

# Same prompt for every model, every image
SYSTEM_PROMPT = (
    "You are an expert scientific figure analyst. Your descriptions will be read by "
    "peer reviewers who cannot see the original figures. Be thorough and precise.\n\n"
    "For charts and plots: state the chart type, list all axis labels and units, "
    "identify every data series with colors, describe trends and comparisons.\n\n"
    "For diagrams: describe every component, box, arrow, label, and data flow.\n\n"
    "For multi-panel figures: describe each panel individually.\n\n"
    "Write up to 500 words. Describe everything visible."
)

USER_PROMPT = "Describe this figure from a scientific paper.\nCaption: {caption}"

# Models to benchmark (in order)
OLLAMA_MODELS = [
    "qwen3-vl:2b",
    "qwen3-vl:4b",
    "qwen3-vl:8b",
    "hf.co/jamesburton/Phi-4-reasoning-vision-15B-GGUF:Q8_0",
]

# The 30B baseline runs on a separate llama-server, not Ollama
LLAMA_SERVER_MODEL = "qwen3-vl"  # alias on :8081

# Test images (different figure types)
TEST_IMAGES = [
    {
        "path": "data/sessions/uploads/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40_figures/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40-figure-9.png",
        "caption": "Figure 9: Rate-distortion efficiency comparison across five scientific datasets.",
        "type": "multi-subplot line charts",
    },
    {
        "path": "data/sessions/uploads/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40_figures/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40-figure-2.png",
        "caption": "Figure 2: The design overview and workflow of our OPAL framework.",
        "type": "architecture diagram",
    },
    {
        "path": "data/sessions/uploads/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40_figures/4b127f4bee364e3680fe3b488d6f45b4_hpdc26-paper40-figure-8.png",
        "caption": "Figure 8: Visual progression of multi-resolution retrieval by OPALI.",
        "type": "multi-panel 3D visualization",
    },
]


def ollama_unload_all():
    """Unload all models from Ollama GPU memory."""
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=10)
        if resp.status_code == 200:
            running = resp.json().get("models", [])
            for m in running:
                name = m.get("name", "")
                if name:
                    print(f"  Unloading {name}...")
                    httpx.post(
                        f"{OLLAMA_URL}/api/generate",
                        json={"model": name, "keep_alive": 0},
                        timeout=30,
                    )
                    time.sleep(2)
    except Exception as e:
        print(f"  Warning: unload failed: {e}")


def ollama_warmup(model: str):
    """Load model into GPU by sending a trivial request."""
    print(f"  Warming up {model}...")
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": "hello", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=300,
        )
        if resp.status_code != 200:
            print(f"  Warmup failed: {resp.status_code}")
            return False
    except Exception as e:
        print(f"  Warmup failed: {e}")
        return False
    print(f"  Model loaded.")
    return True


def get_vram_usage() -> int:
    """Read VRAM usage from mini via Ollama ps."""
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=10)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            total = sum(m.get("size_vram", 0) for m in models)
            return total
    except Exception:
        pass
    return 0


def run_ollama(model: str, image_b64: str, caption: str) -> dict:
    """Run a single image through an Ollama model."""
    prompt = USER_PROMPT.format(caption=caption)
    start = time.time()
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [image_b64]},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 4096},
            },
            timeout=600,
        )
        elapsed = time.time() - start
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "elapsed": elapsed}

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count", 0)
        eval_dur = data.get("eval_duration", 0) / 1e9
        prompt_eval_dur = data.get("prompt_eval_duration", 0) / 1e9
        tps = eval_count / eval_dur if eval_dur > 0 else 0

        return {
            "content": content,
            "words": len(content.split()),
            "tokens": eval_count,
            "tok_per_sec": round(tps, 1),
            "generation_time": round(eval_dur, 1),
            "prompt_eval_time": round(prompt_eval_dur, 1),
            "total_time": round(elapsed, 1),
        }
    except Exception as e:
        return {"error": str(e), "elapsed": round(time.time() - start, 1)}


def run_llama_server(image_b64: str, caption: str) -> dict:
    """Run a single image through the llama-server 30B baseline."""
    prompt = USER_PROMPT.format(caption=caption)
    start = time.time()
    try:
        resp = httpx.post(
            f"{LLAMA_SERVER_URL}/v1/chat/completions",
            json={
                "model": LLAMA_SERVER_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                        }},
                    ]},
                ],
                "temperature": 0.1,
                "top_p": 0.9,
            },
            timeout=600,
        )
        elapsed = time.time() - start
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "elapsed": elapsed}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)

        return {
            "content": content,
            "words": len(content.split()),
            "tokens": completion_tokens,
            "tok_per_sec": round(completion_tokens / elapsed, 1) if elapsed > 0 else 0,
            "total_time": round(elapsed, 1),
        }
    except Exception as e:
        return {"error": str(e), "elapsed": round(time.time() - start, 1)}


def main():
    # Load test images
    images = []
    for img_info in TEST_IMAGES:
        path = Path(img_info["path"])
        if not path.exists():
            print(f"ERROR: Image not found: {path}")
            return
        img_b64 = base64.b64encode(path.read_bytes()).decode()
        images.append((img_b64, img_info))
    print(f"Loaded {len(images)} test images.\n")

    results = {}

    # Test Ollama models
    for model in OLLAMA_MODELS:
        print(f"\n{'='*60}")
        print(f"  MODEL: {model}")
        print(f"{'='*60}")

        ollama_unload_all()
        time.sleep(3)

        if not ollama_warmup(model):
            results[model] = {"error": "warmup failed"}
            continue

        vram = get_vram_usage()
        print(f"  VRAM: {vram / 1e9:.1f} GB")

        model_results = {"vram_bytes": vram, "images": {}}
        for img_b64, img_info in images:
            img_type = img_info["type"]
            print(f"  Testing: {img_type}...")
            result = run_ollama(model, img_b64, img_info["caption"])
            model_results["images"][img_type] = result
            if "error" in result:
                print(f"    ERROR: {result['error']}")
            else:
                print(f"    {result['words']} words | {result['tokens']} tokens | {result['tok_per_sec']} tok/s | {result['total_time']}s")

        results[model] = model_results

    # Test 30B baseline on llama-server
    print(f"\n{'='*60}")
    print(f"  MODEL: Qwen3-VL-30B (llama-server baseline)")
    print(f"{'='*60}")

    # Check if the server is running
    try:
        health = httpx.get(f"{LLAMA_SERVER_URL}/health", timeout=5)
        if health.status_code != 200:
            print("  llama-server not healthy, skipping baseline")
            results["qwen3-vl-30b-baseline"] = {"error": "server not healthy"}
        else:
            model_results = {"vram_bytes": 0, "images": {}}
            for img_b64, img_info in images:
                img_type = img_info["type"]
                print(f"  Testing: {img_type}...")
                result = run_llama_server(img_b64, img_info["caption"])
                model_results["images"][img_type] = result
                if "error" in result:
                    print(f"    ERROR: {result['error']}")
                else:
                    print(f"    {result['words']} words | {result['tokens']} tokens | {result['tok_per_sec']} tok/s | {result['total_time']}s")
            results["qwen3-vl-30b-baseline"] = model_results
    except Exception as e:
        print(f"  llama-server unreachable: {e}")
        results["qwen3-vl-30b-baseline"] = {"error": str(e)}

    # Save results
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n\nResults saved to {OUTPUT_FILE}")

    # Print summary table
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"{'Model':<55} {'VRAM':>6} {'Avg Words':>10} {'Avg tok/s':>10} {'Avg Time':>10}")
    print(f"{'-'*55} {'-'*6} {'-'*10} {'-'*10} {'-'*10}")
    for model, data in results.items():
        if "error" in data:
            print(f"{model:<55} {'FAIL':>6}")
            continue
        imgs = data.get("images", {})
        valid = [v for v in imgs.values() if "error" not in v]
        if not valid:
            print(f"{model:<55} {'FAIL':>6}")
            continue
        vram_gb = data.get("vram_bytes", 0) / 1e9
        avg_words = sum(v["words"] for v in valid) / len(valid)
        avg_tps = sum(v["tok_per_sec"] for v in valid) / len(valid)
        avg_time = sum(v["total_time"] for v in valid) / len(valid)
        print(f"{model:<55} {vram_gb:>5.1f}G {avg_words:>10.0f} {avg_tps:>10.1f} {avg_time:>10.1f}s")


if __name__ == "__main__":
    main()
