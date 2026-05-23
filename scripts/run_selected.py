"""
Run selected BrowseComp-ZH samples concurrently and save full trajectories as JSON.
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_os import AgentOS

POOL = Path("/home/xiemingjie/dev/benchmark/data/browsecomp_zh/pool.jsonl")
SAMPLE_IDS = ["bc_zh_001", "bc_zh_017", "bc_zh_022", "bc_zh_015"]

def load_samples():
    with open(POOL) as f:
        all_samples = [json.loads(l) for l in f if l.strip()]
    return {s["sample_id"]: s for s in all_samples}

async def run_one(osys, sample, log_dir, semaphore):
    async with semaphore:
        sid = sample["sample_id"]
        session = await osys.create_session(name=f"eval_{sid}")
        final = ""
        thinking_parts = []
        raw_events = []
        total_tok = 0
        total_lat = 0.0

        async for event in osys.chat(session.id, sample["prompt"], max_iterations=32):
            raw_events.append(event)
            t = event.get("type", "")
            p = event.get("phase", "")
            if t == "content_stream":
                final += event.get("content", "")
            elif t == "thinking_stream":
                thinking_parts.append(event.get("content", ""))
            elif p == "model.completed":
                u = event.get("payload", {}).get("usage", {})
                total_tok += u.get("total_tokens", 0)
                total_lat += event.get("payload", {}).get("latency_ms", 0)

        thinking = "".join(thinking_parts)
        traj = {
            "sample_id": sid, "session_id": session.id,
            "prompt": sample["prompt"], "answer": sample["answer"],
            "topic": sample.get("topic", ""), "language": sample.get("language", ""),
            "prediction": final.strip(), "prediction_length": len(final.strip()),
            "thinking": thinking, "thinking_length": len(thinking),
            "total_tokens": total_tok, "total_latency_ms": round(total_lat, 1),
            "iterations": sum(1 for e in raw_events if e.get("phase") == "model.completed"),
            "events": raw_events,
        }
        (log_dir / f"{sid}.json").write_text(
            json.dumps(traj, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        passed = sample["answer"] in final.strip() or sample["answer"].replace(" ", "") in final.strip().replace(" ", "")
        status = "PASS" if passed else "FAIL"
        print(f"  [{sid}] {status}  tok={total_tok}  lat={total_lat/1000:.1f}s", flush=True)
        return {"sample_id": sid, "passed": passed}

async def main():
    samples_dict = load_samples()
    samples = [samples_dict[sid] for sid in SAMPLE_IDS if sid in samples_dict]
    print(f"Running {len(samples)} samples concurrently (semaphore=4)...")

    LOG_DIR = Path(__file__).resolve().parent.parent / "outputs" / f"selected_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    osys = AgentOS()
    semaphore = asyncio.Semaphore(4)
    results = await asyncio.gather(*[run_one(osys, s, LOG_DIR, semaphore) for s in samples])

    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'='*40}")
    print(f"Results: {passed}/{len(results)} passed")
    for r in results:
        print(f"  {r['sample_id']}: {'PASS' if r['passed'] else 'FAIL'}")
    print(f"Trajectories: {LOG_DIR}")

if __name__ == "__main__":
    asyncio.run(main())
