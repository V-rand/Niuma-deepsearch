"""
BrowseComp-ZH 评测脚本 — 在 AgentOS 项目中直接运行。
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from agent_os import AgentOS

POOL_PATH = Path("/home/xiemingjie/dev/benchmark/data/browsecomp_zh/pool.jsonl")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / f"browsecomp_zh_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
SEMAPHORE = 5


def _safe_serialize(obj):
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return str(obj)[:500]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)[:1000]


async def run_one(osys: AgentOS, sample: dict, log_dir: Path) -> dict:
    session = await osys.create_session(name=f"eval_{sample['sample_id']}")
    content_parts = []
    last_content = ""
    raw_events: list[dict] = []
    thinking_parts: list[str] = []

    async for event in osys.chat(session.id, sample["prompt"], max_iterations=32):
        clean = _safe_serialize(event)
        raw_events.append(clean)
        evt_type = event.get("type", "")
        if evt_type == "thinking_stream":
            pass  # thin stream consumed by TUI; full text in model.completed
        elif evt_type == "content":
            last_content = event.get("content", "")
        elif evt_type == "content_stream":
            content_parts.append(event.get("content", ""))
        elif event.get("phase") == "model.completed":
            rt = (event.get("payload") or {}).get("reasoning_text", "")
            if rt:
                thinking_parts.append(rt)

    final = "".join(content_parts) or last_content

    iterations = 0
    total_tokens = total_prompt_tokens = total_cached_tokens = total_completion_tokens = 0
    total_latency_ms = 0.0
    model_calls = 0
    for e in raw_events:
        if e.get("phase") == "model.completed":
            model_calls += 1
            u = (e.get("payload") or {}).get("usage") or {}
            total_tokens += u.get("total_tokens", 0) or 0
            total_prompt_tokens += u.get("prompt_tokens", 0) or 0
            total_cached_tokens += u.get("cached_tokens", 0) or 0
            total_completion_tokens += u.get("completion_tokens", 0) or 0
            total_latency_ms += e.get("payload", {}).get("latency_ms", 0) or 0
            iterations = e.get("payload", {}).get("iteration", iterations)
    cache_rate = round(total_cached_tokens / total_prompt_tokens * 100, 1) if total_prompt_tokens > 0 else 0

    log_path = log_dir / f"{sample['sample_id']}.json"
    log_path.write_text(json.dumps({
        "sample_id": sample["sample_id"], "session_id": session.id,
        "prompt": sample["prompt"], "answer": sample["answer"],
        "topic": sample.get("topic", ""), "language": sample.get("language", ""),
        "prediction": final.strip(), "prediction_length": len(final.strip()),
        "thinking": "".join(thinking_parts), "thinking_length": sum(len(t) for t in thinking_parts),
        "iterations": iterations, "model_calls": model_calls,
        "total_tokens": total_tokens, "total_prompt_tokens": total_prompt_tokens,
        "total_cached_tokens": total_cached_tokens, "total_completion_tokens": total_completion_tokens,
        "cache_rate": cache_rate, "total_latency_ms": round(total_latency_ms, 1),
        "total_events": len(raw_events), "events": raw_events,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    tool_names = []
    for e in raw_events:
        if e.get("type") == "tool_call":
            tool_names.append(e.get("name", ""))
    return {
        "sample_id": sample["sample_id"], "session_id": session.id,
        "prompt": sample["prompt"], "answer": sample["answer"],
        "prediction": final.strip(), "prediction_length": len(final.strip()),
        "topic": sample.get("topic", ""),
        "iterations": iterations, "model_calls": model_calls,
        "total_tokens": total_tokens, "cache_rate": cache_rate,
        "total_latency_ms": round(total_latency_ms, 1), "total_events": len(raw_events),
        "tool_names": tool_names, "tool_count": len(tool_names),
    }


async def run_all(osys: AgentOS, samples: list[dict], log_dir: Path) -> list[dict]:
    sem = asyncio.Semaphore(SEMAPHORE)

    async def bounded(sample):
        async with sem:
            print(f"  [{sample['sample_id']}] 开始...", flush=True)
            try:
                result = await run_one(osys, sample, log_dir)
            except Exception as e:
                import traceback
                result = {
                    "sample_id": sample["sample_id"], "session_id": "ERROR",
                    "prompt": sample["prompt"], "answer": sample["answer"],
                    "prediction": "", "prediction_length": 0,
                    "error": str(e), "traceback": traceback.format_exc(),
                    "topic": sample.get("topic", ""),
                    "iterations": 0, "model_calls": 0, "total_tokens": 0,
                    "cache_rate": 0, "total_latency_ms": 0,
                    "tool_names": [], "tool_count": 0, "total_events": 0,
                }
            print(f"  [{sample['sample_id']}] 完成 (tokens={result.get('total_tokens', 0)}, cache={result.get('cache_rate', 0)}%, tools={result.get('tool_count', 0)})", flush=True)
            return result

    return await asyncio.gather(*[bounded(s) for s in samples])


def _normalize(text: str) -> str:
    """Unicode 归一化 + 标点统一 + 去空格 + 小写 + 日期归一，减少假阴性。"""
    import unicodedata, re
    # 统一常见标点到 ASCII 对应
    replacements = {
        "·": ".", "：": ":", "，": ",", "；": ";", "（": "(", "）": ")",
        "　": " ", "”": '"', "“": '"', "‘": "'", "’": "'",
        "—": "-", "–": "-", "…": "...", "、": ",",
    }
    t = unicodedata.normalize("NFKC", text)
    for full, half in replacements.items():
        t = t.replace(full, half)
    # 日期归一: "1946年5月" → "1946.5", "1946年5月1日" → "1946.5.1"
    t = re.sub(r"(\d+)年(\d+)月(\d+)日", r"\1.\2.\3", t)
    t = re.sub(r"(\d+)年(\d+)月", r"\1.\2", t)
    t = re.sub(r"(\d+)年", r"\1", t)  # 纯 "X年" 保留数字
    t = re.sub(r"\s+", "", t)
    return t.lower()


def evaluate_results(predictions: list[dict]) -> list[dict]:
    results = []
    for pred in predictions:
        p = _normalize(pred["prediction"])
        e = _normalize(pred["answer"])
        passed = e in p or p == e
        results.append({**pred, "passed": passed, "normalized_prediction": p, "normalized_answer": e})
        status = "PASS" if passed else "FAIL"
        print(f"  {pred['sample_id']} {status}  expected={e[:50]}  got={p[:80]}", flush=True)
    return results


def main():
    with open(POOL_PATH) as f:
        samples = [json.loads(l) for l in f if l.strip()]
    print(f"加载 {len(samples)} 个 BrowseComp-ZH 样本", flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR = OUTPUT_DIR / "trajectories"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    async def _run():
        osys = AgentOS(data_dir="./data")
        await osys.start()
        try:
            predictions = await run_all(osys, samples, LOG_DIR)
        finally:
            await osys.stop()
        return predictions

    predictions = asyncio.run(_run())

    with open(OUTPUT_DIR / "predictions.jsonl", "w") as f:
        for p in predictions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"\n预测完成，开始评估...", flush=True)
    results = evaluate_results(predictions)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_tokens = sum(r.get("total_tokens", 0) for r in results) / max(total, 1)
    avg_cache = sum(r.get("cache_rate", 0) for r in results) / max(total, 1)
    avg_latency = sum(r.get("total_latency_ms", 0) for r in results) / max(total, 1)
    summary = {
        "total": total, "passed": passed, "accuracy": round(passed / total, 4),
        "avg_total_tokens": round(avg_tokens, 0), "avg_cache_rate": round(avg_cache, 1),
        "avg_latency_ms": round(avg_latency, 0),
    }
    print(f"\n结果: {passed}/{total} = {summary['accuracy']*100:.1f}%", flush=True)
    print(f"平均 tokens: {avg_tokens:.0f} | KV cache: {avg_cache:.1f}% | 延迟: {avg_latency:.0f}ms", flush=True)

    with open(OUTPUT_DIR / "results.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    topic_stats = {}
    for r in results:
        t = r.get("topic", "unknown")
        topic_stats.setdefault(t, {"total": 0, "passed": 0})
        topic_stats[t]["total"] += 1
        if r["passed"]:
            topic_stats[t]["passed"] += 1

    print("\n按领域:", flush=True)
    for t, s in sorted(topic_stats.items()):
        print(f"  {t}: {s['passed']}/{s['total']}", flush=True)
    print(f"\n输出目录: {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
