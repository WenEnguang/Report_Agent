"""
独立脚本：用本地 Git 仓库追踪笔记文件的本月变更内容。

核心逻辑：
    - 找到月初之前最后一个 commit 作为 baseline
    - 用 git diff baseline..HEAD 获取所有变更
    - 只提取"新增的行"（+ 开头），这些是你本月真正写的内容
    - 删除的行（- 开头）说明你修改/删掉了旧内容，对月报价值较低

跑通标准：
    打印出来的"本月新增内容"和你手动 git diff 看到的一致，
    并且不包含上个月写的内容。
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from rich import print as rprint
from rich.table import Table
from dotenv import load_dotenv

load_dotenv(override=True)


def run_git_command(repo_dir: str, args: List[str]) -> str:
    """在指定 Git 仓库目录下执行 git 命令，返回 stdout"""
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git 命令执行失败：git {' '.join(args)}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout


def get_commits_in_range(repo_dir: str, since: datetime, until: datetime) -> List[dict]:
    """
    获取指定时间范围内的所有 commit。

    返回格式：
        [
            {"hash": "a1b2c3d", "date": "2026-08-15 14:30:22", "message": "update transformer notes"},
            ...
        ]
    """
    output = run_git_command(
        repo_dir,
        [
            "log",
            f"--since={since.isoformat()}",
            f"--until={until.isoformat()}",
            "--pretty=format:%H|%ad|%s",
            "--date=format:%Y-%m-%d %H:%M:%S",
        ],
    )
    
    commits = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "hash": parts[0][:7],
                "full_hash": parts[0],
                "date": parts[1],
                "message": parts[2],
            })
    
    return commits

def find_baseline_commit(repo_dir: str, since: datetime) -> Optional[str]:
    """
    找到月初之前最后一个 commit 的 hash，作为 diff 的 baseline。

    例：如果 8月1日 之前最后一个 commit 是 7月28日 的，
    那 baseline 就是那个 commit，diff 出来的就是 7月28日之后的所有变更。
    """
    output = run_git_command(
        repo_dir,
        [
            "log",
            f"--before={since.isoformat()}",
            "--pretty=format:%H",
            "-n", "1",  # 只要最近的一个
        ],
    )
    hash_str = output.strip()
    return hash_str if hash_str else None


def get_monthly_diff(
    repo_dir: str,
    since: datetime,
    until: datetime,
    file_patterns: List[str] = ["*.md", "*.markdown", "*.txt"],
) -> dict:
    """
    获取本月所有笔记文件的变更内容。

    策略：
        1. 找 baseline（月初前最后一个 commit）
        2. 找 until 之前最后一个 commit 作为 end_point
        3. git diff baseline..end_point -- <patterns>
        4. 解析 diff 输出，按文件分组，只提取新增行（+ 开头）

    返回格式：
        {
            "file_path": {
                "added_lines": ["新增行1", "新增行2", ...],
                "deleted_lines": ["删除行1", ...],
                "added_count": 10,
                "deleted_count": 3,
            },
            ...
        }
    """
    # 1. baseline：月初之前的最后一个 commit
    baseline = find_baseline_commit(repo_dir, since)
    if not baseline:
        rprint("[yellow]警告：月初之前没有任何 commit，将从仓库第一个 commit 开始 diff[/yellow]")
        baseline = run_git_command(repo_dir, ["rev-list", "--max-parents=0", "HEAD"]).strip()
    
    # 2. end_point：until 之前最后一个 commit
    end_output = run_git_command(
        repo_dir,
        [
            "log",
            f"--before={until.isoformat()}",
            "--pretty=format:%H",
            "-n", "1",
        ],
    )
    end_point = end_output.strip() or "HEAD"
    
    rprint(f"[bold]Diff 范围:[/bold] {baseline[:7]}..{end_point[:7]}\n")
    
    # 3. 执行 git diff
    diff_args = ["diff", baseline, end_point, "--"]
    diff_args.extend(file_patterns)
    diff_output = run_git_command(repo_dir, diff_args)
    
    # 4. 解析 diff 输出
    return parse_diff_output(diff_output)
def get_monthly_diff_v2(
    repo_dir: str,
    since: datetime,
    until: datetime,
    file_patterns: List[str] = ["*.md", "*.markdown", "*.txt"],
) -> dict:
    """
    方案 B：先拿变更文件列表，再逐个文件 diff。
    避免解析复杂的 multi-file diff 输出。
    """
    baseline = find_baseline_commit(repo_dir, since)
    if not baseline:
        baseline = run_git_command(repo_dir, ["rev-list", "--max-parents=0", "HEAD"]).strip()
    
    end_output = run_git_command(
        repo_dir,
        ["log", f"--before={until.isoformat()}", "--pretty=format:%H", "-n", "1"],
    )
    end_point = end_output.strip() or "HEAD"
    
    # 第一步：拿到变更的文件列表
    name_args = ["diff", "--name-only", baseline, end_point, "--"]
    name_args.extend(file_patterns)
    changed_files = run_git_command(repo_dir, name_args).strip().split("\n")
    
    rprint(f"[bold]Diff 范围:[/bold] {baseline[:7]}..{end_point[:7]}")
    rprint(f"[bold]变更文件:[/bold] {len(changed_files)} 个\n")
    
    # 第二步：对每个文件单独 diff
    results = {}
    for file_path in changed_files:
        if not file_path.strip():
            continue
        
        diff_args = ["diff", baseline, end_point, "--", file_path]
        diff_output = run_git_command(repo_dir, diff_args)
        
        # 单文件 diff，解析起来简单得多
        added_lines = []
        deleted_lines = []
        
        for line in diff_output.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                deleted_lines.append(line[1:])
        
        if added_lines or deleted_lines:
            results[file_path] = {
                "added_lines": added_lines,
                "deleted_lines": deleted_lines,
                "added_count": len(added_lines),
                "deleted_count": len(deleted_lines),
            }
    
    return results

def parse_diff_output(diff_text: str) -> dict:
    """
    解析 git diff 输出，按文件分组提取新增/删除行。
    
    git diff 输出格式：
        diff --git a/file.md b/file.md
        index 1234567..abcdefg 100644
        --- a/file.md
        +++ b/file.md
        @@ -10,3 +10,5 @@
        -删除的行
        +新增的行
         未变动的行
    """
    results = {}
    current_file = None
    current_added = []
    current_deleted = []
    
    for line in diff_text.split("\n"):
        # 新文件开始
        if line.startswith("diff --git"):
            # 保存上一个文件的结果
            if current_file and (current_added or current_deleted):
                results[current_file] = {
                    "added_lines": current_added,
                    "deleted_lines": current_deleted,
                    "added_count": len(current_added),
                    "deleted_count": len(current_deleted),
                }
            # 解析新文件名
            # 格式: diff --git a/path/to/file.md b/path/to/file.md
            parts = line.split(" b/")
            current_file = parts[1] if len(parts) > 1 else line.split(" a/")[1]
            current_added = []
            current_deleted = []
        
        # 新增行（排除 +++ 文件头）
        elif line.startswith("+") and not line.startswith("+++"):
            current_added.append(line[1:])
        
        # 删除行（排除 --- 文件头）
        elif line.startswith("-") and not line.startswith("---"):
            current_deleted.append(line[1:])
    
    # 保存最后一个文件的结果
    if current_file and (current_added or current_deleted):
        results[current_file] = {
            "added_lines": current_added,
            "deleted_lines": current_deleted,
            "added_count": len(current_added),
            "deleted_count": len(current_deleted),
        }
    
    return results


def print_diff_summary(diff_results: dict) -> None:
    """打印 diff 结果摘要表格"""
    if not diff_results:
        rprint("[yellow]本月没有任何笔记文件变更[/yellow]")
        return
    
    table = Table(title=f"本月笔记变更摘要（共 {len(diff_results)} 个文件）")
    table.add_column("文件路径", style="cyan")
    table.add_column("新增行数", style="green", justify="right")
    table.add_column("删除行数", style="red", justify="right")
    table.add_column("新增内容预览", style="white", max_width=60)
    
    for file_path, info in diff_results.items():
        preview = info["added_lines"][0][:60] + "..." if info["added_lines"] else "(无新增)"
        table.add_row(
            file_path,
            f"+{info['added_count']}",
            f"-{info['deleted_count']}",
            preview,
        )
    
    rprint(table)


if __name__ == "__main__":
    # 你的笔记目录
    NOTES_DIR = os.environ.get("NOTES_DIR")
    if not NOTES_DIR:
        rprint("[yellow]提示：未设置 NOTES_DIR 环境变量，使用当前目录作为扫描目录[/yellow]")
        NOTES_DIR = "."
    
    # 月报时间范围
    since = datetime(2026, 8, 1, 0, 0, 0)
    until = datetime(2026, 8, 23, 23, 59, 59)
    
    rprint(f"[bold]笔记目录:[/bold] {NOTES_DIR}")
    rprint(f"[bold]时间范围:[/bold] {since.strftime('%Y-%m-%d')} ~ {until.strftime('%Y-%m-%d')}\n")
    
    # 第一步：看看本月有哪些 commit
    commits = get_commits_in_range(NOTES_DIR, since, until)
    rprint(f"[bold]本月 Git commits:[/bold] 共 {len(commits)} 个\n")
    for c in commits:
        rprint(f"  [{c['date']}] {c['hash']}  {c['message']}")
    
    # 第二步：获取本月的 diff
    rprint("\n" + "=" * 60 + "\n")
    diff_results = get_monthly_diff_v2(NOTES_DIR, since, until)
    print_diff_summary(diff_results)
    
    # 第三步：展示某个文件的具体新增内容
    rprint("\n" + "=" * 60)
    rprint("[bold]详细新增内容:[/bold]\n")
    for file_path, info in diff_results.items():
        rprint(f"[cyan]### {file_path}[/cyan]")
        for line in info["added_lines"][:20]:  # 每个文件最多展示前20行
            rprint(f"  [green]+ {line}[/green]")
        if info["added_count"] > 20:
            rprint(f"  [dim]... 其余 {info['added_count'] - 20} 行省略[/dim]")
        rprint("")