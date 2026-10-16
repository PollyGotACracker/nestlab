# Claude Code 연결

[공통 설치](setup.md)를 마친 뒤 저장소 폴더에서 진행한다.

## 전역 지침 연결

```bash
# 전역 지침 import 줄을 추가한다
grep -qsF "@$(pwd -W)/AGENTS.global.md" ~/.claude/CLAUDE.md || echo "@$(pwd -W)/AGENTS.global.md" >> ~/.claude/CLAUDE.md
```

- 저장소를 옮겼다면 이전 위치의 `AGENTS.global.md` import 줄을 지운다.
