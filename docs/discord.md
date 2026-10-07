# Discord Bot 설치

에이전트별 Discord Bot들을 하나의 Python 프로세스에서 실행한다.

Bot들은 등록된 에이전트 채널에서 캐릭터로 대화한다. 역할마다 맡는 요청과 전용 도구가 있다. 로컬 저장소를 수정하거나 명령을 실행하지 않는다.

## 준비 사항

다음 항목을 준비한다.

- Discord 서버 관리 권한
- Python과 `pip`
- Discord Guild ID
- Discord 사용자 ID
- 에이전트 채널 ID
- 에이전트별 Bot token
- 지식 저장소용 GitHub 저장소와 PAT (선택)

Bot 런타임은 `discord/bot/`의 상위 폴더인 `discord/`를 에이전트 루트로 사용한다.
이 문서의 설정·실행·저장 경로는 별도 표시가 없으면 프로젝트 루트 기준이다.

## Discord ID 확인

Discord에서 개발자 모드를 켠다.

1. Discord의 `사용자 설정`을 연다.
2. `개발자`로 이동한다.
3. `개발자 모드`를 켠다.

필요한 ID를 복사한다.

- 서버 아이콘 우클릭 → `서버 ID 복사하기`
  - 서버 추가하기: 서버 목록을 스크롤하여 최하단 `+` 버튼 클릭
- 사용자 프로필 좌클릭 → `사용자 ID 복사하기`
- 에이전트 채널 우클릭 → `채널 ID 복사하기`
  - 채널 추가하기: 서버에서 채널 목록 우측 `+` 버튼 클릭

## Discord Bot 생성

[Discord Developer Portal](https://discord.com/developers/applications)에서 봇을 만든다.

각 Bot마다 다음 작업을 반복한다.

1. `신규 애플리케이션`을 선택한다.
2. 애플리케이션 이름을 입력한다.
3. `봇` 설정으로 이동한다.
4. 봇 이름과 프로필 이미지를 설정한다.
5. 봇 토큰을 발급한다.
6. `Privileged Gateway Intents`의 `Message Content Intent`를 켠다.
   - 메시지 본문, 첨부 파일, 링크 미리보기를 수신한다.
7. 변경 사항을 저장한다.
8. `설치` 설정으로 이동한다.
9. `설치 환경`에서 `길드 설치`가 활성화되어 있는지 확인한다.

봇 토큰은 외부에 공개하거나 Git에 커밋하지 않는다. 토큰이 유출되면 Discord Developer Portal에서 즉시 재발급해야 한다.

봇 프로필 이미지는 AI를 이용해 편집할 수 있다.

```text
<캐릭터>를 discord 봇 프로필 이미지 규격으로, portrait에 맞도록 상반신만 나오게 스케일업해 줘.
배경색은 ...으로 해. 크기 외 이미지를 임의로 변경하지 마.
작업이 끝난 나머지 이미지의 규격과 캐릭터 크기를 참고해. 최외곽 아웃라인의 계단현상을 제거해. 요청사항 외 작업은 하지 마.
```

```text
SOUL.md 를 참고해서 캐릭터 성격과 어울리는 화풍을 고민해서 적용해줘.
...을 배경으로 ... 모습을 그려줘. 캐릭터의 성격과 상황을 고려해서 옷차림을 새로 작업해.
일러스트 landscape 이미지로 생성해줘. 캐릭터보다 배경을 더 강조하고, 캐릭터는 배경에 어우러지도록 그려. 이미지 크기는 1536*1024로 해. 이미지에 텍스트는 제외해.
```

## Discord 서버 추가

다음은 각 애플리케이션을 Discord 서버에 추가하는 과정이다.

1. `OAuth2` 설정으로 이동한다.
2. OAuth2 URL 생성기에서 `bot`을 선택한다.
3. 하단 목록에서 봇에게 다음 권한을 부여한다.

| 권한                     | 권한 플래그                | 사용하는 기능                                          |
| ------------------------ | -------------------------- | ------------------------------------------------------ |
| 채널 보기                | `VIEW_CHANNEL`             | 모든 기능                                              |
| 메시지 보내기            | `SEND_MESSAGES`            | 모든 발언                                              |
| 메시지 기록 보기         | `READ_MESSAGE_HISTORY`     | 리액션 대상 조회, 채널 기록 조회, 링크 미리보기 재조회 |
| 반응 추가하기            | `ADD_REACTIONS`            | 캐릭터의 이모지 리액션                                 |
| 스레드에서 메시지 보내기 | `SEND_MESSAGES_IN_THREADS` | 포럼 게시글과 스레드 안의 발언                         |
| 공개 스레드 만들기       | `CREATE_PUBLIC_THREADS`    | director의 스레드 만들기                               |
| 메시지 고정              | `PIN_MESSAGES`             | director의 메시지 고정                                 |
| 투표 보내기              | `SEND_POLLS`               | director의 투표 올리기                                 |

`PIN_MESSAGES`는 `메시지 관리`(`MANAGE_MESSAGES`)와 별개의 권한이다.

4. 최하단의 생성된 URL로 접속하여 해당 봇을 서버에 추가한다.
5. 이 과정을 모든 봇에 반복한다.
6. 완료 후 서버 구성원 목록에 생성한 봇들이 표시되는지 확인한다.

## 설정 파일 생성

프로젝트 루트에서 `discord/config.example.json`을 `discord/config.json`으로 복사한다.

### Windows PowerShell

```powershell
Copy-Item discord/config.example.json discord/config.json
```

### Windows 명령 프롬프트

```cmd
copy discord\config.example.json discord\config.json
```

### Windows Git Bash

```bash
cp discord/config.example.json discord/config.json
```

### macOS 또는 Linux

```bash
cp discord/config.example.json discord/config.json
```

## Discord 설정 입력

`discord/config.json`을 열고 다음 값을 입력한다.

- `allowed_user_ids`
  - 필수: 권장
  - 의미: Bot에게 말을 걸 수 있고 알림에서 멘션되는 사용자 ID 목록
- `allowed_guild_ids`
  - 필수: 권장
  - 의미: Bot이 동작할 서버 ID 목록
- `request_channels`
  - 필수: 둘 중 하나 이상
  - 의미:
    - 사용자 메시지에만 답하는 채널 목록
    - 항목은 채널 ID이거나 `id`, 선택으로 `model`, `effort`를 가진 객체
- `chat_channels`
  - 필수: 둘 중 하나 이상
  - 의미:
    - 채팅 프롬프트로 답하고 per_day만큼 자율 채팅도 하는 채널 목록
    - 항목은 채널 ID이거나 `id`, 선택으로 `per_day`, `topic`을 가진 객체
    - `per_day`를 생략하면 0
- `chat`
  - 필수: 필수
  - 의미: 채팅 동작 값. 하단 표 참고
- `attachments`
  - 필수: 필수
  - 의미: 첨부 처리 값. 하단 표 참고
- `links`
  - 필수: 필수
  - 의미: 링크 미리보기 값. 하단 표 참고
- `reminders`
  - 필수: 필수
  - 의미: 알림 값. 하단 표 참고
- `tools`
  - 필수: 필수
  - 의미: 도구가 한 번에 읽는 양. 하단 표 참고
- `summary`
  - 필수: 선택
  - 의미: 이전 대화 요약 값. 하단 표 참고
- `memory`
  - 필수: 선택
  - 의미: 장기기억 주입·검색 한도. 하단 표 참고
- `archive_forum_id`
  - 필수: 선택
  - 의미: 지식 저장소 작업 전용 Discord 포럼 ID
- `archive_repository`
  - 필수: 선택
  - 의미: 지식 저장소 GitHub 저장소 `owner`, `repo`, `token`
- `agents`
  - 필수: 필수
  - 의미: 에이전트별 `role`, `name`, `korean_name`, `token`, `tools`

`allowed_user_ids`나 `allowed_guild_ids`를 비워 두면 해당 제한이 꺼진다. 누구나 Bot에게 말을 걸 수 있게 되므로 두 값을 모두 입력한다.
`chat.sdk_max_turns`는 생략할 수 있으며, 생략하면 SDK 턴 상한을 두지 않는다.
값을 지정하면 1 이상의 정수여야 하며 0, 음수, 명시적인 null은 오류다.
`chat`, `attachments`, `links`, `reminders`, `tools`의 나머지 키는 모두 필수이고 코드에 기본값이 없다.
빠진 키가 있으면 Bot은 빠진 키를 모두 나열한 오류를 내고 시작하지 않는다. 값은 `discord/config.example.json`에 적힌 값에서 시작한다.

| 키                                | 의미                                                                                              |
| --------------------------------- | ------------------------------------------------------------------------------------------------- |
| `chat.model`                      | 채팅 발언과 이미지 설명에 쓰는 모델                                                               |
| `chat.effort`                     | 채팅 발언의 effort. `low`, `medium`, `high`, `xhigh`, `max` 중 하나                               |
| `chat.sdk_max_turns`              | 캐릭터가 한 번 말하는 동안 쓸 수 있는 도구 호출 턴 수. 생략 시 SDK 턴 상한 없음                   |
| `chat.history_hours`              | 요약이 계속 실패할 때 대화 기록을 지우는 상한 시간, 첨부 파일 보관 시간                           |
| `chat.history_max_lines`          | 원문으로 보내는 기록 최대 줄 수. 넘친 줄은 요약 대상                                              |
| `chat.line_max_chars`             | 기록 한 줄의 최대 글자 수                                                                         |
| `chat.failure_notice`             | 채팅 처리에 실패했을 때 채널에 보내는 안내                                                        |
| `chat.scheduler_interval_seconds` | 자율 채팅 시각을 확인하는 주기                                                                    |
| `chat.turn_delay_seconds`         | 캐릭터 발언 사이에 기다리는 초. 0이면 기다리지 않음                                               |
| `chat.max_turns`                  | 한 대화의 최대 발언 수. 1이면 첫 화자가 차례를 넘길 때 응답 없이 대화가 끝나므로 2 이상으로 둔다. |
| `attachments.text_max_kb`         | 받는 텍스트 문서의 최대 크기(KB)                                                                  |
| `attachments.text_preview_chars`  | 대화 기록에 넣는 문서 앞부분 글자 수                                                              |
| `attachments.image_captions_max`  | 보관하는 이미지 설명 개수                                                                         |
| `links.preview_retries`           | 링크 미리보기가 비었을 때 다시 가져오는 횟수                                                      |
| `links.preview_retry_seconds`     | 다시 가져오기 전에 기다리는 초                                                                    |
| `links.description_max_chars`     | 기록에 넣는 링크 설명 최대 글자 수                                                                |
| `reminders.timezone_offset_hours` | 알림 시각의 UTC 오프셋. 9이면 한국 시간                                                           |
| `reminders.max_days`              | 한 번짜리 알림을 예약할 수 있는 최대 일수                                                         |
| `reminders.check_seconds`         | 알림 시각을 확인하는 주기                                                                         |
| `reminders.repeat_grace_minutes`  | 봇이 꺼져 있던 동안 지난 반복 알림을 보내 주는 유예 시간(분)                                      |
| `reminders.late_minutes`          | 한 번짜리 알림을 늦은 알림으로 표시하는 기준(분)                                                  |
| `tools.channel_history_max`       | `channel_history`가 한 번에 읽는 최대 메시지 수                                                   |
| `tools.server_channels_max`       | `server_channels`가 보여 주는 최대 채널 수                                                        |

`summary`와 `memory`는 선택 절이다.
절이나 키를 생략하면 아래 기본값을 쓰고, 지정한 숫자는 1 이상, 문자열은 비어 있지 않아야 한다.

| 키                        | 기본값             | 의미                                                     |
| ------------------------- | ------------------ | -------------------------------------------------------- |
| `summary.raw_hours`       | 2                  | 원문으로 보내는 최근 대화 시간. 더 오래된 줄은 요약 대상 |
| `summary.model`           | `claude-haiku-4-5` | 요약과 캐릭터 기억 추출에 쓰는 모델                      |
| `summary.max_chars`       | 2000               | 채널 요약문 최대 글자 수                                 |
| `summary.batch_min_lines` | 30                 | 요약을 실행하는 최소 대상 줄 수                          |
| `summary.batch_max_lines` | 150                | 요약 1회에 넣는 최대 줄 수                               |
| `summary.batch_max_chars` | 20000              | 요약 1회에 넣는 최대 글자 수                             |
| `summary.flush_minutes`   | 60                 | 대상 줄이 적어도 요약을 실행하는 대기 시간(분)           |
| `summary.max_attempts`    | 3                  | 요약 실패 시 시도 횟수. 모두 실패하면 대상 줄을 버림     |

| 키                                 | 기본값 | 의미                                         |
| ---------------------------------- | ------ | -------------------------------------------- |
| `memory.prompt_max_items`          | 5      | 턴 프롬프트에 자동으로 붙이는 장기기억 개수  |
| `memory.prompt_max_chars`          | 1500   | 자동으로 붙이는 장기기억 최대 글자 수        |
| `memory.search_max_results`        | 10     | `memory_search` 최대 결과 수                 |
| `memory.persona_prompt_max_items`  | 5      | 턴 프롬프트에 붙이는 캐릭터 기억 개수        |
| `memory.persona_prompt_max_chars`  | 1000   | 캐릭터 기억 최대 글자 수                     |
| `memory.persona_setting_max_chars` | 1000   | 시스템 프롬프트에 붙이는 캐릭터 설정 글자 수 |

`chat.turn_delay_seconds`는 0 이상, `reminders.timezone_offset_hours`는 -12에서 14 사이, 나머지 숫자는 1 이상이어야 한다.
문자열 값은 비어 있으면 안 된다.
기존 설정에 남은 `chat.user_min_turns`와 `chat.auto_min_turns`는 읽지 않으며 대화 진행에 영향을 주지 않는다.

에이전트는 다음 6개를 사용한다.

| role       | 에이전트 채널 담당                                                                        |
| ---------- | ----------------------------------------------------------------------------------------- |
| director   | 담당이 불분명하거나 여러 역할이 필요한 요청 배정, 투표 올리기, 스레드 만들기, 메시지 고정 |
| researcher | 새 정보 조사, 최신 정보 확인                                                              |
| worker     | 코드 조각, 글 초안, 정리, 계산 같은 결과물 작성                                           |
| reviewer   | 발언 검토, 인용 대조(채널 기록), 사실 검증(웹 검색)                                       |
| documenter | 지식 저장소 조회와 승인 작업 준비                                                         |
| assistant  | 알림 같은 생활형 요청                                                                     |

`name`은 내부 호출 ID이고 `korean_name`은 채팅에서 사용하는 표시 이름이다.
`name`은 `.claude/agents/<role>/AGENTS.md`의 `name`과 같아야 한다.

`tools`는 모든 에이전트에 공통으로 주는 기본 도구 목록이다.
도구 이름은 설정의 `agents[].tools` 값을 쓴다. 역할 전용 도구는 코드가 역할에 따라 자동으로 붙인다.

설정 키의 정확한 형식은 `discord/config.example.json`을 따른다.

채팅 캐릭터 발언은 `chat.model`, `chat.effort`를 쓴다.
`request_channels`의 객체 항목에 `model`이나 `effort`를 적으면 그 채널에서는 그 값을 쓴다.
포럼 게시글과 스레드는 부모 채널의 값을 따른다.
`archive_forum_id` 포럼과 그 아래 게시글은 `model`을 따로 지정하지 않으면 `claude-opus-5-5`를 쓴다.
이 경우에도 `effort`는 채널 지정값 또는 `chat.effort`를 따른다.

```json
"request_channels": [
  "<REPLY_ONLY_CHANNEL_ID>",
  { "id": "<EXAMPLE_REPLY_ONLY_CHANNEL_ID>", "model": "<MODEL>", "effort": "<EFFORT>" }
]
```

## 지식 저장소 연결

`archive_repository`와 `archive_forum_id`를 설정하면 documenter 역할(pepper)이 전용 archive forum에서 GitHub 저장소 작업을 처리한다.

```json
{
  "request_channels": ["<ARCHIVE_FORUM_ID>"],
  "archive_forum_id": "<ARCHIVE_FORUM_ID>",
  "archive_repository": {
    "owner": "<GITHUB_OWNER>",
    "repo": "<GITHUB_REPO_NAME>",
    "token": "<GITHUB_PAT_WITH_CONTENTS_ISSUES_PULL_REQUESTS_WRITE>"
  }
}
```

`archive_forum_id`는 `request_channels`에도 등록한 ForumChannel이어야 한다.
`chat_channels`에는 등록할 수 없다. 이 값을 쓰려면 `archive_repository`와 documenter 역할 에이전트가 모두 있어야 한다.
Bot이 시작할 때 해당 ID가 접근 가능한 ForumChannel인지 확인한다.

`archive_repository`를 설정하지 않으면 지식 저장소 도구와 관련 안내가 모두 빠진다.

### 토큰 권한

지식 저장소 하나에만 접근하는 fine-grained PAT를 권장한다.

| 권한          | 수준           | 사용하는 기능                                 |
| ------------- | -------------- | --------------------------------------------- |
| Contents      | Read and write | 파일 조회, 브랜치 조회와 생성, 변경 세트 커밋 |
| Issues        | Read and write | Issue 생성                                    |
| Pull requests | Read and write | 열린 PR 조회, PR 생성                         |

- 발급 위치는 GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens이다.
- Repository access는 Only select repositories로 지식 저장소 하나만 고른다.
- `.github/workflows/` 안의 파일은 Workflows 권한이 추가로 필요하다. 이 권한은 주지 않으므로 워크플로 파일은 기록할 수 없다.
- classic PAT를 쓰면 `repo` 범위가 필요하다. 이 범위는 계정의 모든 저장소에 적용되므로 fine-grained PAT를 권장한다.

### 원본 연결

사용자는 “이 자료를 위키에 정리해줘”, “위키에서 찾아줘”, “위키 점검해줘”처럼 요청하며 schema 경로를 지정할 필요가 없다.

Discord에서 사용하려면 설정된 원격 저장소의 대상 브랜치에 `archive/AGENTS.md`와 해당 `archive/schema/` 파일이 있어야 한다.
로컬 schema를 수정하거나 VM에 파일을 복사하는 것만으로 원격 원본이 갱신되지는 않는다.
원격 반영은 별도의 승인된 저장소 변경 절차로 수행한다.

### 변경 승인

일반 채널에서 시작한 조회·점검 요청에도 아래 1~2단계의 thread 이동은 적용한다.
3단계 이후는 변경 작업에 적용한다.

1. 일반 에이전트 채널에서 저장소 작업을 요청하면 documenter가 archive forum에 작업 게시글을 만든다.
2. 게시글의 첫 글에는 최초 사용자 요청을 넣는다. 요청에 첨부가 있으면 같은 archive thread에 복사한다.
3. documenter가 원격 `master`의 Issue 템플릿을 읽고 Issue 제목, 본문, label 전체를 보여 준 뒤 승인 대기에 보관한다.
4. 요청자가 archive thread에 `승인`을 입력하면 Issue와 `<타입>/<이슈번호>` 작업 브랜치를 만든다. `취소`를 입력하면 pending만 취소한다.
5. documenter가 생성·수정·삭제할 전체 파일, 실제 내용, 커밋 메시지를 보여 준 뒤 변경 세트 하나를 승인 대기에 보관한다.
6. 요청자가 승인하면 변경 세트 전체를 커밋 하나로 적용한다.
7. PR이 필요하면 원격 PR 템플릿으로 제목과 본문을 만든 뒤 별도 승인을 기다린다.
8. PR 병합과 브랜치 삭제는 사용자가 GitHub에서 한다.

- pending을 만든 사용자만 해당 pending을 승인하거나 취소할 수 있다.
- Issue, 변경 세트, PR의 승인은 서로 별개다.
- 외부 API 결과가 불명확한 실패는 자동 재시도하지 않고 실패 상태로 남긴다.
- 저장소 Settings → General → Pull Requests에서 Automatically delete head branches를 켜면 병합한 작업 브랜치가 자동으로 지워진다.

## 에이전트 채널

에이전트 채널로 쓸 Discord 채널 ID를 두 목록 중 하나에 등록한다.

- `request_channels`: Bot이 사용자 메시지에만 답한다.
- `chat_channels`: 채팅 프롬프트로 답하고 per_day만큼 자율 채팅도 하는 채널이다.
- 같은 채널을 두 목록에 함께 등록하면 Bot이 시작하지 않는다.
- `request_channels`의 항목은 채널 ID이거나 `id`, `model`, `effort`를 가진 객체다.
  - `model`과 `effort`는 선택이다.
  - 지정하면 해당 채널에서 `chat.model`, `chat.effort` 대신 쓴다.
- `chat_channels`의 항목은 채널 ID이거나 `id`, `per_day`, `topic`을 가진 객체다.
  - `id`: 채널 ID, 필수.
  - `per_day`: 하루 자율 채팅 횟수. 생략하면 0이고, 0이면 자율 채팅을 하지 않는다.
  - `topic`: 자율 채팅 주제, 선택.
  - 같은 ID가 여러 번 나오면 적힌 값을 합친다.

```json
{
  "request_channels": ["<REPLY_ONLY_CHANNEL_ID>"],
  "chat_channels": [
    "<CHAT_CHANNEL_ID>",
    { "id": "<AUTONOMOUS_CHAT_CHANNEL_ID>", "per_day": <PER_DAY> },
    {
      "id": "<TOPIC_CHAT_CHANNEL_ID>",
      "per_day": <PER_DAY>,
      "topic": "<CHAT_TOPIC>"
    }
  ]
}
```

이전 설정의 `chat.auto_conversations_per_day`는 쓰지 않는다. 남아 있으면 Bot이 시작하지 않는다.

`discord/config.json`의 최상위 키는 다음 목록만 허용한다.
목록 밖 키가 있으면 Bot이 시작하지 않는다.

- `agents`, `allowed_guild_ids`, `allowed_user_ids`
- `archive_forum_id`, `archive_repository`
- `attachments`, `chat`, `links`, `memory`, `reminders`, `summary`, `tools`
- `chat_channels`, `request_channels`

텍스트 채널과 포럼 채널을 모두 등록할 수 있다. 등록한 채널 아래의 스레드와 포럼 게시글은 부모 채널 ID로 판정되므로 따로 등록하지 않는다.

### 대화 시작

- 사용자는 Bot을 멘션하지 않아도 대화를 시작할 수 있다.
- 특정 Bot을 멘션하면 해당 캐릭터가 첫 화자가 된다.
- 멘션이 없고 본문에 캐릭터의 `korean_name`이 있으면 가장 앞에 나온 캐릭터가 첫 화자가 된다.
- 둘 다 없고 답장 대상이 이미 조회된 Bot 메시지이면 그 Bot이 첫 화자가 된다.
- 멘션·본문 이름·조회된 답장 대상 Bot이 모두 없으면 캐릭터 중 하나가 무작위로 첫 화자로 선택된다.
- 첫 화자가 본문 없이 다른 캐릭터를 다음 화자로 고르면 그 캐릭터에게 차례가 넘어간다.

### 대화 진행

- 각 캐릭터는 발언 끝에 다음 화자나 종료를 내부 제어값으로 고른다. 제어값은 본문에서 제거된다.
- 사용자 시작 대화와 자율 채팅 모두 최소 발언 수를 강제하지 않는다.
- 캐릭터가 이어 말할 때는 발언 사이에 `chat.turn_delay_seconds`초를 기다린다. 그사이 사용자가 메시지를 보내면 대화가 끝나고 사용자 메시지에 답한다.
- 종료를 선택하거나 다음 화자 선택이 없거나 잘못되면 대화를 끝낸다.
- `chat.max_turns`에 도달하면 종료한다.
- 캐릭터가 사용자에게 질문하거나 확인을 요청하면 대화가 끝난다.
- 질문에 답하는 데 필요한 사실 확인은 하되, 단순 질문을 별도 조사·검색·요약 과제로 확대하지 않는다.
- 담당 역할 연결은 내부 제어값으로 처리하며 역할 한계 설명을 별도 본문으로 늘이지 않는다.
- 대화 도중 사용자가 새 메시지를 보내면 진행 중인 대화는 다음 턴으로 넘어가지 않고 끝난다. 생성 중이던 응답은 보내지 않는다.

### 사용자 판단 대기

- 모델이 질문이나 확인을 요청할 때 `[[wait:user]]`를 출력하면 사용자 판단 대기로 저장한다.
  `[[wait:user]]`는 마지막 `[[next:...]]` 줄 바로 앞이나, next 줄이 없으면 마지막 줄에 둔다.
- `[[wait:user]]`가 있으면 다른 다음 화자 ID를 출력해도 `stop`으로 처리한다.
- 대기 상태는 채널별 `user_wait`로 저장되어 재시작 후에도 유지되며, 질문 전송에 실패해도 남는다.
- 대기 중에는 자율 채팅, 리액션, 투표 결과에서 시작한 후속 발언을 진행하지 않는다.
- `on_message`에서 등록한 실제 사용자 메시지의 대기 항목을 처리할 때 해제한다.
- 일반 `[[next:stop]]`만으로는 지속 대기를 만들지 않으며, 본문의 질문 문장만으로 대기를 감지하지 않는다.
- 새 메시지로 대기가 해제되는 것은 저장소 작업 승인과 별개다.
- 채널 전체 기록 삭제도 해당 채널의 `user_wait`를 제거한다.

### 자율 채팅

- `chat_channels`에 등록한 채널에서만 시작한다.
- 채널별로 `per_day` 횟수만큼 하루의 임의 시각에 시작한다.
- 시각을 놓친 경우 나중에 몰아서 실행하지 않는다.
- 그 시각에 대화가 진행 중이면 건너뛴다.
- 포럼 채널은 자율 채팅에서 제외한다.
- `topic`이 없는 채널은 웹 검색 없이 일상, 취향, 채널에 오간 대화를 화제로 삼는다.
- `topic`이 있는 채널은 웹 검색으로 주제에 관한 최근 소식을 찾아 이야기한다.
- `topic`은 자유 문장이다. 관심 있는 도구나 분야를 구체적으로 적을수록 화제의 범위가 좁아진다.
- 사용자가 말을 걸어 시작한 대화는 채널과 관계없이 웹 검색을 쓸 수 있다.

### 리액션

- 캐릭터는 말 대신, 또는 말과 함께 직전 메시지에 이모지 리액션을 달 수 있다.
- 사용자가 캐릭터의 메시지에 리액션을 달면 그 메시지를 쓴 캐릭터가 한 번 반응한다.
  - 할 말이 있으면 캐릭터의 말로 짧게 반응한다.
  - 다른 사람 메시지에는 말 없이 이모지로만 반응할 수 있다.
  - 반응할 것이 없으면 채널에 아무것도 올리지 않고 끝낸다.
- 사용자가 사람의 메시지에 단 리액션은 대화 기록에만 남긴다.
- 다른 Bot이 단 리액션은 무시한다.

### 답장, 첨부, 링크

- 사용자가 메시지에 답장하면 답장 대상 정보를 기록에 붙인다.
  - 작성자, 메시지 ID, 본문 앞 100자를 붙인다.
  - 모든 캐릭터가 같은 기록을 보고 맥락을 파악한다.
- 사용자가 이미지를 첨부하면 별도 호출로 이미지 설명을 한 번 만들어 대화 기록에 붙인다.
- 텍스트 문서를 첨부하면 앞부분을 대화 기록에 붙인다.
  - 길이는 `attachments.text_preview_chars`를 따른다.
  - 더 긴 문서는 캐릭터가 필요할 때 전체를 읽는다.
  - `attachments.text_max_kb`가 넘는 문서와 PDF는 읽지 않는다.
- 첨부 파일은 에이전트 루트의 `.discord_attachments/`에 저장되고 `chat.history_hours`가 지나면 지운다.
  - 원본 메시지가 삭제되면 그 메시지의 첨부 파일도 바로 지운다. 등록하지 않은 채널의 삭제도 반영한다.
  - 시작할 때 첨부 원본이 남아 있는지 확인하고, 삭제된 메시지의 첨부를 지운다.
- 메시지에 링크가 있으면 Discord 링크 미리보기의 제목, 작성자, 설명(`links.description_max_chars`)을 대화 기록에 붙인다.
- 미리보기가 늦게 붙으면 정해진 간격으로 다시 확인한다.
  - 간격은 `links.preview_retry_seconds`를 따른다.
  - 최대 횟수는 `links.preview_retries`를 따른다.
  - 첫 응답은 두 값의 곱만큼 늦어질 수 있다.

### 대화 기록

- 채널과 스레드마다 대화 기록을 따로 유지한다. 포럼 게시글마다 기록이 분리된다.
- 대화 기록과 채널 요약문은 `discord/bot/chat_state/<채널 ID>.json`에 저장되고 재시작 후에도 다시 불러온다.
- 최근 `summary.raw_hours`시간의 대화는 원문으로 보낸다.
  - 그보다 오래된 줄과 `chat.history_max_lines`를 넘친 줄은 요약 대상이다.
  - 요약 대상 줄은 요약에 성공할 때까지 원문으로 남아 원문과 요약 사이에 빠지는 대화가 없다.
- 요약은 대상 줄이 `summary.batch_min_lines` 이상이거나 대상이 생긴 뒤 `summary.flush_minutes`분이 지나면 실행한다.
  - 채널마다 한 번에 하나씩 백그라운드에서 `summary.model`을 도구 없이 한 번 호출한다.
  - 기존 요약문과 대상 줄을 합쳐 `summary.max_chars`자 이하의 새 요약문을 만들고 대상 줄을 기록에서 지운다.
  - `summary.max_attempts`번 모두 실패하면 요약에 반영하지 않고 대상 줄을 지운다.
  - 요약이 계속 실패해도 `chat.history_hours`가 지난 기록은 지운다.
- 메시지를 지우면 기록의 해당 줄은 지우지만 이미 만든 요약문은 바꾸지 않는다.
- 채팅 턴과 요약 호출마다 사용량(`usage`)과 비용(`total_cost_usd`)을 `token usage` 로그 한 줄로 남긴다.
- 오래 이어지는 논의의 결정 사항은 장기기억으로 저장하거나 documenter에게 지식 저장소에 기록하게 한다.

### 기억 전체 삭제

- 허용 사용자가 에이전트 채널에 `기억 전체 삭제`를 입력하면 Bot 하나가 확인 메시지를 보낸다.
- 같은 사용자가 5분 안에 `확인`을 입력하면 그 채널의 대화 기록, 요약문, `chat_state` 파일, 이미지 설명을 지운다.
- `취소`를 입력하거나 5분이 지나면 취소된다. 다른 사용자의 `확인`은 무시한다.
- 모델을 호출하지 않으며 명령과 확인 메시지는 대화 기록에 남기지 않는다.
- 서버 장기기억과 캐릭터 기억은 지우지 않는다.

### 장기기억

- 장기기억은 서버 단위다. 같은 서버의 모든 채널과 모든 캐릭터가 같은 기억을 쓴다.
- 기억은 `discord/bot/memory.sqlite3`에 저장되어 재시작 후에도 유지된다.
- 저장 대상은 다음과 같다.
  - archive forum 스레드의 모든 메시지(허용 사용자와 캐릭터 Bot 작성). 첨부는 파일명만 저장한다.
  - 사용자가 기억하라고 한 내용
  - 캐릭터 기억: 캐릭터가 대화에서 겪은 일과 사용자가 그 캐릭터에게 정해 준 설정
- archive 메시지 수정은 기억에 반영한다. 시작할 때 archive forum 전체(보관 스레드 포함)를 한 번 다시 맞춘다.
- 캐릭터가 겪은 일은 요약 호출이 함께 추출한다. 요약을 포기하면 그 묶음의 추출도 버린다.
- 기억은 원본 메시지에 묶여 있다.
- 기억 삭제는 `기억 삭제 방법` 절을 따른다.
- 캐릭터에게 기억을 보여 달라고 하면 자기 기억을 보여 주고 채널당 1건의 업로드 대기를 만든다.
  - 기억을 요청한 사용자가 정확히 `승인`이라고 보내면 `.claude/agents/<role>/MEMORY.md`를 새 브랜치에 커밋하고 PR을 만든다. `master` 병합은 사용자가 한다.
  - `취소`라고 보내면 대기를 지운다. 다른 메시지와 다른 사용자의 `승인`은 무시한다.
  - 올린 기억의 원본 메시지가 지워지면 캐릭터별 갱신 필요 표시만 남기고, 다음 `승인` 때 `MEMORY.md`에 함께 반영한다.
  - archive thread에서 같은 사용자의 저장소 작업 대기와 업로드 대기가 함께 있으면 `승인`은 둘 다 실행하지 않고, `취소`는 둘 다 취소한다.
  - `archive_repository`의 token을 쓴다.

### 기억 확인 방법

- 확인 방법은 기억 종류(`kind`)에 따라 다르다.
- 캐릭터 기억(`persona_event`, `persona_setting`)은 캐릭터에게 보여 달라고 한다.
  - 동작은 `장기기억` 절을 따른다.
  - 그 캐릭터 자신의 기억만 `기억 ID N (종류) 원문` 형식으로 보여 준다.
- 서버 장기기억(`archive`, `explicit`)은 낱말로만 찾을 수 있다.
  - 캐릭터 도구 `memory_search`가 낱말로 찾는다.
  - 서버 장기기억을 전부 나열하는 도구는 없다.
- 저장 파일을 직접 조회하면 종류와 개수를 한 번에 볼 수 있다.
  - 기억 내용에는 사용자의 개인 대화에서 나온 내용이 있을 수 있다.
  - 내용 없는 조회를 기본으로 하고, 내용은 기억 ID 하나씩 조회한다.
  - 읽기 전용(`mode=ro`)으로 열어 저장 파일을 바꾸지 않는다.
  - `<프로젝트 경로>`는 저장소 루트이고 경로 구분자는 `/`를 쓴다.

내용 없는 조회는 다음과 같다.

```bash
python -c "import sqlite3;c=sqlite3.connect('file:<프로젝트 경로>/discord/bot/memory.sqlite3?mode=ro',uri=True);[print(r) for r in c.execute('SELECT id,kind,agent,uploaded,length(content) FROM memories ORDER BY id')]"
```

출력 한 줄이 기억 하나이고 열 순서는 `id`, `kind`, `agent`, `uploaded`, 내용 글자 수이다.
`id`는 캐릭터가 보여 주는 기억 ID와 같다.
`uploaded`가 `1`이면 저장소에 반영된 기억이다.

```text
(2, 'explicit', None, 0, 11)
(3, 'persona_event', 'buddy', 0, 18)
```

기억 ID 하나의 내용은 같은 명령에서 조회문만 바꾼다.

```bash
python -c "import sqlite3;c=sqlite3.connect('file:<프로젝트 경로>/discord/bot/memory.sqlite3?mode=ro',uri=True);[print(r[0]) for r in c.execute('SELECT content FROM memories WHERE id=<기억 ID>')]"
```

### 기억 삭제 방법

- 원본 메시지를 지우면 해당 기억도 지워진다.
  - 기억 요청은 요청 메시지와 대상 메시지 중 하나만 지워도 지워진다.
  - 잊기 명령은 없다.
  - 잊게 하려면 해당 메시지를 지운다.
  - 스레드를 지우면 그 스레드의 기억도 지운다.
  - Bot이 꺼져 있던 동안 지운 메시지는 시작할 때 원본 확인으로 반영한다.
- archive 기억은 시작할 때 포럼을 다시 맞추면서 원본이 없는 것도 지운다.
  - 다시 맞추는 중 오류가 없을 때만 지운다.
- `기억 전체 삭제`는 서버 장기기억과 캐릭터 기억을 지우지 않는다.
  - 지우는 범위는 `기억 전체 삭제` 절을 따른다.
- 저장 파일의 행을 직접 지우는 방법은 이 문서에서 안내하지 않는다.
  - 직접 지운 기억이 다시 생기는지는 미확인이다.

## 역할별 도구

- 모든 역할: 서버 채널 목록 조회, 일반 포럼 글쓰기(태그 1~5개), 장기기억 검색·저장, 자기 기억 확인과 업로드 요청
- director: 투표 만들기(선택지 2~10개, 기간 1~768시간), 공개 스레드 만들기, 메시지 고정
- reviewer: 채널 메시지 다시 읽기(최근 `tools.channel_history_max`개)
- assistant: 한 번 알림, 반복 알림, 알림 목록, 알림 취소
- documenter: 지식 저장소 작업 게시글 만들기, 저장소 파일 조회·검색·이력, Issue·변경·PR 승인 대기 등록

올린 투표는 질문, 선택지, 기간이 대화 기록에 남아 다른 캐릭터도 안다.
투표가 끝나면 Discord가 올리는 결과 메시지를 director 봇이 받아 질문, 1위, 득표 수를 기록하고, director 캐릭터가 결과에 반응한다.
봇 계정은 투표할 수 없으므로 투표는 사람만 한다.

알림은 `discord/bot/reminders.json`에 저장되어 재시작해도 유지된다.
알림 메시지는 `allowed_user_ids`의 사용자를 멘션해 푸시 알림이 가게 한다. 봇이 꺼져 있어 놓친 한 번짜리 알림은 재시작 직후 늦은 알림으로 보내고, 놓친 반복 알림 회차는 건너뛴다.

캐릭터는 자기 담당이 아닌 요청을 담당 캐릭터에게 넘긴다.

## 설정 파일 보호

`discord/config.json`이 Git에 포함되지 않도록 `.gitignore`를 확인한다.

```gitignore
discord/config.json
```

다음 명령으로 추적 여부를 확인할 수 있다.

```bash
git status --short discord/config.json
```

`discord/config.json`이 이미 Git에 추가된 경우 token을 먼저 재발급하고 저장소 기록을 별도로 점검한다.

## Python 가상환경 생성

프로젝트 루트에서 가상환경을 만든다.

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Windows 명령 프롬프트

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### Git Bash

```bash
python -m venv .venv
source .venv/Scripts/activate
```

### macOS 또는 Linux

```bash
python -m venv .venv
source .venv/bin/activate
```

성공하면 터미널 프롬프트 앞에 보통 `(.venv)`가 표시된다.

## 패키지 설치

필요한 Python 패키지를 설치한다.

```bash
python -m pip install --upgrade pip
python -m pip install "aiohttp==3.14.3" "claude-agent-sdk==0.2.159" "discord.py==2.7.1"
```

Python 3.14.0에서 위 버전의 패키지로 봇 진입점 import와 지식 저장소 단위 테스트를 검증했다.
위 버전은 로컬 조사 환경에서 확인한 설치 예시이며 저장소의 lock 파일로 관리되는 버전은 아니다.
GCP VM에서의 실행 결과는 해당 환경에서 별도로 확인한다.
`discord.py`가 설치되지 않으면 프로젝트의 `discord/` 폴더가 빈 namespace 모듈로 잡혀 `discord.Client`를 찾지 못할 수 있다.

설치 뒤 실제 import를 확인한다.

```bash
python -c "import sys; sys.path.insert(0, 'discord/bot'); import bot; print('ok')"
```

## Claude 인증 확인

봇을 실행할 사용자 계정에서 Claude Code를 설치하고 로그인한 뒤 응답을 확인한다.
GCP VM의 설치와 로그인은 [Claude Code 로그인](discord-gcp.md#claude-code-로그인)을 따른다.

```bash
claude -p "hi"
```

인증이나 실행 오류가 없고 응답을 받으면 봇 실행으로 진행한다.

## 실행

프로젝트 루트에서 Bot 프로세스를 실행한다.

```bash
python discord/bot/bot.py
```

하나의 Python 프로세스가 설정된 모든 Discord Bot 연결과 채팅 스케줄러를 함께 실행한다. Bot 하나라도 접속에 실패하면 프로세스 전체가 종료된다.

프로세스를 종료하려면 실행한 터미널에서 `Ctrl+C`를 누른다.

컴퓨터를 끄거나 프로세스를 종료하면 Bot도 오프라인이 된다.

## 연결 확인

다음 항목을 확인한다.

1. 터미널에 설정 파일 파싱 오류가 없는지 확인한다.
2. 터미널에 Bot마다 `<name> connected` 로그가 출력되는지 확인한다.
3. 설정한 Bot이 모두 Discord에서 온라인인지 확인한다.
4. Bot을 멘션하지 않고 에이전트 채널에 일반 메시지를 보낸다.
5. 캐릭터 하나가 첫 응답을 하는지 확인한다.
6. `chat.max_turns` 안에서 대화가 종료되는지 확인한다.
7. 본문에서 캐릭터 이름이 `korean_name`으로 표시되는지 확인한다.

예시:

```text
오늘 다들 뭐 함?
여기 어디야?
리오, 점심 메뉴 투표 올려줘.
```

## 문제 해결

### 지식 원본 로딩

- 원본 누락 오류가 나면 반환된 commit SHA와 파일 경로를 기준으로 원격 파일의 존재를 확인한다.
- `archive/AGENTS.md`와 선택한 schema 중 하나라도 읽지 못하면 다른 절차를 추정하지 않는다.
- 검색 결과의 누락·잘림 표시가 있으면 다음 페이지나 개별 파일 조회로 필요한 범위를 확인한다.

### Bot이 오프라인으로 표시됨

다음 항목을 확인한다.

- Bot token이 올바른가
- Bot이 대상 서버에 추가되었는가
- Bot 실행 중 인증 오류가 발생했는가
- Discord 연결 오류가 출력되었는가
- `python discord/bot/bot.py` 프로세스가 실행 중인가

Bot 하나라도 접속에 실패하면 모든 Bot이 함께 종료된다.

### `DisallowedIntents` 오류가 발생함

다음 항목을 확인한다.

- 해당 Bot의 `Message Content Intent`를 켰는가
- 설정 변경 후 Bot 프로세스를 재시작했는가

### 에이전트 채널 무응답

다음 항목을 확인한다.

- 채널 ID가 `request_channels`나 `chat_channels`에 등록되었는가
- 스레드나 포럼 게시글이라면 부모 채널이 등록되었는가
- Guild ID와 사용자 ID가 허용 목록에 있는가
- Bot이 해당 채널을 볼 수 있는가
- Bot이 해당 채널에 메시지를 보낼 수 있는가
- 모든 Bot 연결이 완료되었는가

다른 Bot과 웹훅이 보낸 메시지에는 응답하지 않는다.

### `chat.failure_notice` 안내가 표시됨

채팅 처리 중 오류가 난 것이다.
원인 확인 명령은 [채팅 오류 원인](discord-debug.md#채팅-오류-원인)을 따른다.

### 대화가 도중에 끝남

턴별 다음 화자와 종료 원인은 [다음 화자 확인](discord-debug.md#다음-화자-확인)을 따른다.

### 투표, 스레드, 고정이 실패함

- 4장의 권한 표에서 해당 권한을 부여했는가
- 투표라면 discord.py가 2.4 이상인가
- 스레드 안에서는 스레드를 만들 수 없다

### 설정 파일을 읽지 못함

다음 항목을 확인한다.

- `discord/config.json`이 존재하는가
- 마지막 항목 뒤에 불필요한 쉼표가 있는가
- 마지막 `}` 뒤에 남은 내용이 있는가
- 키를 큰따옴표로 감쌌는가
- ID와 token을 요구된 문자열 형식으로 입력했는가

JSON 문법 확인 명령은 [설정 JSON 문법](discord-debug.md#설정-json-문법)을 따른다.

### Git Bash 가상환경 오류

Windows Git Bash에서는 macOS/Linux 경로를 사용하지 않는다.

잘못된 예:

```bash
source .venv/bin/activate
```

Windows Git Bash에서는 다음 명령을 사용한다.

```bash
source .venv/Scripts/activate
```

### 일부 Bot만 실행됨

다음 항목을 확인한다.

- 설정한 Bot token을 모두 입력했는가
- 같은 token을 여러 에이전트에 중복 입력하지 않았는가
- 오류가 발생한 Bot의 Application 설정이 올바른가
- 오류가 발생한 Bot도 서버에 추가했는가
