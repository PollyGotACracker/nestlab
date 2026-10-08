# BRAINSTACK

에이전트 기반 개발 환경과 관련된 지식 및 운영 자산을 관리하는 저장소이다.

- 에이전트 팀의 규칙 및 페르소나 파일
- Discord 봇 설정 및 스크립트 파일
- Obsidian 기반 지식 저장소
- 도구별 에이전트 설정 및 스킬 연동 문서
- 공용 스킬 파일

## Agent

| 에이전트   | 이름         | 역할                                                       |
| ---------- | ------------ | ---------------------------------------------------------- |
| assistant  | buddy(버디)  | 기본 작업: 독립적인 웹 서치, 파일 변경 등                  |
| director   | rio(리오)    | 프로젝트 작업: 저장소 사전 조사, 판단·승인 조율, 전체 흐름 |
| researcher | nico(니코)   | 프로젝트 작업: 외부 조사, 비교, 계획 초안                  |
| worker     | jelly(젤리)  | 프로젝트 작업: 구현                                        |
| reviewer   | ricky(리키)  | 프로젝트 작업: 자료 및 주장 반증, 구현·문서 검수           |
| documenter | pepper(페퍼) | 프로젝트 작업: 기록, 지침·문서 작성                        |

각 에이전트의 역할과 persona는 .claude/agents/ 폴더를 참고한다.

### Agent 기본값 및 호출

기본 에이전트는 실행 경로에 따라 다음과 같이 설정된다.

- brainstack/: rio
- brainstack/archive/: pepper
- brainstack 외부 경로: buddy
- brainstack 하위 경로(archive 제외): buddy

에이전트는 Git bash에서 다음과 같이 호출할 수 있다.

```bash
# claude code
# claude agent <에이전트_이름>
claude agent rio
```

```bash
# codex
# codex agent <에이전트_이름>
codex agent rio
```

## Discord

### Agent

Discord 채널의 에이전트 봇 목록

| 에이전트   | 이름         | 역할                                          |
| ---------- | ------------ | --------------------------------------------- |
| assistant  | buddy(버디)  | 일회성 및 반복 알림 예약                      |
| director   | rio(리오)    | 대화 흐름 조정, 투표와 스레드 생성            |
| researcher | nico(니코)   | 외부 정보 조사, 최신 정보 확인                |
| worker     | jelly(젤리)  | 코드 조각, 글 초안, 정리, 계산 등 결과물 작성 |
| reviewer   | ricky(리키)  | 발언 검토, 인용 대조, 사실 검증               |
| documenter | pepper(페퍼) | 지식 저장소 조회, 기록, 삭제                  |

- [봇 권한 목록](docs/discord.md#discord-서버-추가)

Discord 채널의 웹훅 봇 목록

| 이름       | 역할                                                    |
| ---------- | ------------------------------------------------------- |
| Hugo(휴고) | 디스코드 webhook 봇: 현재 repository의 push 이벤트 알림 |

- [Github 웹훅 설정 방법](docs/discord-github-webhook.md)
- [추가: 긱뉴스 디스코드 봇](https://news.hada.io/discordbot)

## 환경별 연결

- Claude Code: [docs/claude-code.md](docs/claude-code.md)
- Codex: [docs/codex.md](docs/codex.md)
- Cursor: [docs/cursor.md](docs/cursor.md)
- Discord: [docs/discord.md](docs/discord.md)
  - GCP 배포: [docs/discord-gcp.md](docs/discord-gcp.md)
  - GCP 운영: [docs/discord-gcp-operations.md](docs/discord-gcp-operations.md)
  - 동작 명세: [docs/discord-spec.md](docs/discord-spec.md)
  - 디버그: [docs/discord-debug.md](docs/discord-debug.md)
- OpenClaw: [docs/openclaw.md](docs/openclaw.md)
- Hermes: [docs/hermes.md](docs/hermes.md)

## 전역 설정(Hooks) 설치

- [Claude Code 설치 절차](docs/claude-code.md#전역-설정-설치)
- [Codex 설치 절차](docs/codex.md#전역-설정-설치)

## Agent 전역 연결

- [Claude Code 연결 절차](docs/claude-code.md#에이전트-전역-연결)
- [Codex 연결 절차](docs/codex.md#에이전트-전역-연결)

## Skill 연결

- [Skill 연결 절차](docs/skill-link.md)

### 추가 사용 Skill 목록

- [Anthropics/skills](https://github.com/anthropics/skills)
- [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)

## 폴더 구조

```text
./
├── .claude/                       # Claude Code 설정
│   ├── agents/<role>/
│   │   ├── AGENTS.md              # 에이전트별 지침
│   │   └── SOUL.md                # 에이전트별 성향
│   ├── assets/                    # 에이전트 에셋
│   └── settings.json              # Claude settings
├── .codex/                        # Codex 설정
│   ├── agents/<name>.toml         # 에이전트별 설정
│   └── config.toml                # 승인 및 hooks 활성화 설정
├── .github/                       # Github 저장소 설정 및 workflow
├── archive/                       # Obsidian 기반 지식 저장소
│   ├── .claude/settings.json      # Claude settings
│   ├── <folders>/                 # 하위 폴더. archive/README.md 참고
│   ├── AGENTS.md                  # 지식 저장소 작업 지침 문서
│   ├── CLAUDE.md                  # archive/AGENTS.md를 불러오는 문서
│   └── README.md                  # 지식 저장소 구조 문서
├── discord/                       # Discord 봇
│   ├── bot/                       # 봇 스크립트
│   ├── prompts/                   # 봇 스크립트에 주입되는 프롬프트
│   ├── tests/                     # 봇 스크립트 테스트
│   └── config.example.json        # config.json 예시
├── docs/                          # 환경별 연결 문서
├── hooks/                         # 훅 스크립트
├── log/                           # 작업 관련 로그
│   ├── state/                     # 상태 로그
│   └── incident/                  # 사건 로그
├── schema/                        # 문서 작성 절차 및 양식
├── shared/
│   └── settings/                  # 공통 설정(권한 등)
│   └── skills/<skill>/SKILL.md    # 공통 skill
├── tools/                         # 직접 실행 스크립트
│   ├── <script_name>/             # 해당 스크립트의 모듈 폴더
│   ├── check_doc_rule.py          # 에이전트 문서 지침 준수 확인 도구
│   ├── set_hooks.py               # 전역 설정 설치 도구
│   ├── set_agents.py              # 에이전트 전역 연결·제거 및 상태 확인 도구
│   └── set_skills.py              # 스킬 전역 연결·제거 및 상태 확인 도구
├── AGENTS.md                      # 지침 통합 문서
├── CLAUDE.md                      # Claude Code 전용 지침 통합 문서
└── README.md                      # 저장소 정보 문서
```
