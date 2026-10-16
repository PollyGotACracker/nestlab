---
name: write-portfolio
description: 개발 메모, 회고와 소재 조사 자료를 바탕으로 국내 테크기업 기술 블로그처럼 맥락, 기술 원리, 핵심 코드와 적용 과정을 연결한 포트폴리오 글감 또는 글 초안을 작성한다. 저장소의 소재 발굴과 프로젝트 사용 설명서 작성은 각각 조사와 운영 문서 작업으로 구분한다.
argument-hint: 자료=<메모 또는 조사 문서> 형식=<글감 | 초안>
---

# 포트폴리오 기술 글

## 인수

- `자료`: 사용자가 제공한 개발 메모, 회고, 작업 기록 또는 소재 조사 문서
	참조된 코드와 기록은 해당 주제의 근거를 확인하는 범위에서 읽는다.
- `형식`: 글감 또는 초안
	별도 지정이 없으면 글의 흐름과 코드 뼈대가 있는 글감으로 작성한다.
	글감은 확인된 내용과 빈 부분을 함께 담고, 초안은 같은 자료를 독자가 읽을 글로 전개한다.

## 작성 절차

1. 자료를 주제별로 나눈다.
	- 같은 문제나 기술 도입에 관한 메모, 코드와 기록을 모은다.
	- 보류와 제외 판정은 유지하고, 사용법 설명만 남는 주제는 부족한 근거를 알린다.
2. 글의 순서를 정한다.
	- 상황과 불편에서 시작해 기술 원리, 선택 이유, 적용 과정과 결과를 배치한다.
	- 원자료의 흐름에 맞춰 순서와 소제목을 정한다.
3. 핵심 코드와 그림을 준비한다.
	- 문제가 드러나는 기존 코드와 선택한 방법을 적용한 코드를 추린다.
	- 핵심 함수, 호출 관계, 상태 변화와 오류 처리는 남긴다.
	- 코드로 설명하기 어려운 순서는 흐름도나 전후 예시로 보여 준다.
4. 설명과 코드를 연결해 쓴다.
	- 기술의 개념과 동작 원리를 설명하고, 그 환경에서 선택한 이유를 붙인다.
	- 변경을 다룬 글은 기존 코드, 수정 코드와 같은 조건에서의 동작 차이를 이어서 보여 준다.
	- 설명에서 변경한 심볼이나 식을 직접 가리키고, 그 변경이 처음 문제를 어떻게 해결하는지 적는다.
	- 변경 전 코드를 확보하지 못하면 확인할 커밋이나 파일을 보완 항목에 적는다.
5. 근거를 대조하고 저장한다.
	- 처음 문제와 변경 결과를 비교하고, 실제 확인한 내용과 기대 효과를 구분한다.
	- 자료가 없는 내용은 보완 질문으로 남긴다.
	- 문서를 저장하고 위치와 남은 보완 사항을 보고한다.

## 본문 구성

### 1. 제목 규칙

- 제목은 다룬 문제나 도입 내용이 드러나는 명사구로 쓴다.
- 소제목은 4어절 이하의 명사구로 쓴다.
- 여러 주제를 묶으면 전체 제목 아래 주제마다 소제목을 두고, 주제 사이에 구분선을 넣는다.

### 2. 단계별 내용

- 도입: 상황과 불편, 이후 다룰 내용
- 기술 설명: 개념, 판단에 필요한 동작 원리, 적용 조건
- 적용: 선택 이유와 코드 뼈대
- 결과: 확인한 변화와 확인 방법
	측정값이 없으면 구조나 동작에서 확인한 변화와 한계를 적는다.

### 3. 표현 방식

- 맥락은 짧은 설명으로, 구현은 코드와 그림으로 보여 준다.
- 비교 항목은 목록으로, 선후 관계는 번호나 흐름도로 쓴다.
- 코드와 그림에 드러난 내용은 코드와 그림으로만 전달한다.

### 4. 자료 부족

- 소제목은 조사표의 항목명 대신 글의 내용으로 정한다.
- 확인되지 않은 실패, 대안 비교, 수치, 배운 점은 쓰지 않고 보완으로 남긴다.

## 코드와 근거

- 코드는 언어를 지정한 코드 블록으로 작성하고 발췌나 단순화한 범위를 밝힌다.
	핵심 오류 처리와 검증은 보존하고 생략한 초기화나 주변 구현은 짧게 표시한다.
- 실제 코드에 접근할 수 없으면 필요한 코드와 위치를 보완 항목에 적는다.
- 기술 설명을 보충할 때는 적용 버전에 맞는 공식 자료를 확인한다.
	외부 자료의 일반 사실과 사용자가 실제로 수행한 일을 구분한다.
- 개인 기여와 선택 이유는 사용자 답변이나 작업 기록으로 확인한다.
	Git 작성자 정보와 현재 코드만으로 개인의 경험 전체를 확정하기 어렵다.
- 테스트 내용 확인과 실행 성공, 기대 효과와 측정값을 구분한다.
	수치에는 측정 조건과 출처를 붙여 과장된 성과 주장을 줄인다.
- 근거는 관련 설명 근처의 링크나 짧은 주석으로 연결한다.
	- 원본 경로, 심볼, 행 번호와 커밋은 필요하면 글 끝의 작성 근거에 모은다.
	- 공개용 본문에는 개인 경로와 민감한 식별자를 대체한 표현을 사용한다.
- 부족한 자료는 아래 형식으로 표시한다.

```markdown
> **보완**: 확인된 내용과 연결해 필요한 코드, 기록이나 답변을 구체적으로 적는다.
```

## 결과물 예시

e.g. TanStack Query v5에서 연속 수정 중 롤백 충돌을 다루는 글감이다.
상황과 코드, 판단은 형식을 설명하기 위한 가상 원자료이다.
실제 사용자의 경험이나 성과를 뜻하는 예시는 아니다.

````markdown
# 낙관적 업데이트의 롤백 충돌

작업 제목의 변경을 즉시 화면에 반영하기 위해 낙관적 업데이트를 적용했다.
연속 저장 중 앞선 요청이 실패하면, 롤백 코드가 이후에 입력한 제목까지 덮어쓰는 문제가 발생했다.

## 롤백의 입력 유실

기존 구현은 `onMutate`에서 서버 조회값을 보관하고, 캐시의 제목을 제출값으로 바꿨다.
화면은 이 캐시를 읽으므로 저장 응답 전에 새 제목을 표시했다.

```typescript
// 변경 전: useItemTitle 내부의 저장 처리와 반환값 발췌
type Item = { id: string; title: string };

const edit = useMutation({
  mutationFn: (title: string) => updateItemTitle(id, title),
  onMutate: async (title) => {
    await client.cancelQueries({ queryKey, exact: true });
    const previous = client.getQueryData<Item>(queryKey);
    client.setQueryData<Item>(queryKey, (current) =>
      current ? { ...current, title } : current,
    );
    return { previous };
  },
  onError: (_error, _title, snapshot) => {
    if (snapshot?.previous) {
      client.setQueryData(queryKey, snapshot.previous);
    }
  },
  onSettled: () => client.invalidateQueries({ queryKey, exact: true }),
});

return { title: item.data?.title, save: edit.mutate };
```

문제는 `onError`의 `setQueryData(queryKey, snapshot.previous)`였다.
이 코드는 요청 직전의 항목 전체를 복원한다.
첫 요청 이후에 사용자가 제목을 다시 저장해도 첫 요청의 스냅샷에는 그 변경이 없다.

```text
처음 제목: 초안
요청 1: 검토 중으로 저장, 복구용 값은 '초안'
요청 2: 확정으로 저장, 화면에는 '확정' 표시
요청 1 실패: 복구용 값인 '초안'을 캐시에 기록
문제: 요청 2의 입력까지 화면에서 사라짐
```

요청 1의 onError는 실패한 변경과 이후 입력을 구분하지 않고 캐시 전체를 덮어썼다.
이 복구 방식으로는 연속 편집 중 최신 입력을 보존하기 어려웠다.

## 상태 분리의 선택

연속 편집을 유지하기 위해 저장 중 편집을 차단하는 방안은 제외했다.
요청별 변경 이력을 관리하는 방안도 검토했지만, 이 화면은 최신 제목을 여러 컴포넌트에서 공유할 필요가 없었다.

이 조건에서는 TanStack Query의 UI 방식으로 낙관적 업데이트를 구현할 수 있다.
캐시 방식은 `setQueryData`로 조회 데이터를 바꾸지만, UI 방식은 저장 중인 `variables`를 화면에 표시한다.
따라서 `onMutate`의 캐시 수정과 `onError`의 스냅샷 복구를 함께 제거하고, 훅이 반환하는 `title`을 다음과 같이 바꿨다.

```diff
- title: item.data?.title,
+ title: edit.isPending ? edit.variables : item.data?.title,
```

두 번째 저장이 진행 중이면 `edit.variables`는 최신 제출값인 `확정`이다.
첫 저장이 실패해도 이 값을 과거 스냅샷으로 덮어쓰는 코드가 없으므로, 화면은 `확정`을 계속 표시한다.

```text
서버 조회 → query.data ───────────┐
                                ├→ 화면의 제목
최신 제출 → mutation.variables  ─┘
            저장 중에는 이 값을 표시
```

이 변경과 함께 onError의 캐시 복구를 제거했다.
편집 컴포넌트는 마지막 저장과 재조회가 끝나면 최신 제출값 대신 서버 조회값을 표시한다.

## 요청 순서와 재조회

표시값을 분리하는 것만으로는 서버의 저장 순서까지 제어할 수 없다.
같은 항목의 요청을 제출 순서대로 실행하도록 항목별 scope를 지정했다.

서버 재조회는 마지막 저장이 끝난 뒤 한 번만 실행하도록 바꿨다.
앞선 요청의 완료 시점에 재조회하면 다음 저장 이전의 값이 도착할 수 있기 때문이다.
onSettled에서 같은 항목의 mutation 수를 확인해 현재 요청 하나만 남았을 때 재조회한다.

아래 코드는 표시값 분리, 저장 순서와 최종 재조회를 담당한다.
Provider, 입력 폼과 API 함수 내부는 생략했다.
API 함수는 HTTP 오류를 예외로 전달한다.
화면의 상위 컴포넌트는 항목 ID가 바뀌면 편집 컴포넌트를 새로 마운트한다.

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getItem, updateItemTitle } from "./api";

export function useItemTitle(id: string) {
  const client = useQueryClient();
  const queryKey = ["item", id] as const;
  const mutationKey = ["rename-item", id] as const;

  const item = useQuery({
    queryKey,
    queryFn: ({ signal }) => getItem(id, signal),
  });

  const edit = useMutation({
    mutationKey,
    scope: { id: `rename-item:${id}` },
    mutationFn: (title: string) => updateItemTitle(id, title),
    onSettled: () => {
      if (client.isMutating({ mutationKey, exact: true }) === 1) {
        return client.invalidateQueries({ queryKey, exact: true });
      }
    },
  });

  return {
    title: edit.isPending ? edit.variables : item.data?.title,
    save: edit.mutate,
    isSaving: edit.isPending,
    saveError: edit.error,
    queryError: item.error,
  };
}
```

- **표시값 변경**: `title`이 저장 중에는 `edit.variables`를 반환한다.
  기존 코드는 캐시에 복원한 `초안`을 표시했지만, 수정 코드는 다음 요청의 제출값 `확정`을 표시한다.
- **저장 순서 지정**: 같은 항목에 같은 `scope.id`를 지정했다.
  해당 QueryClient의 저장 요청을 제출 순서대로 실행해, 앞선 요청이 뒤늦게 서버 값을 덮어쓰는 상황을 줄인다.
- **재조회 조건 추가**: 매 요청 뒤 실행하던 재조회를 `isMutating(...) === 1` 조건 안으로 옮겼다.
  같은 항목의 저장이 남아 있으면 재조회를 미루고, 마지막 요청에서는 재조회 Promise를 반환해 조회가 끝날 때까지 최신 제출값을 표시한다.

## 변경 결과와 한계

앞서 제시한 두 번의 저장을 기준으로, 수정 전후 코드에서 다음 동작 차이를 예상할 수 있다.
아래 내용은 코드 분석이며 실행 결과는 보완해야 한다.

```text
조건: '검토 중' 제출 → '확정' 제출 → 첫 저장 실패
변경 전: onError가 캐시에 '초안' 복원 → title이 캐시를 읽음 → '초안' 표시
변경 후: 캐시 복구 없음 → title이 두 번째 요청의 variables를 읽음 → '확정' 표시
마지막 저장 성공과 재조회 완료 → title이 서버 조회값 '확정'을 표시
```

이 구현은 편집 컴포넌트 하나의 표시값을 다룬다.
여러 화면이 같은 낙관적 값을 공유해야 한다면 상태 공유 방식을 다시 검토해야 한다.
또한 요청을 직렬 실행하므로, 같은 항목의 요청이 쌓이면 최종 저장 완료까지의 대기 시간이 늘어난다.

최신 저장이나 재조회가 실패하면 폼은 오류를 알리고 입력 원문과 재시도 조작을 제공해야 한다.
이 코드의 요청 순서 제어 범위는 같은 QueryClient의 항목별 scope이다.
다른 탭이나 사용자의 동시 수정은 서버가 별도로 제어해야 한다.
서버는 저장을 마친 뒤 응답해야 한다.
서버가 타임아웃 이후에도 처리를 계속한다면 클라이언트의 요청 순서 제어 외에 서버의 충돌 제어가 필요하다.

> **보완**: 첫 저장 실패 후에도 다음 입력이 남는지 확인한 변경 전후 테스트 결과를 붙인다.
> **보완**: 마지막 저장과 재조회가 실패하는 경우의 화면 처리, 직접 구현한 범위를 확인한다.

## 기술 참고

- [TanStack Query의 UI 방식](https://tanstack.com/query/latest/docs/framework/react/guides/optimistic-updates)
- [Mutation Scopes](https://tanstack.com/query/latest/docs/framework/react/guides/mutations#mutation-scopes)
- [마지막 요청에서 재조회하는 조건](https://github.com/TanStack/query/discussions/11408)
````

## 적용 구분

- 적용: 제공한 메모나 소재 조사 자료를 기술적 경험과 코드가 있는 글로 정리하는 요청
- 다른 작업: 저장소의 소재를 새로 발굴하는 요청, 프로젝트 기능과 실행 방법을 설명하는 요청
