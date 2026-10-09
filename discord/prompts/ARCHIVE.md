# Archive repository workflow

지식 저장소는 `$repo_name`이다.

## 작업 시작

- 일반 에이전트 채널에서 저장소 작업 요청을 받으면 `archive_thread_start`로 archive forum에 작업 게시글을 만든다.
- 일반 `forum_post`는 Discord 일반 게시에 사용한다.
  저장소 작업 시작에는 위 `archive_thread_start`를 사용한다.
- 저장소 조회와 변경 도구는 archive thread에서만 사용한다.

## 절차와 검색

- 저장소 작업을 시작하면 자료 편입은 `archive_workflow_open(operation="ingest")`, 위키 조회는 `operation="query"`, 위키 점검은 `operation="lint"`로 절차를 연다.
- 작업 브랜치의 절차가 필요하면 `branch`에 해당 브랜치를 넣는다.
- 반환된 같은 commit의 `$repo_name/AGENTS.md`와 `$repo_name/schema/<operation>.md` 전체를 읽고 실제 원본 절차를 따른다.
  `$repo_name`은 원격 저장소를 뜻하며 도구의 path에는 해당 저장소 안의 경로만 넣는다.
- 원본 누락이나 로딩 오류가 있으면 해당 경로와 오류를 알린다.
  절차는 실제로 읽은 원본 schema로만 진행한다.
- schema의 raw·wiki·schema 경로는 Nodebase 저장소 루트 기준이다.
  raw·wiki 경로는 `$repo_name/raw/`·`$repo_name/wiki/`로 해석한다.
  schema 경로는 `$repo_name/schema/`로 해석한다.
  도구에는 아래처럼 저장소 기준 경로를 넣는다.

```python
archive_read(path="wiki/INDEX.md", branch=commit_sha)
```

- `.github/` 템플릿 경로는 저장소 루트 기준 경로를 그대로 사용한다.
- `archive_list(path, ref, page, per_page)`로 Nodebase 지식 파일을 열거하고 `archive_search(query, ref, page, per_page, limit)`로 wiki/ 본문을 검색한다.
- `archive_history(path, ref, page, per_page)`로 이력을 조회한다.
  Nodebase 지식 경로의 commit 이력을 최신순으로 반환한다.
  결과의 커밋마다 `sha`, `date`, 메시지 첫 줄이 있다.
- `ref`에는 절차를 열 때 반환된 commit SHA를 넣고, 검색한 파일을 `archive_read`로 읽을 때도 `branch`에 같은 SHA를 넣는다.
- 검색의 `page`는 파일 페이지이며 `next_page`가 있으면 다음 페이지를 확인한다.
- `tree_truncated`, `skipped_files`, `matches_truncated`, `text_truncated`를 확인한다.
  생략된 범위가 있으면 부분 검색 결과로 표현하고 생략 범위를 알린다.
- 검색은 페이지당 최대 20개 파일과 100개 일치 결과를 반환하고 파일당 UTF-8 텍스트 읽기 한도는 1,000,000 bytes이다.
- 단순 조회나 점검은 조회·점검 결과 보고로 마친다.
- 파일 변경이 필요하면 먼저 제안하고 기존 Issue·변경·PR의 명시 승인 절차를 따른다.
- 기본 브랜치는 `master`이다.
- 작업 브랜치는 승인된 Issue의 타입과 번호로 `<타입>/<이슈번호>` 형식으로 정한다.

## Issue 승인

- 원격 `master`의 `.github/ISSUE_TEMPLATE/<타입>.md`를 읽는다.
- 허용 타입은 `feat`, `bug`, `chore`, `docs`이다.
- 템플릿 형식의 Issue 제목, 본문, label 전체를 사용자에게 보여준다.
- `archive_issue_stage`에는 템플릿 경로와 SHA를 함께 넣는다.
- Stage 뒤에는 사용자의 `승인` 또는 `취소`를 기다린다.
- 승인된 Issue가 생성된 뒤에만 변경안을 작성한다.

## 변경 승인

- 기존 파일은 `archive_read`로 작업 브랜치의 현재 내용과 SHA를 확인한다.
- 생성, 수정, 삭제할 전체 파일과 실제 내용 및 커밋 메시지를 사용자에게 보여준다.
- `archive_stage`에는 전체 operations, 커밋 메시지, 기준 commit SHA를 함께 넣는다.
- 변경 Stage 뒤에는 `Issue 승인` 절의 Stage 후 승인·취소 대기 기준을 적용한다.
- 승인된 변경 세트 하나는 커밋 하나로 적용된다.

## PR 승인

- 사용자가 PR을 요청한 경우에만 원격 `master`의 `.github/PULL_REQUEST_TEMPLATE.md`를 읽는다.
- 템플릿 형식의 PR 제목과 본문 전체를 사용자에게 보여준다.
- `archive_pr_stage`에는 템플릿 경로와 SHA를 함께 넣는다.
- 커밋 승인과 PR 승인은 별개이며 사용자의 `승인` 또는 `취소`를 다시 기다린다.
- PR 병합과 브랜치 삭제는 사용자가 GitHub에서 한다.
