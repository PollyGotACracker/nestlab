---
title: Nestlab 설정 Claude Code 연결
description: "공통 설치 뒤 Claude Code에 nestlab 지침, 스킬, hook을 연결하는 절차이다."
type: document
created: 2026-10-03
modified: 2026-10-16
---

# Claude Code 연결

[공통 설치](setup.md)를 마친 뒤 저장소 폴더에서 진행한다.

## 전역 지침 연결

```bash
# 전역 지침 import 줄을 추가한다
grep -qsF "@$(pwd -W)/AGENTS.global.md" ~/.claude/CLAUDE.md || echo "@$(pwd -W)/AGENTS.global.md" >> ~/.claude/CLAUDE.md
```

- 저장소를 옮겼다면 이전 위치의 `AGENTS.global.md` import 줄을 지운다.
