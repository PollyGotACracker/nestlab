# Discord 커밋 알림 Webhook

GitHub push 발생 시 Discord 채널로 알림을 보내는 Hugo(휴고) webhook 적용 방법이다.

## Discord Webhook 생성

- 작업 위치: Discord

1. 알림 받을 채널에서 `채널 설정 → 연동 → 웹후크 → 새 웹후크`를 만든다.
2. `웹후크 URL 복사`를 누른다. 끝에 `/github`를 붙이지 않는다.

```text
https://discord.com/api/webhooks/...
```

## GitHub Secret 등록

- 작업 위치: GitHub repo → Settings

```text
Settings → Secrets and variables → Actions → New repository secret
```

- 이름: `DISCORD_WEBHOOK_URL`
- 값: `Discord Webhook 생성` 절에서 복사한 URL

## Workflow 배치

`.github/workflows/discord.yml`을 저장소에 둔다(현재 반영됨). 새 저장소에 적용할 때는 이 파일을 그대로 복사한다.

push 시 동작 순서:

1. repository, branch, actor는 환경변수에서, commit 목록과 비교 URL은 `$GITHUB_EVENT_PATH`의 push 이벤트 JSON에서 읽는다.
2. commit 수가 0이면 알림 없이 종료한다.
3. commit이 1개이면 제목의 `feat`, `fix`, `docs`, `chore` 접두사에 맞는 휴고 안내 문구 중 하나를 무작위로 고른다.
   그 밖의 접두사는 일반 안내 문구를 쓴다.
4. commit이 여러 개이면 전체 개수를 알리는 안내 문구 하나를 무작위로 골라 알림 한 건으로 합친다.
5. jq로 `username: 휴고`와 안내 본문, embed payload를 만든다.
   제목은 저장소 이름이며 description에는 push의 마지막 commit 한 건의 짧은 SHA, 링크, 타입, 제목만 넣는다.
   fields는 `Branch`, `Pushed by`, `Commits`이고 비교 URL이 있으면 embed 링크로 붙인다.
6. curl로 `$DISCORD_WEBHOOK_URL`에 POST한다.

## 확인

커밋을 push해 Discord 채널에 알림이 오는지 본다.

## 문제 해결

| 증상            | 원인                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------- |
| 알림이 안 옴    | Secret 미등록, URL 오탈자. GitHub repo → Actions 탭 workflow 실행 로그로 확인                     |
| 알림이 두 번 옴 | 기존 GitHub 기본 Webhook(`Settings → Webhooks`, `/github` suffix)이 남아 있음. 삭제 및 비활성화 필요 |
