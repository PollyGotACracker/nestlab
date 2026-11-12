---
title: Nestlab 설정 공통 설치
description: "도구별 연결 전 모든 환경에서 수행하는 nestlab 설정 공통 설치 절차이다."
type: document
created: 2026-10-10
modified: 2026-11-12
---

# 공통 설치

공통 설치를 마친 뒤 [도구별 연결](#도구별-연결)로 넘어간다.

## 준비 사항

- Python 3.11 이상
- Bash
  Windows는 Git Bash를 쓴다.
- 연결할 도구 하나 이상 설치
  설치 및 연결 커맨드는 설정 폴더(`~/.claude`, `~/.codex` 등)가 없는 도구를 건너뛰므로, 도구를 한 번 실행한 뒤 진행한다.

## 저장소 받기

```bash
# 저장소를 nestlab 폴더로 받는다. 이후 커맨드는 이 폴더에서 실행한다
git clone https://github.com/PollyGotACracker/nestlab.git nestlab
cd nestlab
```

## 전역 설정 설치

```bash
# bashrc 실행 함수, Claude Code 설정과 hook, Codex 설정과 hook, Codex 커맨드 규칙을 설치한다
python tools/set_hooks.py install
# 실행 함수를 현재 셸에 적용한다
source ~/.bashrc
```

| **설치 위치**                  | **들어가는 내용**                                                                        |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| `~/.bashrc`                    | `claude agent`, `codex agent` 실행 함수                                                  |
| `~/.claude/settings.json`      | 기본 에이전트, 권한, hook, 상태줄                                                        |
| `~/.codex/config.toml`         | 샌드박스와 승인 정책, hook, hooks 기능 활성화, Windows 샌드박스(MXC 우선, elevated 대체) |
| `~/.codex/rules/default.rules` | 커맨드 허용 규칙(읽기 커맨드 포함)                                                       |
| `~/.local/bin/`                | 작업 문서 생성 커맨드 `nestlab-task-docs`와 Windows용 `.cmd` 파일                        |

- `~/.local/bin/`을 PATH에 포함하고 설치 출력의 커맨드 해석 결과를 확인한다.
- 같은 저장소 폴더에서 다시 실행하면 결과가 같다.
- 설치 원본에 없는 키와 목록 항목은 그대로 두고, 원본과 같은 키는 원본 값으로 덮어쓴다.
  `~/.codex/config.toml`의 `approvals_reviewer` 줄은 지운다.
- 다른 폴더에 새로 clone해 설치하면 이전 폴더의 hook 항목이 `~/.claude/settings.json`에 남는다.

## 에이전트 연결

```bash
# ~/.claude/agents, ~/.codex/agents에 저장소 에이전트 폴더를 연결한다
python tools/set_agents.py link
```

- 같은 경로에 다른 파일이나 폴더가 있으면 건드리지 않고 `충돌`로 표시한다.
- Windows에서 symlink 권한이 없으면 junction을 만든다.

## 스킬 연결

```bash
# ~/.claude/skills, ~/.agents/skills에 공용 스킬을 연결한다
python tools/set_skills.py link
```

- `~/.agents/skills`는 Codex, Cursor, OpenClaw가 함께 읽는다.

## 도구별 연결

- 전역 지침 연결과 도구 고유 설정은 각 문서를 따른다.
  - [Claude Code](claude-code.md)
  - [Codex](codex.md)
  - [Cursor](cursor.md)
  - [OpenClaw](openclaw.md)
  - [Hermes](hermes.md)

## 연결 확인

```bash
# 에이전트 연결 상태를 출력한다
python tools/set_agents.py check
# 스킬 연결, 중복, Hermes 설정 상태를 출력한다
python tools/set_skills.py check
```

## 연결 해제

```bash
# 이 저장소를 가리키는 링크만 지운다
python tools/set_agents.py unlink
python tools/set_skills.py unlink
```
