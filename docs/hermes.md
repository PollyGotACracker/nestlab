# Hermes 연결

[공통 설치](setup.md)를 마친 뒤 저장소 폴더에서 진행한다.

## 스킬

- `~/.hermes/config.yaml`에 아래 설정을 추가한다.
  역할 profile은 이 설정을 복사해 만들므로 profile 생성 전에 추가한다.

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
```

## 에이전트 profile

- Hermes는 profile마다 `SOUL.md`를 전역 파일로 읽는다.
  그래서 persona, 역할 지침, 전역 지침을 합쳐 역할 profile의 `SOUL.md`로 만든다.

```bash
# 기본 profile의 config.yaml과 .env를 복사해 역할 profile을 만든다
hermes profile create <역할> --clone
```

```bash
# persona, 역할 지침, 전역 지침을 합쳐 역할 profile의 SOUL.md로 만든다
cat ".claude/agents/<역할>/SOUL.md" ".claude/agents/<역할>/AGENTS.md" AGENTS.global.md > ~/.hermes/profiles/<역할>/SOUL.md
```

```bash
# profile 목록을 확인한다
hermes profile list
```

- `<역할>`은 `.claude/agents/` 아래 폴더 이름이다.
- 합친 세 파일 중 하나를 고치면 두 번째 명령을 다시 실행한다.
- `--clone`은 기본 profile의 skill과 memory 파일도 복사한다.
  cf. [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles), [Hermes context files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files)
