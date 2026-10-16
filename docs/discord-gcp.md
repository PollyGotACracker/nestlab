# Discord 봇 GCP 배포 절차

Discord 봇을 GCP Compute Engine VM에서 실행하도록 설치하는 절차다.

## 관련 문서

- 업데이트와 점검: [discord-gcp-operations.md](discord-gcp-operations.md)
- 로그 확인: [discord-debug.md](discord-debug.md)

## 실행 구성

- 설정 값: Secret Manager의 `DISCORD_BOT_CONFIG`
- 설정 생성: `run_gcp.py`가 Secret 기반 설정을 RAM에 만든다.
- 봇 실행: `run_gcp.py`가 `bot.py`를 실행한다.
- 공통 파일: 로컬과 GCP에서 같은 봇 코드 파일을 쓴다.

## 사전 확인

- 작업 위치: 로컬 PC → `discord/bot/run_gcp.py`

`run_gcp.py` 상단의 두 값을 확인한다.

```python
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "discord-party-parrots")
CONFIG_SECRET_ID = os.environ.get("CONFIG_SECRET_ID", "DISCORD_BOT_CONFIG")
```

현재 GCP 프로젝트 ID가 `discord-party-parrots`, Secret 이름이 `DISCORD_BOT_CONFIG`이면 기본값을 쓴다.
다른 값을 쓰면 실행 환경의 `GCP_PROJECT_ID`, `CONFIG_SECRET_ID`를 지정한다.
상시 실행에서는 같은 환경변수를 systemd 서비스에도 설정한다.

## Secret Manager API

- 실행 위치: GCP 콘솔

1. 프로젝트 `discord-party-parrots`를 선택한다.
2. `Secret Manager API`를 검색한다.
3. `사용`을 누른다.

## Secret 생성

- 실행 위치: GCP 콘솔 → Secret Manager

- Secret 이름: `DISCORD_BOT_CONFIG`

Secret 값에는 Discord 설정 전체 JSON을 넣는다. 형식은 `discord/config.example.json`을 따른다.

## 서비스 계정 생성

- 실행 위치: GCP 콘솔

1. `IAM 및 관리자`
2. `서비스 계정`
3. `서비스 계정 만들기`
4. 서비스 계정 이름: `Discord Bot`
5. 서비스 계정 ID: `discord-bot`
6. 생성

서비스 계정 주소:

```text
discord-bot@discord-party-parrots.iam.gserviceaccount.com
```

## Secret 읽기 권한

### 프로젝트 IAM

- 실행 위치: GCP 콘솔 → IAM

1. `주 구성원별로 보기`
2. `액세스 권한 부여`
3. 새 주 구성원:

```text
discord-bot@discord-party-parrots.iam.gserviceaccount.com
```

4. 역할 선택:

```text
Secret Manager Secret Accessor
또는 roles/secretmanager.secretAccessor
```

### Secret 권한

- 실행 위치: GCP 콘솔 → Secret Manager → `DISCORD_BOT_CONFIG`

1. `권한`
2. 서비스 계정 추가:

```text
discord-bot@discord-party-parrots.iam.gserviceaccount.com
```

3. 역할 선택:

```text
Secret Manager Secret Accessor
또는 roles/secretmanager.secretAccessor
```

## 서비스 계정 연결

- 실행 위치: GCP 콘솔 → Compute Engine → VM 인스턴스

1. VM 중지
2. VM 이름 클릭
3. `수정`
4. 서비스 계정(디스크): `discord-bot`
5. Access scopes: `Allow full access to all Cloud APIs`
6. 저장
7. VM 시작

- Zone: `us-west1-b`

## 저장소 Clone

- 실행 위치: GCP 콘솔 → Compute Engine → VM 인스턴스 → SSH

```bash
sudo apt update
sudo apt install -y git
```

```bash
git --version
```

VM에서 저장소를 clone한다.
`--filter=blob:none`으로 파일 내용을 처음부터 모두 다운로드하지 않는다.
`--sparse`로 일부 경로만 checkout할 수 있게 한다.
저장소 루트로 이동한 후 checkout할 폴더 및 파일을 지정한다.

```bash
cd ~
git clone --filter=blob:none --sparse https://github.com/PollyGotACracker/nestlab.git
cd ~/nestlab

git sparse-checkout set .claude discord
```

필요한 프로젝트 파일과 디렉터리가 있는지 확인한다.

```bash
ls -la
```

## 가상환경 생성

```bash
cd ~/nestlab
```

- 실행 위치: VM SSH → `nestlab/`

`python3 -m venv .venv` 실행 시 `python3.14-venv`가 필요하다는 안내가 나오면 먼저 설치한다.

```bash
sudo apt update
sudo apt install -y python3.14-venv
```

venv 가상환경을 만들고 활성화한다.

```bash
cd ~/nestlab/discord
python3 -m venv .venv
source .venv/bin/activate
which python
```

`which python` 출력은 `discord/.venv/bin/python`이어야 한다.

## 패키지 설치

```bash
cd ~/nestlab/discord
```

- 실행 위치: VM SSH → `discord/`

가상환경을 활성화한 상태에서 설치하면 `.venv` 안에 설치된다.

```bash
# 가상환경을 중복 활성화하더라도 문제가 발생하지 않는다
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "aiohttp==3.14.3" "claude-agent-sdk==0.2.159" "discord.py==2.7.1" google-cloud-secret-manager
```

앞의 고정 버전은 로컬 조사 환경의 Python 3.14.0에서 import와 단위 테스트로 확인한 설치 예시다.
`google-cloud-secret-manager`는 GCP 런처가 Secret Manager를 읽는 데 추가로 필요하다.
설치 결과와 실제 VM에서의 실행은 다음 단계에서 확인한다.

```bash
python -m pip show aiohttp discord.py claude-agent-sdk google-cloud-secret-manager
```

## Claude Code 로그인

- 실행 위치: VM SSH (디렉터리 무관)

claude CLI를 설치하고 로그인한다.

```bash
curl -fsSL https://claude.ai/install.sh | bash
~/.local/bin/claude
```

AI 응답을 확인한다.

```bash
~/.local/bin/claude -p "hi"
```

## 수동 실행

```bash
cd ~/nestlab/discord
```

- 실행 위치: VM SSH → `discord/`

```bash
.venv/bin/python bot/run_gcp.py
```

Discord에서 봇이 접속했는지 확인하고 `Ctrl+C`로 종료한다.

## systemd 상시 실행

SSH 창을 닫아도 봇이 계속 실행되게 한다.
`수동 실행` 절이 정상일 때 진행한다.
수동으로 실행한 봇은 먼저 종료한다.

- 실행 위치: VM SSH (디렉터리 무관)

```bash
VM_USER="$(whoami)"
REPO_ROOT="/home/$VM_USER/nestlab"

sudo tee /etc/systemd/system/discord-bot.service >/dev/null <<EOF
[Unit]
Description=Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$VM_USER
WorkingDirectory=$REPO_ROOT/discord
ExecStart=$REPO_ROOT/discord/.venv/bin/python $REPO_ROOT/discord/bot/run_gcp.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

`REPO_ROOT`는 `저장소 Clone` 절에서 clone한 경로다.
저장소가 VM 홈 폴더 바로 밑에 있다고 가정한다.
다른 위치에 clone했다면 실제 경로로 바꾼다.

설정을 적용하고 실행한다. `enable --now`는 부팅 시 자동 실행 등록과 즉시 실행을 함께 한다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-bot
sudo systemctl status discord-bot
```

등록한 설정 파일 내용은 아래 명령으로 확인한다.

```bash
sudo systemctl cat discord-bot
```
