#!/usr/bin/env python3
"""독립 템플릿으로 작업 문서를 생성하거나 보고 템플릿을 출력한다."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks" / "common"))
from sub_docs import RESPONSE_TEMPLATES, template_text
from sub_paths import TASK_REL
from sub_task_docs import STAGES, TASK_ID, approval_text, create
from sub_task_index import refresh_index


# 작업 문서로 구현 승인 요청 응답 본문을 생성한다. 입력은 템플릿 이름, 프로젝트 경로와 작업 ID이다.
# 본문은 작업 문서 링크와 명세 요구사항 항목의 제목, 목표, 기대 결과, 한계, 잠재 위험 원문뿐이다.
def render(form: str, root: Path, task: str) -> str:
    return approval_text(root / TASK_REL / task / "status.md")


# CLI 입력으로 문서를 생성하고 새 파일 경로를 출력한다.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", dest="task_id")
    parser.add_argument("--title")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--print-template", choices=RESPONSE_TEMPLATES)
    parser.add_argument("--select", action="store_true", help="기존 작업을 색인의 현재 진행 작업으로 지정한다.")
    args = parser.parse_args()
    if args.select:
        # 작업 단계는 바꾸지 않고 색인의 현재 진행 작업만 지정한다.
        if not args.task_id or args.title or args.stage or args.print_template:
            parser.error("--select에는 --task만 함께 사용할 수 있습니다.")
        root = args.root or Path.cwd()
        if not TASK_ID.fullmatch(args.task_id) or not (root / TASK_REL / args.task_id / "status.md").is_file():
            parser.error(f"경과 문서가 없는 작업입니다: {args.task_id}")
        try:
            print(refresh_index(root, args.task_id, current=True))
        except (ValueError, OSError) as error:
            parser.error(str(error))
        return 0
    if args.print_template:
        if args.title or args.stage:
            parser.error("템플릿 출력에는 생성 인자를 함께 사용할 수 없습니다.")
        # 작업 ID가 있으면 승인 요청 템플릿의 원문 절을 작업 문서로 채워 출력한다.
        if args.task_id:
            if args.print_template != "approval-implement":
                parser.error("작업 문서로 채우는 템플릿은 approval-implement뿐입니다.")
            try:
                print(render(args.print_template, args.root or Path.cwd(), args.task_id))
            except (OSError, TypeError, ValueError) as error:
                parser.error(f"작업 문서를 읽을 수 없습니다: {error}")
            return 0
        print(template_text(args.print_template))
        return 0
    if not args.task_id or not args.title or not args.stage:
        parser.error("문서 생성에는 --task, --title, --stage가 필요합니다.")
    try:
        for path in create(args.root or Path.cwd(), args.task_id, args.title, args.stage):
            print(path)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
