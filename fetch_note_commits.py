"""
独立脚本：扫描本地笔记目录，提取文件路径、最后修改时间、文件格式。

学习重点：
    这一步和 GitHub 拉取脚本一样，先不接 LangGraph，
    单独验证"本地文件系统扫描"这个数据源是否正常工作。

跑通标准：
    打印出来的文件列表和你在文件管理器里看到的一致，
    最后修改时间能正确反映你最近编辑的时间，
    扩展名分类（.md / .ipynb / .txt / 其他）正确。
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List
from rich import print as rprint
from rich.table import Table
from dotenv import load_dotenv

load_dotenv(override=True)

# 支持的笔记文件扩展名
SUPPORTED_EXTS = {".md", ".markdown", ".ipynb", ".txt", ".rst", ".org"}

# 要跳过的目录（虚拟环境、缓存、隐藏目录等）
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".obsidian", ".trash", ".idea"}


def _scan_notes_directory(base_dir: str) -> List[dict]:
    """
    递归扫描指定目录下的所有笔记文件。

    返回格式：
        [
            {
                "path": "/absolute/path/to/file.md",
                "last_modified": "2026-08-15 14:30:22",
                "last_modified_ts": 1755246622.0,   # 用于排序
                "ext": ".md",
                "size_bytes": 1234,
            },
            ...
        ]

    注意：
        - 用 os.walk 而不是 Path.rglob，方便在遍历时跳过目录
        - 跳过隐藏目录和虚拟环境
        - 按最后修改时间降序排列（最近编辑的排前面）
    """
    base_path = Path(base_dir).resolve()
    
    if not base_path.exists():
        raise RuntimeError(f"目录不存在: {base_dir}")
    if not base_path.is_dir():
        raise RuntimeError(f"路径不是目录: {base_dir}")

    results: List[dict] = []

    for root, dirs, files in os.walk(base_path):
        # 原地修改 dirs 列表，跳过不需要的目录
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for filename in files:
            file_path = Path(root) / filename
            ext = file_path.suffix.lower()

            # 只收集支持的笔记格式
            if ext not in SUPPORTED_EXTS:
                continue

            stat = file_path.stat()
            results.append({
                "path": str(file_path),
                "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "last_modified_ts": stat.st_mtime,
                "ext": ext,
                "size_bytes": stat.st_size,
            })

    # 按最后修改时间降序排列
    results.sort(key=lambda x: x["last_modified_ts"], reverse=True)
    return results


def scan_notes_in_time_range(
    base_dir: str,
    since: datetime,
    until: datetime,
) -> List[dict]:
    """
    扫描目录，但只返回在 [since, until] 时间范围内修改过的笔记文件。

    这是月报场景真正要用的函数——只关心"本月动过的笔记"。
    """
    all_notes = _scan_notes_directory(base_dir)
    return [
        note for note in all_notes
        if since.timestamp() <= note["last_modified_ts"] <= until.timestamp()
    ]


def print_notes_table(notes: List[dict]) -> None:
    """用 rich 表格打印扫描结果，方便肉眼核对。"""
    if not notes:
        rprint("[yellow]没有找到任何笔记文件[/yellow]")
        return

    table = Table(title=f"笔记文件扫描结果（共 {len(notes)} 个）")
    table.add_column("最后修改时间", style="cyan", width=20)
    table.add_column("格式", style="magenta", width=8)
    table.add_column("大小", style="green", width=10, justify="right")
    table.add_column("路径", style="white")

    for note in notes:
        size_kb = note["size_bytes"] / 1024
        table.add_row(
            note["last_modified"],
            note["ext"],
            f"{size_kb:.1f} KB",
            note["path"],
        )

    rprint(table)


if __name__ == "__main__":
    notes_dir = os.environ.get("NOTES_DIR")
    if not notes_dir:
        rprint("[yellow]提示：未设置 NOTES_DIR 环境变量，使用当前目录作为扫描目录[/yellow]")
        notes_dir = "."

    rprint(f"[bold]扫描目录:[/bold] {Path(notes_dir).resolve()}\n")

    since = datetime(2026, 8, 1, 0, 0, 0)
    until = datetime(2026, 8, 23, 23, 59, 59)
    
    rprint(f"\n[bold]时间范围筛选:[/bold] {since.strftime('%Y-%m-%d')} ~ {until.strftime('%Y-%m-%d')}")
    monthly_notes = scan_notes_in_time_range(notes_dir, since, until)
    print_notes_table(monthly_notes)