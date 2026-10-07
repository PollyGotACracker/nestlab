"""링크(symlink·junction)의 판별, 생성, 삭제이다.

사용: set_skills.py, set_agents.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path



# 링크(symlink 또는 junction)인지 확인한다.
def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


# 링크가 가리키는 실제 경로를 반환한다.
# 링크가 아니면 None이다.
def link_target(path: Path) -> Path | None:
    if not is_link(path):
        return None
    return Path(os.path.realpath(path))


# symlink를 먼저 시도하고, Windows에서 권한이 없으면 junction을 만든다.
def make_link(src: Path, dst: Path) -> str:
    try:
        dst.symlink_to(src, target_is_directory=True)
        return "symlink"
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(src), str(dst))
        return "junction"


# 링크 자체만 지운다.
# 링크가 가리키던 대상에는 손대지 않는다.
def remove_link(path: Path) -> None:
    if sys.platform == "win32":
        os.rmdir(path)
    else:
        os.unlink(path)
