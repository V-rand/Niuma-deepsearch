"""
事件总线 - 组件间通信
"""

from typing import Dict, List, Callable, Awaitable, Any, Optional
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging


logger = logging.getLogger(__name__)


class EventType(Enum):
    """事件类型"""
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    INTERRUPT_FIRED = "interrupt.fired"
    INTERRUPT_HANDLED = "interrupt.handled"


@dataclass
class Event:
    """事件"""
    type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """事件总线 - 发布订阅模式"""

    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = {t: [] for t in EventType}
        self._all_handlers: List[EventHandler] = []

    def subscribe(self, event_type: EventType, handler: EventHandler):
        """订阅特定类型的事件"""
        self._handlers[event_type].append(handler)
    
    def subscribe_all(self, handler: EventHandler):
        """订阅所有事件"""
        self._all_handlers.append(handler)
    
    async def publish(self, event: Event):
        """发布事件"""
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Event handler failed for %s", event.type.value)

        for handler in self._all_handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Global event handler failed for %s", event.type.value)
    
    async def publish_typed(
        self,
        event_type: EventType,
        payload: Dict[str, Any] = None,
        session_id: Optional[str] = None,
    ):
        """便捷方法：创建并发布事件"""
        event = Event(
            type=event_type,
            payload=payload or {},
            session_id=session_id,
        )
        await self.publish(event)


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取事件总线实例（单例）"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
