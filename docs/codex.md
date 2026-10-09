# Codex 연결

## 구성된 설정

설정 원본과 권한 안내는 [shared/settings/codex/](/shared/settings/codex/)이다.
[공통 설치와 연결](setup.md)의 설치 도구가 `config.example.toml`에서 `config.toml`을 생성하고 설정과 `rules/default.rules`를 `~/.codex/`에 병합한다.

### hooks와 에이전트

`SessionStart`와 `SubagentStart`에서 에이전트의 Role·Persona를 주입한다.
공통 지침은 `AGENTS.md`를 전역 `~/.codex/AGENTS.md`에 연결한다.

에이전트 정의는 `.codex/agents/<이름>.toml`에 있다.

Hook 실행 오류 시 검사는 통과한다.
director(rio)는 사용자 메시지의 마지막 줄이 승인 명령으로 끝날 때만 하위 에이전트를 호출할 수 있다.
승인 명령은 `승인`·`진행`·`그래`·`응`과 `구현해`·`진행해` 같은 실행 동사이고, 바로 앞에 `안`·`못`이 오면 승인이 아니다.
실행 승인된 작업의 `작업 종류: 재작업` 호출은 예외이다.

### 작업 기록 위치

- 작업 기록의 기준은 훅 입력 `cwd`가 속한 Git 저장소 루트이다.
	- Git worktree에서는 해당 worktree 루트를 사용한다.
	- Git 저장소가 없으면 훅 입력 `cwd`를 사용한다.
- 상태·입력·조사 문서, 활성 작업 표시, 사건 기록과 조사·반증 결과는 작업 프로젝트의 `log/`를 사용한다.
- director의 기록 쓰기 예외와 승인 조회도 같은 프로젝트를 기준으로 적용한다.
- 역할 지침·Skill·양식·설정의 원본 위치는 nestlab에 유지한다.
- 기존 기록은 원래 위치에 보존한다.
- 기록 경로의 세부 처리는 [공용 기록 경로](../hooks/common/sub_docs.py)에서 확인한다.

## 설치와 연결

- 전역 설정 설치와 변경별 조치는 [공통 설치와 연결](setup.md)에서 확인한다.
- 에이전트 연결·상태 확인·제거는 [에이전트 연결](setup.md#에이전트-연결)에서 확인한다.
- Skill 연결은 [공통 설치와 연결](setup.md#skill-연결)에서 확인한다.

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
