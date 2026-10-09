# Skill 링크 설정

공용 skill 원본은 `shared/skills/<skill>/SKILL.md`이다.
전역 skill 폴더의 링크가 원본을 가리키므로 수정 내용이 바로 반영된다.

## 연결 경로

| 링크 위치                  | 원본                             |
| -------------------------- | -------------------------------- |
| `~/.claude/skills/<skill>` | `<저장소>/shared/skills/<skill>` |
| `~/.agents/skills/<skill>` | `<저장소>/shared/skills/<skill>` |

Hermes는 `~/.hermes/config.yaml`에 다음 경로를 추가한다.

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
```

## 링크 구조

symbolic link(symlink)는 원본 폴더를 가리키는 링크이다.
도구는 symlink를 먼저 생성하고, Windows에서 실패하면 junction을 생성한다.
junction은 Windows의 폴더 연결 방식이다.
두 방식 모두 원본 폴더를 복사하지 않는다.
링크를 제거해도 원본은 남는다.

## 사용법

저장소 루트에서 실행한다.
Python 3.12 이상이 필요하다.

### 상태 확인

```sh
python -B tools/set_skills.py check
```

연결 상태와 중복·충돌·깨진 링크를 확인한다.
Hermes가 설치돼 있으면 `external_dirs` 설정도 확인한다.

### 링크 생성

```sh
python -B tools/set_skills.py link
```

비어 있는 경로에 링크를 만들고, 깨진 공용 skill 링크를 정리한다.
기존 실제 폴더와 다른 원본을 가리키는 링크는 보존한다.
Hermes 설정은 직접 추가한다.

### 링크 제거

```sh
python -B tools/set_skills.py unlink
```

`shared/skills`를 가리키는 링크만 제거한다.
원본이 없어진 링크와 예전 저장소 경로를 가리키는 끊어진 링크도 제거한다.
기존 실제 폴더와 다른 원본을 가리키는 링크는 보존한다.
전역 skill 폴더가 없으면 만들지 않고 건너뛴다.
Hermes 설정은 직접 제거한다.

skill을 추가·삭제·이름 변경하거나 저장소를 이동한 뒤에는 두 명령을 다시 실행한다.

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
