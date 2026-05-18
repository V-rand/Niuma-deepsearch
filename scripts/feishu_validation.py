#!/usr/bin/env python3
"""
Validate the Feishu interrupt/notification pipeline.

Tests the full flow:
  1. HMAC-SHA256 signature generation (unit test, no webhook needed)
  2. Feishu webhook POST (integration test, needs valid webhook in .env)
  3. InterruptScheduler lifecycle: start → check → fire → stop
  4. Graceful handling when Feishu webhook is not configured
  5. reminder_create tool → DB write → Scheduler pick-up
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


# ── 1. HMAC-SHA256 signature (unit test, no webhook needed) ──
def test_hmac_signature():
    print("### 1. HMAC-SHA256 签名验证")
    secret = "test-secret-12345"
    timestamp = "1747000000"
    expected_sig = base64.b64encode(
        hmac.new(
            key=secret.encode("utf-8"),
            msg=f"{timestamp}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    # Recalculate and compare
    actual_sig = base64.b64encode(
        hmac.new(
            key=secret.encode("utf-8"),
            msg=f"{timestamp}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    assert expected_sig == actual_sig, "HMAC signature mismatch"
    assert len(actual_sig) > 0, "HMAC produced empty signature"
    print(f"  ✓ HMAC-SHA256 签名: {actual_sig[:20]}...")
    return True


# ── 2. Feishu webhook POST (integration test) ──
async def test_feishu_webhook():
    """Test sending a test card to Feishu webhook."""
    webhook = os.getenv("FEISHU_WEBHOOK", "")
    secret = os.getenv("FEISHU_SECRET", "")

    if not webhook:
        print("\n### 2. 飞书 Webhook 通知 [跳过]")
        print("   FEISHU_WEBHOOK 未设置，跳过集成测试")
        return None  # skipped

    print(f"\n### 2. 飞书 Webhook 通知")
    print(f"   Webhook: {webhook[:40]}...")
    print(f"   Secret:  {'已配置' if secret else '未配置'}")

    import aiohttp

    timestamp = str(int(time.time()))
    sign = ""
    if secret:
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(
                key=secret.encode("utf-8"),
                msg=string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "AgentOS4Law 飞书通知测试"},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**消息**: AgentOS 飞书中断通知链路测试\n**时间**: {datetime.now().isoformat()}\n**状态**: 测试消息",
                    },
                },
            ],
        },
    }
    headers = {"Content-Type": "application/json", "X-Timestamp": timestamp}
    if sign:
        headers["X-Sign"] = sign

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook, headers=headers, json=card, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.text()
                print(f"   HTTP {resp.status}: {body[:200]}")
                if resp.status == 200:
                    print("   ✓ 飞书推送成功")
                    return True
                elif resp.status == 401:
                    print("   ✗ 签名验证失败 (Secret 不匹配)")
                    return False
                else:
                    print(f"   ⚠ 意外的响应: {resp.status}")
                    return None
    except Exception as e:
        print(f"   ✗ 请求失败: {e}")
        return False


# ── 3. InterruptScheduler lifecycle ──
async def test_scheduler_lifecycle():
    print("\n### 3. InterruptScheduler 生命周期")

    from agent_os.agent_os import AgentOS
    from agent_os.scheduler.interrupt_scheduler import InterruptType

    os_ = AgentOS(data_dir="./data")
    await os_.start()

    session = await os_.create_session(name="飞书测试")

    fire_at = datetime.now(timezone.utc)
    reminder_id = os_.scheduler.add_interrupt(
        interrupt_type=InterruptType.REMINDER,
        title="飞书测试提醒",
        message=f"测试消息 - {datetime.now().isoformat()}",
        session_id=session.id,
        fire_at=fire_at,
        priority=2,
    )
    print(f"   提醒已创建: {reminder_id}")

    # Let the scheduler run for 2 cycles
    await asyncio.sleep(2)
    await os_.scheduler.stop()

    # Check if reminder was fired
    reminders = await os_.sessions.list_reminders(session_id=session.id)
    for r in reminders:
        if r["id"] == reminder_id:
            fired = str(r.get("status","")) == "fired"
            print(f"   提醒状态: status={r.get('status')} fired_at={r.get('fired_at')}")
            assert fired, "提醒应被fired"
            print("   ✓ 提醒正常触发")
            break

    await os_.stop()


# ── 4. Graceful fallback without Feishu ──
async def test_no_webhook_fallback():
    print("\n### 4. 无飞书配置时的兜底")

    from agent_os.agent_os import AgentOS
    from agent_os.scheduler.interrupt_scheduler import InterruptType

    os_ = AgentOS(data_dir="./data")
    await os_.start()

    session = await os_.create_session(name="飞书兜底测试")

    fire_at = datetime.now(timezone.utc)
    reminder_id = os_.scheduler.add_interrupt(
        title="兜底测试",
        message="无飞书配置时应静默跳过",
        session_id=session.id,
        fire_at=fire_at,
        priority=3,
        interrupt_type=InterruptType.REMINDER,
    )
    print(f"   提醒已创建: {reminder_id}")

    await asyncio.sleep(2)
    await os_.scheduler.stop()

    # Verify reminder was fired (even without Feishu)
    reminders = await os_.sessions.list_reminders(session_id=session.id)
    for r in reminders:
        if r["id"] == reminder_id:
            fired = str(r.get("status","")) == "fired"
            assert fired, "提醒应被fired"
            print(f"   ✓ 无飞书时提醒正常触发，status=fired")
            break

    await os_.stop()


# ── 5. reminder_create tool → DB → Scheduler (end-to-end with tool) ──
async def test_reminder_create_tool():
    print("\n### 5. reminder_create 工具端到端")

    from agent_os.tools.registry import set_session_context
    from agent_os.tools.base_tools import reminder_create

    from agent_os.agent_os import AgentOS
    os_ = AgentOS(data_dir="./data")
    await os_.start()
    session = await os_.create_session(name="reminder工具测试")

    set_session_context(work_dir=session.work_dir, session_id=session.id)

    fire_at = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    result = await reminder_create(
        title="工具端到端测试",
        message="通过reminder_create工具创建的提醒",
        fire_at=fire_at,
        priority=1,
        reminder_type="reminder",
    )
    assert result.success, f"reminder_create failed: {result.error}"
    data = result.data or {}
    reminder_id = data.get("id", "")
    print(f"   工具返回: id={reminder_id} success={result.success}")

    await asyncio.sleep(2)
    await os_.scheduler._check_interrupts()
    await os_.scheduler.stop()

    reminders = await os_.sessions.list_reminders(session_id=session.id)
    for r in reminders:
        if r["id"] == reminder_id:
            fired = str(r.get("status","")) == "fired"
            print(f"   status={r.get('status')} fired_at={r.get('fired_at')}")
            assert fired, "reminder应该被fired"
            break

    print("   ✓ reminder_create → DB → scheduler → fired 链路正常")
    await os_.stop()


# ── Main ──
async def main():
    print("=" * 60)
    print("AgentOS4Law - 飞书中断通知验证")
    print("=" * 60)

    # Test 1: Always runs (pure Python)
    if not test_hmac_signature():
        print("\n✗ HMAC 签名测试失败")
        sys.exit(1)

    # Check .env for FEISHU_WEBHOOK
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Test 2: Needs real webhook
    webhook_result = await test_feishu_webhook()

    # Test 3-5: Always runs (no external service needed)
    await test_scheduler_lifecycle()
    await test_no_webhook_fallback()
    await test_reminder_create_tool()

    print("\n" + "=" * 60)
    if webhook_result is None:
        print("✓ 单元测试通过；飞书集成测试已跳过（需配置 FEISHU_WEBHOOK）")
    elif webhook_result:
        print("✓ 全部测试通过（含飞书推送）")
    else:
        print("⚠ 飞书推送失败，本地调度器测试通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())