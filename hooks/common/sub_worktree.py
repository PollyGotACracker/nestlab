"""linked worktree의 변경 결과를 원본 checkout에 반영한다.

입력: 원본 checkout과 linked worktree.
반환: 반영한 파일과 보류한 파일의 상대 경로 목록. 실행 오류는 ValueError 또는 OSError이다.
한계: worktree 생성은 runner가 담당하며, 원본 내용이 기준 커밋과 다른 파일은 반영하지 않고 보류한다.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys

from sub_paths import MANAGE_WORKTREE


# 원본 도구의 apply 단일 호출과 등록된 worktree 경로만 외부 경로 검사 예외로 인정한다.
def transfer_command(command: str, project: Path) -> bool:
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
        if args.pop(0) != "apply" or len(args) % 2:
            return False
        options = {}
        for key, value in zip(args[::2], args[1::2]):
            if key not in ("--source", "--worktree") or key in options:
                return False
            options[key] = value
        worktree = Path(options["--worktree"]).resolve()
        source = Path(options["--source"]).resolve() if "--source" in options else origin(worktree)
        if project.resolve() not in (source, worktree):
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


# Git 조회 명령의 결과를 반환하고 실패하면 원문 오류를 포함해 중단한다.
def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git worktree 조회에 실패했습니다.")
    return result.stdout.strip()


# 두 경로가 동일 저장소의 서로 다른 checkout인지 확인한다.
def metadata(source: Path, worktree: Path) -> None:
    roots = [Path(git(root, "rev-parse", "--show-toplevel")).resolve() for root in (source, worktree)]
    if roots != [source, worktree] or source == worktree:
        raise ValueError("원본과 worktree는 서로 다른 checkout 루트여야 합니다.")
    common = [(root / git(root, "rev-parse", "--git-common-dir")).resolve() for root in roots]
    if common[0] != common[1] or not (worktree / ".git").is_file():
        raise ValueError("같은 저장소에 등록된 linked worktree가 필요합니다.")


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


# 저장한 파일 내용과 권한을 적용하며 None이면 명시적으로 지정한 파일을 삭제한다.
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


# 원본 파일이 기준 커밋과 같으면 True이다. 기준 커밋에 있으면 Git 줄바꿈 정규화 기준의 diff가 없는지, 없으면 원본에도 없는지 본다.
def same_as_base(root: Path, base: str, name: str) -> bool:
    def run(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=15).returncode
    if run("cat-file", "-e", f"{base}:{name}") == 0:
        return run("diff", "--quiet", base, "--", name) == 0
    return not file_path(root, name).exists()


# worktree의 기준 커밋(원본 HEAD와의 merge-base) 이후 바뀐 파일 중 원본 내용이 기준 커밋과 같은 파일만 원본에 쓴다.
# 반환은 (반영한 파일, 보류한 파일)이며 보류는 원본이 기준과 다르거나 전달할 수 없는 경로이다.
def apply(source: Path, worktree: Path) -> tuple[list[str], list[str]]:
    source, worktree = source.resolve(), worktree.resolve()
    metadata(source, worktree)
    base = git(worktree, "merge-base", "HEAD", git(source, "rev-parse", "HEAD"))
    changed = git(worktree, "diff", "--name-only", "-z", base).split("\0")
    changed += git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    states, held = {}, []
    for name in dict.fromkeys(filter(None, changed)):
        try:
            target = file_path(worktree, name)
            same = same_as_base(source, base, name)
        except (OSError, ValueError):
            held.append(name)
            continue
        if same:
            states[name] = snapshot(target)
        else:
            held.append(name)
    write_files(source, states)
    return list(states), held
