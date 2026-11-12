"""기존 linked worktree에 배정 파일을 전달하고 변경 결과를 원본에 반영한다.

입력: 원본 checkout, linked worktree, 명세 경로, 역할과 배정 파일 목록.
반환: 전달 또는 반영한 상대 파일 경로 목록. 충돌과 실행 오류는 ValueError 또는 OSError이다.
한계: worktree 생성은 runner가 담당하며 파일 목록은 명세의 배정 범위로 제한한다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from sub_paths import MANAGE_WORKTREE, TASK_REL

MANIFEST = "nestlab-task-transfer.json"


# Git 메타데이터 기록을 교체하며 쓰기 실패 시 기존 기록을 보존한다.
def save_record(path: Path, record: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# 원본 도구의 단일 호출과 등록된 worktree 경로만 외부 경로 검사 예외로 인정한다.
def transfer_command(command: str, project: Path, child_role: str | None = None) -> bool:
    if any(mark in command for mark in (";", "&", "|", "`", "$", "%", "<", ">", "\n", "\r")):
        return False
    try:
        args = [value.strip("\"'") for value in shlex.split(command, posix=False)]
        executable = args.pop(0)
        if executable not in ("python", "python3", "py", "python.exe", "py.exe") and Path(executable).resolve() != Path(sys.executable).resolve():
            return False
        if args[:1] == ["-B"]:
            args.pop(0)
        script = MANAGE_WORKTREE
        if (project / args.pop(0)).resolve() != script.resolve():
            return False
        action = args.pop(0)
        if action not in ("prepare", "apply", "recover") or (child_role and action != "prepare"):
            return False
        if len(args) % 2:
            return False
        options = {}
        for key, value in zip(args[::2], args[1::2]):
            if key not in ("--source", "--worktree", "--spec", "--role", "--file") or (key != "--file" and key in options):
                return False
            options[key] = value
        worktree = Path(options["--worktree"]).resolve()
        source = Path(options["--source"]).resolve() if "--source" in options else origin(worktree)
        if project.resolve() not in (source, worktree) or (child_role and options.get("--role") != child_role):
            return False
        metadata(source, worktree)
        return True
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return False


# linked worktree에서 원본 checkout 루트를 구한다. linked worktree가 아니면 지원 범위를 알리는 오류를 낸다.
def origin(worktree: Path) -> Path:
    worktree = worktree.resolve()
    if not (worktree / ".git").is_file():
        raise ValueError("지원 범위는 runner나 git worktree로 만든 linked worktree입니다. 해당 경로는 linked worktree가 아닙니다. "
                         "Claude 하위 isolation 또는 Codex create_worktree로 생성한 뒤 호출하십시오.")
    return (worktree / git(worktree, "rev-parse", "--git-common-dir")).resolve().parent


# 전달이 끝나지 않은 기록이 cwd의 linked worktree에 남아 있으면 반영 안내 사유를 반환한다. 해당 없거나 조회 실패면 None이다.
def pending_transfer(cwd: str | Path | None) -> str | None:
    try:
        root = Path(git(Path(cwd), "rev-parse", "--show-toplevel")).resolve()
        if not (root / ".git").is_file():
            return None
        manifest = (root / git(root, "rev-parse", "--git-dir")).resolve() / MANIFEST
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None
    return (f"원본에 반영하지 않은 배정 결과가 있습니다. 같은 --worktree로 manage_task_worktree.py apply를 실행하십시오. "
            f"충돌이면 보고에 포함하십시오. 원본과 worktree는 보존됩니다: {manifest}") if manifest.exists() else None


# Git 조회 명령의 결과를 반환하고 실패하면 원문 오류를 포함해 중단한다.
def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git worktree 조회에 실패했습니다.")
    return result.stdout.strip()


# 두 경로가 동일 저장소의 서로 다른 checkout인지 확인하고 worktree 전용 메타데이터 경로를 반환한다.
def metadata(source: Path, worktree: Path) -> Path:
    roots = [Path(git(root, "rev-parse", "--show-toplevel")).resolve() for root in (source, worktree)]
    if roots != [source, worktree] or source == worktree:
        raise ValueError("원본과 worktree는 서로 다른 checkout 루트여야 합니다.")
    common = [(root / git(root, "rev-parse", "--git-common-dir")).resolve() for root in roots]
    if common[0] != common[1] or not (worktree / ".git").is_file():
        raise ValueError("같은 저장소에 등록된 linked worktree가 필요합니다.")
    return (worktree / git(worktree, "rev-parse", "--git-dir")).resolve() / MANIFEST


# checkout 안의 일반 파일 경로를 반환하며 보안 파일과 Git 메타데이터 및 링크 탈출을 거부한다.
def file_path(root: Path, name: str) -> Path:
    from sub_path import is_secret, norm, secret_patterns
    path = Path(name)
    if path.is_absolute() or not path.parts or any(part in ("..", ".git") for part in path.parts):
        raise ValueError(f"상대 파일 경로가 필요합니다: {name}")
    if any(part.lower().startswith(".env") or part.lower() in ("credentials", "id_rsa", "id_ed25519") for part in path.parts):
        raise ValueError(f"보안 파일은 전달할 수 없습니다: {name}")
    target = root / path
    patterns = secret_patterns()
    if is_secret(norm(str(target), root), patterns) or is_secret(norm(str(target.resolve()), root), patterns):
        raise ValueError(f"보안 파일은 전달할 수 없습니다: {name}")
    if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in [target, *target.parents] if p != root and p.is_relative_to(root)):
        raise ValueError(f"링크 경로는 전달할 수 없습니다: {name}")
    if target.exists() and not target.is_file():
        raise ValueError(f"일반 파일이 필요합니다: {name}")
    return target


# 파일 내용과 실행 권한을 직렬화하며 없는 파일은 None으로 기록한다.
def snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    content = path.read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest(), "data": base64.b64encode(content).decode("ascii"),
            "mode": path.stat().st_mode & 0o777}


# 저장한 파일 내용과 권한을 적용하며 None이면 명시적으로 배정한 파일을 삭제한다.
def restore(path: Path, state: dict | None) -> None:
    if state is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(state["data"], validate=True))
    path.chmod(state["mode"])


# 여러 파일을 적용하고 실패하면 변경을 시작한 파일까지 기존 내용으로 복구한다.
def write_files(root: Path, states: dict[str, dict | None]) -> None:
    before = {name: snapshot(file_path(root, name)) for name in states}
    started = []
    try:
        for name, state in states.items():
            started.append(name)
            restore(file_path(root, name), state)
    except OSError as error:
        errors = []
        for name in reversed(started):
            try:
                restore(file_path(root, name), before[name])
            except OSError as recovery:
                errors.append(f"{name}: {recovery}")
        if errors:
            raise OSError(f"{error}; 복구 실패: {'; '.join(errors)}. worktree와 Git 메타데이터를 보존했습니다.") from error
        raise


# 명세의 역할 배정 범위에 들어 있는 파일만 현재 내용으로 전달하고 재시도 가능한 기준을 기록한다.
def prepare(source: Path, worktree: Path, spec: str, role: str, files: list[str]) -> list[str]:
    from sub_docs import section
    from sub_task_docs import TASK_ID
    source, worktree = source.resolve(), worktree.resolve()
    manifest = metadata(source, worktree)
    if manifest.exists():
        raise ValueError(f"기존 전달 기록을 보존했습니다. 먼저 반영 또는 복구하십시오: {manifest}")
    match = re.fullmatch(re.escape(TASK_REL) + r"/([^/]+)/spec\.md", spec)
    if not match or not TASK_ID.fullmatch(match[1]) or role not in ("worker", "documenter"):
        raise ValueError(f"worker 또는 documenter와 {TASK_REL}/<작업-id>/spec.md 경로가 필요합니다.")
    plan = file_path(source, spec).read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^- 작업 ID:[ \t]*" + re.escape(match[1]) + r"[ \t]*$", plan):
        raise ValueError("명세 문서의 작업 ID가 경로와 다릅니다.")
    assignment = re.search(r"(?m)^- " + re.escape(role) + r":[^\n]+", section(plan, "배정 범위"))
    if not assignment or not files:
        raise ValueError("명세의 역할 배정과 담당 파일 목록이 필요합니다.")
    names = list(dict.fromkeys(Path(name).as_posix() for name in files))
    for name in names:
        file_path(source, name)
        file_path(worktree, name)
        prefixes = [Path(*Path(name).parts[:i]).as_posix() for i in range(1, len(Path(name).parts) + 1)]
        if not any(re.search(r"(?<![A-Za-z0-9_./-])" + re.escape(prefix) + r"/?(?![A-Za-z0-9_./-])", assignment[0]) for prefix in prefixes):
            raise ValueError(f"명세의 {role} 배정 범위 밖 파일입니다: {name}")
    delivered = list(dict.fromkeys([spec, *names]))
    if git(worktree, "status", "--porcelain", "--", *delivered):
        raise ValueError("전달 대상 worktree 파일에 기존 변경이 있습니다. 원본과 worktree를 보존했습니다.")
    payload = {name: snapshot(file_path(source, name)) for name in delivered}
    record = {"source": str(source), "worktree": str(worktree), "files": names, "payload": payload,
              "baseline": {name: payload[name] for name in names},
              "worktree_before": {name: snapshot(file_path(worktree, name)) for name in delivered}, "phase": "prepare"}
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False)
    write_files(worktree, payload)
    record["phase"] = "ready"
    save_record(manifest, record)
    return names


# 원본이 전달 기준과 같을 때만 담당 파일 결과를 반영하며 충돌 시 모든 파일을 보존한다.
def apply(source: Path, worktree: Path) -> list[str]:
    source, worktree = source.resolve(), worktree.resolve()
    manifest = metadata(source, worktree)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if record["source"] != str(source) or record["worktree"] != str(worktree) or record["phase"] not in ("ready", "apply"):
        raise ValueError("전달이 완료된 같은 checkout의 기록이 필요합니다.")
    baseline = record["baseline"]
    results = {name: snapshot(file_path(worktree, name)) for name in record["files"]}
    current = {name: snapshot(file_path(source, name)) for name in results}
    if record["phase"] == "apply" and current == record.get("result"):
        manifest.unlink()
        return list(results)
    if conflicts := [name for name in results if current[name] != baseline[name]]:
        raise ValueError(f"원본이 기준과 달라 반영을 보류했습니다: {', '.join(conflicts)}")
    record.update(phase="apply", result=results)
    save_record(manifest, record)
    write_files(source, results)
    manifest.unlink()
    return list(results)


# 전송이나 반영 오류 후 해당 단계의 기준 내용으로 복구하며 외부 변경은 덮어쓰지 않는다.
def recover(source: Path, worktree: Path) -> list[str]:
    source, worktree = source.resolve(), worktree.resolve()
    manifest = metadata(source, worktree)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if record["source"] != str(source) or record["worktree"] != str(worktree):
        raise ValueError("같은 checkout의 전달 기록이 필요합니다.")
    phase = record["phase"]
    if phase == "ready":
        raise ValueError("전달이 완료된 작업 결과를 보존했습니다. apply로 반영하십시오.")
    root = worktree if phase == "prepare" else source
    before = record["worktree_before"] if phase == "prepare" else record["baseline"]
    after = record["payload"] if phase == "prepare" else record["result"]
    if conflicts := [name for name in before if snapshot(file_path(root, name)) not in (before[name], after[name])]:
        raise ValueError(f"복구 기준 밖의 변경을 보존했습니다: {', '.join(conflicts)}")
    write_files(root, before)
    if phase == "prepare":
        manifest.unlink()
    else:
        record["phase"] = "ready"
        save_record(manifest, record)
    return list(before)
