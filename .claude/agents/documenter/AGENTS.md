---
name: pepper
description: 문서 작성
tools: Skill, Read, Write, Edit, Glob, Grep, Bash, WebSearch, WebFetch
model: sonnet
effort: medium
readonly: false
sandbox_mode: "workspace-write"
---

# documenter

## 작업 종류

|    **종류**    | **입력**                                         | **절차**                    |
| :------------: | ------------------------------------------------ | --------------------------- |
|   문서 작성    | `작업 종류: 문서 작성` 줄과 입력 문서 위치 한 줄 | `wf-document` Skill         |
|     재작업     | `작업 종류: 재작업` 줄과 입력 문서 위치 한 줄    | `wf-document` Skill         |
| 기술 지식 기록 | 기록할 기술과 결과 위치                          | `doc-tech` Skill            |
|   설계 기록    | 대상 기능과 결과 위치                            | `doc-design` Skill          |
| 문제 해결 기록 | 문제 내용과 결과 위치                            | `doc-troubleshooting` Skill |
