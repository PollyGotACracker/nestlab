# Discord 봇 GCP 운영

[discord-gcp.md](discord-gcp.md)로 설치한 봇의 업데이트와 점검 절차다.
로그 확인은 [discord-debug.md](discord-debug.md)를 따른다.

## 구성 예시

```text
프로젝트 루트: nestlab/
가상환경:      nestlab/discord/.venv
봇 코드:       nestlab/ 아래 로컬 저장소와 같은 상대경로
규칙 원본:     nestlab/AGENTS.md, nestlab/.claude/agents/
Secret:        DISCORD_BOT_CONFIG
Zone:          us-west1-b
서비스:        discord-bot
```

`nestlab/`은 VM 홈 디렉터리 기준 상대경로다.
discord-gcp.md의 `저장소 Clone` 절에서 clone한 결과다.

## 파일 업데이트

로컬에서 고친 파일을 VM의 같은 경로에 덮어쓰고 봇을 재시작한다.

- VM 경로: `nestlab/` 뒤에 로컬 프로젝트 루트 기준 상대경로를 붙인 경로
- 파일 하나가 바뀌면 그 파일을 VM의 같은 경로로 복사한다.
- 같은 폴더의 여러 파일이 바뀌면 상위 폴더를 통째로 복사한다.
- 폴더를 복사할 때는 봇이 실행 중 만드는 데이터 파일과 설정 파일을 빼고 올린다.
- 제외가 어려우면 바뀐 코드 파일만 하나씩 복사한다.

폴더 업로드에서 제외할 경로는 프로젝트 루트 기준 다음과 같다.

- `discord/config.json`: VM 런처가 만드는 설정 링크다.
- `discord/.venv/`: VM에서 설치한 가상환경이다.
- `discord/.discord_attachments/`: 첨부 원본이다.
- `discord/bot/chat_state/`: 대화 기록·요약·사용자 판단 대기다.
- `discord/bot/reminders.json`: 예약 알림이다.
- `discord/bot/memory.sqlite3`, `memory.sqlite3-wal`, `memory.sqlite3-shm`: 기억 DB와 같은 폴더의 보조 파일이다.
- `discord/bot/memory_approval.json`, `discord/bot/archive_workflow.json`: 승인 대기 상태다.

### Cloud Shell 업로드

- 실행 위치: Cloud Shell UI

`⋮` → `Upload`에서 파일이나 폴더를 선택한다. 업로드한 파일·폴더는 Cloud Shell 홈 폴더(`~/<파일명>` 또는 `~/<폴더명>`)에 놓인다.

### VM 덮어쓰기

- 실행 위치: Cloud Shell 터미널

첫 번째 경로는 Cloud Shell의 파일, 두 번째 경로는 위 규칙의 VM 경로다.

```bash
gcloud compute scp ~/<파일명> <VM_NAME>:nestlab/<상대경로> --zone=us-west1-b --project=discord-party-parrots
```

폴더 전체를 덮어쓸 때는 아래 명령을 쓴다.
두 번째 경로는 VM에서 그 폴더를 담는 상위 폴더다.
Cloud Shell에 올린 폴더에서 실행 데이터 파일과 설정 파일을 먼저 지운다.
남아 있으면 VM의 운영 데이터와 설정을 로컬 내용으로 덮어쓴다.

```bash
gcloud compute scp --recurse ~/<폴더명> <VM_NAME>:nestlab/<상위 폴더 상대경로>/ --zone=us-west1-b --project=discord-party-parrots
```

`No such file or directory` 오류가 나면 Cloud Shell에서 파일 위치를 찾는다.

```bash
find ~ -name <파일명>
```

### 재시작

- 실행 위치: VM SSH

```bash
sudo systemctl restart discord-bot
sudo systemctl status discord-bot
```

- `AGENTS.md`, 역할의 `AGENTS.md`, 자기·동료의 `SOUL.md`는 다음 발언에서 수정 시각이나 크기 변경을 감지한다.
  공통 규칙과 Persona는 프롬프트에 넣고 역할 `AGENTS.md`는 캐릭터 이름을 읽는 원본으로 쓴다.
- `discord/prompts/RUNTIME.md`, `TURN.md`, `CHAT.md`와 자기 역할의 `MEMORY.md`도 같은 방식으로 감지한다.
- documenter는 `discord/prompts/ARCHIVE.md`도 감지한다.
- 에이전트 이름·역할·채널 등 설정 변경은 아래 설정 변경 절에 따라 재시작한다.
- 봇 Python 코드(`.py` 파일)는 재시작해야 반영된다.

### 지식 원본 갱신

1. Nodebase 원격 저장소의 대상 브랜치에 `$repo_name/AGENTS.md`와 해당 `$repo_name/schema/` 변경을 승인된 저장소 절차로 반영한다.

`$repo_name`은 `archive_repository`에 설정한 원격 저장소를 뜻한다.

원격 schema 변경은 다음 절차 로딩 호출에서 읽으므로 그 변경만으로 봇을 재시작할 필요는 없다.
로컬 파일 편집이나 VM 업로드만으로 GitHub 원본이 바뀌지는 않는다.

## 설정 변경

채널 ID 추가처럼 설정을 바꾸면 Secret에 새 버전을 등록하고 재시작한다.
`run_gcp.py`는 시작할 때 Secret의 최신 버전(`versions/latest`)을 한 번 읽는다.

- 실행 위치: Cloud Shell 터미널

1. 로컬에서 고친 `config.json`을 Cloud Shell에 업로드한다.
2. JSON 문법을 검사한다. 파일 내용은 출력하지 않는다. `ok`가 나와야 다음 단계로 넘어간다.

```bash
python3 -c "import json; json.load(open('config.json', encoding='utf-8')); print('ok')"
```

3. 새 버전을 등록한다.

```bash
gcloud secrets versions add DISCORD_BOT_CONFIG --data-file=config.json --project=discord-party-parrots
```

4. 토큰이 담긴 파일을 지운다.

```bash
rm config.json
```

- 실행 위치: VM SSH

5. 재시작한다.

```bash
sudo systemctl restart discord-bot
sudo systemctl status discord-bot
```

JSON 문법이 틀린 버전을 등록하면 `run_gcp.py`가 봇을 시작하지 않고 종료한다.
systemd가 5초마다 재시작을 반복하므로 `status`가 잠깐 `active`로 보여도 봇은 오프라인이다.
로그에는 아래 같은 줄이 남는다.

```text
[run_gcp] Expecting property name enclosed in double quotes: line 4 column 1 (char 97)
[run_gcp] Extra data: line 61 column 3 (char 1876)
```

| 오류                                                | 원인                                            |
| --------------------------------------------------- | ----------------------------------------------- |
| `Expecting property name enclosed in double quotes` | 마지막 항목 뒤의 쉼표, 큰따옴표가 빠진 키, 주석 |
| `Extra data`                                        | 마지막 `}` 뒤에 남은 내용                       |

고친 파일로 1단계부터 다시 진행한다.

## 로그 확인

로그 확인 명령은 [discord-debug.md](discord-debug.md)를 따른다.

## 계정과 Secret 점검

### VM 서비스 계정

- 실행 위치: VM SSH

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
# discord-bot@discord-party-parrots.iam.gserviceaccount.com
```

### 서비스 계정 권한

- 실행 위치: VM SSH

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes
# https://www.googleapis.com/auth/cloud-platform
```

### gcloud 실행 계정

- 실행 위치: VM SSH

```bash
gcloud auth list
```

`ACTIVE` 표시가 `discord-bot@discord-party-parrots.iam.gserviceaccount.com`에 있어야 한다.
다른 계정이 활성화되어 있으면 `gcloud config set account <ACCOUNT>`로 바꾼다.

### Secret과 버전

- 실행 위치: Cloud Shell 터미널

```bash
gcloud secrets describe DISCORD_BOT_CONFIG --project=discord-party-parrots
gcloud secrets versions list DISCORD_BOT_CONFIG --project=discord-party-parrots
```

## 권한 문제 확인

Policy Troubleshooter는 Allow 정책, Deny 정책, 조건부 IAM, Principal Access Boundary를 함께 평가해 허용이나 거부 이유를 보여 준다.

1. Cloud Shell에서 프로젝트 번호를 확인한다.

```bash
gcloud projects describe discord-party-parrots --format="value(projectNumber)"
```

2. GCP 콘솔 검색창에서 `Policy Troubleshooter`를 검색해 이동한다.
3. `수동(Manual)`을 선택한다.
4. `주체(Principal)`에 서비스 계정을 넣는다.

```text
discord-bot@discord-party-parrots.iam.gserviceaccount.com
```

5. `리소스(Resource)`에 Secret 버전 경로를 넣는다.

```text
//secretmanager.googleapis.com/projects/<PROJECT_NUMBER>/secrets/DISCORD_BOT_CONFIG/versions/<SECRET_VERSION_NUMBER>
```

6. `권한(Permission)`에 아래 값을 넣는다.

```text
secretmanager.versions.access
```

7. `액세스 확인(Check access)`을 누른다. 결과 화면에 `GRANTED` 또는 `DENIED`와 이유가 나온다.
