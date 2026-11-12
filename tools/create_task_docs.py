#!/usr/bin/env python3
"""독립 양식으로 작업 문서를 생성하거나 보고 양식을 출력한다.

입력: 작업 ID, 제목, 단계와 출력 프로젝트 또는 출력할 양식 이름.
반환: 새로 생성한 문서 경로 목록 또는 보고 양식 원문.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "common"))
from sub_docs import RESPONSE_TEMPLATES, template_text
from sub_task_docs import STAGES, TASK_ID, create


# CLI 입력으로 문서를 생성하고 새 파일 경로를 출력한다.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", dest="task_id")
    parser.add_argument("--title")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--print-template", choices=RESPONSE_TEMPLATES)
    args = parser.parse_args()
    if args.print_template:
        if args.task_id or args.title or args.stage or args.root:
            parser.error("양식 출력에는 생성 인자를 함께 사용할 수 없습니다.")
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
