# MessageDisplay 응답 교체 재적용

- 작업 ID: 20261027-151500-display-hook-reapply
- 작업 상태: 미개시

## 요구사항

### 요구사항 목록

1. 응답 교체 재적용: 도구 호출 앞 중간 메시지를 오판정하지 않는 방식으로 MessageDisplay 응답 교체를 다시 등록한다.

### 문제 정의

- 중간 메시지 오판정: transcript 기록 시점에 의존하는 판별 때문에 도구 호출 앞 중간 메시지가 교체 문구로 표시됐다.
  - 근거 내용: 작업 20261027-142227-kickoff-without-spec 세션에서 두 번 발생했고, 그 작업 요구사항 4에서 MessageDisplay 등록을 뺐다.
  - 근거 출처: hooks/replace_blocked_response.py:97, https://code.claude.com/docs/en/hooks
  - 재현 절차: MessageDisplay를 다시 등록하고, 응답 검사에 걸리는 안내 문장을 쓴 뒤 바로 도구를 호출한다.
  - 처리: 요구사항 1

### 원인 분석

- 입력에 중간 메시지 구분 필드가 없음: 확인된 원인
  - 근거 내용: MessageDisplay 입력은 turn_id, message_id, index, final, delta뿐이고, message_id는 transcript 메시지 ID와 대응시킬 수 없다.
  - 근거 출처: https://code.claude.com/docs/en/hooks
  - 한계: 판정 시점의 transcript 기록 여부는 실행 기록이 없어 확인하지 못했다.

### 검토 필요

<!-- 사용자의 검토가 필요하거나 구현 후 잠재적인 위험이 있는 대상 목록 -->
<!-- 모든 목록이 검토 완료 또는 사후실측 필요일 때 승인 가능 -->

- 중간 메시지 판별 수단
  - 상태: 미검토
  - 사용자 검토 시각: 기록 없음
  - 대응 근거: 착수 조사에서 정한다.
  - 대응 방안:
    - hooks/replace_blocked_response.py, shared/settings/claude/settings.example.json
    - 판별 신호를 조사한 뒤 정한다.

### 재검토 조건

- Claude Code가 MessageDisplay 입력에 도구 호출 여부나 stop reason을 추가하면 요구사항 1을 다시 검토한다.

## 승인

### 착수 승인 범위

- 사용자 승인 상태: 승인 대기
- 사용자 승인 시각: 기록 없음
- 연결 대상: 요구사항 1
- 승인 행동: 착수 조사에서 정한다.
- 영향 범위: 착수 조사에서 정한다.
- 범위 밖: 착수 조사에서 정한다.

### 실행 승인 범위

- 사용자 승인 상태: 승인 대기
- 사용자 승인 시각: 기록 없음
- 연결 대상: 요구사항 1
- 승인 행동: 계획 단계에서 정한다.
- 영향 범위: 계획 단계에서 정한다.
- 범위 밖: 계획 단계에서 정한다.

## 작업 경과

- 갱신 시각: 2026-10-27 15:05:27
- 진행 상황:
  - 사용자 지시로 작업 20261027-142227-kickoff-without-spec에서 분리해 문서만 만들었다.
- 다음 작업: 착수 조사 (director)

## 결정 기록

### 작업 분리

- 시각: 2026-10-27 15:05:27
- 담당: director
- 결정: MessageDisplay 재적용은 이 작업으로 분리하고 나중에 착수한다.
- 근거: 사용자가 `안 1 + 재적용 분리 확정`을 골랐다.

## 검증 결과

검증 미개시

## 중단 사유
