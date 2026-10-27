"""고정 commit의 Nodebase 지식 절차와 파일을 읽는다."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

from config import ARCHIVE_RULES_FILE


SHA = re.compile(r"[0-9a-fA-F]{40}")
MAX_FILE_BYTES = 1_000_000


# Nodebase 지식 경계 안의 저장소 기준 POSIX 경로인지 검사한다.
def archive_path(path: str) -> str:
    # 형식과 금지 문자를 확인한다.
    if not isinstance(path, str) or not path or any(c in path for c in ("\\", "%", ":")):
        raise ValueError("path는 인코딩하지 않은 Nodebase POSIX 경로여야 합니다.")

    # 저장소 기준 경계 안의 경로인지 확인한다.
    parts = path.split("/")
    if (parts[0] not in ("raw", "wiki", "schema") and path != ARCHIVE_RULES_FILE
            or any(p in ("", ".", "..") for p in parts)):
        raise ValueError(f"path는 {ARCHIVE_RULES_FILE} 또는 raw/wiki/schema 경계 안의 저장소 기준 경로여야 합니다.")

    # 제어 문자가 없는지 확인한다.
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError("path에 제어 문자를 사용할 수 없습니다.")
    return path


# 40자리 commit SHA인지 검사하고 소문자로 반환한다.
def commit_ref(ref: str) -> str:
    # ref가 40자리 SHA인지 확인하고 소문자로 돌려준다.
    if not isinstance(ref, str) or SHA.fullmatch(ref) is None:
        raise ValueError("ref에는 archive_workflow_open이 반환한 40자리 commit SHA를 넣어야 합니다.")
    return ref.lower()


# git branch 이름 규칙에 맞는지 검사하고 그대로 반환한다.
def branch_name(branch: str) -> str:
    # branch 이름 규칙을 어기면 오류를 내고 지키면 그대로 돌려준다.
    if (not isinstance(branch, str) or not branch or branch == "@"
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "~^:?*[\\%" for c in branch)
            or ".." in branch or "@{" in branch
            or any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch.split("/"))):
        raise ValueError("유효한 branch 이름이 필요합니다.")
    return branch


# 이력 조회의 ref는 40자리 commit SHA 또는 branch 이름을 받는다.
def history_ref(ref: str) -> str:
    # SHA는 소문자로 돌려주고 그 밖의 값은 branch 이름 규칙으로 검사한다.
    if isinstance(ref, str) and SHA.fullmatch(ref) is not None:
        return ref.lower()
    try:
        return branch_name(ref)
    except ValueError:
        raise ValueError("ref에는 archive_workflow_open이 반환한 commit SHA 또는 branch 이름을 넣어야 합니다.") from None


# 1 이상 최댓값 이하의 정수인지 검사하고 그대로 반환한다.
def bounded_number(value: Any, name: str, maximum: int) -> int:
    # 값이 1 이상 최댓값 이하의 정수인지 확인한다.
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name}은 1~{maximum} 정수여야 합니다.")
    return value


class ArchiveReader:
    # HTTP 인증·오류 처리와 branch 조회는 기존 bot helper를 주입한다.
    def __init__(self, get_json, branch_sha):
        self.get_json = get_json
        self.branch_sha = branch_sha

    # commit의 전체 tree 항목과 잘림 여부를 읽는다.
    async def tree(self, ref: str):
        # ref의 커밋을 조회해 해시가 일치하는지 확인한다.
        ref = commit_ref(ref)
        commit = await self.get_json(f"/git/commits/{quote(ref, safe='')}")
        if commit is None or commit.get("sha", "").lower() != ref:
            raise ValueError(f"commit을 찾을 수 없습니다: {ref}")

        # 커밋의 tree를 재귀로 조회한다.
        tree_sha = commit_ref(commit["tree"]["sha"])
        tree = await self.get_json(f"/git/trees/{tree_sha}", params={"recursive": "1"})
        if tree is None:
            raise ValueError(f"commit의 tree를 찾을 수 없습니다: {ref}")
        return ref, tree["tree"], bool(tree.get("truncated", False))

    # tree 항목 하나를 blob SHA로 확인하고 UTF-8 텍스트 전체를 읽는다.
    async def read_entry(self, entry):
        # 항목이 일반 파일이고 크기 한도 안인지 확인한다.
        path = archive_path(entry["path"])
        if entry.get("type") != "blob" or entry.get("mode") not in ("100644", "100755"):
            raise ValueError(f"일반 파일이 아닙니다: {path}")
        if entry.get("size", 0) > MAX_FILE_BYTES:
            raise ValueError(f"파일 읽기 한도 {MAX_FILE_BYTES} bytes 초과: {path}")

        # blob을 조회해 인코딩과 SHA를 확인한다.
        sha = commit_ref(entry["sha"])
        data = await self.get_json(f"/git/blobs/{quote(sha, safe='')}")
        if data is None:
            raise ValueError(f"파일 blob이 없습니다: {path}")
        if data.get("encoding") != "base64" or data.get("sha", "").lower() != sha:
            raise ValueError(f"전체 파일 내용 또는 blob SHA를 확인할 수 없습니다: {path}")

        # base64를 해독해 크기와 UTF-8 텍스트인지 확인한다.
        try:
            raw = base64.b64decode("".join(data["content"].split()), validate=True)
            if len(raw) != entry.get("size"):
                raise ValueError(f"파일 크기 불일치: {path}")
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError(f"파일 읽기 한도 초과: {path}")
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError(f"텍스트 파일이 아닙니다: {path}")
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"파일 전체를 UTF-8 텍스트로 읽을 수 없습니다: {path}") from exc
        return {"path": path, "blob_sha": sha, "content": text}

    # branch의 현재 commit을 고정하고 작업 종류별 절차 파일을 읽는다.
    async def workflow_open(self, operation: str, branch: str = "master"):
        # 작업 종류와 브랜치를 확인하고 브랜치 커밋을 구한다.
        if operation not in ("ingest", "query", "lint"):
            raise ValueError("operation은 ingest, query, lint 중 하나여야 합니다.")
        branch = branch_name(branch)
        sha = await self.branch_sha(branch)
        if sha is None:
            raise ValueError(f"branch를 찾을 수 없습니다: {branch}")

        # 커밋 tree에서 지침과 작업 스키마 원본 항목을 찾는다.
        ref, entries, truncated = await self.tree(sha)
        paths = (ARCHIVE_RULES_FILE, f"schema/{operation}.md")
        by_path = {entry["path"]: entry for entry in entries}
        missing = [path for path in paths if path not in by_path]
        if missing:
            reason = "tree가 잘려 존재 확인 불가" if truncated else "원본 누락"
            raise ValueError(f"{reason}: {', '.join(missing)} (commit {ref})")

        # 두 원본을 읽어 반환한다.
        sources = [await self.read_entry(by_path[path]) for path in paths]
        return {"operation": operation, "commit_sha": ref, "base_directory": ".", "sources": sources}

    # 고정 commit에서 경로 아래 파일을 페이지 단위로 나열한다.
    async def list_files(self, path: str, ref: str, page: int = 1, per_page: int = 50):
        # 경로와 쪽 번호를 확인한다.
        path = archive_path(path)
        page = bounded_number(page, "page", 1_000_000)
        per_page = bounded_number(per_page, "per_page", 100)

        # 커밋 tree에서 범위 안 파일을 골라 쪽별로 자른다.
        ref, entries, truncated = await self.tree(ref)
        files = sorted(
            (entry for entry in entries if entry["type"] != "tree"
             and (entry["path"] == path or entry["path"].startswith(path + "/"))),
            key=lambda entry: entry["path"],
        )
        start = (page - 1) * per_page
        selected = files[start:start + per_page]
        for entry in selected:
            archive_path(entry["path"])

        # 다음 쪽 여부와 완전성을 계산해 반환한다.
        more = start + per_page < len(files)
        return {
            "commit_sha": ref, "scope": path, "page": page, "per_page": per_page,
            "entries": [{k: entry.get(k) for k in ("path", "sha", "type", "mode", "size")} for entry in selected],
            "known_files": len(files), "tree_truncated": truncated,
            "truncated": truncated or more, "next_page": page + 1 if more else None,
            "scope_found": any(entry["path"] == path for entry in entries) or bool(files),
            "complete": page == 1 and not truncated and not more
                        and (any(entry["path"] == path for entry in entries) or bool(files)),
        }

    # 고정 commit의 wiki 파일에서 검색어가 들어간 줄을 찾는다.
    async def search(self, query: str, ref: str, page: int = 1, per_page: int = 20, limit: int = 100):
        # 검색어와 한도를 검사하고 wiki 목록을 읽어 파일마다 검색 결과를 모은다.
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("query는 1~1000자의 비어 있지 않은 문자열이어야 합니다.")
        bounded_number(per_page, "per_page", 20)
        bounded_number(limit, "limit", 100)
        listing = await self.list_files("wiki", ref, page, per_page)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches, skipped = [], []
        matches_truncated = False
        scanned = []
        for entry in listing["entries"]:
            try:
                source = await self.read_entry(entry)
            except ValueError as exc:
                skipped.append({"path": entry["path"], "reason": str(exc)})
                continue
            scanned.append(entry["path"])
            for number, line in enumerate(source["content"].splitlines(), 1):
                match = pattern.search(line)
                if match is None:
                    continue
                if len(matches) >= limit:
                    matches_truncated = True
                    continue
                position = match.start()
                # 일치 지점 주위의 유한한 본문만 반환하고 생략 여부를 명시한다.
                start = max(0, position - 200) if len(line) > 2000 else 0
                excerpt = line[start:start + 2000]
                matches.append({"path": entry["path"], "line": number, "text": excerpt,
                                "text_truncated": len(excerpt) != len(line)})
        return {
            "commit_sha": listing["commit_sha"], "scope": "wiki", "query": query,
            "page": page, "per_page": per_page, "limit": limit, "matches": matches,
            "scanned_files": scanned, "skipped_files": skipped, "known_files": listing["known_files"],
            "scope_found": listing["scope_found"], "tree_truncated": listing["tree_truncated"],
            "matches_truncated": matches_truncated, "next_page": listing["next_page"],
            "truncated": listing["truncated"] or bool(skipped) or matches_truncated
                         or any(match["text_truncated"] for match in matches),
            "complete": page == 1 and not listing["truncated"] and not skipped
                        and not matches_truncated and listing["scope_found"]
                        and not any(match["text_truncated"] for match in matches),
        }

    # Nodebase 지식 경로의 commit 이력을 GitHub 기본 순서인 최신순으로 반환한다.
    # 응답이 per_page개로 차면 다음 페이지가 있을 수 있어 next_page를 둔다.
    async def history(self, path: str, ref: str, page: int = 1, per_page: int = 20):
        # 경로, ref, 쪽 번호를 확인한다.
        path = archive_path(path)
        ref = history_ref(ref)
        page = bounded_number(page, "page", 1_000_000)
        per_page = bounded_number(per_page, "per_page", 100)

        # 커밋 이력을 조회한다.
        data = await self.get_json(
            "/commits", params={"sha": ref, "path": path, "page": page, "per_page": per_page},
        )
        if data is None:
            raise ValueError(f"ref를 찾을 수 없습니다: {ref}")
        if not isinstance(data, list):
            raise ValueError(f"commit 이력 응답 형식이 올바르지 않습니다: {path}")

        # 커밋마다 해시, 날짜, 메시지 첫 줄을 뽑는다.
        commits = []
        for item in data:
            commit = item.get("commit") or {}
            author = commit.get("author") or {}
            committer = commit.get("committer") or {}
            commits.append({
                "sha": item.get("sha"),
                "date": author.get("date") or committer.get("date"),
                "message": ((commit.get("message") or "").splitlines() or [""])[0],
            })

        # 다음 쪽 여부를 계산해 반환한다.
        more = len(data) >= per_page
        return {
            "ref": ref, "scope": path, "page": page, "per_page": per_page, "commits": commits,
            "truncated": more, "next_page": page + 1 if more else None,
            "complete": page == 1 and not more,
        }
