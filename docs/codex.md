# Codex 연결

## 구성된 설정

전역 설치 원본은 `tools/set_hooks/sub_settings.py`이다.

| 항목               | 반영 위치                      |
| ------------------ | ------------------------------ |
| Role·Persona hooks | `~/.codex/hooks.json`          |
| hooks 활성화       | `~/.codex/config.toml`         |
| 실행 권한          | `~/.codex/rules/default.rules` |
| 공통 지침 연결     | `~/.codex/AGENTS.md`           |
| 에이전트 정의 연결 | `~/.codex/agents`              |
| Git Bash 실행 함수 | `~/.bashrc`                    |

### 승인과 실행 정책

이 저장소의 `.codex/config.toml` 설정:

```toml
approval_policy = "on-request"
approvals_reviewer = "auto_review"

[features]
hooks = true
```

전역 실행 권한:

- 승인 요청:
  `git checkout`, `git switch`, `git restore`, `git stash`, `git tag`, `rm`, `rmdir`
- 금지:
  `git commit`, `git push`, `git merge`, `git rebase`, `git reset`,
  `git revert`, `git cherry-pick`, `git clean`

이 실행 규칙은 샌드박스 밖에서 실행하는 명령에 적용한다.

### hooks와 에이전트

`SessionStart`와 `SubagentStart`에서 에이전트의 Role·Persona를 주입한다.
공통 지침은 `AGENTS.md`를 전역 `~/.codex/AGENTS.md`에 연결한다.

에이전트 정의는 `.codex/agents/<이름>.toml`에 있다.

Hook 실행 오류 시 검사는 통과한다.
원본 구현은 `hooks/check_tool_use.py`(기능별 모듈은 `hooks/check_tool_use/`), `hooks/check_refute_verdict.py`, `hooks/save_agent_result.py`, `tools/check_doc_rule.py`에서 확인한다.
director(rio)는 사용자 마지막 메시지에 `승인`이 있을 때만 하위 에이전트를 호출할 수 있다. 실행 승인된 작업의 `작업 종류: 재작업` 호출은 예외이다.
director의 파일 쓰기는 `log/state/`·`log/incident/`로 한정하고, researcher·reviewer는 쓰기를 막는다.
Codex는 `spawn_agent`에 PreToolUse를 실행하지 않는다([openai/codex#49736](https://github.com/openai/codex/issues/49736)). 그래서 승인 검사는 하위 thread의 도구 호출에서 부모 thread의 사용자 메시지로 한다.

## 전역 설정 설치

저장소 루트에서 실행한다.

```sh
python -B tools/set_hooks.py install --dry-run
python -B tools/set_hooks.py install
python -B tools/set_hooks.py check
```

설정 원본을 수정한 뒤 같은 install 명령으로 재반영한다.
관리 밖 사용자 설정과 Orca hook은 보존한다.
설치 기록과 백업 경로는 `~/.brainstack/install-record.json`에 남는다.

전역 `[features] hooks` 키가 없으면 install이 `true`를 추가한다.
기존 값이 `false`이면 직접 `true`로 바꿔야 Role·Persona hook이 실행된다.
`~/.codex/AGENTS.override.md`가 있으면 install이 중단된다.

제거 명령:

```sh
python -B tools/set_hooks.py uninstall
```

설치 기록의 관리 항목을 제거한다.
설치 전 파일은 기록 폴더의 백업에 남는다.

## 에이전트 전역 연결

저장소 루트에서 실행한다.

```sh
python -B tools/set_agents.py link
python -B tools/set_agents.py check
```

`~/.claude/agents`와 `~/.codex/agents`를 저장소의 에이전트 정의에 연결한다.
기존 폴더나 다른 연결과 충돌하면 해당 항목을 보존하고 충돌을 표시한다.

제거 명령:

```sh
python -B tools/set_agents.py unlink
```

이 저장소의 에이전트 정의를 가리키는 링크만 제거한다.
원본 폴더가 없어진 링크도 제거한다.
기존 폴더나 다른 연결과 충돌하면 해당 항목을 보존하고 충돌을 표시한다.
저장소의 원본 폴더 내용은 남는다.

## 사용

### 메인 역할 지정

Git Bash에서 실행한다.

```sh
codex agent rio
```

설치된 함수가 `BRAINSTACK_AGENT`를 설정하고 Codex를 실행한다.
Git Bash의 실행 함수는 `--no-daemon`을 기본으로 붙인다.

PowerShell에서는 환경변수를 지정한다.

```powershell
$env:BRAINSTACK_AGENT = "rio"
codex
```

이 설정은 같은 PowerShell 창의 후속 실행에도 적용된다.
기본 선택으로 돌아가려면 환경변수를 제거한다.

```powershell
Remove-Item Env:BRAINSTACK_AGENT
```

### 서브에이전트 호출

대화에서 에이전트를 지정해 요청한다.

```text
nico 에이전트로 요구사항을 조사해 주세요.
jelly 에이전트로 승인된 범위를 구현해 주세요.
ricky 에이전트로 구현 결과를 검수해 주세요.
ricky 에이전트로 조사 주장을 독립 반증해 주세요.
pepper 에이전트로 작업 결과를 기록해 주세요.
```

### 호출 입력 형식

대화의 호출 요청을 실제 서브에이전트 입력으로 전달할 때 다음 형식을 사용한다.
researcher는 `작업 종류: 조사` 줄과 조사 문서 경로 한 줄을 받는다.

```text
작업 종류: 조사
상태 문서: log/state/<작업-id>.md
```

worker 구현과 reviewer 일반 검수는 `작업 종류` 줄과 입력 문서 경로 한 줄을 받는다.

```text
작업 종류: 구현
입력 문서: log/state/<작업-id>-input.md
```

reviewer의 독립 반증은 다음 입력을 받는다.
`작업 종류: 반증` 표식은 정확히 한 번, 비어 있지 않은 `주장:` 필드는 한 번 이상 필요하다.
나머지 허용 필드는 `증거:`, `출처:`, `판정 기준:`, `원문 발췌:`이다.
각 필드 값은 한 줄이며, 여러 줄 발췌는 줄바꿈을 이스케이프한 JSON 문자열로 적는다.

```text
작업 종류: 반증
주장: Claude Code 설정 원본의 하위 호출 깊이는 2이다.
증거: CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH 설정값
출처: tools/set_hooks/sub_settings.py:13
판정 기준: 원본 설정값과 주장 일치 여부
원문 발췌: "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2"
```

reviewer는 반증에 필요한 웹 검색·원문 열람을 읽기 전용으로 수행한다.
