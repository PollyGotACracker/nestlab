"""GitHub API를 호출하고 지식 저장소(archive) 도구를 구성한다."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote

import aiohttp
from archive_workflow import BASE_BRANCH, ArchiveWorkflowStore
from archive_reader import ArchiveReader
from claude_agent_sdk import create_sdk_mcp_server, tool

from config import ArchiveRepositoryConfig, tool_text

GITHUB_API_BASE = "https://api.github.com"


# GitHub API 인증·형식 헤더를 만든다.
def github_headers(cfg: ArchiveRepositoryConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.token}", "Accept": "application/vnd.github+json"}


# 지식 저장소의 GitHub API 기본 URL을 만든다.
def github_repo_url(cfg: ArchiveRepositoryConfig) -> str:
    return f"{GITHUB_API_BASE}/repos/{cfg.owner}/{cfg.repo}"


# 지식 저장소의 지정 브랜치에서 파일 하나를 읽는다. 없으면 (None, None)을 반환한다.
async def github_get_file(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, path: str, branch: str | None = None
) -> tuple[str | None, str | None]:
    url = f"{github_repo_url(cfg)}/contents/{quote(path, safe='/')}"
    params = {"ref": branch} if branch else None
    async with session.get(url, headers=github_headers(cfg), params=params) as resp:
        if resp.status == 404:
            return None, None
        resp.raise_for_status()
        data = await resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]


# 지식 저장소의 작업 브랜치에 파일을 생성하거나 수정하는 커밋을 만든다.
async def github_put_file(
    session: aiohttp.ClientSession,
    cfg: ArchiveRepositoryConfig,
    path: str,
    content: str,
    message: str,
    sha: str | None,
    branch: str,
) -> None:
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha is not None:
        payload["sha"] = sha
    async with session.put(f"{github_repo_url(cfg)}/contents/{path}", headers=github_headers(cfg), json=payload) as resp:
        resp.raise_for_status()


# 지식 저장소의 작업 브랜치에서 파일을 삭제하는 커밋을 만든다. sha는 삭제할 파일의 blob SHA다.
async def github_delete_file(
    session: aiohttp.ClientSession,
    cfg: ArchiveRepositoryConfig,
    path: str,
    message: str,
    sha: str,
    branch: str,
) -> None:
    payload = {"message": message, "sha": sha, "branch": branch}
    async with session.delete(f"{github_repo_url(cfg)}/contents/{path}", headers=github_headers(cfg), json=payload) as resp:
        resp.raise_for_status()


# 브랜치가 가리키는 커밋 SHA를 읽는다. 브랜치가 없으면 None이다.
async def github_branch_sha(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, branch: str
) -> str | None:
    url = f"{github_repo_url(cfg)}/git/ref/heads/{quote(branch, safe='/')}"
    async with session.get(url, headers=github_headers(cfg)) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        data = await resp.json()
        return data["object"]["sha"]


# 지정 commit에서 새 브랜치를 만든다.
async def github_create_branch(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, branch: str, sha: str
) -> None:
    payload = {"ref": f"refs/heads/{branch}", "sha": sha}
    async with session.post(f"{github_repo_url(cfg)}/git/refs", headers=github_headers(cfg), json=payload) as resp:
        resp.raise_for_status()


# branch에서 base로 가는 열린 PR의 주소를 찾는다. 없으면 None이다.
async def github_find_open_pr(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, branch: str, base: str
) -> str | None:
    params = {"state": "open", "head": f"{cfg.owner}:{branch}", "base": base}
    async with session.get(f"{github_repo_url(cfg)}/pulls", headers=github_headers(cfg), params=params) as resp:
        resp.raise_for_status()
        pulls = await resp.json()
    return pulls[0]["html_url"] if pulls else None


# 작업 브랜치의 PR을 만들고 PR 주소를 반환한다.
async def github_create_pr(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, branch: str, base: str, title: str, body: str
) -> str:
    payload = {"title": title, "head": branch, "base": base, "body": body}
    async with session.post(f"{github_repo_url(cfg)}/pulls", headers=github_headers(cfg), json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return data["html_url"]


# 승인된 미리보기의 제목, 본문, label만 사용해 Issue를 한 번 생성한다.
async def github_create_issue(
    session: aiohttp.ClientSession,
    cfg: ArchiveRepositoryConfig,
    title: str,
    body: str,
    labels: list[str],
) -> tuple[int, str]:
    payload = {"title": title, "body": body, "labels": labels}
    async with session.post(f"{github_repo_url(cfg)}/issues", headers=github_headers(cfg), json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return int(data["number"]), data["html_url"]


# 승인된 변경 세트 전체를 Git tree 하나와 commit 하나로 적용한다.
# ref 갱신 전 기준 SHA와 파일 SHA가 달라지면 중단해 승인 뒤 생긴 변경을 덮지 않는다.
async def github_apply_change_set(
    session: aiohttp.ClientSession,
    cfg: ArchiveRepositoryConfig,
    branch: str,
    source_sha: str,
    message: str,
    operations: list[dict[str, Any]],
) -> str:
    if await github_branch_sha(session, cfg, branch) != source_sha:
        raise ValueError("작업 브랜치가 Stage 이후 변경되어 적용을 중단했습니다.")

    # blob을 만들기 전에 모든 대상 SHA를 대조해 충돌한 변경 세트가 일부라도 진행되지 않게 한다.
    for operation in operations:
        path = str(operation["path"])
        _, current_sha = await github_get_file(session, cfg, path, branch)
        if current_sha != operation.get("expected_sha"):
            raise ValueError(f"{path}의 SHA가 Stage 이후 변경되어 적용을 중단했습니다.")

    # 새 blob과 tree, commit을 만든 뒤 마지막에만 ref를 비강제 갱신한다.
    # ref 갱신 전 객체는 브랜치에 연결되지 않으므로 중간 실패가 작업 브랜치를 바꾸지 않는다.
    tree_entries = []
    for operation in operations:
        path = str(operation["path"])
        if operation["type"] == "delete":
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        blob_payload = {
            "content": base64.b64encode(str(operation["content"]).encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }
        async with session.post(
            f"{github_repo_url(cfg)}/git/blobs",
            headers=github_headers(cfg),
            json=blob_payload,
        ) as resp:
            resp.raise_for_status()
            blob = await resp.json()
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    async with session.get(f"{github_repo_url(cfg)}/git/commits/{source_sha}", headers=github_headers(cfg)) as resp:
        resp.raise_for_status()
        source_commit = await resp.json()
    tree_payload = {"base_tree": source_commit["tree"]["sha"], "tree": tree_entries}
    async with session.post(
        f"{github_repo_url(cfg)}/git/trees",
        headers=github_headers(cfg),
        json=tree_payload,
    ) as resp:
        resp.raise_for_status()
        tree = await resp.json()
    commit_payload = {"message": message, "tree": tree["sha"], "parents": [source_sha]}
    async with session.post(
        f"{github_repo_url(cfg)}/git/commits",
        headers=github_headers(cfg),
        json=commit_payload,
    ) as resp:
        resp.raise_for_status()
        commit = await resp.json()
    ref_payload = {"sha": commit["sha"], "force": False}
    ref_url = f"{github_repo_url(cfg)}/git/refs/heads/{quote(branch, safe='/')}"
    async with session.patch(ref_url, headers=github_headers(cfg), json=ref_payload) as resp:
        resp.raise_for_status()

    # ref 갱신 성공만 믿지 않고 commit과 승인된 파일 결과를 다시 읽어 완료를 판정한다.
    if await github_branch_sha(session, cfg, branch) != commit["sha"]:
        raise ValueError("커밋 후 브랜치 검증에 실패했습니다.")
    for operation in operations:
        content, _ = await github_get_file(session, cfg, str(operation["path"]), branch)
        if operation["type"] == "delete" and content is not None:
            raise ValueError(f"{operation['path']} 삭제 검증에 실패했습니다.")
        if operation["type"] != "delete" and content != str(operation["content"]):
            raise ValueError(f"{operation['path']} 내용 검증에 실패했습니다.")
    return commit["sha"]


# 작업 브랜치와 출발 브랜치 인자를 검사한다. 문제가 있으면 사유 문자열을 반환한다.
def check_archive_branches(branch: str, base: str) -> str | None:
    if not branch or not base:
        return "branch(작업 브랜치)와 base(출발 브랜치)는 사용자에게 물어본 이름으로 모두 넣어야 합니다."
    if branch == base:
        return "작업 브랜치는 출발 브랜치와 달라야 합니다. 출발 브랜치에 직접 커밋하지 않습니다."
    return None


# 작업 브랜치가 없으면 출발 브랜치의 최신 커밋에서 만든다. 문제가 있으면 사유 문자열을 반환한다.
async def ensure_archive_branch(
    session: aiohttp.ClientSession, cfg: ArchiveRepositoryConfig, branch: str, base: str
) -> str | None:
    base_sha = await github_branch_sha(session, cfg, base)
    if base_sha is None:
        return f"출발 브랜치 {base}가 저장소에 없습니다."
    if await github_branch_sha(session, cfg, branch) is None:
        await github_create_branch(session, cfg, branch, base_sha)
    return None


# GitHub 요청 오류를 도구 결과 문구로 바꾼다.
def github_error_text(exc: aiohttp.ClientResponseError) -> str:
    return f"GitHub 요청이 실패했습니다: {exc.status} {exc.message}"


# archive thread의 documenter에게 조회와 승인 미리보기 도구만 제공한다.
# Issue, commit, PR을 직접 만드는 도구는 노출하지 않아 승인 경계를 Python에 둔다.
def build_archive_server(
    cfg: ArchiveRepositoryConfig,
    workflow: ArchiveWorkflowStore,
    thread_id: int,
    requester_id: int,
):
    # ArchiveReader 메서드를 호출하고 결과나 오류를 도구 결과로 바꾼다.
    async def reader_call(method: str, **kwargs) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession() as session:
                # 저장소 API 경로를 GET으로 읽는다. 없으면 None을 반환한다.
                async def get_json(path: str, params=None):
                    async with session.get(
                        github_repo_url(cfg) + path, headers=github_headers(cfg), params=params,
                    ) as resp:
                        if resp.status == 404:
                            return None
                        resp.raise_for_status()
                        return await resp.json()

                # 현재 세션으로 브랜치의 commit SHA를 읽는다.
                async def branch_sha(branch: str):
                    return await github_branch_sha(session, cfg, branch)

                result = await getattr(ArchiveReader(get_json, branch_sha), method)(**kwargs)
            return tool_text(json.dumps(result, ensure_ascii=False))
        except (ValueError, aiohttp.ClientError) as exc:
            result = tool_text(str(exc))
            result["isError"] = True
            return result

    # 작업 절차 파일을 고정 commit에서 연다.
    @tool(
        "archive_workflow_open",
        "지식 작업의 원본 절차를 같은 commit에서 읽는다. 작업 시작 시 먼저 호출한다.",
        {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["ingest", "query", "lint"]},
            "branch": {"type": "string"},
        }, "required": ["operation"]},
    )
    async def archive_workflow_open(args: dict[str, Any]) -> dict[str, Any]:
        return await reader_call("workflow_open", operation=args.get("operation"), branch=args.get("branch", BASE_BRANCH))

    # 고정 commit의 Nodebase 지식 파일을 나열한다.
    @tool(
        "archive_list", "workflow_open의 commit SHA에서 AGENTS.md 또는 raw/wiki/schema 파일을 페이지별로 열거한다.",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "ref": {"type": "string"},
            "page": {"type": "integer"}, "per_page": {"type": "integer"},
        }, "required": ["path", "ref"]},
    )
    async def archive_list(args: dict[str, Any]) -> dict[str, Any]:
        return await reader_call("list_files", path=args.get("path"), ref=args.get("ref"),
                                 page=args.get("page", 1), per_page=args.get("per_page", 50))

    # 고정 commit의 wiki에서 검색한다.
    @tool(
        "archive_search", "같은 commit의 wiki 텍스트를 파일 페이지별로 검색한다. 누락과 잘림을 반환한다.",
        {"type": "object", "properties": {
            "query": {"type": "string"}, "ref": {"type": "string"},
            "page": {"type": "integer"}, "per_page": {"type": "integer"}, "limit": {"type": "integer"},
        }, "required": ["query", "ref"]},
    )
    async def archive_search(args: dict[str, Any]) -> dict[str, Any]:
        return await reader_call("search", query=args.get("query"), ref=args.get("ref"),
                                 page=args.get("page", 1), per_page=args.get("per_page", 20), limit=args.get("limit", 100))

    # lint가 최근 활동과 최근 ingest 날짜를 커밋 이력으로 확인할 때 쓴다.
    @tool(
        "archive_history", "AGENTS.md 또는 raw/wiki/schema 경로의 commit 이력을 ref 기준 최신순으로 페이지별 조회한다. sha, 날짜, 메시지 첫 줄을 반환한다.",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "ref": {"type": "string"},
            "page": {"type": "integer"}, "per_page": {"type": "integer"},
        }, "required": ["path", "ref"]},
    )
    async def archive_history(args: dict[str, Any]) -> dict[str, Any]:
        return await reader_call("history", path=args.get("path"), ref=args.get("ref"),
                                 page=args.get("page", 1), per_page=args.get("per_page", 20))

    # 지식 저장소 파일 하나를 브랜치 기준으로 읽는다.
    @tool(
        "archive_read",
        "지식 저장소에서 파일 하나를 읽는다. branch를 주면 그 브랜치에서, 없으면 기본 브랜치에서 읽는다.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["path"],
        },
    )
    async def archive_read(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        branch = str(args.get("branch") or "").strip() or None
        try:
            async with aiohttp.ClientSession() as session:
                content, sha = await github_get_file(session, cfg, path, branch)
        except aiohttp.ClientResponseError as exc:
            return tool_text(github_error_text(exc))
        if content is None:
            where = f" (브랜치 {branch})" if branch else ""
            return tool_text(f"{path} 파일이 없습니다{where}.")
        return tool_text(f"path: {path}\nblob_sha: {sha}\n\n{content}")

    # 브랜치의 현재 commit SHA를 알려 준다.
    @tool(
        "archive_branch",
        "브랜치의 현재 commit SHA를 읽는다. 변경 Stage의 기준 SHA를 확인할 때 쓴다.",
        {"branch": str},
    )
    async def archive_branch(args: dict[str, Any]) -> dict[str, Any]:
        branch = str(args.get("branch", "")).strip()
        try:
            async with aiohttp.ClientSession() as session:
                sha = await github_branch_sha(session, cfg, branch)
        except aiohttp.ClientResponseError as exc:
            return tool_text(github_error_text(exc))
        return tool_text(f"{branch}: {sha}" if sha else f"{branch} 브랜치가 없습니다.")

    # 모델 호출은 외부 변경 대신 Python이 검증할 pending만 저장한다.
    def stage_tool(name: str, description: str, schema: dict[str, Any], kind: str):
        # 변경안을 승인 대기 상태로 보관한다.
        @tool(name, description, schema)
        async def stage(args: dict[str, Any]) -> dict[str, Any]:
            try:
                pending = workflow.stage(kind, thread_id, requester_id, args)
            except (ValueError, PermissionError) as exc:
                return tool_text(str(exc))
            return tool_text(f"{kind} 변경안을 보관했습니다. pending_id={pending.pending_id}. 사용자의 '승인' 또는 '취소'를 기다립니다.")
        return stage

    issue_stage = stage_tool(
        "archive_issue_stage", "Issue 제목, 본문, 타입과 원격 템플릿 정보를 승인 대기로 보관한다.",
        {
            "issue_type": str,
            "title": str,
            "body": str,
            "labels": list,
            "template_path": str,
            "template_sha": str,
        },
        "issue",
    )
    change_stage = stage_tool(
        "archive_stage", "전체 파일 작업과 커밋 메시지를 단일 커밋 승인 대기로 보관한다.",
        {"operations": list, "commit_message": str, "source_commit_sha": str}, "change",
    )
    pr_stage = stage_tool(
        "archive_pr_stage", "PR 제목, 전체 본문과 원격 템플릿 정보를 별도 승인 대기로 보관한다.",
        {"title": str, "body": str, "template_path": str, "template_sha": str}, "pr",
    )

    return create_sdk_mcp_server(
        name="archive",
        version="1.0.0",
        tools=[archive_workflow_open, archive_list, archive_search, archive_history,
               archive_read, archive_branch, issue_stage, change_stage, pr_stage],
    )


ARCHIVE_TOOL_NAMES = [
    "mcp__archive__archive_workflow_open",
    "mcp__archive__archive_list",
    "mcp__archive__archive_search",
    "mcp__archive__archive_history",
    "mcp__archive__archive_read",
    "mcp__archive__archive_branch",
    "mcp__archive__archive_issue_stage",
    "mcp__archive__archive_stage",
    "mcp__archive__archive_pr_stage",
]

# 주제가 없는 채널의 자율 채팅에서 빼는 웹 검색 도구.
WEB_TOOL_NAMES = ("WebSearch", "WebFetch")
