# Discord Agent 시스템 명세

## 목적

에이전트별 AI Agent를 Discord Bot으로 제공한다.
Bot은 하나의 프로세스에서 실행한다. 등록된 에이전트 채널에서 캐릭터들이 사용자와 대화하고, 역할마다 정해진 요청을 전용 도구로 처리한다.
Bot은 로컬 저장소를 수정하거나 명령을 실행하지 않는다. 로컬 저장소 작업은 이 시스템의 범위가 아니다.
검증한 런타임은 Python 3.14.0, `aiohttp 3.14.3`, `claude-agent-sdk 0.2.159`, `discord.py 2.7.1`이다.

## 에이전트

- director
  - Discord 담당: 역할 배정, 투표 올리기, 스레드 만들기, 메시지 고정
  - 전용 도구:
    - `poll_create`
    - `thread_create`
    - `message_pin`
- researcher
  - Discord 담당: 새 정보 조사, 최신 정보 확인
  - 전용 도구: 없음
- worker
  - Discord 담당: 코드 조각, 글 초안, 정리, 계산 같은 결과물 작성
  - 전용 도구: 없음
- reviewer
  - Discord 담당: 발언 검토, 인용 대조(채널 기록), 사실 검증(웹 검색)
  - 전용 도구:
    - `channel_history`
- documenter
  - Discord 담당: 지식 저장소 조회와 승인 작업 준비
  - 전용 도구:
    - `archive_thread_start`
    - `archive_workflow_open`
    - `archive_list`
    - `archive_search`
    - `archive_history`
    - `archive_read`
    - `archive_branch`
    - `archive_issue_stage`
    - `archive_stage`
    - `archive_pr_stage`
- assistant
  - Discord 담당: 알림 같은 생활형 요청
  - 전용 도구:
    - `reminder_set`
    - `reminder_repeat`
    - `reminder_list`
    - `reminder_cancel`

모든 에이전트는 설정의 `tools`에 적힌 기본 도구와 `server_channels`, `forum_post`를 공통으로 가진다. `forum_post`는 `archive_forum_id`가 가리키는 포럼을 제외한다.

Discord 담당은 `prompts.py`의 `ROLE_DUTIES`에 정의한다. `.claude/agents/<role>/AGENTS.md`의 역할은 로컬 저장소 작업 세션의 역할이며 Discord 담당과 구분한다.

## 역할별 규칙

### 공통

- 채팅과 대상이 불분명한 발화는 어느 담당이든 Persona로 답한다.
- 담당이 정해진 요청이 자기 담당이 아니면 직접 처리하지 않고 다음 화자로 담당 캐릭터를 지정해 넘긴다.
- 담당이 불분명하거나 여러 역할이 필요한 요청은 director에게 넘긴다.
- 담당 목록은 사용자가 할 수 있는 일을 직접 물을 때만 말한다. 로컬 작업 세션의 역할은 물어볼 때만 구분해 말한다.
- 결과물은 응답 본문으로만 쓴다.
- 보통 1~4문장으로 말한다. worker가 작성 요청에 답할 때만 결과물 본문이 길어도 된다.
- Discord는 마크다운 표를 표시하지 않으므로 표 대신 목록이나 코드 블록을 쓴다.
- 서버 채널에 관한 질문은 `server_channels`로 조회한 결과를 근거로 답한다.
- 포럼 글은 사용자가 요청했을 때만 `forum_post`로 올린다.
- 포럼 태그는 `server_channels`에서 확인한 태그 이름만 쓴다.

### director

- 사용자의 선택이 필요할 때만 투표를 올린다.
- 올린 투표는 `(투표) 질문 / 선택지 / 기간` 형식으로 director 이름과 투표 메시지 ID와 함께 대화 기록에 남긴다.
- 투표가 끝나 Discord가 결과 메시지(`MessageType.poll_result`)를 올리면 director 봇만 처리한다. 임베드의 `poll_question_text`, `victor_answer_text`, `victor_answer_votes`, `total_votes`를 `[투표 결과]` 화자로 기록하고, director를 첫 화자로 대화를 시작한다. 기록의 메시지 ID는 원래 투표 메시지를 가리킨다.
- 결과 메시지는 작성자가 봇일 수 있으므로 봇·자기 메시지 필터보다 먼저 판정한다.
- 봇 계정은 투표할 수 없다.
- 대화 기록의 메시지 ID로 스레드를 시작하거나 메시지를 고정한다.

### reviewer

- 인용이 실제 메시지와 맞는지 `channel_history`로 대조한다.

### documenter

- 지식 저장소 연결이나 내용에 관한 질문에는 `archive_read`로 실제 조회한 결과를 근거로 답한다.
- 다른 에이전트는 지식 저장소 요청을 직접 처리하지 않고 documenter에게 넘긴다.
- 일반 에이전트 채널에서 저장소 작업 요청을 받으면 `archive_thread_start`로 최초 사용자 메시지와 첨부를 archive forum의 새 thread로 옮긴다.
- 저장소 조회와 Stage 도구는 `archive_forum_id`가 가리키는 포럼 아래의 thread에서만 사용한다.
- 자료 편입·위키 조회·위키 점검은 각각 `archive_workflow_open`의 `ingest`·`query`·`lint`로 시작한다.
- 같은 commit에서 반환된 `$repo_name/AGENTS.md`와 해당 schema의 전체 내용을 읽고 절차를 적용한다.
  `$repo_name`은 `archive_repository`에 설정한 원격 저장소를 뜻한다.
- 목록·검색과 후속 파일 읽기는 반환된 commit SHA를 사용한다.
- 조회·점검만 요청한 경우에는 Issue·변경·PR 승인 작업으로 자동 전환하지 않는다.
- Issue 템플릿, 변경 세트, PR 템플릿 전체를 각각 보여 준 뒤 해당 Stage 도구로 승인 대기에 보관한다.
- 각 Stage 뒤에는 요청자의 `승인` 또는 `취소`를 기다린다.
- Issue 승인 뒤에만 `<타입>/<이슈번호>` 작업 브랜치와 변경안을 만든다.
- 승인된 변경 세트 하나는 커밋 하나로 적용한다.
- PR 승인은 변경 승인과 별도로 받는다.
- PR 병합과 브랜치 삭제는 사용자가 GitHub에서 한다.

### assistant

- 한 번 울리는 알림은 1분에서 `reminders.max_days`일 뒤까지 예약할 수 있다.
- 반복 알림은 매일, 평일, 주말 또는 지정한 요일의 정해진 시각에 울린다.
- 시각은 `reminders.timezone_offset_hours` 기준이다.
- 특정 시각에 울릴 한 번짜리 알림은 턴 프롬프트의 현재 시각을 기준으로 분을 계산한다. 현재 시각을 확인하려고 `reminder_list`를 부르지 않는다. SDK 내부 턴 한도(`chat.sdk_max_turns`)를 넘지 않게 하기 위해서다.
- 알림은 `discord/bot/reminders.json`에 원자적으로 저장하고 시작할 때 불러온다. 재시작해도 유지된다.
- 알림은 등록한 봇 계정으로 등록한 채널에 보낸다. `allowed_user_ids`의 사용자를 멘션하고 `⏰ 알림: 내용` 형식으로 쓴다.
- 보낸 알림은 `(알림) 내용`으로 대화 기록에 남긴다.
- `reminders.check_seconds`초마다 울릴 알림을 확인한다. 중복 발송을 막으려고 보내기 전에 파일을 먼저 저장한다.
- 봇이 꺼져 있어 놓친 한 번짜리 알림은 재시작 직후 `⏰ 늦은 알림 (예정 월-일 시:분): 내용`으로 한 번 보낸다.
- 봇이 꺼져 있어 놓친 반복 알림 회차는 예정 시각 `reminders.repeat_grace_minutes`분 안에 켜졌을 때만 보내고, 그 뒤면 건너뛴다.
- 보내지 못한 알림은 경고 로그를 남기고 다시 보내지 않는다.

## 채널 구성

에이전트 채널은 두 목록 중 하나에 Discord 채널 ID를 등록한다. 두 목록을 합쳐 하나 이상 등록해야 한다.

| 키                 | 동작                                                |
| ------------------ | --------------------------------------------------- |
| `request_channels` | 사용자 메시지에만 답한다                            |
| `chat_channels`    | 채팅 프롬프트로 답하고 per_day만큼 자율 채팅도 한다 |

같은 채널을 두 목록에 함께 등록할 수 없다.

`request_channels`의 항목은 채널 ID이거나 객체다.

| 키       | 필수 | 의미                                                                                      |
| -------- | ---- | ----------------------------------------------------------------------------------------- |
| `id`     | 필수 | 채널 ID                                                                                   |
| `model`  | 선택 | 이 채널에서 `chat.model` 대신 쓸 모델                                                     |
| `effort` | 선택 | 이 채널에서 `chat.effort` 대신 쓸 effort. `low`, `medium`, `high`, `xhigh`, `max` 중 하나 |

스레드와 포럼 게시글은 부모 채널의 `model`, `effort`를 따른다.

`chat_channels`의 항목은 채널 ID이거나 객체다.
같은 ID가 여러 번 나오면 적힌 값을 합친다.

| 키        | 필수 | 의미                                   |
| --------- | ---- | -------------------------------------- |
| `id`      | 필수 | 채널 ID                                |
| `per_day` | 선택 | 하루 자율 채팅 횟수. 0 이상, 생략 시 0 |
| `topic`   | 선택 | 자율 채팅 주제. 자유 문장으로 쓴다     |

텍스트 채널과 포럼 채널을 등록할 수 있다. 스레드와 포럼 게시글은 부모 채널이 등록되어 있으면 에이전트 채널로 처리하고, 요청·채팅 구분도 부모 채널을 따른다.

`archive_forum_id`는 `request_channels`에 등록된 ForumChannel이어야 한다. `chat_channels`에는 등록할 수 없다.
`archive_repository`와 documenter 역할 에이전트가 없으면 설정 오류로 처리한다.

등록되지 않은 채널, 허용 목록 밖의 서버와 사용자의 메시지는 처리하지 않는다. 다른 Bot과 웹훅의 메시지도 처리하지 않는다.

## 대화 흐름

### 대화 시작

첫 화자는 다음 순서로 정한다.

1. 사용자가 멘션한 Bot
2. 본문에서 가장 앞에 나온 캐릭터의 `korean_name`. 바로 앞 글자가 한글 음절이면 다른 단어의 일부로 보고 제외한다
3. 이미 조회된 답장 대상 메시지의 작성자가 등록된 Bot이면 그 캐릭터
4. 위 조건에 맞는 대상이 없으면 무작위로 고른 캐릭터

사용자 메시지로 시작한 대화의 첫 화자는 차례를 넘길 수 있다.
본문 없는 응답은 채널에 보내지 않고, 그 응답의 다음 화자에게 차례를 넘긴다.
넘기는 발언도 `chat.max_turns`의 발언 수에 포함된다.
`chat.max_turns`가 1이면 첫 화자가 차례를 넘길 때 응답 없이 대화가 끝나므로 2 이상으로 둔다.

자율 채팅은 사용자 메시지 없이 시작한다. 하루의 시작 시각은 Python 런타임이 채널과 날짜로 계산하며, 이 계산에는 Claude를 호출하지 않는다.

### 후속 발언

각 캐릭터는 응답 마지막 줄에 다음 화자 제어값을 출력한다.

```text
[[next:<내부 ID>]]
[[next:stop]]
```

리액션과 사용자 판단 대기를 함께 쓰는 경우 제어 줄 순서는 다음과 같다.
마지막 `[[next:...]]` 줄이 없으면 본문을 보내고 다음 화자를 `stop`으로 처리한다.
이때 마지막 줄의 `[[wait:user]]`·`[[react:...]]`도 분리해 처리한다.

```text
본문
[[react:<이모지>]]
[[wait:user]]
[[next:stop]]
```

제어값은 Discord 메시지 본문에서 제거한다.
중복되거나 형식·위치가 잘못된 제어 줄은 오류로 처리한다.
`[[wait:user]]`가 있으면 마지막 줄의 다음 화자 ID와 관계없이 `stop`으로 처리한다.
다음 화자를 넘기는 본문에서 담당 넘기기 안내 문장은 제거하고 실제 답변 내용은 남긴다.

다음 화자 선택이 없거나, 종료이거나, 자기 자신이거나, 없는 ID이면 대화를 끝낸다.

### 사용자 판단 대기

- `[[wait:user]]`를 파싱하면 채널별 `user_wait`에 `reason: user_decision`과 발언한 에이전트를 저장한다.
- 질문 전송 전에 상태를 저장하므로 전송 실패나 재시작 후에도 대기를 유지한다.
- 대기 중 자율 채팅, 리액션, 투표 결과 등 사용자 메시지가 아닌 경로에서는 후속 발언을 시작하지 않는다.
- 정상 대화 재개 시 실제 `on_message`가 등록한 `reason: user_message`의 pending 항목을 처리할 때 대기를 해제한다.
  요청은 자율·단일 리액션 턴이 아니어야 하며, 작성자 ID는 양의 정수이고 Bot 명단에 없어야 한다.
- 일반 `[[next:stop]]`과 질문 본문만으로는 지속 대기를 만들지 않는다.
- 사용자 메시지로 대기를 해제하는 것은 archive pending의 `승인`·`취소` 처리와 별개다.
- 채널 전체 기록 삭제도 해당 채널의 `user_wait`를 제거한다.

### 턴 제한

| 대화          | 최대 발언        |
| ------------- | ---------------- |
| 사용자 시작   | `chat.max_turns` |
| 자율 시작     | `chat.max_turns` |
| 리액션 트리거 | 1                |

한 발언을 만드는 동안 Claude Agent SDK의 내부 도구 호출 턴은 최대 `chat.sdk_max_turns`회다.
이 키를 생략하면 `None`을 SDK에 전달하여 SDK 턴 상한을 두지 않는다.
값을 지정하면 1 이상의 정수여야 하며 0, 음수, 명시적인 null은 오류다.
대화 발언 수의 `chat.max_turns`와 이미지 캡션의 별도 한도는 그대로 적용한다.

두 번째 발언부터는 발언을 만들기 전에 `chat.turn_delay_seconds`초 동안 기다린다.
기다리는 동안 1초마다 사용자의 새 메시지를 확인하고, 새 메시지가 있으면 대화를 끝낸다. 사용자 메시지에 대한 첫 발언은 기다리지 않는다.

### 중단

채널마다 대화를 하나씩만 진행한다. 새 대화는 진행 중인 대화가 끝날 때까지 기다린다.

사용자의 새 메시지가 대기 중이면 진행 중인 대화는 다음 턴으로 넘어가지 않고 끝난다. 한 턴을 생성하는 도중 새 메시지가 도착하면 그 응답은 보내지 않는다.

### 자율 채팅

- `chat_channels`에 등록한 채널에서만 시작한다.
- 채널별로 `per_day`개의 시작 시각을 하루의 임의 분으로 정한다.
- `chat.scheduler_interval_seconds`초마다 시각을 확인한다.
- 시각을 놓친 경우 나중에 몰아서 실행하지 않는다.
- 그 시각에 대화가 진행 중이면 건너뛴다.
- 포럼 채널은 자율 채팅에서 제외한다.
- `topic`이 없는 채널의 자율 채팅에서는 `WebSearch`와 `WebFetch`를 빼고, 지금 아는 것만으로 말할 수 있는 화제를 고르도록 안내한다.
- `topic`이 있는 채널의 자율 채팅에서는 웹 검색을 쓴다. 첫 발언은 주제에 관한 최근 소식을 검색해 꺼내고, 모든 발언은 주제 범위 안에서 화제를 고르도록 안내한다.
- 사용자 메시지로 시작한 대화는 채널의 `topic`과 관계없이 설정의 `tools`를 모두 쓴다.

### 리액션

캐릭터는 응답에 리액션 제어값을 넣어 직전 메시지에 이모지를 달 수 있다.

```text
[[react:<이모지>]]
```

- 본문 없이 리액션만 할 수 있다.
- 자기 메시지에는 리액션하지 않는다.
- 리액션 결과(성공, 실패, 대상 없음, 자기 메시지라 건너뜀)를 대화 기록에 남긴다.
- 리액션 요청은 리액션을 결정한 Bot의 토큰으로 보낸다.

사용자가 캐릭터의 메시지에 리액션을 달면 그 캐릭터가 한 번 반응한다. 사람의 메시지에 단 리액션은 기록에만 남긴다. Bot이 단 리액션은 무시한다.

리액션 턴의 응답은 턴 프롬프트로 다음과 같이 안내한다.

- 할 말이 있으면 캐릭터의 말로 짧게 반응한다.
- 다른 사람 메시지에 이모지로만 반응하려면 `[[react:<이모지>]]` 줄과 `[[next:stop]]` 줄만 출력한다.
- 반응할 것이 없으면 `[[next:stop]]` 한 줄만 출력한다.

## 입력 처리

### 답장

- 사용자의 메시지가 답장이면 답장 대상을 대화 기록에 붙인다.
- 대상 메시지는 `message.reference.resolved`에서 찾고, 없으면 `fetch_message`로 가져온다.
- 작성자(캐릭터면 `korean_name`), 메시지 ID, 본문 앞 100자를 적는다.
- 대상이 삭제됐거나 가져오지 못하면 메시지 ID와 "원문을 찾을 수 없음"만 적는다.

```text
[답장 대상] 젤리의 메시지 (메시지 ID …) 「본문 앞 100자」
```

### 이미지와 문서

- 사용자가 이미지를 첨부하면 에이전트 루트의 `.discord_attachments/`에 저장한다.
- 페르소나 없는 별도 호출로 이미지 설명을 한 번 만들고 대화 기록에 붙인다.
- 설명은 메시지 ID별로 최근 `attachments.image_captions_max`개까지 보관해 리액션 트리거에서도 쓴다.
- 텍스트 문서 첨부도 같은 폴더에 저장한다. 형식이 `text/*`나 `application/json`이거나, 확장자가 `.md .txt .log .csv .json .yaml .yml .toml .ini .py .js .ts .tsx .jsx .html .css .sh .sql`이면 문서로 본다.
- 문서는 `attachments.text_max_kb`KB까지 받는다. 넘으면 내려받지 않고 `[첨부 문서 크기 초과] 이름 (크기)`만 기록한다.
- 문서 본문은 앞 `attachments.text_preview_chars`자까지 `[첨부 문서] 이름 (크기)` 아래에 기록한다. 이 길이를 넘으면 머리줄에 저장 경로를 적고, 캐릭터는 필요할 때 `Read`로 전체를 연다.
- UTF-8로 읽을 수 없는 문서는 `[첨부 문서 읽기 실패] 이름 (크기)`로 기록한다.
- PDF와 압축 파일은 처리하지 않는다.
- 이미지 설명, 본문, 문서 순으로 기록한다.
- `chat.history_hours`시간이 지난 첨부 파일은 다음 저장 때와 시작할 때 지운다.
- 저장한 첨부는 `memory.sqlite3`의 `attachment_sources`에 원본 메시지 ID·채널 ID로 연결한다.
- 원본 메시지 삭제(단건, bulk, 미등록 채널 포함) 시 `<메시지 ID>_*` 첨부 파일을 지운다.
- 저장 직후 이미 삭제 이벤트를 받은 메시지면 저장한 파일을 바로 지운다.
- 시작할 때 연결된 첨부의 원본을 `fetch_message`로 확인해 `NotFound`면 지운다. 연결 기록이 없는 기존 첨부는 시간 기준으로만 지운다.

### 링크

- 메시지에 링크가 있으면 Discord 링크 미리보기의 제목, 작성자, 설명, 링크를 대화 기록에 붙인다.
- 설명은 `links.description_max_chars`자까지 쓴다.
- 미리보기가 비어 있으면 `links.preview_retry_seconds`초 간격으로 최대 `links.preview_retries`번 메시지를 다시 가져온다.
- 끝내 미리보기가 없으면 링크만 남긴 채 진행한다.

## 대화 기록과 세션

### 대화 기록

- 채널과 스레드마다 기록을 따로 유지한다.
- 기록 한 줄은 순번, 시각, 화자, 본문, Discord 메시지 ID로 구성한다.
- 프롬프트에는 `[표시 이름] (메시지 ID …) 본문` 형식으로 넣는다. 메시지 ID는 도구에 넘기는 값이며 본문에 쓰지 않는다.
- 발언이 여러 조각으로 나뉘어 전송되면 첫 조각의 메시지 ID를 기록하고 전체 조각 ID를 `message_groups`에 연결한다.
- 한 줄 최대 `chat.line_max_chars`자를 유지한다.
- 요약 대상 줄은 `summary.raw_hours`시간보다 오래된 줄과 `chat.history_max_lines`를 넘친 줄이며 기록 앞쪽부터 센다.
- 요약 대상 줄은 요약에 성공하거나 요약을 포기할 때까지 기록에 남아 원문으로 전송된다.
- `chat.history_hours`시간이 지난 줄은 요약이 계속 실패할 때의 상한으로 지운다. 발언을 추가할 때와 프롬프트용 기록을 만들 때 확인한다.
- 기록과 채널 요약문(`summary`), 분할 메시지 연결(`message_groups`), 사용자 판단 대기(`user_wait`)는 `discord/bot/chat_state/<채널 또는 스레드 ID>.json`에 원자적으로 저장한다.
- 시작할 때 등록 채널과 저장 파일이 있는 모든 채널·스레드의 기록과 요약문을 불러온다. 순번은 파일 값과 기록 최댓값 중 큰 값을 쓴다.
- 메시지 삭제 시 기록의 해당 줄(분할 조각 포함)은 지우고 요약문은 바꾸지 않는다.

### 채널 요약

- 실행 조건: 요약 대상 줄이 `summary.batch_min_lines` 이상이거나, 대상 줄이 처음 관측된 뒤 `summary.flush_minutes`분이 지남.
  - 발언 추가 때와 자율 채팅 스케줄러 주기마다 확인한다.
- 채널마다 동시에 하나의 백그라운드 태스크가 채널 락 밖에서 `summary.model`을 도구 없이 한 번 호출한다.
- 한 번에 대상 줄 앞쪽에서 최대 `summary.batch_max_lines`줄, `summary.batch_max_chars`자를 넣는다.
- 입력은 기존 요약문과 대상 줄이며 `<previous_summary>`, `<conversation>` 데이터 블록으로 감싼다.
  - 줄마다 시각, 화자(`agent:<내부 ID>` 또는 `user:<표시 이름>`), 메시지 ID를 붙인다.
  - 도구·출처 기록 줄은 입력에서 빼고, 같은 범위의 요약이 끝나면 함께 지운다.
- 출력은 JSON schema `{summary, persona_events[{agent, kind, content, source_message_ids}]}`이다.
  - 요약문은 `summary.max_chars`자로 자른다.
- 성공하면 요약문을 저장하고 persona 항목을 검증해 저장한 뒤 대상 줄을 지운다.
- 실패하면 `summary.max_attempts`번까지 다시 시도하고, 모두 실패하면 요약에 반영하지 않고 대상 줄을 지우며 경고 로그를 남긴다.
- 턴 프롬프트의 `이전 대화 요약:` 줄에 요약문을 넣고, 요약이 없으면 `(없음)`을 넣는다.

### 채널 전체 삭제

- 에이전트 채널에서 허용 사용자가 `기억 전체 삭제`를 입력하면 처리 봇(`min(runtime.clients)`) 하나가 확인 요청을 보낸다.
- 같은 사용자가 5분 안에 `확인`을 입력하면 실행하고, `취소`를 입력하거나 5분이 지나면 취소한다. 다른 사용자 입력은 무시한다.
- 모델을 호출하지 않으며 명령·확인 메시지는 채팅 기록에 넣지 않는다.
- 삭제 대상: 해당 채널의 기록, 요약문, `chat_state` 파일, 이미지 설명 캐시, 진행 중인 요약 태스크.
- 유지 대상: 서버 장기기억(archive·기억 요청)과 persona 기억.

### 장기기억

- 저장 파일: `discord/bot/memory.sqlite3`. 표는 `memories`, `memory_sources`, `attachment_sources`이다.
  - `memories`: id, guild_id, kind, agent, content, author, channel_id, thread_id, uploaded, 시각
  - kind: `archive`, `explicit`, `persona_event`, `persona_setting`
  - `memory_sources`: 기억과 원본 메시지 ID·채널 ID의 연결
- 공유 단위는 서버(guild)다. 조회는 항상 현재 서버로 한정한다.
- archive 수집
  - 처리 봇이 `on_message`의 자기 메시지 필터 전에 기록한다.
  - 대상: 허용 서버 archive thread에서 허용 사용자나 캐릭터 Bot이 쓴 메시지
  - 형식: 본문과 `[첨부: 파일명]`, 메시지 ID 기준으로 추가·갱신
  - 수정: `on_raw_message_edit`의 메시지로 갱신한다.
  - 시작 동기화: 시작 시 한 번 백그라운드에서 활성·보관 thread 전체를 thread 단위 트랜잭션으로 반영한다.
    - 오류가 없을 때만 시작 이전 archive 기억 중 찾지 못한 메시지를 지운다.
    - 동기화 중 삭제된 메시지는 다시 넣지 않는다.
- 기억 요청(`memory_save`)은 요청 메시지와 대상 메시지(분할 조각 포함)를 원본으로 저장한다.
- persona 기억은 캐릭터별·서버별이다.
  - 겪은 일과 설정은 채널 요약 호출의 `persona_events`로 추출한다.
  - 로스터 밖 캐릭터, 요약 묶음 밖 메시지 ID, 출처 없는 항목은 버린다.
  - 저장 직전에 원본 삭제를 다시 확인한다.
  - 사용자가 캐릭터에게 정한 설정은 `memory_save`의 scope로도 저장한다.
- 삭제
  - 원본 메시지 중 하나라도 삭제되면 해당 기억 전체를 지운다. 잊기 명령은 없다.
  - 삭제 처리기는 채널 확인 전에 기억·첨부를 지우고 최근 삭제 ID를 기록한다.
  - `on_raw_thread_delete`는 그 thread의 메시지를 원본으로 둔 기억과 첨부를 지운다.
  - 시작 시 explicit·persona 기억의 원본을 `fetch_message`로 확인해 `NotFound`면 지우고, 그 밖의 오류는 보존하고 로그를 남긴다.
- 주입
  - 턴 프롬프트 `관련 장기기억:`에 최근 대화 낱말이 맞는 장기기억을 `memory.prompt_max_items`개, `memory.prompt_max_chars`자까지 넣는다.
  - 턴 프롬프트 `캐릭터 기억:`에 자기 겪은 일을 `memory.persona_prompt_max_items`개, `memory.persona_prompt_max_chars`자까지 넣는다.
  - 자기 설정은 시스템 프롬프트 끝에 최신 순으로 `memory.persona_setting_max_chars`자까지 붙인다.
  - 턴 프롬프트 치환은 알려진 키를 긴 이름부터 한 번에 처리해 기록·기억 안의 `$이름`을 다시 치환하지 않는다.
- 저장소 반영
  - `persona_memory_list`가 채널당 1건의 업로드 대기(요청자 ID 포함)를 만든다.
  - 요청자가 정확히 `승인`을 보내면 모델을 거치지 않고 Python이 반영한다. `취소`면 대기를 지우고, 그 밖의 메시지는 무시한다.
  - 대기 건의 캐릭터 Bot만 실행·안내하고 다른 Bot은 같은 메시지를 소비만 한다.
  - `.claude/agents/<role>/MEMORY.md`를 `memory/<role>-<시각>` 브랜치에 커밋하고 `master`로 가는 PR을 만든다.
  - 올린 기억의 원본이 삭제되면 캐릭터별 갱신 필요 표시만 저장하고, 다음 `승인` 때 남은 항목으로 `MEMORY.md`를 다시 만든다.
  - archive thread에서 같은 요청자의 저장소 작업 대기와 업로드 대기가 공존하면 `승인`은 둘 다 실행하지 않고 안내하며, `취소`는 둘 다 취소한다.
  - 대기와 표시는 `discord/bot/memory_approval.json`에 원자 저장한다. 재시작 때 실행 중이던 대기는 실패로 바꾸고, 실행 실패도 실패 상태로 남긴다.
- 캐릭터 기억 파일
  - 로컬 `.claude/agents/<role>/MEMORY.md`가 있으면 요청·채팅 시스템 프롬프트의 Persona 뒤에 `# Character memory` 절로 넣는다.

### 세션

- 매 턴 새 Claude Agent SDK 세션을 쓰고, 프롬프트에 채널 요약문과 원문 기록을 넣는다.
- 세션을 이어 쓰지 않으며 도구 결과 원문 전체를 다음 턴에 전달하지 않는다.
- SDK의 도구 호출·결과에서 도구 이름과 URL을 수집해 `[이번 턴 도구·출처 기록]`을 대화 기록에 남긴다.
  성공한 `WebFetch` 또는 `web_fetch` 결과에 URL이 있으면 `확인`, 오류 결과는 `실패·미확인`, 그 밖은 `결과`로 표시한다.
  호출은 별도로 `사용`으로 기록한다.
- `확인`은 도구 결과의 오류 판정과 URL 유무에 따른 분류이며 전문 열람이나 사실 검증을 보장하지 않는다.
- 도구·출처 기록은 최근 `summary.raw_hours`시간의 원문 창에서만 전달하고 요약 입력에서 제외한다.
  이 기록은 새 사실 확인을 대신하지 않는다.
- 시작할 때 `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1`을 설정해 SDK 세션 기록 파일(`~/.claude/projects`)을 쓰지 않는다.
- 이전 형식 `chat_state` 파일의 `sessions` 항목은 무시한다.

### 턴 프롬프트

매 턴 프롬프트에는 다음 내용이 들어간다.

- 현재 채널 이름과 서버 이름. 스레드이면 부모 채널 이름과 스레드 이름
- assistant 역할에만 `reminders.timezone_offset_hours` 기준 현재 시각 (`현재 시각: YYYY-MM-DD (요일) HH:MM (한국 시간)`). 오프셋이 9가 아니면 `(한국 시간)` 대신 `(UTC+N)`으로 적는다
- 이전 대화 요약
- 대화 기록
- 관련 장기기억과 캐릭터 기억
- 리액션 대상 메시지의 작성자와 본문 앞부분
- 현재 발언 순서와 최대 발언 수
- 사용자 경로 첫 턴의 넘기기 지시

## 프롬프트 구성

시스템 프롬프트는 채널 종류에 따라 다르게 조립한다.
스레드와 포럼 게시글은 부모 채널의 종류를 따른다.

요청 채널(`request_channels`)은 Claude Code preset 뒤에 다음 순서의 작업 프롬프트를 붙인다.

```text
# Global rules         ← /AGENTS.md
# Persona              ← /.claude/agents/<role>/SOUL.md
# Character memory     ← /.claude/agents/<role>/MEMORY.md (있을 때만)
# Discord chat runtime ← /discord/prompts/RUNTIME.md에 prompts.py의 build_request_system_prompt가 역할·도구 정보를 주입한다.
```

채팅 채널(`chat_channels`)은 Claude Code preset 없이 다음 순서의 채팅 프롬프트 문자열만 쓴다.

```text
# Global rules          ← /AGENTS.md
# Persona               ← /.claude/agents/<role>/SOUL.md
# Character memory      ← /.claude/agents/<role>/MEMORY.md (있을 때만)
# Discord chat channel  ← /discord/prompts/CHAT.md에 prompts.py의 build_chat_system_prompt가 도구·기억 안내를 주입한다.
## Response control     ← RUNTIME.md의 `## Response control` 절(채팅 변형)
```

`RUNTIME.md`와 `TURN.md`의 줄 머리 `{{request:...}}`·`{{chat:...}}` 표식은 채널 종류에 맞는 내용만 남기고 나머지는 지운다.
깨진 표식은 프롬프트 생성 오류로 처리한다.

채팅 프롬프트는 작업용 담당 넘기기·지식 저장소 절차·작업 요청 조건을 넣지 않는다.
동료 명단에는 이름·내부 ID·종·MBTI만 넣는다.
사실 확인 규칙은 그대로 넣는다.
`CHAT.md`가 없으면 Bot이 시작하지 않는다.

역할 지침(`/.claude/agents/<role>/AGENTS.md`)은 프롬프트에 넣지 않는다.
캐릭터 이름은 역할 지침 frontmatter의 `name`에서 읽는다.

요청 채널의 `Discord chat runtime`에는 다음 내용이 들어간다.

- Discord 봇 계정으로 참여한다는 사실
- 표시 이름 사용 규칙
- 자기 역할과 담당, 넘기기 규칙
- 역할 전용 도구 안내
- 지식 저장소 안내
- 장기기억 안내(`$memory_block`, 모든 역할 동일)
- 사실 확인 규칙
- 말투와 길이 규칙
- 리액션과 다음 화자 제어 규칙
- 다른 캐릭터의 종·MBTI, 담당과 전용 도구 목록

`/AGENTS.md`, 역할 지침, 자기 `SOUL.md`, 다른 캐릭터의 `SOUL.md`, `discord/prompts/RUNTIME.md`, `TURN.md`, `CHAT.md`, 자기 `MEMORY.md`의 수정 시각이나 크기가 바뀌면 다음 발언에서 해당 역할의 시스템 프롬프트를 다시 만든다.
`MEMORY.md`가 없으면 오류 없이 없는 파일로 감시한다.
documenter는 `discord/prompts/ARCHIVE.md`도 변경 감지 대상에 포함하며, 지식 저장소 설정이 있을 때 해당 문서를 지식 안내로 읽는다.

로컬 Skill은 `shared/skills/wiki/SKILL.md`를 통해 파일 원본에 연결한다.
Discord는 이 Skill의 자동 로딩에 의존하지 않고 `ARCHIVE.md`와 전용 도구로 원격의 같은 schema 경로에 연결한다.
절차 본문은 Nodebase 원격 저장소의 `$repo_name/schema/`에만 유지한다.

실행 가능한 에이전트 목록은 `.claude/agents/*/AGENTS.md`의 `name`에서 읽는다. 설정의 `name`이 이 값과 다르면 시작하지 않는다.

모델과 effort는 `chat.model`, `chat.effort`를 쓰고, `request_channels` 항목에 `model`이나 `effort`가 있으면 그 채널에서는 그 값을 쓴다.
스레드와 포럼 게시글은 부모 채널의 값을 따른다.
`archive_forum_id` 채널과 그 아래 게시글은 `model` 지정값이 없으면 `claude-opus-5-5`를 쓰며 effort는 채널 지정값 또는 `chat.effort`를 따른다.
이미지 설명은 `chat.model`을, 채널 요약과 persona 추출은 `summary.model`을 쓴다. `setting_sources=[]`로 파일시스템의 Claude Code 설정을 상속하지 않는다.

## 도구

### 공통 도구

- 설정의 `tools`
  - 도구 이름은 설정의 `agents[].tools` 값을 쓴다.
- `server_channels`
  - 이 Bot이 볼 수 있는 채널을 카테고리, 종류, 에이전트 채널 여부, 현재 채널 여부와 함께 최대 `tools.server_channels_max`개 반환한다.
  - 포럼은 사용할 수 있는 태그 이름도 반환한다.
- `forum_post`
  - 에이전트 채널로 등록된 일반 포럼에 제목, 본문(2000자), 태그 1~5개로 글을 올린다.
  - `archive_forum_id`가 가리키는 포럼은 제외한다.
  - 태그는 이름으로 받아 포럼 태그와 대소문자 구분 없이 맞춘다.
  - 없는 태그가 있으면 글을 올리지 않고 사용할 수 있는 태그를 반환한다.
  - 본문은 새 글 기록에, `(포럼 글 작성) 포럼 / 제목 / 태그`는 현재 채널 기록에 남긴다.
- `memory_search`
  - 입력: query
  - 제한: 현재 서버의 장기기억(archive, 기억 요청)만 최대 `memory.search_max_results`개 반환한다.
- `memory_save`
  - 입력: content, target_message_id(선택, 생략 시 요청 메시지), scope(`guild`, `self`, 캐릭터 내부 ID)
  - 제한:
    - 사용자 메시지로 시작한 대화의 턴에서만 쓰며 요청 메시지는 그 대화의 트리거 메시지다.
    - 현재 채널 기록에 없는 메시지 ID와 모르는 scope는 거부한다.
    - scope가 `guild`면 서버 장기기억, 그 밖이면 해당 캐릭터의 persona 설정으로 저장한다.
- `persona_memory_list`
  - 입력: 없음
  - 제한:
    - 현재 서버의 자기 persona 기억만 반환한다.
    - 아직 올리지 않은 항목이나 갱신 필요 표시가 있으면 채널당 1건의 업로드 대기를 만든다.
    - 대기는 `archive_repository`가 있고, 사용자 메시지로 시작한 턴이며, 요청자 ID가 있고, 같은 요청자의 저장소 작업 대기가 없을 때만 만든다.
    - GitHub를 호출하지 않는다. 브랜치와 PR은 요청자의 `승인` 뒤 Python이 만들고 병합은 사용자가 한다.
- 장기기억 도구는 서버 ID와 기억 저장소가 있을 때만 붙는다.

### 역할 전용 도구

지식 도구 중 원본 로딩·목록·검색은 `discord/bot/archive_reader.py`가 담당한다.
도구 등록은 `discord/bot/archive_tools.py`, 역할·archive thread 노출 조건은 `discord/bot/bot.py`에서 관리한다.

- `poll_create`
  - 입력: 질문, 선택지, 기간(시간), 복수 선택 여부
  - 제한:
    - 선택지 2~10개, 선택지 55자, 질문 300자, 기간 1~768시간
    - discord.py에 `Poll`이 있을 때만 제공
- `thread_create`
  - 입력: 이름, 메시지 ID(선택), 첫 글(선택)
  - 제한:
    - 이름 1~100자, 첫 글 2000자
    - 스레드 안에서는 만들지 않는다.
    - 현재 채널 기록에 `(스레드 생성) 이름`을 남기고, 첫 글은 스레드 기록에 남긴다.
- `message_pin`
  - 입력: 메시지 ID
  - 제한: 현재 채널의 메시지만
- `channel_history`
  - 입력: 개수
  - 제한: 1~`tools.channel_history_max`개, 오래된 순
- `reminder_set`
  - 입력: 분, 내용
  - 제한: 1분~`reminders.max_days`일(분 단위), 내용 1~1500자
- `reminder_repeat`
  - 입력: 요일, 시각, 내용
  - 제한: 요일은 `매일`, `평일`, `주말` 또는 `월,수,금`, 시각은 `HH:MM`, 내용 1~1500자
- `reminder_list`
  - 입력: 없음
  - 제한: 현재 시각과 현재 채널의 알림 목록을 번호와 함께 반환
- `reminder_cancel`
  - 입력: 번호
  - 제한: 현재 채널의 알림만
- `archive_thread_start`
  - 입력: 제목, 내용
  - 제한:
    - documenter에게만 제공한다.
    - 최초 사용자 메시지를 첫 글로 쓰고, 전달된 내용이 다르면 작업 자료로 덧붙인다.
    - 최초 사용자 메시지의 첨부를 같은 thread에 복사한다.
- `archive_read`
  - 입력: 경로, 브랜치(선택)
  - 제한:
    - archive thread에서만 제공한다.
    - 브랜치를 생략하면 `master`에서 읽는다.
- `archive_branch`
  - 입력: 브랜치
  - 제한: archive thread에서 브랜치 commit SHA를 조회한다.
- `archive_workflow_open`
  - 입력: operation, branch(선택)
  - 제한: ingest/query/lint만 받으며 기본 master를 commit SHA로 확정하고 같은 commit의 Nodebase 지침과 schema 전체 및 blob SHA를 반환한다.
- `archive_list`
  - 입력: path, ref, page·per_page(선택)
  - 제한: AGENTS.md와 raw/wiki/schema 내부 파일을 commit SHA 기준으로 페이지당 기본 50개, 최대 100개 열거한다.
- `archive_search`
  - 입력: query, ref, page·per_page·limit(선택)
  - 제한: wiki/ 본문을 파일 페이지당 최대 20개, 결과 최대 100개로 검색하고 경로·행·본문 일부와 누락·잘림 정보를 반환한다.
- `archive_history`
  - 입력: path, ref, page·per_page(선택)
  - 제한:
    - Nodebase 지식 경로의 commit 이력을 ref(commit SHA 또는 브랜치) 기준 최신순으로 반환한다.
    - 페이지당 기본 20개, 최대 100개이다.
    - 커밋마다 sha, 날짜, 메시지 첫 줄을 반환한다.
- `archive_issue_stage`
  - 입력: 타입, 제목, 본문, label, 템플릿 경로와 SHA
  - 제한: 원격 Issue 템플릿 기반 미리보기를 요청자 소유 pending으로 저장한다.
- `archive_stage`
  - 입력: 전체 operations, 커밋 메시지, 기준 commit SHA
  - 제한: Issue 승인 뒤 전체 변경 세트를 요청자 소유 pending 하나로 저장한다.
- `archive_pr_stage`
  - 입력: 제목, 본문, 템플릿 경로와 SHA
  - 제한: 검증된 변경 커밋 뒤 PR 미리보기를 별도 pending으로 저장한다.

메시지 ID는 JavaScript 정수 범위를 넘으므로 문자열로 받는다.

신규 조회 도구의 path는 저장소 기준 POSIX 경로이며 AGENTS.md와 raw/wiki/schema 경계 밖의 경로와 우회 표기를 거부한다.
ref에는 40자리 commit SHA를 사용한다.
`archive_history`의 ref는 브랜치 이름도 받는다.
schema의 상대 경로와 `.github/` 템플릿은 Nodebase 저장소 루트 기준이다.
검색 파일은 UTF-8 텍스트이며 파일당 1,000,000 bytes, 일치 본문은 결과당 2,000자 한도를 적용한다.
파일 페이지의 `next_page`로 후속 범위를 확인하며 tree 잘림·읽지 못한 파일·결과 생략을 전체 검색으로 표현하지 않는다.
원본 누락은 경로를 포함한 오류이며 대체 schema를 추정하지 않는다.

도구 실패는 예외를 던지지 않고 실패 사유를 도구 결과로 돌려준다.

## 보안

### 허용 목록

- `allowed_guild_ids`에 없는 서버의 메시지와 리액션은 처리하지 않는다.
- `allowed_user_ids`에 없는 사용자의 메시지와 리액션은 처리하지 않는다.
- 두 목록이 비어 있으면 해당 제한을 적용하지 않는다.

### 승인

Stage 도구는 외부 객체를 만들지 않고 `discord/bot/archive_workflow.json`에 pending만 원자 저장한다.

- pending을 만든 사용자만 같은 archive thread에서 `승인` 또는 `취소`할 수 있다.
- `승인`과 `취소`는 모델을 거치지 않고 Python이 요청자 ID와 pending을 대조해 처리한다.
- Issue 승인 시 원격 템플릿 SHA, 제목 접두사, label을 다시 확인하고 Issue와 `<타입>/<이슈번호>` 브랜치를 만든다.
- 변경 승인 시 기준 commit SHA와 기존 파일 SHA를 확인하고 전체 변경 세트를 Git tree와 커밋 하나로 적용한다.
- PR 승인 시 원격 템플릿 SHA를 다시 확인한다. 같은 방향의 열린 PR이 있으면 기존 주소를 반환한다.
- 외부 API 응답이 불명확한 오류는 pending을 `failed`로 저장하고 자동 재호출하지 않는다.
- Issue, 변경 세트, PR의 승인은 서로 별개다.
- `master` 반영, PR 병합, 브랜치 삭제는 사용자가 GitHub에서 처리한다.

### GitHub 토큰 권한

`archive_repository.token`은 지식 저장소 하나에만 접근하는 fine-grained PAT로 발급한다.

| 권한          | 수준           | 호출하는 API                                                                                      |
| ------------- | -------------- | ------------------------------------------------------------------------------------------------- |
| Contents      | Read and write | 파일 조회(`/contents`), 브랜치 조회·생성(`/git/ref`, `/git/refs`), tree와 commit 생성 및 ref 갱신 |
| Issues        | Read and write | Issue 생성(`/issues`)                                                                             |
| Pull requests | Read and write | 열린 PR 조회, PR 생성(`/pulls`)                                                                   |

`.github/workflows/` 안의 파일은 Workflows 권한이 추가로 필요하다. 이 권한은 주지 않는다.

### 파일 읽기 제한

`Read`는 에이전트 루트의 `.discord_attachments/` 안의 파일만 읽을 수 있다.

- `PreToolUse` 훅이 경로를 실제 경로로 풀어 첨부 폴더 안인지 확인한다.
- 폴더 밖이면 거부하고 `read blocked` 경고 로그를 남긴다.
- 상대 경로, `..` 우회, 폴더 밖을 가리키는 링크도 실제 경로 기준으로 판정한다.

작업 디렉터리 안의 파일 읽기는 권한 규칙 없이 승인되므로 허용 규칙이 아니라 훅으로 막는다. 이 제한은 설정 파일의 Bot 토큰과 GitHub PAT가 채팅에 노출되지 않게 한다.

### 외부 입력

웹 페이지, 링크 미리보기, 채널 기록은 사용자가 쓰지 않은 내용일 수 있다. 이 내용에 지시가 섞여도 파일 읽기 제한과 PR 병합 승인으로 영향 범위를 채팅과 작업 브랜치 안으로 한정한다.

## 오류 및 예외 처리

### 설정 오류

다음 경우 시작하지 않고 오류를 출력한다.

- 설정 파일이 없거나 JSON이 아님
- 최상위 키가 허용 목록 밖에 있음
  - 허용 목록: `agents`, `allowed_guild_ids`, `allowed_user_ids`, `archive_forum_id`, `archive_repository`, `attachments`, `chat`, `chat_channels`, `links`, `memory`, `reminders`, `request_channels`, `summary`, `tools`
  - 오류 문구: `알 수 없는 설정 키: <키 목록>. 채팅 채널은 chat_channels에 적는다.`
- `request_channels`와 `chat_channels`가 모두 비어 있음
- 같은 채널이 `request_channels`와 `chat_channels`에 함께 있음
- `chat_channels`의 객체 항목에 `id`가 없거나, `per_day`가 음수이거나, `topic`이 비어 있음
- `request_channels`의 객체 항목에 `id`가 없거나, `model`이나 `effort`가 비어 있거나, `effort`가 허용 값이 아님
- `archive_forum_id`가 있는데 `archive_repository`가 없거나, `request_channels`에 등록되지 않았거나, `chat_channels`에도 등록됨
- `archive_forum_id`가 있는데 documenter 역할 에이전트가 없거나, 해당 ID가 Bot이 볼 수 있는 ForumChannel이 아님
- `chat.auto_conversations_per_day`가 남아 있음
- `chat`, `attachments`, `links`, `reminders`, `tools`에 `chat.sdk_max_turns` 외의 빠진 키가 있음.
  - 빠진 키를 모두 한 번에 나열한다.
- 선택 절 `summary`, `memory`가 객체가 아니거나 지정한 값이 비어 있거나 1 미만임
- `chat.effort`가 허용 값이 아님
- 문자열 값이 비어 있음
- 숫자 값이 범위를 벗어남. `chat.turn_delay_seconds`는 0 이상, `reminders.timezone_offset_hours`는 -12~14, 나머지는 1 이상
- 에이전트 규칙 파일이 없음
- 설정의 `role`이나 `name`이 에이전트 규칙 파일과 맞지 않음
- 같은 에이전트가 중복 등록됨

### 접속 실패

Bot 하나라도 Discord 접속에 실패하면 프로세스 전체가 종료된다.

### 발언 실패

채팅 처리 중 예외가 나면 로그에 `chat handling failed`로 기록하고, 채널에는 `chat.failure_notice`만 보낸다.
이 안내를 보내지 못해도 추가 예외를 내지 않는다.

### 빈 응답

본문과 리액션이 모두 없는 응답은 반응하지 않은 것으로 본다.
채널에 본문을 보내지 않고 다음 화자 선택은 그대로 따른다.
실제 도구·출처 기록이 있으면 본문이 없어도 해당 기록은 남긴다.

원인을 구분할 수 있도록 SDK 결과 종류(`subtype`), 내부 턴 수, 원문 앞 200자를 `chat turn empty` 경고 로그로 남긴다.

### 대화 추적

턴마다 대화 ID(`conversation`), 채널, 발언 순서, 화자, 선택한 다음 화자를 `chat turn` 로그로 남긴다.
사용자 판단 대기를 저장한 턴은 이 로그 전에 대화를 종료한다.

- `chat lock wait|acquire|cancel`은 대화별 채널 락 대기·획득·취소를 기록한다.
- `chat pending change`는 사용자 메시지별 pending 등록과 해제 사유를 기록한다.
- `chat turn discarded reason=pending_discard`는 응답 생성 중 새 사용자 메시지가 와서 응답을 보내지 않은 경우다.
- `chat end reason=`은 `pending_before_turn`, `pending_after_turn`, `pending_discard`, `turn_limit`, `model_stop`, `invalid_control`, `invalid_next`, `missing_client`로 종료 원인을 구분한다.

채팅 턴과 요약 호출마다 ResultMessage의 `usage`, `total_cost_usd`를 `token usage kind=<chat|summary>` 로그 한 줄로 남긴다.

## 미정 항목

현재 명세만으로 확정할 수 없는 항목은 다음과 같다.

- 운영 계정의 Claude 인증 상태와 GCP VM에서의 실제 실행 결과

설치와 인증 절차는 [discord.md](discord.md)와 [discord-gcp.md](discord-gcp.md)를 따른다.
인증 완료나 VM 정상 실행 여부는 해당 환경에서 응답과 로그로 확인한다.
다른 Bot과 웹훅 메시지는 현재 코드에서 대화 트리거로 처리하지 않는다.
