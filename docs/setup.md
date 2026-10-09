# 공통 설치와 연결

Claude Code와 Codex의 전역 설정 설치 및 에이전트 연결 절차이다.
저장소 루트의 Git Bash에서 실행한다.
Python 3.12 이상이 필요하다.

## 전역 설정 설치

1. [설정 설치 도구](../tools/set_hooks.py)를 실행한다.

```bash
# ~/.bashrc의 claude agent·codex agent 함수와 두 도구의 전역 설정을 설치한다.
python tools/set_hooks.py install
```

2. 실행할 Git Bash에서 설치된 함수를 읽는다.

```bash
# 현재 셸에서 갱신된 함수를 사용한다.
source ~/.bashrc
```

- **설치 입력**은 아래 네 파일이다.

| **설치 입력** | **반영 위치** |
| :--- | :--- |
| [bashrc.sh](../shared/settings/bashrc.sh) | `~/.bashrc` |
| [settings.example.json](../shared/settings/claude/settings.example.json) | `~/.claude/settings.json` |
| [config.example.toml](../shared/settings/codex/config.example.toml) | `~/.codex/config.toml` |
| [default.rules](../shared/settings/codex/rules/default.rules) | `~/.codex/rules/default.rules` |

- **설정 예제**의 `<NESTLAB>`·`<PYTHON>`은 저장소 경로와 실행한 Python 경로로 채운다.
	- `shared/settings/claude/settings.json`과 `shared/settings/codex/config.toml`을 생성한 뒤 전역 설정에 병합한다.
	- 생성 파일을 직접 수정하면 다음 `install`에서 덮어쓴다.
	- 설정을 바꿀 때는 위 설치 입력을 수정하고 `install`을 다시 실행한다.
- **전역 설정**의 저장소 관리 항목은 설치 입력에 맞춰 갱신한다.
	- 관리 항목 밖의 기존 사용자 설정은 보존한다.
	- Codex 전역 설정의 `approvals_reviewer` 항목은 제거한다.
- **공통 지침**의 Claude Code import와 Codex `AGENTS.md` 연결은 `install` 대상에 포함되지 않는다.

## 에이전트 연결

1. [에이전트 연결 도구](../tools/set_agents.py)로 전역 폴더를 연결한다.

```bash
# 두 도구의 전역 에이전트 폴더를 저장소 원본에 연결한다.
python -B tools/set_agents.py link
```

2. 연결 상태와 충돌을 확인한다.

```bash
# 파일을 변경하지 않고 연결 상태를 확인한다.
python -B tools/set_agents.py check
```

| **연결 위치** | **원본** |
| :--- | :--- |
| `~/.claude/agents` | `<저장소>/.claude/agents` |
| `~/.codex/agents` | `<저장소>/.codex/agents` |

- **폴더 전체**를 연결하므로 원본 내부의 정의 추가·삭제·내용 변경에는 재연결이 필요 없다.
	- 역할의 `AGENTS.md`·`SOUL.md` 내용 변경도 재설치할 필요가 없다.
- **연결 방식**은 symlink이며 Windows에서 생성에 실패하면 junction을 사용한다.
- **충돌 항목**인 기존 파일·실제 폴더·다른 원본을 가리키는 링크는 보존하고 충돌을 표시한다.

### 연결 제거

```bash
# 이 저장소의 에이전트 정의를 가리키는 전역 링크만 제거한다.
python -B tools/set_agents.py unlink
```

- 이 저장소의 에이전트 정의를 가리키는 링크만 제거한다.
	- 원본 폴더가 없어진 링크도 제거한다.
	- 기존 파일·실제 폴더·다른 연결은 보존하고 충돌을 표시한다.
	- 저장소의 원본 폴더 내용은 남는다.

## Skill 연결

공용 skill 원본은 `shared/skills/<skill>/SKILL.md`이다.
전역 skill 폴더의 링크가 원본을 가리키므로 원본 내부 수정에는 재설치가 필요 없다.
Skill은 에이전트와 달리 개별 Skill 폴더마다 연결한다.

### 연결 경로

| **링크 위치** | **원본** |
| :--- | :--- |
| `~/.claude/skills/<skill>` | `<저장소>/shared/skills/<skill>` |
| `~/.agents/skills/<skill>` | `<저장소>/shared/skills/<skill>` |

### 링크 구조

symbolic link(symlink)는 원본 폴더를 가리키는 링크이다.
도구는 symlink를 먼저 생성하고, Windows에서 실패하면 junction을 생성한다.
junction은 Windows의 폴더 연결 방식이다.
두 방식 모두 원본 폴더를 복사하지 않는다.
링크를 제거해도 원본은 남는다.

### 상태 확인

```bash
# 연결 상태와 중복·충돌·깨진 링크를 확인한다.
python -B tools/set_skills.py check
```

### 링크 생성

```bash
# 공용 Skill 링크를 생성한다.
python -B tools/set_skills.py link
```

비어 있는 경로에 링크를 만들고, 깨진 공용 skill 링크를 정리한다.
기존 실제 폴더와 다른 원본을 가리키는 링크는 보존한다.
skill을 추가·삭제·이름 변경하거나 저장소를 이동한 뒤에는 링크 생성 후 상태 확인 순서로 실행한다.

```bash
# Skill 목록 변경을 연결에 반영한 뒤 연결 상태를 확인한다.
python -B tools/set_skills.py link
python -B tools/set_skills.py check
```

### 링크 제거

```bash
# shared/skills를 가리키는 전역 링크만 제거한다.
python -B tools/set_skills.py unlink
```

`shared/skills`를 가리키는 링크만 제거한다.
원본이 없어진 링크와 예전 저장소 경로를 가리키는 끊어진 링크도 제거한다.
기존 실제 폴더와 다른 원본을 가리키는 링크는 보존한다.
전역 skill 폴더가 없으면 만들지 않고 건너뛴다.

## 외부 프로젝트 사용

- 전역 Skill 폴더와 연결된 공용 Skill 원본은 외부 프로젝트에서도 읽기 허용 대상이다.
	- Skill 폴더 내부의 참조 문서와 리소스도 읽기 허용 대상이다.
	- Skill 폴더 밖으로 연결된 링크와 비밀 파일에는 기존 차단을 적용한다.
- Skill 읽기 허용은 외부 쓰기 권한을 부여하지 않는다.
- 읽기 경계의 세부 처리는 [경로 검사](../hooks/check_tool_use/sub_path.py)에서 확인한다.

## Nodebase 개인 설정

- 위키 Skill 원본은 nestlab에 유지한다.
- 기존 전역 링크를 통해 다른 프로젝트에서도 같은 Skill을 사용한다.
- 로컬 Nodebase 위치는 nestlab의 `shared/settings/local.json`에 작성한다.
	해당 파일은 Git에서 제외하며 별도 설치나 환경변수 등록 없이 다음 호출부터 읽는다.
- `archive_root`에는 존재하는 Nodebase 저장소의 절대경로를 작성한다.
	저장소를 옮기면 이 값만 수정한다.

```json
{
  "archive_root": "C:/workspace/nodebase"
}
```

- 설정 파일의 외부 접근 예외는 읽기에만 적용한다.
- 등록된 Nodebase 내부의 파일 작업에는 기존 승인 규칙과 비밀 파일 차단을 적용한다.
- 설정 누락·손상·잘못된 루트 또는 원본 읽기 실패는 오류로 알린다.
- Discord는 `archive_repository`의 GitHub API 연결을 사용한다.
	이 로컬 설정은 Discord 연결을 변경하지 않는다.

## 변경별 조치

| **변경 대상** | **필요한 조치** |
| :--- | :--- |
| 위 네 설치 입력 | `python tools/set_hooks.py install` |
| 기존 Skill 폴더 내부 내용·파일 | 재설치 불필요 |
| Skill 추가·삭제·폴더명 변경 | `set_skills.py link` 후 `check` |
| 연결된 에이전트 폴더 내부 정의·지침 | 재설치 불필요 |
| 기존 경로의 hook 코드 내용 | 재설치 불필요 |
| hook 등록·실행 경로·인자 설정 | 설정 예제 수정 후 `install` |

- **Skill 목록 변경** 시 실행할 전체 명령은 [링크 생성](#링크-생성)에서 확인한다.
- **연결 대상 변경**이나 연결 누락은 에이전트·Skill의 `check`로 확인한 뒤 해당 연결 절차를 수행한다.
	- 충돌 항목은 자동으로 덮어쓰지 않으므로 기존 대상과 영향을 확인한 뒤 정리한다.
- **재설치 불필요**는 원본 파일을 복사할 필요가 없다는 의미이다.
	- 실행 중인 세션에서 설정·에이전트·Skill을 다시 읽는 시점까지 보장하지는 않는다.
- **도구별 사용법**은 [Claude Code](claude-code.md#사용)와 [Codex](codex.md#사용)에서 확인한다.
