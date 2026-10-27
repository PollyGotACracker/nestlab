"""캐릭터 기억 업로드의 승인 대기와 저장소 갱신 필요 표시를 저장한다."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


# persona_memory_list가 만든 채널당 1건의 업로드 승인 대기.
# status는 pending, executing, done, failed 중 하나다.
@dataclass
class MemoryApproval:
    channel_id: int
    agent: str
    requester_id: int
    memory_ids: list[int]
    sync: bool = False
    status: str = "pending"


# 재시작 뒤에도 승인 대상이 바뀌지 않도록 대기와 갱신 필요 표시를 원자 저장한다.
@dataclass
class MemoryApprovalStore:
    path: Path
    approvals: dict[int, MemoryApproval] = field(default_factory=dict)
    # 저장소에 올린 기억이 원본 삭제로 사라져 다음 승인 때 MEMORY.md를 다시 만들어야 하는 캐릭터.
    sync_needed: set[str] = field(default_factory=set)

    # 손상된 상태는 승인 대상으로 복원하지 않고 빈 상태로 둔다.
    # 실행 중에 종료된 대기는 외부 결과가 불명확하므로 failed로 바꾼다.
    @classmethod
    def load(cls, path: Path) -> "MemoryApprovalStore":
        # 빈 저장소 객체를 만들고 파일이 없으면 그대로 반환한다.
        store = cls(path)
        if not path.is_file():
            return store

        # 승인 항목과 동기화 대기 목록을 복원한다. 실행 중이던 항목은 실패로 바꾼다.
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key, value in raw.get("approvals", {}).items():
                approval = MemoryApproval(**value)
                if approval.status == "executing":
                    approval.status = "failed"
                store.approvals[int(key)] = approval
            store.sync_needed = {str(agent) for agent in raw.get("sync_needed", [])}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls(path)
        return store

    # 부분 기록이 승인 대상으로 오인되지 않도록 임시 파일을 원자 교체한다.
    def save(self) -> None:
        # 상위 폴더를 만든다.
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # 저장할 데이터를 임시 파일에 쓰고 권한을 좁힌다.
        temporary = self.path.with_suffix(".tmp")
        data = {
            "approvals": {str(key): asdict(value) for key, value in self.approvals.items()},
            "sync_needed": sorted(self.sync_needed),
        }
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass

        # 임시 파일로 원본을 교체한다.
        os.replace(temporary, self.path)

    # 채널의 대기를 새 목록으로 교체한다. 갱신 필요 표시가 있으면 삭제 반영도 함께 맡는다.
    def offer(self, channel_id: int, agent: str, requester_id: int, memory_ids: list[int]) -> MemoryApproval:
        # 승인 대기 건을 만들어 저장하고 돌려준다.
        approval = MemoryApproval(
            channel_id, agent, requester_id, list(memory_ids), sync=agent in self.sync_needed,
        )
        self.approvals[channel_id] = approval
        self.save()
        return approval

    # 승인이나 취소를 받을 수 있는 채널의 대기를 반환한다.
    def active(self, channel_id: int) -> MemoryApproval | None:
        # 채널의 대기 중인 승인만 돌려준다.
        approval = self.approvals.get(channel_id)
        if approval is None or approval.status != "pending":
            return None
        return approval

    # 채널의 대기를 지운다. 지운 대기가 있었으면 True다.
    def cancel(self, channel_id: int) -> bool:
        # 채널의 승인 건을 삭제하고 저장한다.
        if self.approvals.pop(channel_id, None) is None:
            return False
        self.save()
        return True

    # 원본 삭제로 저장소 MEMORY.md가 달라진 캐릭터를 표시한다. PR은 다음 승인 때 만든다.
    def mark_sync_needed(self, agents: set[str] | list[str]) -> None:
        # 새로 동기화가 필요한 에이전트를 기록하고 저장한다.
        added = set(agents) - self.sync_needed
        if not added:
            return
        self.sync_needed |= added
        self.save()
