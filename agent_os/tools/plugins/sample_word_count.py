"""
Sample plugin — counts words in a string.
"""
from agent_os.tools.registry import ToolResult


async def handler(text: str = "", **kw) -> ToolResult:
    words = len(text.split()) if text.strip() else 0
    chars = len(text)
    return ToolResult.ok(data={"word_count": words, "char_count": chars})


def register(r):
    r.register(
        "word_count",
        "plugins",
        {
            "name": "word_count",
            "description": "统计文本的词数和字符数",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要统计的文本"},
                },
                "required": ["text"],
            },
        },
        handler,
    )
