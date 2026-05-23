from __future__ import annotations

from textual.widgets import Static

from tui.event_bridge import TuiRunMetrics


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #10243d;
        color: white;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self.session_name = "未选择工作区"
        self.model_name = "-"
        self.elapsed_s = 0
        self.metrics = TuiRunMetrics()

    def set_context(self, *, session_name: str, model_name: str) -> None:
        self.session_name = session_name or "未选择工作区"
        self.model_name = model_name or "-"
        self.render_status()

    def update_metrics(self, metrics: TuiRunMetrics, *, elapsed_s: int | None = None) -> None:
        self.metrics = metrics
        if elapsed_s is not None:
            self.elapsed_s = elapsed_s
        self.render_status()

    def render_status(self) -> None:
        m = self.metrics
        self.update(
            f"{self.session_name} · {self.model_name} · "
            f"tokens: {m.total_tokens:,} (缓存 {m.cache_rate}% / 压缩 {m.compression_pct}%) · "
            f"模型 {m.model_latency_ms_total / 1000:.1f}s · 工具 {m.tool_latency_ms_total / 1000:.1f}s · "
            f"耗时 {self.elapsed_s}s"
        )
