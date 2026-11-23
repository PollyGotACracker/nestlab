---
title: Nestlab Discord 봇 GCP 운영
description: "GCP VM에 설치한 Discord 봇의 업데이트와 점검 절차이다."
type: document
created: 2026-10-03
modified: 2026-11-24
---

# Discord 봇 GCP 운영

[discord-gcp.md](discord-gcp.md)로 설치한 봇의 업데이트와 점검 절차다.
로그 확인은 [discord-debug.md](discord-debug.md)를 따른다.

## 구성 예시

```text
프로젝트 루트: nestlab/
가상환경:      nestlab/discord/.venv
봇 코드:       nestlab/ 아래 로컬 저장소와 같은 상대경로
규칙 원본:     nestlab/AGENTS.global.md, nestlab/.claude/agents/
Secret:        DISCORD_BOT_CONFIG
Zone:          us-west1-b
서비스:        discord-bot
```

## 저장소 갱신

원격 저장소의 변경을 VM에 반영한다.

- 실행 위치: VM SSH

1. 저장소 루트로 이동한다.

```bash
cd ~/nestlab
```

2. sparse checkout 범위에 `shared/rules`가 없는 VM에서는 한 번만 추가한다.

```bash
git sparse-checkout add shared/rules
```

3. 변경을 받는다.

```bash
git pull
```

4. 봇을 재시작한다.

```bash
sudo systemctl restart discord-bot
```

5. 기동 로그를 확인한다.

```bash
sudo journalctl -u discord-bot -n 50 --no-pager
```

### Claude Agent SDK 버전 업데이트

```shell
cd ~/nestlab/discord
source .venv/bin/activate
python -m pip install --upgrade claude-agent-sdk
```

### Cloud Shell 업로드

- 실행 위치: Cloud Shell UI

`⋮` → `Upload`에서 파일이나 폴더를 선택한다. 업로드한 파일 및 폴더는 Cloud Shell 홈 폴더(`~/<파일명>` 또는 `~/<폴더명>`)에 놓인다.

### VM 덮어쓰기

- 실행 위치: Cloud Shell 터미널

첫 번째 경로는 Cloud Shell의 파일, 두 번째 경로는 위 규칙의 VM 경로다.

```bash
gcloud compute scp ~/<파일명> <VM_NAME>:nestlab/<상대경로> --zone=us-west1-b --project=discord-party-parrots
```

폴더 전체를 덮어쓸 때는 아래 커맨드를 쓴다.
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

- `AGENTS.global.md`, 에이전트의 `AGENTS.md`, 자기 및 동료의 `SOUL.md`는 다음 발언에서 수정 시각이나 크기 변경을 감지한다.
  공통 규칙과 Persona는 프롬프트에 넣고 에이전트 `AGENTS.md`는 캐릭터 이름을 읽는 원본으로 쓴다.
- `discord/prompts/RUNTIME.md`, `TURN.md`, `CHAT.md`와 자기 에이전트의 `MEMORY.md`도 같은 방식으로 감지한다.
- documenter는 `discord/prompts/ARCHIVE.md`도 감지한다.
- 에이전트 이름, 역할, 채널 등 설정 변경은 아래 설정 변경 절에 따라 재시작한다.
- 봇 Python 코드(`.py` 파일)는 재시작해야 반영된다.

### 지식 원본 갱신

1. 원격 위키 저장소의 대상 브랜치에 `$repo_name/AGENTS.md`와 해당 `$repo_name/schema/` 변경을 승인된 저장소 절차로 반영한다.

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

로그 확인 커맨드는 [discord-debug.md](discord-debug.md)를 따른다.

## Claude 인증 만료

봇은 서비스 환경변수 `CLAUDE_CODE_OAUTH_TOKEN`의 장기 토큰으로 인증한다.
토큰은 발급일부터 1년간 유효하고, 사용해도 연장되지 않는다.
토큰 발급과 서비스 설정은 [discord-gcp.md](discord-gcp.md#claude-code-로그인)를 따른다.

### 증상

`discord-bot` 서비스는 `active`로 보이지만 채팅 턴마다 응답이 없다.
로그에 `ProcessError`가 남고, 실제 원인은 같은 시각의 `claude cli stderr:` 줄에 있다.
로그 확인 커맨드는 [discord-debug.md](discord-debug.md)를 따른다.

e.g. 브라우저 로그인 세션이 만료된 경우

```text
claude cli stderr: Failed to authenticate: OAuth session expired and could not be refreshed
claude_agent_sdk._errors.ProcessError: Command failed with exit code 1
```

### 토큰 재발급

- 실행 위치: VM SSH (봇 서비스 사용자, 새 SSH 창)

토큰 만료 전 점검에서 재발급 시점이 되었거나 위 증상이 나오면 진행한다.

1. 토큰을 발급하고 파일에 저장한다. [discord-gcp.md](discord-gcp.md#claude-code-로그인)의 2~3단계를 따른다.
2. 토큰 형식과 AI 응답을 확인한다. [discord-gcp.md](discord-gcp.md#claude-code-로그인)의 4~5단계를 따른다.
3. 봇을 재시작하고 Discord 채팅 채널에서 응답을 확인한다.

```bash
sudo systemctl restart discord-bot
sudo systemctl status discord-bot
```

### 서비스 설정 보완

`EnvironmentFile`이 없는 기존 서비스에 추가한다.
`sudo` 없이 봇 서비스 사용자로 접속한 셸에서 실행한다.

- 실행 위치: VM SSH

1. 토큰 파일이 있는지 확인한다. `있음`이 나오지 않으면 [discord-gcp.md](discord-gcp.md#claude-code-로그인)의 2~3단계로 파일을 먼저 만든다.

```bash
test -s ~/.config/discord-bot/claude.env && echo 있음 || echo 없음
```

2. 서비스 설정을 추가한다.

```bash
sudo mkdir -p /etc/systemd/system/discord-bot.service.d
sudo tee /etc/systemd/system/discord-bot.service.d/claude-token.conf >/dev/null <<EOF
[Service]
EnvironmentFile=$HOME/.config/discord-bot/claude.env
EOF
```

3. 서비스 설정의 경로와 파일 경로가 같은지 확인한다.

```bash
sudo systemctl cat discord-bot | grep EnvironmentFile
ls -l ~/.config/discord-bot/claude.env
```

4. 설정을 적용하고 재시작한다.

```bash
sudo systemctl daemon-reload
sudo systemctl restart discord-bot
```

### 기동 실패

`systemctl restart` 결과가 `Job for discord-bot.service failed because of unavailable resources or another system error`이면 `EnvironmentFile`을 읽지 못한 것이다.
systemd는 `EnvironmentFile`의 파일이 없거나 읽을 수 없거나 내용이 잘못되면 서비스를 시작하지 않는다.

- 실행 위치: VM SSH

1. 원인 줄을 확인한다. `Failed to load environment files`가 나온다.

```bash
sudo journalctl -xeu discord-bot -n 20 --no-pager
```

2. 서비스가 가리키는 경로와 파일을 비교한다. 파일이 없거나 경로의 사용자 이름이 다르면 `discord-gcp.md`의 3단계로 파일을 만들거나 서비스 설정의 경로를 고친다.

```bash
sudo systemctl cat discord-bot | grep EnvironmentFile
ls -l ~/.config/discord-bot/claude.env
```

3. 수정 후 재시작한다.

```bash
sudo systemctl daemon-reload
sudo systemctl restart discord-bot
```

### 401 Invalid bearer token

토큰 파일이 있는데 로그에 `401 Invalid bearer token`이 나오면 토큰 값이 거부된 것이다.
원인은 브라우저의 인증 코드를 토큰으로 저장한 경우, 토큰의 줄바꿈 혼입, 잘린 토큰, 셸이나 서비스에 남은 다른 인증 변수이다.
저장할 값은 터미널에 출력된 토큰이다.

- 실행 위치: VM SSH

1. 토큰이 한 줄인지 확인한다. 값은 출력하지 않는다. [discord-gcp.md](discord-gcp.md#claude-code-로그인)의 4단계를 따른다.
2. 다른 인증 변수가 있는지 확인한다. `0`이 아니면 그 변수가 토큰보다 먼저 쓰인다.

```bash
env | grep -c '^ANTHROPIC'
sudo systemctl show discord-bot -p Environment | grep -c ANTHROPIC
```

3. 문제가 있으면 [discord-gcp.md](discord-gcp.md#claude-code-로그인)의 2단계부터 토큰을 다시 발급한다.

### 만료 전 점검

- 토큰 파일의 저장 시각이 발급일이다. 11개월이 지나기 전에 토큰을 재발급한다.

```bash
stat -c %y ~/.config/discord-bot/claude.env
```

- 서비스 환경에 `ANTHROPIC_API_KEY`가 있으면 토큰보다 먼저 쓰인다. 키가 만료되었거나 비활성이면 인증이 실패한다.

cf. [Authentication](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)

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
