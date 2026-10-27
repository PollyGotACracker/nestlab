"""Secret Manager의 설정으로 GCP VM에서 Discord 봇을 실행한다.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# 서드파티 import 오류 메시지도 UTF-8로 나오도록 먼저 설정한다.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from google.cloud import secretmanager

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
BOT_PATH = HERE / "bot.py"
CONFIG_LINK = PROJECT_ROOT / "config.json"

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "discord-party-parrots")
CONFIG_SECRET_ID = os.environ.get("CONFIG_SECRET_ID", "DISCORD_BOT_CONFIG")

RAM_DIR = Path("/dev/shm")
RAM_CONFIG = RAM_DIR / f"discord-bot-config-{os.getuid()}.json"


# Secret Manager에서 봇 설정 JSON을 읽는다.
def read_secret() -> bytes:
    # Secret Manager에서 설정 값을 읽는다.
    client = secretmanager.SecretManagerServiceClient()
    name = (
        f"projects/{GCP_PROJECT_ID}"
        f"/secrets/{CONFIG_SECRET_ID}"
        f"/versions/latest"
    )
    response = client.access_secret_version(request={"name": name})
    payload = response.payload.data

    # secret이 올바른 JSON이 아니면 bot.py 실행 전에 실패시킨다.
    json.loads(payload.decode("utf-8"))
    return payload


# 이전 실행이 남긴 설정 링크와 RAM 파일을 지운다.
def remove_runtime_config() -> None:
    # 설정 링크가 램 디스크 설정을 가리키면 링크를 지운다.
    if CONFIG_LINK.is_symlink():
        try:
            target = os.readlink(CONFIG_LINK)
        except OSError:
            target = ""

        # 링크가 메모리 설정을 가리키면 링크를 지운다.
        if target == str(RAM_CONFIG):
            CONFIG_LINK.unlink(missing_ok=True)

    # 램 디스크 설정 파일을 지운다.
    RAM_CONFIG.unlink(missing_ok=True)


# 설정을 RAM 파일에 쓰고 config.json 링크로 연결한다.
def prepare_runtime_config(payload: bytes) -> None:
    # 메모리 폴더가 없으면 실행을 중단한다.
    if not RAM_DIR.is_dir():
        raise RuntimeError("/dev/shm이 없습니다. 이 런처는 Linux VM의 /dev/shm을 사용합니다.")

    # VM에 실제 config.json 파일이 있으면 Secret Manager를 쓰는 의미가 없다.
    if CONFIG_LINK.exists() and not CONFIG_LINK.is_symlink():
        raise RuntimeError(
            f"{CONFIG_LINK}가 일반 파일로 존재합니다. "
            f"VM의 {CONFIG_LINK}를 삭제한 뒤 다시 실행하세요."
        )

    # 이전 링크와 설정 파일을 지운다.
    if CONFIG_LINK.is_symlink():
        CONFIG_LINK.unlink()

    # 이전 메모리 설정 파일을 지운다.
    RAM_CONFIG.unlink(missing_ok=True)

    # 소유자만 읽도록 권한을 줘서 설정 파일을 쓴다.
    fd = os.open(
        RAM_CONFIG,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
    except Exception:
        RAM_CONFIG.unlink(missing_ok=True)
        raise

    # 설정 링크를 메모리 설정 파일에 연결한다.
    CONFIG_LINK.symlink_to(RAM_CONFIG)


# 같은 Python으로 봇을 실행하고 종료 코드를 반환한다.
def run_bot() -> int:
    # 봇 프로세스를 시작한다.
    child = subprocess.Popen(
        [sys.executable, str(BOT_PATH)],
        cwd=str(PROJECT_ROOT),
        start_new_session=True,
    )

    # 받은 종료 신호를 봇 프로세스 그룹에 전달한다.
    def forward_signal(signum: int, _frame: object) -> None:
        # 자식 프로세스가 실행 중이면 신호를 프로세스 그룹에 보낸다.
        if child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    # 종료 신호를 봇 프로세스에 전달한다.
    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    # 봇 프로세스의 종료 코드를 반환한다.
    return child.wait()


# 설정 준비, 봇 실행, 정리를 차례로 수행한다.
def main() -> int:
    # 봇 스크립트가 있는지 확인한다.
    if not BOT_PATH.is_file():
        raise RuntimeError(f"bot.py를 찾을 수 없습니다: {BOT_PATH}")

    # 이전 설정을 지우고 비밀 값으로 실행 설정을 만든다.
    remove_runtime_config()
    payload = read_secret()
    prepare_runtime_config(payload)

    # 봇을 실행하고 끝나면 설정을 지운다.
    try:
        return run_bot()
    finally:
        remove_runtime_config()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"[run_gcp] {exc}", file=sys.stderr)
        raise SystemExit(1)
