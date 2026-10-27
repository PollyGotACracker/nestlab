---
title: Nestlab Discord 봇 디버그
description: "Discord 봇의 동작과 오류 원인을 확인하는 명령 모음이다."
type: document
created: 2026-10-03
modified: 2026-10-28
---

# Discord 봇 디버그

Discord 봇의 동작과 오류 원인을 확인하는 명령 모음이다.

- `journalctl` 명령은 GCP VM의 systemd 서비스 `discord-bot` 기준이다.
- 로컬에서 `python discord/bot/bot.py`로 실행하면 같은 로그가 실행한 터미널에 출력된다.
- `journalctl` 시각은 VM의 시간대 설정을 따른다.
  KST 시각을 UTC로 대조하려면 9시간을 빼고 `journalctl --utc`로 출력한다.

## 실시간 로그

- 실행 위치: VM SSH

봇 로그를 실시간으로 본다.

```bash
journalctl -u discord-bot -f
```

- 새 로그 줄이 생기는 대로 출력된다.
- `Ctrl+C`를 누르면 출력을 끝낸다.
- 키워드 의미는 [로그 키워드](#로그-키워드)를 본다.

## 동작 키워드 로그

- 실행 위치: VM SSH

최근 10분 로그에서 턴 진행, `Read` 거부, 리액션 줄만 본다.

```bash
journalctl -u discord-bot --since "10 min ago" --no-pager | grep -E "chat turn|chat end|chat lock|chat pending change|read blocked|reaction"
```

- 키워드 의미는 [로그 키워드](#로그-키워드)를 본다.
- 턴 줄의 해석은 [다음 화자 확인](#다음-화자-확인)을 본다.

## 다음 화자 확인

- 실행 위치: VM SSH

최근 1시간 대화에서 턴마다 고른 다음 화자와 대화가 끝난 이유를 본다.

```bash
journalctl -u discord-bot --since "1 hour ago" --no-pager | grep -E "chat turn|chat end|chat lock|chat pending change|chat response control invalid"
```

턴마다 아래 형식의 줄이 남는다.

```text
chat turn conversation=... channel=... turn=3/<chat.max_turns> speaker=ricky next=stop
```

- `conversation`: 대화 시작부터 종료까지 같은 대화의 로그를 묶는 ID
- `turn`: 현재 턴과 턴 한도
  - 턴 한도는 `chat.max_turns`이고 리액션 턴은 1이다.
  - 현재 턴이 턴 한도에 닿으면 `next`와 관계없이 대화가 끝난다.
- `speaker`: 이번 턴에 발언한 캐릭터의 내부 ID
- `next=<내부 ID>`: 다음 화자로 고른 캐릭터
  - 현재 화자이거나 등록되지 않은 ID이면 대화가 끝난다.
- `next=stop`: 캐릭터가 stop을 고름
  - 사용자의 새 메시지로 멈춘 턴도 `next=stop`으로 남는다.
  - 마지막 줄에 `[[next:...]]`가 없는 응답도 본문을 보낸 뒤 `next=stop`으로 남는다.
  - 이때는 같은 시각에 `chat turn discarded reason=pending_discard` 줄이 있다.
- `next=None`: 제어 줄 형식 오류
  - 원인은 같은 시각의 `chat response control invalid` 줄 끝에 있다.
  - `chat reaction must not be empty`: 리액션 제어 줄의 이모지가 비어 있음
  - `chat control lines are duplicated, malformed or misplaced`: 제어 줄의 중복이나 형식 및 위치 오류
- `chat turn discarded reason=pending_discard`: 사용자의 새 메시지로 대화가 멈춤
  - 생성 중이던 응답은 채널에 보내지 않는다.
- `chat turn empty`: 본문 및 리액션 없는 응답이며 다음 화자로 넘어감
  - 다음 화자는 같은 턴 `chat turn` 줄의 `next` 값이다.

`chat end reason=`의 값으로 종료 시점을 구분한다.

- `pending_before_turn`: 다음 발언 전 새 사용자 메시지가 대기 중이다.
- `pending_after_turn`: 발언 후 새 사용자 메시지가 대기 중이다.
- `pending_discard`: 응답 생성 중 새 사용자 메시지가 와서 생성 응답을 버렸다.
- `model_stop`: 모델이 종료를 선택했다.
- `invalid_control`: 제어 줄 파싱이 실패했다.
- `invalid_next`: 자기 자신이나 없는 ID를 다음 화자로 골랐다.
- `turn_limit`: 대화의 발언 한도에 닿았다.
- `missing_client`: 다음 화자의 Bot 클라이언트가 없다.

`[[wait:user]]`를 처리한 턴은 상태 저장 후 `chat turn` 및 `chat end` 로그 전에 종료한다.
이 경우 로그 누락만으로 오류를 판단하지 않고 `discord/bot/chat_state/<채널 ID>.json`의 `user_wait`와 실제 사용자 메시지 후 재개 여부를 확인한다.

## 사용량과 요약

- 실행 위치: VM SSH

채팅과 요약 호출의 토큰 사용량, 비용, 요약 오류를 본다.

```bash
journalctl -u discord-bot --since "1 hour ago" --no-pager | grep -E "token usage|summary"
```

`token usage kind=chat`은 발언 호출, `kind=summary`는 채널 요약 호출이다.
`usage`와 `total_cost_usd`는 SDK가 반환한 값이다.

## 채팅 오류 원인

- 실행 위치: VM SSH

채널에 `chat.failure_notice` 안내가 올라왔을 때 오류 원인을 찾는다.

```bash
journalctl -u discord-bot --since "1 hour ago" --no-pager | grep -A 20 "chat handling failed"
```

- `chat handling failed` 줄 뒤에 예외 traceback이 이어진다.
- traceback의 마지막 줄이 예외 종류와 메시지이다.
- traceback이 20줄보다 길면 `-A` 값을 늘린다.

## 최근 오류 로그

- 실행 위치: VM SSH

최근 3일 로그에서 오류, 예외와 첨부, 이미지 관련 줄을 본다.

```bash
journalctl -u discord-bot --since "-3 days" | grep -iE "error|exception|attachment|image"
```

- 키워드는 대소문자를 구분하지 않고 찾는다.
- 줄의 로그 수준(`ERROR`, `WARNING`, `INFO`)으로 심각도를 구분한다.

## 서비스 이름 찾기

- 실행 위치: VM SSH

위 명령의 서비스 이름 `discord-bot`이 맞지 않을 때 실제 이름을 찾는다.

```bash
systemctl list-units --type=service | grep -i discord
```

- `<이름>.service` 형식의 값이 서비스 이름이다.
- 위 명령의 `-u` 값을 이 이름으로 바꾼다.

## 설정 JSON 문법

- 실행 위치: 로컬 프로젝트 루트

`discord/config.json`의 JSON 문법을 확인한다.

```bash
python -c "import json; json.load(open('discord/config.json', encoding='utf-8')); print('ok')"
```

- `ok`가 나오면 문법이 맞다.
- 문법이 틀리면 오류 메시지에 줄 및 열 위치가 나온다.
- 파일 내용은 출력하지 않는다.

## 로그 키워드

위 명령 출력에 나오는 주요 키워드의 의미다.

| 키워드                                       | 의미                                              |
| -------------------------------------------- | ------------------------------------------------- |
| `connected id=`                              | Bot 하나가 Discord에 접속함                       |
| `[run_gcp]`                                  | 런처 단계 오류. 주로 Secret JSON 문법 오류        |
| `chat turn`                                  | 대화 ID, 턴마다 발언한 캐릭터와 고른 다음 화자    |
| `chat end reason=`                           | 대화가 끝난 원인                                  |
| `chat lock`                                  | 대화별 채널 락 대기, 획득, 취소                   |
| `chat pending change`                        | 사용자 메시지 pending 등록 및 해제 사유           |
| `token usage`                                | SDK 호출의 사용량과 비용                          |
| `chat turn discarded reason=pending_discard` | 사용자의 새 메시지 때문에 생성 중이던 응답을 버림 |
| `read blocked`                               | 첨부 폴더 밖의 `Read`를 거부함                    |
| `reaction`                                   | 리액션을 건너뛰었거나 실패함                      |
