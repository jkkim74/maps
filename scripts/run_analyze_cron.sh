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

# 중복 실행 방지 — 직전 실행(또는 행)이 안 끝났으면 이번 회차는 건너뛴다.
exec 9>"${MAPS_ANALYZE_LOCK:-/tmp/maps_analyze.lock}"
if ! flock -n 9; then
  log "직전 analyze 실행이 진행 중 — 이번 회차 건너뜀"; exit 0
fi

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

# strategy-selector 입력은 Claude가 문서에서 추측하지 않도록 DB에서 먼저 고정한다.
if ! STRATEGY_STAGE_JSON="$(python "$APP_DIR/scripts/export_strategy_stages.py")"; then
  log "strategy stage JSON 생성 실패 — analyze 중단"; exit 1
fi
if [ -z "$STRATEGY_STAGE_JSON" ]; then
  log "strategy stage JSON 비어 있음 — analyze 중단"; exit 1
fi
ANALYZE_PROMPT="$(printf '/analyze\n\nSTRATEGY_STAGE_CONTEXT_JSON:\n%s' "$STRATEGY_STAGE_JSON")"
log "strategy stage JSON 주입 완료"

# headless /analyze 실행. 무인 hang 방지를 위해 timeout 적용.
#
# stream-json + --verbose 로 각 단계(에이전트 호출·도구 실행·텍스트·완료)를 실시간 이벤트로
# 받아, scripts/analyze_stream_to_log.py가 사람이 읽는 진행로그로 변환해 $LOG에 남긴다.
# 원본 JSON 이벤트는 디버깅용으로 $RAW_LOG에 그대로 보존한다.
# 파이프라인 소요가 늘고 있다: 7/27 17분 → 7/30 28분 → 7/31 29분 → 8/3 30분 초과로
# 강제 종료(0건, $8.25 소모). 1800s 는 이미 여유가 없었다. 45분으로 올린다.
# 여전히 넘긴다면 시간을 더 늘릴 게 아니라 단계별 소요를 봐야 한다 — 8/3 실패는
# strategy-selector 가 승격 단계를 HANDOFF.md 로 추측해 재선정을 한 번 더 돈 탓이 크다.
ANALYZE_TIMEOUT="${ANALYZE_TIMEOUT:-2700}"
RAW_LOG="$LOG_DIR/analyze_cron_${TS}.jsonl"
log "claude -p /analyze 실행 시작 (timeout ${ANALYZE_TIMEOUT}s, 진행로그 스트리밍, raw=$RAW_LOG)"
timeout "${ANALYZE_TIMEOUT}s" "$CLAUDE_BIN" -p "$ANALYZE_PROMPT" "${perm_args[@]}" \
    --verbose --output-format stream-json 2>>"$LOG" \
  | tee -a "$RAW_LOG" \
  | python "$APP_DIR/scripts/analyze_stream_to_log.py" "$LOG"
rc=${PIPESTATUS[0]}  # claude의 종료코드 (tee/python이 아니라)
if [ "$rc" -eq 0 ]; then
  log "analyze cron 완료 (성공) — 로그: $LOG"
  exit 0
fi
if [ "$rc" -eq 124 ]; then
  log "analyze cron 시간초과(${ANALYZE_TIMEOUT}s) — claude 강제종료. 로그: $LOG"
  fail_reason="timeout(${ANALYZE_TIMEOUT}s)"
else
  log "analyze cron 실패 (claude exit=$rc) — 로그 확인: $LOG"
  fail_reason="claude exit=$rc"
fi

# 실패 실행기록 — claude가 8단계까지 못 가 completed row를 못 남겼으므로, cron이
# analysis_run에 status=failed 1건을 best-effort로 남긴다(0종목 정상완료와 구분).
echo '{"trade_plan": []}' | python "$APP_DIR/scripts/load_analysis_picks.py" \
    --allow-empty --status failed --error "$fail_reason" >>"$LOG" 2>&1 \
  || log "failed-run 기록 실패(무시) — analysis_run 미기록"
exit "$rc"
