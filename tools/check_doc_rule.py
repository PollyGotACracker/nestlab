#!/usr/bin/env python3
"""지침 문서의 끊어진 경로 및 절 참조와 금지 패턴을 검사한다."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# 같은 이름 폴더의 모듈을 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_doc_rule"))

from sub_rules import check_file, find_targets
from sub_config import KINDS
from sub_paths import build_name_index
from sub_config import ROOT

HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "common"


# PreToolUse hook 입력(Edit, Write, apply_patch)의 쓰기 뒤 파일 내용 중 검사 대상만 검사한다.
# 쓰기 전 내용에 없던 위반이 있으면 deny JSON을 출력하고, 쓰기 뒤 내용을 계산하지 못하면 판정을 건너뛴다. 오류는 통과한다.
def hook_main() -> int:
    # 표준 입출력을 UTF-8로 맞춘다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # 쓰기 대상 중 저장소 안의 검사 대상만 골라 쓰기 뒤 내용을 검사한다.
    try:
        sys.path.insert(0, str(HOOKS))
        from sub_write import added_lines, targets as write_targets
        data = json.load(sys.stdin)
        paths = write_targets(data) or []
        wanted, _ = find_targets()
        names = build_name_index()
        found = []
        for path in paths:
            try:
                rel = path.resolve().relative_to(ROOT.resolve()).as_posix()
            except ValueError:
                continue
            new = added_lines(data, path)[1] if rel in wanted else None
            if new is None:
                continue
            before = {(kind, detail) for _, _, kind, detail in check_file(rel, names, {})} if path.is_file() else set()
            found += [v for v in check_file(rel, names, {}, new) if (v[2], v[3]) not in before]
    except Exception:
        return 0

    # 위반이 있으면 deny JSON으로 알린다.
    if found:
        lines = [f"{r}:{n}: [{k}] {d}" for r, n, k, d in found[:20]]
        reason = "\n".join(["지침 검사 위반을 고쳐 다시 쓰십시오.", *lines])
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, sys.stdout, ensure_ascii=False)
    return 0


# 모든 대상 파일을 검사하고 파일별 결과와 합계를 출력한다.
def main() -> int:
    # 표준 입출력을 UTF-8로 맞춘다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    names = build_name_index()
    heading_cache: dict[Path, set[str]] = {}
    violations: list[tuple[str, int, str, str]] = []
    targets, missing = find_targets()

    # 대상 파일마다 검사하고 위반을 출력한다.
    for rel in targets:
        file_violations = check_file(rel, names, heading_cache)
        violations.extend(file_violations)
        print(f"== {rel}: 위반 {len(file_violations)}")
        for _, lineno, kind, detail in file_violations:
            print(f"{rel}:{lineno}: [{kind}] {detail}")

    # 없는 대상과 종류별 합계를 출력하고 위반이 있으면 1을 반환한다.
    print()
    for pattern in missing:
        print(f"없음: {pattern}")
    counts = Counter(kind for _, _, kind, _ in violations)
    for kind in KINDS:
        print(f"{kind}: {counts.get(kind, 0)}")
    print(f"대상 파일 없음: {len(missing)}")
    total = len(violations) + len(missing)
    print(f"검사 파일: {len(targets)}, 위반 합계: {total}")

    # 위반이 있으면 종료 코드 1을 반환한다.
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(hook_main() if "--hook" in sys.argv[1:] else main())
