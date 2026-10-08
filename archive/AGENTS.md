# 지식 저장소 원칙

## 작업 절차

- 파일을 생성하기 전, 원본 내용에 저작권 침해 요소가 있는 경우 사용자에게 알린다.

### 경로 기준

- `raw/`, `wiki/`, `schema/`, 위키링크는 `archive/`를 기준으로 해석한다.
- 저장소 루트 기준으로 파일 경로를 작성하면 `archive/`를 붙인다.  
  이때 위키링크는 `[[...]]` 형식을 유지한다.
- 저장소 루트를 명시한 지침 및 `.github/` 템플릿 경로는 저장소 루트를 기준으로 해석한다.

### 처리 순서

1. 현재 작업에 선택된 schema의 원본 절차를 적용한다.
2. 해당 schema의 승인·결과 보고 절차에 따라 작업을 완료한다.

## 폴더

### 구성

- `raw/`: 수집 자료의 원본
- `wiki/entities/`: 고유명사 1개당 1페이지를 배치
- `wiki/concepts/`: 개념·방법론·원리 1개당 1페이지를 배치
- `wiki/sources/`: `raw/` 파일 1개당 요약 1페이지를 배치, 생성 주체는 ingest
- `wiki/INDEX.md`: 전체 목차
- `schema/`: ingest·query·lint 절차를 배치

### 배치 기준

`entities`, `concepts`, `sources` 페이지는 파일명의 주제순 정렬이 가능하도록 배치한다.

## 문서 서식

- 이 절은 문서 파일을 작성·수정할 때 적용한다.

### 문장 작성

- 짧은 항목은 마침표 없는 명사구, 규칙·설명은 `~한다.`, `~이다.` 완결 문장으로 쓴다.
- 문장마다 핵심 하나만 담는다.
- 문서 작성 과정을 언급하는 메타 표현을 제거한다.
  단, 문서 내용에 해당하는 사용자 요구사항과 제약은 보존한다.
- 문서의 본문·목록·주석은 완결된 문장마다 줄바꿈한다.

### 구조 표현

- 핵심 단어는 굵게, 강조할 단어는 기울임으로 표시한다.
- 본문은 중첩 글머리 기호 목록을 중심으로 작성한다.
  항목 아래 들여쓴 무기호 줄로 보충 설명을 붙인다.
- 서술 문단은 도입·전환·결론에만 쓴다.
- 목록 들여쓰기는 탭 1개를 한 단계로 사용한다.
  예외로 YAML 프론트매터는 공백 2개를 사용한다.
- 예시는 `e.g.`, 참고 정보는 `cf.`로 표기하며 뒤에 내용을 한 칸 띄어 작성한다.
- 예시는 그 대상을 명시하고, 길면 언어를 지정한 코드 블록으로 작성한다.
- 소제목은 4어절 이하의 명사구로 작성한다.
- 폴더 구조는 tree 형태로 작성한다.
  - `├──`, `└──`, `│`
- 인물 발언은 인용 블록 마지막 줄에 `— 이름, 소속(직책)`을 붙인다.
- 표의 머리글은 모두 굵게 한다.
  짧은 값 열은 가운데 정렬, 설명 열은 왼쪽 정렬한다.
- 커맨드, 프롬프트, 템플릿은 코드 블록으로 작성한다.
- 전체 흐름은 코드 블록 안에 `→` 또는 `↔`로 이어 쓴다.
- 이미지는 `![설명](파일명.확장자)` 형식이다.
- 링크는 `[표시 이름](URL)` 형식이다.

### 공개 문서

- 외부 공개 문서의 프로젝트 구조·경로는 개인 폴더명을 임의 폴더명으로 대체한다.

## 파일명

- `wiki/INDEX.md`는 관리 파일명을 유지하고 아래 페이지 명명 규칙의 적용 대상에서 제외한다.
- 페이지 파일명은 영문 소문자·숫자·하이픈만으로 kebab-case로 구성한다.
- 대주제는 파일명의 첫 단어이며 파일명에는 대상을 구별하는 핵심 단어만 포함한다.
  같은 세부 주제의 페이지는 대주제의 이름순 정렬에서 함께 배치한다.

```text
wiki/entities/ai-harness-codex.md
wiki/entities/ai-loop-claude-agent-sdk.md
wiki/concepts/ai-harness-engineering.md
wiki/concepts/ai-loop-ralph-wiggum.md
wiki/concepts/ai-prompt-caching.md
wiki/sources/ai-harness-long-running-agents.md
```

## 프론트매터

### raw

```yaml
---
title: 제목
date: YYYY-MM-DD
---
```

- `title`: 문서 제목
- `date`: 문서 마지막 편집 시각

### wiki

`wiki/INDEX.md`를 제외한 모든 위키 페이지에는 아래 프론트매터를 포함한다.

```yaml
---
aliases: []
title: 제목
date: YYYY-MM-DD
tags: [wiki, 분류]
description: ""
draft: false
related: # 선택
  - "[[wiki/concepts/ai-harness-engineering]]"
  - "[[wiki/entities/ai-harness-codex]]"
---
```

- `aliases`:문서 제목 외의 별칭
- `title`: 문서 제목
- `date`: 문서 마지막 편집 시각
- `tags`: 문서 분류 태그 목록
- `description`: 문서의 짧은 설명
- `draft`: 초안 여부
- `related`(선택 항목): 관련 문서 링크 목록  
  위키링크를 따옴표로 감싸고 글머리 기호를 적용하며, 공백 2개를 사용한다.
  목적은 Obsidian의 YAML 인식이다.

## 링크

### 기본 형식

- 위키링크는 항상 전체 경로로 작성하고 별칭은 필요한 경우 첨부한다.

```markdown
[[wiki/concepts/ai-harness-engineering|Harness engineering]]
[[wiki/concepts/ai-loop-ralph-wiggum|Ralph Wiggum loop]]
[[wiki/sources/ai-harness-long-running-agents|Long-running agents]]
```

### 외부 링크

- 외부 URL은 `[표시 이름](URL)` 형식의 마크다운 링크로 작성한다.
- 표시 이름은 서비스명 또는 `소유자/저장소`이다.
- 코드 블록 안의 원문 URL은 원문 보존을 위해 변환에서 제외한다.

```markdown
- [TypeSafe](https://typesafe.ai/)
- [PollyGotACracker/brainstack](https://github.com/PollyGotACracker/brainstack)
```

### 표 안 링크

- 표 안 링크는 별칭 구분자 `|`를 백슬래시 하나로 이스케이프해 한 셀로 표시한다.

```markdown
| [[wiki/concepts/ai-prompt-caching\|Prompt caching]] | 반복되는 프롬프트 앞부분을 캐시한다 |
```

## Branch

- 기본 브랜치는 `master`이다.
- 브랜치 생성 규칙은 `<타입>/이슈번호`이다.

## Commit

- `.github/`의 템플릿 파일을 참고한다.
- 커밋 단위는 승인된 변경 세트이다.
- 메시지 형식은 `<타입>: <제목>`이다.
- 허용 타입은 `feat`, `fix`, `chore`, `docs`, `refactor` 이다.
- 제목 언어는 한국어이다.
- 제목 길이는 40자 이내이다.
- 본문 규칙은 아래 형식을 따른다.

```text
<타입>: <제목> (제목은 40자 이내)

<본문> (한 줄 띄우고 작성. 72자 이내로 줄 바꿈)

Resolves: #<이슈번호>(생략 가능)
See also: #<이슈번호>(생략 가능)
```
