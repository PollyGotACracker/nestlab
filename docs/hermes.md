# Hermes 연결

Hermes에서는 profile 이름을 README.md 표의 이름으로 사용한다.

Hermes는 `SOUL.md`를 작업 디렉터리에서 읽지 않는다.
각 profile의 `HERMES_HOME/SOUL.md`에서 읽는다.
따라서 persona는 profile마다 배치해야 한다.

## 공식 문서

- [Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Context files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files)
- [Profile commands](https://hermes-agent.nousresearch.com/docs/reference/profile-commands)

## profile 생성

```sh
hermes profile create buddy
hermes profile create rio
hermes profile create nico
hermes profile create jelly
hermes profile create ricky
hermes profile create pepper
```

각 명령은 독립 profile을 만든다.

## profile 위치 확인

```sh
hermes profile show buddy
hermes profile show rio
hermes profile show nico
hermes profile show jelly
hermes profile show ricky
hermes profile show pepper
```

각 명령은 해당 profile의 실제 `HERMES_HOME` 경로를 보여준다.

## persona 복사

다음 원본을 대응하는 profile의 `SOUL.md`로 복사한다.

```text
buddy  ← .claude/agents/assistant/SOUL.md
rio    ← .claude/agents/director/SOUL.md
nico   ← .claude/agents/researcher/SOUL.md
jelly  ← .claude/agents/worker/SOUL.md
ricky  ← .claude/agents/reviewer/SOUL.md
pepper ← .claude/agents/documenter/SOUL.md
```

Hermes 공식 동작상 이 복사는 필요하다. symlink는 사용하지 않는다.

## 작업 저장소 지정

각 profile의 `terminal.cwd`는 실제 작업 저장소로 설정한다.

예:

```sh
hermes -p nico config set terminal.cwd "/absolute/path/to/repository"
```

이 명령은 nico profile의 terminal 작업 시작 위치를 해당 저장소로 설정한다.

Hermes는 그 작업 디렉터리에서 `AGENTS.md` 계층을 읽는다.

## 지침 연결 확인

위 절차는 profile 생성, persona 배치와 terminal 작업 위치를 설정한다.
역할별 `AGENTS.md`와 구성 루트의 공통 Core를 각 profile의 프로젝트 지침에 연결하는 설정은 아직 포함하지 않는다.
`SOUL.md` 복사만으로 역할 지침과 공통 Core까지 로딩되는 것으로 가정하지 않는다.

실제 운영 전에는 해당 profile에서 원본 역할 지침과 `AGENTS.md`의 접근 가능한 실제 경로를 명시해 읽도록 요청한다.
예를 들어 `nico`에는 `.claude/agents/researcher/AGENTS.md`와 공통 지침 파일을 지정한다.
각 profile의 세션에서 역할·공통 지침과 persona가 함께 적용되는지 확인한다.

## Skill 연결

- 공용 Skill 링크 생성·상태 확인·제거는 [공통 설치와 연결](setup.md#skill-연결)에서 진행한다.
- Hermes는 `~/.hermes/config.yaml`에 다음 경로를 추가한다.

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
```

- Hermes 설정은 직접 추가한다.
- Hermes가 설치돼 있으면 아래 명령으로 `external_dirs` 설정도 확인한다.

```bash
# 공용 Skill 연결 상태와 Hermes external_dirs 설정을 확인한다.
python -B tools/set_skills.py check
```

- Skill 링크를 제거할 때 Hermes 설정은 직접 제거한다.
