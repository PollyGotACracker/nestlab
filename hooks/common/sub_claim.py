"""출처 없이 측정이나 확인을 주장한 문장을 찾는 공용 함수이다.

호출자:
- hooks/check_workflow_response.py
- hooks/check_tool_use/sub_question.py
입력: 검사할 전체 텍스트(message)와 코드 블록 및 인용 줄을 뺀 본문(body)
반환: unsourced_claims는 출처 없는 주장 문장 목록
한계: 출처가 주장을 실제로 뒷받침하는지는 판정하지 못한다.
"""
from __future__ import annotations

import re

PATH = re.compile(
    r"(?:[\w.@~-]+[\\/])+[\w.@-]+|\b[\w.-]+\.(?:md|py|jsonl?|ya?ml|toml|txt|csv|log|js|tsx?|html|css)\b")
URL = re.compile(r"https?://\S+")
INLINE = re.compile(r"`[^`\n]+`")
SOURCE = re.compile(PATH.pattern + "|" + URL.pattern + "|" + INLINE.pattern + r"|\[추론\]")
CLAIM = re.compile(r"호출됐|호출되었|확인했|측정했|동작합니다|동작해요|동작했|통과했")
NEGATION = re.compile(r"하지 않|않았|못 |못했|없")
# 불가 주장: 부정형 능력 주장
INACCESSIBLE = re.compile(r"(?:볼|읽을|열|접근할|확인할|찾을|조회할|할) 수 없|불가능")
QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|'[^'\n]*'|‘[^’\n]*’")

REASON_SOURCE = ("측정이나 확인을 주장한 문장 '{sentences}'에 근거가 없습니다.\n"
                 "근거의 출처(파일:행, 파일 경로, 링크, 명령 결과 인용)를 포함하여 다시 작성하십시오.")


# 출처 없이 측정이나 확인을 주장한 긍정형 문장과 출처 없는 불가 주장 문장 목록을 반환한다.
def unsourced_claims(message: str, body: str) -> list[str]:
    split = lambda text: [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    sentences = split(body)

    # 불가 주장은 응답 다른 곳의 출처로 대신할 수 없어 같은 문단(빈 줄로 나뉜 줄 묶음) 안에 출처가 있어야 한다.
    # 따옴표로 인용한 예시는 주장이 아니므로 인용 구간을 뺀 문장으로 판정한다.
    blind = [s for paragraph in re.split(r"\n\s*\n", body) if not SOURCE.search(paragraph)
             for s in split(paragraph) if INACCESSIBLE.search(QUOTED.sub("", s))]

    # 긍정형 주장의 출처 판정 단위는 응답 전체이다. 출처는 코드 블록과 인용을 뺀 본문 앞의 원문에서도 찾는다.
    if SOURCE.search(message):
        return blind
    return blind + [s for s in sentences if CLAIM.search(s) and not NEGATION.search(s)]
