"""
Re-run previously failed BrowseComp-ZH samples with updated prompts.
"""
import asyncio, json, re, unicodedata, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_os import AgentOS

POOL = Path("/home/xiemingjie/dev/benchmark/data/browsecomp_zh/pool.jsonl")
SAMPLE_IDS = ["bc_zh_001","bc_zh_002","bc_zh_004","bc_zh_008","bc_zh_015",
              "bc_zh_017","bc_zh_020","bc_zh_022","bc_zh_080","bc_zh_226"]
SEM = 5

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / f"rerun_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def _normalize(text: str) -> str:
    replacements = {
        "·":".", "：":":", "，":",", "；":";", "（":"(", "）":")",
        "　":" ", "”":'"', "“":'"', "‘":"'", "’":"'",
        "—":"-", "–":"-", "…":"...", "、":",",
    }
    t = unicodedata.normalize("NFKC", text)
    for f, h in replacements.items():
        t = t.replace(f, h)
    t = re.sub(r"(\d+)年(\d+)月(\d+)日", r"\1.\2.\3", t)
    t = re.sub(r"(\d+)年(\d+)月", r"\1.\2", t)
    t = re.sub(r"(\d+)年", r"\1", t)
    t = re.sub(r"\s+", "", t)
    return t.lower()

def _safe(obj):
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return str(obj)[:500]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        json.dumps(obj); return obj
    except: return str(obj)[:1000]

async def run_one(osys, sample, log_dir, sem):
    async with sem:
        sid = sample["sample_id"]
        session = await osys.create_session(name=f"eval_{sid}")
        content_parts, last_content, raw_events, thinking_parts = [], "", [], []
        last_status = ""; tok_count = 0; iter_count = 0
        async for event in osys.chat(session.id, sample["prompt"], max_iterations=32):
            clean = _safe(event)
            raw_events.append(clean)
            t, p = event.get("type",""), event.get("phase","")
            if t == "content":
                last_content = event.get("content","")
            elif t == "content_stream":
                content_parts.append(event.get("content",""))
            elif t == "thinking_stream":
                pass  # consumed by model.completed reasoning_text
            elif p == "model.completed":
                u = (event.get("payload") or {}).get("usage") or {}
                tok_count = u.get("total_tokens", 0) or 0
                iter_count = event.get("payload",{}).get("iteration", iter_count)
                rt = (event.get("payload") or {}).get("reasoning_text","")
                if rt: thinking_parts.append(rt)
                cr = u.get("cached_tokens", 0) / max(u.get("prompt_tokens", 1), 1) * 100
                cls = event.get("payload",{}).get("latency_ms", 0) / 1000
                print(f"  [{sid}] iter={iter_count} tok={tok_count} cache={cr:.0f}% lat={cls:.0f}s", flush=True)
            elif p == "content.stripped":
                saved = (event.get("payload") or {}).get("saved_tokens", 0)
                print(f"  [{sid}] ~{saved} tokens stripped", flush=True)
        final = "".join(content_parts) or last_content

        iterations = total_tok = total_prompt = total_cached = total_comp = 0
        total_lat = 0.0; model_calls = 0
        for e in raw_events:
            if e.get("phase") == "model.completed":
                model_calls += 1
                u = (e.get("payload") or {}).get("usage") or {}
                total_tok += u.get("total_tokens", 0) or 0
                total_prompt += u.get("prompt_tokens", 0) or 0
                total_cached += u.get("cached_tokens", 0) or 0
                total_comp += u.get("completion_tokens", 0) or 0
                total_lat += e.get("payload",{}).get("latency_ms", 0) or 0
                iterations = e.get("payload",{}).get("iteration", iterations)
        cache_rate = round(total_cached / total_prompt * 100, 1) if total_prompt > 0 else 0

        (log_dir / f"{sid}.json").write_text(json.dumps({
            "sample_id": sid, "session_id": session.id,
            "prompt": sample["prompt"], "answer": sample["answer"],
            "topic": sample.get("topic",""), "language": sample.get("language",""),
            "prediction": final.strip(), "prediction_length": len(final.strip()),
            "thinking": "".join(thinking_parts), "thinking_length": sum(len(t) for t in thinking_parts),
            "iterations": iterations, "model_calls": model_calls,
            "total_tokens": total_tok, "total_prompt_tokens": total_prompt,
            "total_cached_tokens": total_cached, "total_completion_tokens": total_comp,
            "cache_rate": cache_rate, "total_latency_ms": round(total_lat, 1),
            "total_events": len(raw_events), "events": raw_events,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        tool_names = [e.get("name","") for e in raw_events if e.get("type") == "tool_call"]
        return {
            "sample_id": sid, "session_id": session.id,
            "prompt": sample["prompt"], "answer": sample["answer"],
            "prediction": final.strip(), "prediction_length": len(final.strip()),
            "topic": sample.get("topic",""),
            "iterations": iterations, "model_calls": model_calls,
            "total_tokens": total_tok, "cache_rate": cache_rate,
            "total_latency_ms": round(total_lat, 1), "total_events": len(raw_events),
            "tool_names": tool_names, "tool_count": len(tool_names),
        }

async def main():
    with open(POOL) as f:
        all_samples = {s["sample_id"]: s for s in [json.loads(l) for l in f if l.strip()]}
    samples = [all_samples[sid] for sid in SAMPLE_IDS if sid in all_samples]
    print(f"Running {len(samples)} failed samples (sem={SEM})...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "trajectories").mkdir(exist_ok=True)

    osys = AgentOS(data_dir="./data"); await osys.start()
    try:
        sem = asyncio.Semaphore(SEM)
        tasks = [run_one(osys, s, OUTPUT_DIR/"trajectories", sem) for s in samples]
        predictions = await asyncio.gather(*tasks)
    finally:
        await osys.stop()

    with open(OUTPUT_DIR / "predictions.jsonl", "w") as f:
        for p in predictions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"\n评估...")
    results = []
    for p in predictions:
        np = _normalize(p["prediction"]); na = _normalize(p["answer"])
        passed = na in np or np == na
        results.append({**p, "passed": passed, "normalized_prediction": np, "normalized_answer": na})
        status = "PASS" if passed else "FAIL"
        print(f"  {p['sample_id']} {status}  exp={na[:50]}  got={np[:80]}")

    passed = sum(1 for r in results if r["passed"]); total = len(results)
    avg_tok = sum(r.get("total_tokens",0) for r in results) / max(total,1)
    avg_cache = sum(r.get("cache_rate",0) for r in results) / max(total,1)
    avg_lat = sum(r.get("total_latency_ms",0) for r in results) / max(total,1)

    with open(OUTPUT_DIR / "results.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {"total": total, "passed": passed, "accuracy": round(passed/total, 4),
               "avg_total_tokens": round(avg_tok,0), "avg_cache_rate": round(avg_cache,1),
               "avg_latency_ms": round(avg_lat,0)}
    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n结果: {passed}/{total} = {summary['accuracy']*100:.1f}%")
    print(f"平均 tokens: {avg_tok:.0f} | KV cache: {avg_cache:.1f}% | 延迟: {avg_lat:.0f}ms")
    print(f"输出: {OUTPUT_DIR}")

if __name__ == "__main__":
    asyncio.run(main())
