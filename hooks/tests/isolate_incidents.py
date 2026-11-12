"""시험이 사건 문서를 저장소에 기록하지 않도록 사건 폴더 기준을 임시 폴더로 바꾸고 분석 세션 호출을 끈다.

호출자: 응답 미흡 판정을 실행할 수 있는 시험 모듈의 import 문
입력: 없음. import만으로 적용된다.
반환: 없음. 임시 폴더는 프로세스 종료 때 삭제된다.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_invalid_response

TEMP = tempfile.TemporaryDirectory()
check_invalid_response.INCIDENT_ROOT = Path(TEMP.name)
check_invalid_response.launch_analysis = lambda *args, **kwargs: None
