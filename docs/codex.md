# Codex 연결

[공통 설치](setup.md)를 마친 뒤 저장소 폴더에서 진행한다.

## 전역 지침 연결

```bash
# 전역 지침 symlink를 만든다
MSYS=winsymlinks:nativestrict ln -s "$(pwd -W)/AGENTS.global.md" ~/.codex/AGENTS.md
```

- `File exists`가 나오면 기존 `~/.codex/AGENTS.md`를 확인해 지운 뒤 다시 실행한다.
- `Operation not permitted`가 나오면 Windows 개발자 모드를 켜고 Git Bash를 새로 연다.
