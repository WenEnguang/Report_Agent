"""
独立脚本：用 requests 直接调 GitHub REST API 拉取指定仓库、指定用户、指定时间范围内的commit。

学习重点：
    这一步先不接LangGraph，目的是把"数据源本身能不能正确工作"这件事
    和"图结构编排对不对"这件事分开验证——出问题时能一眼定位是哪一层的锅。

跑通标准：
    print出来的commit条数和你去GitHub网页上手动筛选看到的条数对得上，
    这一步才算真正跑通，不是"没报错"就算数。
"""

import os
import requests
from datetime import date, datetime
from typing import TypedDict, List, Optional
from dotenv import load_dotenv
from rich import print as rprint

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"


class CommitInfo(TypedDict):
    sha: str
    message: str
    date: str


def fetch_commits(
    owner: str,
    repo: str,
    author: str,
    since: date,
    until: date,
) -> List[CommitInfo]:
    """
    调 GET /repos/{owner}/{repo}/commits，按 author + since/until 过滤。

    GitHub API文档关键点（写代码前务必确认）：
        - since/until 参数要求 ISO 8601 格式，且是"提交时间"的范围过滤，
          不是"作者时间"，两者在rebase/cherry-pick场景下可能不同——
          Phase 1先不管这个边界情况，按提交时间即可。
        - author 参数按GitHub用户名过滤，不是邮箱。
        - 默认分页 per_page=30，超过要翻页（用响应header里的Link字段判断有没有下一页）。
          Phase 1先假设单月commit数不会超过30条，翻页逻辑留到健壮性迭代阶段。
    """
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN 未设置，检查 .env 文件是否正确加载")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {
        "author": author,
        "since": datetime.combine(since, datetime.min.time()).isoformat() + "Z",
        "until": datetime.combine(until, datetime.max.time()).isoformat() + "Z",
        "per_page": 100,  # Phase 1先设大一点规避翻页问题，超过100条再处理翻页
    }

    response = requests.get(url, headers=headers, params=params)

    # 先不做复杂的异常处理（Happy Path优先），但至少要能看清失败原因
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub API请求失败：status={response.status_code}, body={response.text}"
        )

    raw_commits = response.json()

    commits: List[CommitInfo] = []
    for i,item in enumerate(raw_commits):
        if i > 0:
            break
        rprint(f"item\n{item}")
        commits.append(
            CommitInfo(
                sha=item["sha"],
                message=item["commit"]["message"],
                date=item["commit"]["author"]["date"],
            )
        )
    return commits


if __name__ == "__main__":
    OWNER = "WenEnguang"
    REPO = "train_llm"        # 换成你要统计的具体仓库名
    AUTHOR = "WenEnguang"          # GitHub用户名，不是邮箱

    result = fetch_commits(
        owner=OWNER,
        repo=REPO,
        author=AUTHOR,
        since=date(2026, 8, 1),
        until=date(2026, 8, 19),
    )

    print(f"共拉取到 {len(result)} 条commit：\n")
    for c in result:
        print(f"- [{c['date']}] {c['sha'][:7]}  {c['message'].splitlines()[0]}")
