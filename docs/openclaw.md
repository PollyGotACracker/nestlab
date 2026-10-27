---
title: Nestlab 설정 OpenClaw 연결
description: "공통 설치 뒤 OpenClaw에 nestlab 스킬을 연결하는 절차이다."
type: document
created: 2026-10-03
modified: 2026-10-16
---

# OpenClaw 연결

[공통 설치](setup.md)를 마친 뒤 저장소 폴더에서 진행한다.

## 에이전트 등록

- 역할 에이전트는 자기 workspace의 `AGENTS.md`와 `SOUL.md`만 읽는다.
  그래서 역할 workspace에 전역 지침과 역할 지침을 합친 `AGENTS.md`를 둔다.

```bash
# 역할 에이전트를 ~/.openclaw/workspace-<역할> workspace로 등록한다
openclaw agents add <역할> --workspace "$(cygpath -m ~/.openclaw/workspace-<역할>)"
```

```bash
# 전역 지침과 역할 지침을 합친 AGENTS.md와 persona를 역할 workspace에 넣는다
mkdir -p ~/.openclaw/workspace-<역할>
cat AGENTS.global.md ".claude/agents/<역할>/AGENTS.md" > ~/.openclaw/workspace-<역할>/AGENTS.md
cp ".claude/agents/<역할>/SOUL.md" ~/.openclaw/workspace-<역할>/SOUL.md
```

```bash
# 등록한 에이전트를 확인한다
openclaw agents list --bindings
```

- `<역할>`은 `.claude/agents/` 아래 폴더 이름이다.
- `AGENTS.global.md`, 역할 `AGENTS.md`, `SOUL.md`를 고치면 두 번째 명령을 다시 실행한다.
  cf. [OpenClaw multi-agent](https://docs.openclaw.ai/concepts/multi-agent), [OpenClaw agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)

## 전역 지침 연결

```bash
# 기본 에이전트 workspace에 전역 지침 symlink를 만든다
MSYS=winsymlinks:nativestrict ln -s "$(pwd -W)/AGENTS.global.md" ~/.openclaw/workspace/AGENTS.md
```

- 이 연결은 기본 에이전트(`main`)에 적용된다.
  역할 에이전트는 [에이전트 등록](#에이전트-등록)의 합친 `AGENTS.md`로 전역 지침을 읽는다.
- `File exists`가 나오면 기존 `~/.openclaw/workspace/AGENTS.md`를 확인해 지운 뒤 다시 실행한다.
- `Operation not permitted`가 나오면 Windows 개발자 모드를 켜고 Git Bash를 새로 연다.
- sandbox 모드는 workspace 밖을 가리키는 symlink를 복사하지 않는다.
  sandbox를 쓰면 `ln -s` 대신 `cp`로 복사본을 둔다.
