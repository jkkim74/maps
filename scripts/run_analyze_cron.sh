#!/usr/bin/env bash
# run_analyze_cron.sh — 매 거래일 16:00 KST에 /analyze 파이프라인을 무인 실행한다.
#
# /analyze(Claude 멀티에이전트)가 시장국면→전략→섹터→스크리닝→안전마진→기술→트레이드플래너
# 순으로 분석하고, 마지막 단계에서 scripts/load_analysis_picks.py로 분석 워치리스트
# (analysis_pick)에 적재한다. 적재 픽은 기본 state=WATCH(자동 무장 없음).
#
# /etc/cron.d/maps-analyze 에서 호출:
#   0 16 * * 1-5 ubuntu /opt/maps/scripts/run_analyze_cron.sh
#
# 필요 사전조건(운영서버):
#   - claude CLI 설치+인증 (운영서버는 ubuntu 사용자에 ~/.local/bin/claude, 구독 OAuth 로그인됨)
#   - cron은 반드시 인증된 그 사용자(ubuntu)로 실행 → 기존 ~/.claude 인증 재사용(별도 API키 불필요)
#   - (선택) API 키 방식을 쓰려면 /etc/maps/anthropic.env 에 ANTHROPIC_API_KEY=... 를 둔다
set -uo pipefail

APP_DIR="${MAPS_APP_DIR:-/opt/maps}"
SECRETS_FILE="${MAPS_ANTHROPIC_ENV:-/etc/maps/anthropic.env}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
LOG_DIR="$APP_DIR/logs"
TS="$(date '+%Y%m%d_%H%M%S')"
LOG="$LOG_DIR/analyze_cron_${TS}.log"

# cron 최소 PATH 보강 (claude는 ~/.local/bin, npm 전역 bin 등)
export PATH="${HOME:-/home/ubuntu}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

mkdir -p "$LOG_DIR"
cd "$APP_DIR" || { echo "APP_DIR 없음: $APP_DIR" >&2; exit 1; }

log() { echo "[$(date '+%F %T %Z')] $*" | tee -a "$LOG"; }

log "analyze cron 시작 (APP_DIR=$APP_DIR)"

# venv 활성화 — 에이전트 Bash 호출의 pykrx/DART 의존성 + 거래일 가드 import에 필요
# shellcheck disable=SC1091
if ! source "$APP_DIR/.venv/bin/activate"; then
  log "venv 활성화 실패: $APP_DIR/.venv"; exit 1
fi

# 거래일 가드 — 주말/공휴일이면 토큰 낭비 없이 종료
if ! python -c "import datetime; from maps.market.trading_rules import is_krx_closed_date; raise SystemExit(1 if is_krx_closed_date(datetime.date.today()) else 0)"; then
  log "오늘은 KRX 휴장 — /analyze 건너뜀"; exit 0
fi

# 인증: 운영서버 claude는 구독 OAuth(~/.claude)로 인증돼 있어 별도 API 키가 필요 없다.
# 단, API 키 방식을 쓰려면 $SECRETS_FILE(ANTHROPIC_API_KEY=...)을 두면 그때만 로드한다.
if [ -f "$SECRETS_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
  set +a
  log "시크릿 파일 로드: $SECRETS_FILE"
fi

if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  log "claude CLI 없음 (CLAUDE_BIN=$CLAUDE_BIN) — npm i -g @anthropic-ai/claude-code"; exit 1
fi

# 권한 모드: 무인 cron이라 대화형 승인이 불가하다. 기본은 **필요한 도구만 허용하는
# 화이트리스트**로 두어 전체 권한 우회를 피한다(헤드리스 권장 패턴). 목록이 부족하면
# 첫 수동 테스트에서 조정한다. 전체 우회가 꼭 필요하면 CLAUDE_SKIP_PERMISSIONS=1 로 명시.
CLAUDE_ALLOWED_TOOLS="${CLAUDE_ALLOWED_TOOLS:-Bash Read Write Edit Glob Grep WebFetch WebSearch Task TodoWrite}"
if [ "${CLAUDE_SKIP_PERMISSIONS:-0}" = "1" ]; then
  perm_args=(--dangerously-skip-permissions)
  log "권한 모드: skip-permissions (전체 우회, 명시 설정됨)"
else
  perm_args=(--allowedTools "$CLAUDE_ALLOWED_TOOLS")
  log "권한 모드: allowedTools 화이트리스트 [$CLAUDE_ALLOWED_TOOLS]"
fi

# headless /analyze 실행 (명령은 고정 /analyze)
log "claude -p /analyze 실행 시작"
if "$CLAUDE_BIN" -p "/analyze" "${perm_args[@]}" >>"$LOG" 2>&1; then
  log "analyze cron 완료 (성공) — 로그: $LOG"
  exit 0
fi
rc=$?
log "analyze cron 실패 (claude exit=$rc) — 로그 확인: $LOG"
exit "$rc"
