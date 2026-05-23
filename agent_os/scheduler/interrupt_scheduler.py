"""
Persistent reminder and interrupt scheduler.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..core.event_bus import EventBus, EventType, get_event_bus
from ..core.session import SessionManager, format_local_timestamp


logger = logging.getLogger(__name__)


class InterruptType(Enum):
    TIME_EVENT = "time_event"
    EXTERNAL_EVENT = "external_event"
    REMINDER = "reminder"
    DEADLINE = "deadline"
    FOLLOW_UP = "follow_up"


@dataclass
class Interrupt:
    id: str
    type: InterruptType
    title: str
    message: str
    session_id: str
    fire_at: datetime
    priority: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fired: bool = False
    fired_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "session_id": self.session_id,
            "fire_at": self.fire_at.isoformat(),
            "priority": self.priority,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "fired": self.fired,
            "fired_at": self.fired_at.isoformat() if self.fired_at else None,
        }


class InterruptScheduler:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        feishu_webhook: str | None = None,
        feishu_secret: str | None = None,
        event_bus: EventBus | None = None,
        check_interval: int = 30,
    ):
        self.session_manager = session_manager
        self.feishu_webhook = feishu_webhook
        self.feishu_secret = feishu_secret
        self.event_bus = event_bus or get_event_bus()
        self.check_interval = check_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_interrupts()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("InterruptScheduler loop failed: %s", exc)
                await asyncio.sleep(self.check_interval)

    async def _check_interrupts(self) -> None:
        reminders = await self.session_manager.list_reminders(status="pending")
        now = datetime.now(timezone.utc)
        for reminder in reminders:
            fire_at = datetime.fromisoformat(reminder["fire_at"].replace("Z", "+00:00"))
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=timezone.utc)
            if fire_at <= now:
                await self._fire_interrupt(
                    Interrupt(
                        id=reminder["id"],
                        type=InterruptType(reminder["type"]),
                        title=reminder["title"],
                        message=reminder["message"],
                        session_id=reminder["session_id"],
                        fire_at=fire_at,
                        priority=int(reminder["priority"]),
                        metadata=reminder.get("metadata", {}) or {},
                    )
                )

    async def _fire_interrupt(self, interrupt: Interrupt) -> None:
        interrupt.fired = True
        interrupt.fired_at = datetime.now(timezone.utc)
        self.session_manager.mark_reminder_fired(
            interrupt.id,
            interrupt.fired_at.isoformat().replace("+00:00", "Z"),
        )
        await self.event_bus.publish_typed(
            EventType.INTERRUPT_FIRED,
            payload=interrupt.to_dict(),
            session_id=interrupt.session_id,
        )
        if self.feishu_webhook:
            await self._send_feishu_notification(interrupt)

    async def _send_feishu_notification(self, interrupt: Interrupt) -> None:
        try:
            import aiohttp

            timestamp = str(int(time.time()))
            sign = ""
            if self.feishu_secret:
                string_to_sign = f"{timestamp}\n{self.feishu_secret}"
                sign = base64.b64encode(
                    hmac.new(
                        key=self.feishu_secret.encode("utf-8"),
                        msg=string_to_sign.encode("utf-8"),
                        digestmod=hashlib.sha256,
                    ).digest()
                ).decode("utf-8")
            card = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": interrupt.title},
                        "template": {1: "red", 2: "orange", 3: "blue"}.get(interrupt.priority, "blue"),
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"**会话**: {interrupt.session_id}\n**触发时间**: {format_local_timestamp(interrupt.fire_at.isoformat())}",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": interrupt.message},
                        },
                    ],
                },
            }
            headers = {"Content-Type": "application/json", "X-Timestamp": timestamp}
            if sign:
                headers["X-Sign"] = sign
            async with aiohttp.ClientSession() as session:
                await session.post(self.feishu_webhook, headers=headers, json=card)
        except Exception:
            logger.exception("Failed to send Feishu notification for interrupt %s", interrupt.id)

    def add_interrupt(
        self,
        *,
        interrupt_type: InterruptType,
        title: str,
        message: str,
        session_id: str,
        fire_at: datetime,
        priority: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.session_manager.create_reminder(
            session_id=session_id,
            reminder_type=interrupt_type.value,
            title=title,
            message=message,
            fire_at=fire_at.isoformat(),
            priority=priority,
            metadata=metadata,
        )

    async def list_interrupts(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return await self.session_manager.list_reminders(session_id=session_id)
