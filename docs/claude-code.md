# Claude Code 연결

## 구성된 설정

설정 원본은 `tools/set_hooks/sub_settings.py`이다.

| 항목                     | 반영 위치                 |
| ------------------------ | ------------------------- |
| 기본 에이전트·env·상태줄 | `~/.claude/settings.json` |
| 권한·hooks               | `~/.claude/settings.json` |
| 공통 지침 import         | `~/.claude/CLAUDE.md`     |
| 에이전트 정의 연결       | `~/.claude/agents`        |
| Git Bash 실행 함수       | `~/.bashrc`               |

### hooks와 상태줄

- 상태줄: `Agent: <이름>` 표시
- Windows Orca: 기존 상태줄 스크립트가 있으면 상태 입력 전달

Hook 실행 오류 시 검사는 통과한다.
director(rio)는 사용자 메시지의 마지막 줄이 승인 명령으로 끝날 때만 하위 에이전트를 호출할 수 있다.
승인 명령은 `승인`·`진행`·`그래`·`응`과 `구현해`·`진행해` 같은 실행 동사이고, 바로 앞에 `안`·`못`이 오면 승인이 아니다.
실행 승인된 작업의 `작업 종류: 재작업` 호출은 예외이다.

### env

`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`는 `2`이다.

### 권한

- [관련 파일 참고](/tools/set_hooks/sub_settings.py)

## 전역 설정 설치

저장소 루트에서 실행한다.
Python 3.11 이상이 필요하다.
macOS에서는 `python` 대신 `python3`를 쓴다.

```sh
python -B tools/set_hooks.py install --dry-run
python -B tools/set_hooks.py install
python -B tools/set_hooks.py check
```

설정 원본을 수정한 뒤 같은 install 명령으로 재반영한다.
관리 밖 사용자 설정과 Orca hook은 보존한다.
설치 기록과 백업 경로는 `~/.brainstack/install-record.json`에 남는다.

제거 명령:

```sh
python -B tools/set_hooks.py uninstall
```

설치 기록의 관리 항목을 제거한다.
관리한 agent·env·statusLine 값은 제거하며 이전 값을 복원하지 않는다.

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

### 메인 에이전트 지정

```sh
claude --agent rio
```

bash에서는 다음 명령도 사용할 수 있다.
macOS에서 bash를 기본 셸로 쓰면 `~/.bash_profile`에 `source ~/.bashrc`를 추가한다.

```sh
claude agent rio
```

### 서브에이전트 호출

대화에서 에이전트를 지정해 요청한다.

```text
@agent-nico 요구사항을 조사해 주세요.
@agent-jelly 승인된 범위를 구현해 주세요.
@agent-ricky 구현 결과를 검수해 주세요.
@agent-ricky 조사 주장을 독립 반증해 주세요.
@agent-pepper 작업 결과를 기록해 주세요.
```
