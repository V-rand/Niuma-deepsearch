"""端到端全工具链路测试 — 直接调 API，绕开 CLI session 恢复问题。"""

import asyncio, sys, json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

BOLD = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; RESET = "\033[0m"

def log(tag, msg):
    print(f"  {tag} {msg}")

async def chat(aos, sid, msg, max_iter=48):
    """Run a chat and return (content, events_list)."""
    content = ""
    events = []
    async for evt in aos.chat(sid, msg, max_iterations=max_iter):
        events.append(evt)
        t = evt.get("type", "")
        if t == "content":
            content += str(evt.get("content", ""))
        elif t == "activity":
            p = evt.get("phase", "")
            d = evt.get("detail", "")
            if p in ("model.completed", "tool.completed", "run.completed", "run.failed"):
                log(f"{GREEN}✓{RESET}", f"{p}: {d[:120]}")
            elif p in ("model.requested", "tool.executing", "tools.planned"):
                log(f"{YELLOW}→{RESET}", f"{p}: {d[:80]}")
        elif t == "tool_call":
            log(f"{YELLOW}🔧{RESET}", f"{evt.get('name','')} {json.dumps(evt.get('arguments',{}))[:120]}")
        elif t == "error":
            log(f"{RED}✗{RESET}", f"error: {evt.get('error','')[:200]}")
    return content, events

async def main():
    from agent_os.agent_os import AgentOS

    aos = AgentOS(data_dir="./data")
    await aos.start()

    session = await aos.create_session("e2e_full", stage="intake")
    sid = session.id
    print(f"\n{BOLD}[0] Session{RESET} {sid}")

    # ---- 1. law_retrieve ----
    print(f"\n{BOLD}[1] law_retrieve{RESET}")
    c, _ = await chat(aos, sid, "检索民法典第563条关于合同法定解除的规定")
    log("", f"response: {c[:200]}...")

    # ---- 2. web_search ----
    print(f"\n{BOLD}[2] web_search{RESET}")
    c, _ = await chat(aos, sid, "搜索：民法典合同解除 司法解释 2024")
    log("", f"response: {c[:200]}...")

    # ---- 3. file_write ----
    print(f"\n{BOLD}[3] file_write{RESET}")
    c, _ = await chat(aos, sid, "把检索结果写入 drafts/analysis.md")
    log("", f"response: {c[:200]}...")

    # ---- 4. file_read (first read) ----
    print(f"\n{BOLD}[4] file_read (first){RESET}")
    c, _ = await chat(aos, sid, "读取 drafts/analysis.md")
    log("", f"response: {c[:200]}...")

    # ---- 5. file_read (dedup) ----
    print(f"\n{BOLD}[5] file_read (dedup){RESET}")
    c, _ = await chat(aos, sid, "再读一次 drafts/analysis.md")
    if "未修改" in c or "unchanged" in c or "省略" in c:
        log(f"{GREEN}✓{RESET}", f"dedup HIT: {c[:200]}...")
    else:
        log(f"{YELLOW}⚠{RESET}", f"dedup unclear: {c[:200]}...")

    # ---- 6. file_grep ----
    print(f"\n{BOLD}[6] file_grep{RESET}")
    c, _ = await chat(aos, sid, "搜索 drafts/ 目录下所有 .md 文件中的 合同 关键词")
    log("", f"response: {c[:200]}...")

    # ---- 7. spawn sub-agent ----
    print(f"\n{BOLD}[7] spawn explore{RESET}")
    c, _ = await chat(aos, sid, "启动 explore 子 agent，统计工作区 .md 文件数量")
    log("", f"response: {c[:200]}...")

    # ---- 8. todowrite ----
    print(f"\n{BOLD}[8] todowrite{RESET}")
    c, _ = await chat(aos, sid, "建 3 个 todo: 法条检索 [completed], 判例检索 [in_progress], 意见书 [pending]")
    log("", f"response: {c[:200]}...")

    # ---- 9. skill_use ----
    print(f"\n{BOLD}[9] skill_use{RESET}")
    c, _ = await chat(aos, sid, "加载 legal_case 技能")
    log("", f"response: {c[:200]}...")

    # ---- 10. workspace_search ----
    print(f"\n{BOLD}[10] workspace_search{RESET}")
    c, _ = await chat(aos, sid, "搜索工作区中所有包含 民法典 的文件")
    log("", f"response: {c[:200]}...")

    # ---- Summary ----
    print(f"\n{BOLD}{GREEN}✓ 全工具链路测试完成{RESET}")
    print(f"  Session: {sid}")
    print(f"  Work dir: {session.work_dir}")

    await aos.stop()

asyncio.run(main())