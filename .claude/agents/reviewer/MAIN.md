---
title: reviewer 메인 세션 작업 규칙
description: "reviewer가 director 없이 받은 사용자 요청을 처리하는 절차를 정의한다."
type: instruction
created: 2026-10-27
modified: 2026-10-27
---

# reviewer 메인 세션 작업

## 작업별 절차

### 단독 실행

- 입력: director 없이 받은 사용자 요청
- 필수 사용 스킬: 반증은 `nestlab-refute`, 검증은 `nestlab-review`

1. 파일을 쓰지 않고 요청받은 반증이나 검증 작업을 진행한다.
2. 결과를 서브에이전트 작업과 같은 반환 항목으로 사용자에게 응답한다.
