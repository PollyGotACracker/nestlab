"""지식 저장소 작업의 승인 전 상태를 보관하고 검증한다."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_BRANCH = "master"
ARCHIVE_TYPES = ("feat", "bug", "chore", "docs")
BRANCH_PATTERN = re.compile(r"^(feat|bug|chore|docs)/[1-9][0-9]*$")


# 사용자 문자열 대신 허용 타입과 GitHub Issue 번호로 안전한 ref를 만든다.
def make_branch_name(issue_type: str, issue_number: int) -> str:
    branch = f"{issue_type}/{issue_number}"
    if not BRANCH_PATTERN.fullmatch(branch):
        raise ValueError("작업 브랜치는 <feat|bug|chore|docs>/<양의 이슈 번호> 형식이어야 합니다.")
    return branch


# 원격 Issue 템플릿의 frontmatter와 사용자에게 보여줄 본문을 분리한다.
def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text.strip()
    values: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values, parts[2].strip()


# Issue, 변경 세트, PR에 공통인 승인 소유권과 상태를 담는다.
@dataclass
class Pending:
    kind: str
    pending_id: str
    thread_id: int
    requester_id: int
    data: dict[str, Any]
    status: str = "pending"


# archive thread 하나의 Issue와 후속 작업을 같은 문맥에 묶는다.
@dataclass
class ThreadWorkflow:
    issue_number: int | None = None
    issue_url: str | None = None
    issue_type: str | None = None
    branch: str | None = None
    pending: Pending | None = None
    change_applied: bool = False


# 재시작 뒤에도 승인 대상이 바뀌지 않도록 상태를 원자 저장한다.
@dataclass
class ArchiveWorkflowStore:
    path: Path
    threads: dict[int, ThreadWorkflow] = field(default_factory=dict)

    # 손상된 상태는 승인 대상으로 복원하지 않고 빈 상태로 격리한다.
    @classmethod
    def load(cls, path: Path) -> "ArchiveWorkflowStore":
        store = cls(path)
        if not path.is_file():
            return store
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key, value in raw.get("threads", {}).items():
                pending = Pending(**value["pending"]) if value.get("pending") else None
                store.threads[int(key)] = ThreadWorkflow(
                    issue_number=value.get("issue_number"),
                    issue_url=value.get("issue_url"),
                    issue_type=value.get("issue_type"),
                    branch=value.get("branch"),
                    pending=pending,
                    change_applied=bool(value.get("change_applied", False)),
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls(path)
        return store

    # Thread별 상태를 한 곳에서 만들고 이후 승인 단계가 같은 객체를 사용하게 한다.
    def thread(self, thread_id: int) -> ThreadWorkflow:
        return self.threads.setdefault(thread_id, ThreadWorkflow())

    # 부분 기록이 승인 대상으로 오인되지 않도록 임시 파일을 원자 교체한다.
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        data = {"threads": {str(key): asdict(value) for key, value in self.threads.items()}}
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    # 현재 단계에 맞는 미리보기만 교체하며 GitHub는 호출하지 않는다.
    def stage(self, kind: str, thread_id: int, requester_id: int, data: dict[str, Any]) -> Pending:
        workflow = self.thread(thread_id)
        if kind == "issue":
            issue_type = data.get("issue_type")
            expected_path = f".github/ISSUE_TEMPLATE/{issue_type}.md"
            if (
                issue_type not in ARCHIVE_TYPES or not data.get("title") or not data.get("body")
                or data.get("template_path") != expected_path or not data.get("template_sha")
            ):
                raise ValueError("Issue 타입, 제목, 본문을 올바르게 넣어야 합니다.")
        elif kind == "change":
            if workflow.issue_number is None or workflow.branch is None:
                raise ValueError("승인된 Issue를 먼저 생성해야 합니다.")
            operations = data.get("operations")
            if (
                not isinstance(operations, list)
                or not operations
                or not data.get("commit_message")
                or not data.get("source_commit_sha")
            ):
                raise ValueError("전체 변경 작업, 커밋 메시지, 기준 commit SHA가 필요합니다.")
            paths: set[str] = set()
            for operation in operations:
                operation_type = operation.get("type")
                path = str(operation.get("path", "")).strip().lstrip("/")
                if (
                    operation_type not in {"create", "update", "delete"}
                    or not path
                    or path in paths
                ):
                    raise ValueError("각 경로에는 create, update, delete 중 하나만 지정해야 합니다.")
                if operation_type != "delete" and "content" not in operation:
                    raise ValueError(f"{path}의 content가 없습니다.")
                if operation_type in {"update", "delete"} and not operation.get("expected_sha"):
                    raise ValueError(f"{path}의 기존 blob SHA가 없습니다.")
                paths.add(path)
            data = {
                **data,
                "branch": workflow.branch,
                "base": BASE_BRANCH,
                "issue_number": workflow.issue_number,
            }
        elif kind == "pr":
            if not workflow.change_applied or workflow.branch is None:
                raise ValueError("검증이 끝난 변경 커밋이 먼저 필요합니다.")
            if (
                not data.get("title") or not data.get("body")
                or data.get("template_path") != ".github/PULL_REQUEST_TEMPLATE.md"
                or not data.get("template_sha")
            ):
                raise ValueError("PR 제목과 본문이 필요합니다.")
            data = {
                **data,
                "branch": workflow.branch,
                "base": BASE_BRANCH,
                "issue_number": workflow.issue_number,
            }
        else:
            raise ValueError("알 수 없는 pending 종류입니다.")
        pending = Pending(kind, uuid.uuid4().hex, thread_id, requester_id, data)
        workflow.pending = pending
        self.save()
        return pending

    # 요청자 본인의 활성 pending만 승인 대상으로 반환한다.
    def pending_for(self, thread_id: int, requester_id: int) -> Pending:
        pending = self.thread(thread_id).pending
        if pending is None or pending.status != "pending":
            raise ValueError("승인할 pending이 없습니다.")
        if pending.requester_id != requester_id:
            raise PermissionError("pending을 요청한 사용자만 승인하거나 취소할 수 있습니다.")
        return pending

    # pending만 폐기하고 이미 생성된 외부 객체는 변경하지 않는다.
    def cancel(self, thread_id: int, requester_id: int) -> None:
        pending = self.pending_for(thread_id, requester_id)
        pending.status = "cancelled"
        self.save()
