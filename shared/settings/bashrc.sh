# >>> nestlab >>>
# nestlab 설치 도구가 관리하는 블록이다.
# claude agent <이름> [인자...]: claude --agent <이름> [인자...]로 실행한다.
# codex agent <이름> [인자...]: NESTLAB_AGENT=<이름>으로 codex [인자...]를 실행한다.
# claude는 첫 인자가 agent가 아니면 원래 명령에 그대로 전달한다.
# codex는 --no-daemon을 기본 부착하고 나머지 인자를 그대로 전달한다.
claude() {
  if [ "$1" = "agent" ] && [ -n "$2" ]; then
    local name="$2"
    shift 2
    command claude --agent "$name" "$@"
  else
    command claude "$@"
  fi
}

codex() {
  local name=""
  if [ "$1" = "agent" ] && [ -n "$2" ]; then
    name="$2"
    shift 2
  fi
  local arg
  local default_args=(--no-daemon)
  for arg in "$@"; do
    if [ "$arg" = "--no-daemon" ]; then
      default_args=()
      break
    fi
  done
  if [ -n "$name" ]; then
    NESTLAB_AGENT="$name" command codex "${default_args[@]}" "$@"
  else
    command codex "${default_args[@]}" "$@"
  fi
}
# <<< nestlab <<<
