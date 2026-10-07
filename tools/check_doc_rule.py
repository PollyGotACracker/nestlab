#!/usr/bin/env python3
"""지침 문서의 끊어진 경로·절 참조와 금지 패턴을 검사한다.

사용법: 저장소 루트나 다른 위치에서 실행한다.
    python tools/check_doc_rule.py
    python tools/check_doc_rule.py --hook   (PostToolUse hook: stdin hook 입력의 변경 파일만 검사)

검사1: 백틱 안의 저장소 경로가 있는지 확인한다.
       `<...>` 자리표시자가 있는 경로는 건너뛴다.
검사2: "`<제목>` 절" 참조가 대상 파일의 제목과 일치하는지 확인한다.
       대상 파일은 같은 줄 앞쪽에 적힌 파일이다.
       적힌 파일이 없으면 현재 파일, SECTION_EXTRA_FILES, SCHEMA_PATTERN 파일에서 찾는다.
       제목은 코드 블록 밖에서 읽는다.
       SCHEMA_PATTERN 파일은 예시 코드 블록이 절 이름을 정의하므로 코드 블록 안에서도 읽는다.
검사3: discord/prompts 파일에서 ASCII 단어 경계의 Rule ID 패턴 [AHT]\\d{3}을 찾는다.
검사4: 부정형 표현(않는다|하지 마|금지)을 찾는다.
검사5: 첫 구간이 DIRECT_PATH_ROOTS에 속한 백틱 경로를 찾는다.
       공백으로 나눈 토큰마다 검사하므로 명령 안의 경로도 포함한다.
       PATH_LIST_FILE의 PATH_LIST_SECTION 안에서만 허용한다.
       SKILL.md에서는 첫 구간이 해당 Skill의 폴더이면 허용한다.
검사6: 다른 지침 파일 참조를 찾는다.
       같은 줄에 .md 경로를 적고 그 파일의 절을 부르는 참조와 CROSS_REF 표현이 대상이다.
       같은 파일 절과 양식 파일 절 참조는 허용한다.

검사 대상 파일은 TARGET_PATTERNS로 찾는다.
결과는 파일별로 출력한다.
위반은 `파일:행` 형식으로 출력한다.
일치하는 파일이 없는 패턴은 `없음:`으로 출력한다.
위반이나 없는 대상 파일이 하나라도 있으면 종료 코드 1을 반환한다.

설정과 패턴은 같은 폴더의 config.py에 있다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# 같은 이름 폴더의 모듈을 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_doc_rule"))

from rules import check_file, find_targets
from config import KINDS
from paths import build_name_index
from config import ROOT

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


# PostToolUse hook 입력의 변경 파일 중 검사 대상만 검사한다.
# 위반이 있으면 {"decision": "block", "reason": ...}을 출력한다. 오류는 통과한다.
def hook_main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.path.insert(0, str(HOOKS))
        from sub_write import targets as write_targets
        data = json.load(sys.stdin)
        paths = write_targets(data) or []
        wanted, _ = find_targets()
        rels = []
        for path in paths:
            try:
                rels.append(path.resolve().relative_to(ROOT.resolve()).as_posix())
            except ValueError:
                continue
        rels = [r for r in rels if r in wanted]
        if not rels:
            return 0
        names = build_name_index()
        found = [v for r in rels for v in check_file(r, names, {})]
    except Exception:
        return 0
    if found:
        lines = [f"{r}:{n}: [{k}] {d}" for r, n, k, d in found[:20]]
        reason = "\n".join(["지침 검사 위반을 고치십시오.", *lines])
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    return 0


# 모든 대상 파일을 검사하고 파일별 결과와 합계를 출력한다.
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    names = build_name_index()
    heading_cache: dict[Path, set[str]] = {}
    violations: list[tuple[str, int, str, str]] = []
    targets, missing = find_targets()

    for rel in targets:
        file_violations = check_file(rel, names, heading_cache)
        violations.extend(file_violations)
        print(f"== {rel}: 위반 {len(file_violations)}")
        for _, lineno, kind, detail in file_violations:
            print(f"{rel}:{lineno}: [{kind}] {detail}")

    print()
    for pattern in missing:
        print(f"없음: {pattern}")
    counts = Counter(kind for _, _, kind, _ in violations)
    for kind in KINDS:
        print(f"{kind}: {counts.get(kind, 0)}")
    print(f"대상 파일 없음: {len(missing)}")
    total = len(violations) + len(missing)
    print(f"검사 파일: {len(targets)}, 위반 합계: {total}")

    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(hook_main() if "--hook" in sys.argv[1:] else main())
