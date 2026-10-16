# Cursor 연결

[공통 설치](setup.md)를 마친 뒤 진행한다.

## 에이전트

- 저장소를 Cursor에서 열고 subagent 목록에 역할 에이전트가 보이는지 확인한다.
	cf. [Cursor subagents](https://cursor.com/docs/subagents)

## hook

- Cursor에서 nestlab hook이 동작하는지 확인한다.
- 동작하지 않으면 `~/.cursor/hooks.json`에 직접 등록한다.
	cf. [Cursor hooks](https://cursor.com/docs/agent/hooks)

## 전역 지침 연결

- `AGENTS.global.md` 내용을 설정의 Rules > User Rules에 붙여 넣는다.
- `AGENTS.global.md`를 고치면 다시 붙여 넣는다.
