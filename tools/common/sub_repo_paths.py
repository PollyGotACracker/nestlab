"""hooks의 경로 상수 파일을 tools 모듈 이름과 겹치지 않게 불러온다.

입력: 없음
반환: 경로 상수 모듈 `repo_paths`
한계: tools 안에 같은 이름(sub_paths) 모듈이 있어 파일 경로로 직접 불러온다.
"""
import importlib.util
import sys
from pathlib import Path

_FILE = Path(__file__).resolve().parents[2] / "hooks" / "common" / "sub_paths.py"


# 경로 상수 파일을 `repo_paths` 이름으로 불러와 반환한다. 이미 불러왔으면 같은 모듈을 반환한다.
def load():
    if "repo_paths" not in sys.modules:
        spec = importlib.util.spec_from_file_location("repo_paths", _FILE)
        module = importlib.util.module_from_spec(spec)
        sys.modules["repo_paths"] = module
        spec.loader.exec_module(module)
    return sys.modules["repo_paths"]


paths = load()
