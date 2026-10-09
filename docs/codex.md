# Codex 연결

## 구성된 설정

설정 원본과 권한 안내는 [shared/settings/codex/](/shared/settings/codex/)이다.
사용자가 `config.example.toml`로 만든 `config.toml`과 `rules/default.rules`를 `~/.codex/`에 직접 반영한다.

### hooks와 에이전트

`SessionStart`와 `SubagentStart`에서 에이전트의 Role·Persona를 주입한다.
공통 지침은 `AGENTS.md`를 전역 `~/.codex/AGENTS.md`에 연결한다.

에이전트 정의는 `.codex/agents/<이름>.toml`에 있다.

Hook 실행 오류 시 검사는 통과한다.
director(rio)는 사용자 메시지의 마지막 줄이 승인 명령으로 끝날 때만 하위 에이전트를 호출할 수 있다.
승인 명령은 `승인`·`진행`·`그래`·`응`과 `구현해`·`진행해` 같은 실행 동사이고, 바로 앞에 `안`·`못`이 오면 승인이 아니다.
실행 승인된 작업의 `작업 종류: 재작업` 호출은 예외이다.

## 전역 설정 설치

저장소 루트에서 실행한다.

```sh
# ~/.bashrc에 codex agent 함수 블록을 추가하고, 설정 예제의 <NESTLAB>·<PYTHON>을 채워 config.toml을 만든다.
python tools/set_hooks.py install
```

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

bash에서 실행한다.
macOS에서 bash를 기본 셸로 쓰면 `~/.bash_profile`에 `source ~/.bashrc`를 추가한다.

```sh
codex agent rio
```

설치된 함수가 `NESTLAB_AGENT`를 설정하고 Codex를 실행한다.
bash 실행 함수는 `--no-daemon`을 기본으로 붙인다.

### 서브에이전트 호출

대화에서 에이전트를 지정해 요청한다.

```text
nico 에이전트로 요구사항을 조사해 주세요.
jelly 에이전트로 승인된 범위를 구현해 주세요.
ricky 에이전트로 구현 결과를 검수해 주세요.
ricky 에이전트로 조사 주장을 독립 반증해 주세요.
pepper 에이전트로 작업 결과를 기록해 주세요.
```
