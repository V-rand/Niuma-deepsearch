"""
文件系统 - Session 的文件操作
"""

import os
import re
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class FileNode:
    """文件/目录节点"""
    name: str
    path: str
    type: str  # "file" or "directory"
    size: int = 0
    lines: int = 0
    modified: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "lines": self.lines,
            "modified": self.modified,
        }


class FileSystem:
    """Session 文件系统"""

    def __init__(self, base_dir: str, read_only_prefixes: Optional[List[str]] = None):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.read_only_prefixes = tuple(
            prefix.strip("/").rstrip("/")
            for prefix in (read_only_prefixes or ["uploads"])
            if prefix.strip("/").rstrip("/")
        )

    def _resolve(self, path: str) -> Path:
        """解析路径，确保安全"""
        if path.startswith("/"):
            full_path = Path(path)
        else:
            full_path = self.base_dir / path
        
        full_path = full_path.resolve()
        
        # 安全检查：确保在 base_dir 内
        try:
            full_path.relative_to(self.base_dir)
        except ValueError:
            raise ValueError(f"Path traversal detected: {path}")
        
        return full_path

    def _is_read_only(self, full_path: Path) -> bool:
        relative = full_path.relative_to(self.base_dir).as_posix()
        return any(
            relative == prefix or relative.startswith(f"{prefix}/")
            for prefix in self.read_only_prefixes
        )

    def _ensure_writable(self, full_path: Path, path: str) -> None:
        if self._is_read_only(full_path):
            raise ValueError(f"Path is read-only: {path}")
    
    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """读取文件"""
        full_path = self._resolve(path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not full_path.is_file():
            raise ValueError(f"Not a file: {path}")
        return full_path.read_text(encoding=encoding)
    
    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> Path:
        """写入文件"""
        full_path = self._resolve(path)
        self._ensure_writable(full_path, path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding=encoding)
        return full_path
    
    def append_file(self, path: str, content: str, encoding: str = "utf-8") -> Path:
        """追加文件"""
        full_path = self._resolve(path)
        self._ensure_writable(full_path, path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "a", encoding=encoding) as f:
            f.write(content)
        return full_path

    def delete_file(self, path: str) -> Path:
        """删除文件"""
        full_path = self._resolve(path)
        self._ensure_writable(full_path, path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if full_path.is_dir():
            raise ValueError(f"Refusing to delete directory: {path}")
        full_path.unlink()
        return full_path
    
    def list_dir(self, path: str = ".") -> List[FileNode]:
        """列出目录"""
        full_path = self._resolve(path)
        if not full_path.is_dir():
            raise ValueError(f"Not a directory: {path}")
        
        nodes = []
        for item in full_path.iterdir():
            stat = item.stat()
            lines = 0
            if item.is_file() and stat.st_size > 0:
                try:
                    with open(item, "rb") as fh:
                        buf = fh.read(min(stat.st_size, 512 * 1024))
                        lines = buf.count(b"\n") + (0 if buf.endswith(b"\n") else 1)
                except OSError:
                    pass
            nodes.append(FileNode(
                name=item.name,
                path=str(item.relative_to(self.base_dir)),
                type="directory" if item.is_dir() else "file",
                size=stat.st_size if item.is_file() else 0,
                lines=lines,
                modified=str(stat.st_mtime),
            ))
        return sorted(nodes, key=lambda x: (x.type != "directory", x.name))
    
    def grep(self, pattern: str, path: str = ".", file_pattern: str = "*") -> List[Dict[str, Any]]:
        """搜索文件内容"""
        full_path = self._resolve(path)
        regex = re.compile(pattern)
        results = []
        
        for file_path in full_path.rglob(file_pattern):
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    for i, line in enumerate(content.split("\n"), 1):
                        if regex.search(line):
                            results.append({
                                "file": str(file_path.relative_to(self.base_dir)),
                                "line": i,
                                "content": line.strip(),
                            })
                except (OSError, UnicodeDecodeError, re.error) as exc:
                    logger.debug("Skipping file during grep: %s (%s)", file_path, exc)
        return results
    
    def get_tree(self, path: str = ".", max_depth: int = 10) -> Dict[str, Any]:
        """获取目录树"""
        full_path = self._resolve(path)
        
        def build_tree(p: Path, depth: int) -> Dict[str, Any]:
            result = {
                "name": p.name or str(p),
                "type": "directory" if p.is_dir() else "file",
            }
            if p.is_file():
                result["size"] = p.stat().st_size
            elif p.is_dir() and depth < max_depth:
                try:
                    result["children"] = [
                        build_tree(child, depth + 1) 
                        for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
                    ]
                except PermissionError:
                    logger.debug("Permission denied while building tree: %s", p)
            return result
        
        return build_tree(full_path, 0)
    
    def exists(self, path: str) -> bool:
        """检查路径是否存在"""
        try:
            return self._resolve(path).exists()
        except ValueError:
            return False

    def snapshot_uploads(self) -> dict[str, tuple[float, int]]:
        """Return {(relative_path): (mtime, size)} fingerprint for all uploads/ files."""
        uploads_dir = self.base_dir / "uploads"
        if not uploads_dir.exists():
            return {}
        snapshot: dict[str, tuple[float, int]] = {}
        for item in uploads_dir.iterdir():
            if item.is_file():
                stat = item.stat()
                rel = f"uploads/{item.name}"
                snapshot[rel] = (stat.st_mtime, stat.st_size)
        return snapshot
