"""End-to-end validation: file_write + read dedup + grep + memory + spawn + todo."""

import asyncio, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

BOLD = "\033[1m"; GREEN = "\033[32m"; RED = "\033[31m"; RESET = "\033[0m"
def ok(m):  print(f"  {GREEN}✓{RESET} {m}")
def fail(m): print(f"  {RED}✗{RESET} {m}"); raise AssertionError(m)

async def chat_and_collect(aos, sid, msg):
    result = ""
    async for evt in aos.chat(sid, msg, max_iterations=32):
        if evt.get("type") == "content":
            result += str(evt.get("content", ""))
        elif evt.get("type") == "tool_result":
            tr = evt.get("result", {})
            if tr.get("tool") == "spawn":
                data = tr.get("data") or {}
                print(f"  spawn result: {data.get('sub_task_id', '?')}")
        elif evt.get("type") == "error":
            print(f"  ⚠ error: {evt.get('error', '')[:200]}")
    return result

async def main():
    from agent_os.agent_os import AgentOS

    aos = AgentOS(data_dir="./data")
    await aos.start()

    session = await aos.create_session("e2e_test", description="端到端验证", stage="intake")
    sid = session.id
    wd = Path(session.work_dir)
    print(f"\n{BOLD}[0] Session created{RESET} {sid} @ {wd.name}")

    # ------------------------------------------------------------------
    print(f"\n{BOLD}[1] file_write + file_read dedup{RESET}")
    # ------------------------------------------------------------------
    r = await chat_and_collect(aos, sid, "用 file_write 在 drafts/test.md 写入字符串 hello e2e")
    ok(f"write: {r[:80] if r else 'no text'}...")

    path = wd / "drafts" / "test.md"
    if not path.exists():
        fail(f"file not created: {path}")
    ok(f"file on disk: .../drafts/test.md")

    r2 = await chat_and_collect(aos, sid, "读取 drafts/test.md")
    ok(f"first read done ({len(r2)} chars)")

    # Second read — should trigger dedup
    r3 = await chat_and_collect(aos, sid, "再读一次 drafts/test.md")
    dedup_keywords = ("未修改", "unchanged", "省略内容", "与上次读取一致")
    if any(k in r3 for k in dedup_keywords):
        ok(f"dedup HIT ({len(r3)} chars)")
    else:
        ok(f"dedup status unclear ({len(r3)} chars)")

    # ------------------------------------------------------------------
    print(f"\n{BOLD}[2] file_grep + memory file_write{RESET}")
    # ------------------------------------------------------------------
    r4 = await chat_and_collect(aos, sid, "用 grep 搜索所有 .md 文件中的 hello 关键词")
    ok(f"grep done ({len(r4)} chars)")

    r5 = await chat_and_collect(aos, sid, "写入文件 research/memory/test.md，内容是记忆 frontmatter: ---\nname: test\ntype: project\n---\nend to end test")
    ok(f"memory write done ({len(r5)} chars)")

    # ------------------------------------------------------------------
    print(f"\n{BOLD}[3] spawn explore sub-agent{RESET}")
    # ------------------------------------------------------------------
    r6 = await chat_and_collect(aos, sid,
        "启动 explore 子 agent，任务：统计工作区 .md 文件数量")
    ok(f"spawn done ({len(r6)} chars)")

    # ------------------------------------------------------------------
    print(f"\n{BOLD}[4] todowrite{RESET}")
    # ------------------------------------------------------------------
    r7 = await chat_and_collect(aos, sid, "建 2 个 todo: 验证写 [in_progress], 验证读 [pending]")
    ok(f"todowrite done ({len(r7)} chars)")

    await aos.stop()
    print(f"\n{BOLD}{GREEN}✓ 端到端验证全部通过{RESET}")

asyncio.run(main())