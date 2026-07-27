#!/usr/bin/env bash
# run_blog_cron.sh — 매 거래일 저녁, 그날의 매매 기록 블로그 글을 무인 생성한다.
#
# 2단계 구조:
#   1) maps.ops.daily_digest 가 DB에서 하루치 JSON(digest)을 만든다 — 수치의 유일한 출처
#   2) claude -p /blog 가 그 JSON만 읽고 Markdown을 쓴다 — DB 조회 권한 없음
#
# 글쓰기 단계에 Bash/WebSearch를 주지 않는 것이 핵심이다. 도구가 없으면 수치를
# 지어낼 경로 자체가 없다. 객관성은 프롬프트가 아니라 이 구조가 담보한다.
#
# /etc/cron.d/maps-blog 에서 호출 (stock_report 18:00 완료 후):
#   30 18 * * 1-5 ubuntu /opt/maps/scripts/run_blog_cron.sh
#
# 사전조건은 run_analyze_cron.sh 와 동일하다 (claude CLI + ubuntu 사용자 OAuth 인증).
set -uo pipefail

APP_DIR="${MAPS_APP_DIR:-/opt/maps}"
SECRETS_FILE="${MAPS_ANTHROPIC_ENV:-/etc/maps/anthropic.env}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
LOG_DIR="$APP_DIR/logs"
BLOG_DIR="${MAPS_BLOG_DIR:-$APP_DIR/blog}"
TS="$(date '+%Y%m%d_%H%M%S')"
LOG="$LOG_DIR/blog_cron_${TS}.log"

export PATH="${HOME:-/home/ubuntu}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

mkdir -p "$LOG_DIR" "$BLOG_DIR"
cd "$APP_DIR" || { echo "APP_DIR 없음: $APP_DIR" >&2; exit 1; }

log() { echo "[$(date '+%F %T %Z')] $*" | tee -a "$LOG"; }

log "blog cron 시작 (APP_DIR=$APP_DIR, BLOG_DIR=$BLOG_DIR)"

# 중복 실행 방지 — 직전 실행(또는 행)이 안 끝났으면 이번 회차는 건너뛴다.
exec 9>"${MAPS_BLOG_LOCK:-/tmp/maps_blog.lock}"
if ! flock -n 9; then
  log "직전 blog 실행이 진행 중 — 이번 회차 건너뜀"; exit 0
fi

# shellcheck disable=SC1091
if ! source "$APP_DIR/.venv/bin/activate"; then
  log "venv 활성화 실패: $APP_DIR/.venv"; exit 1
fi

# 거래일 가드 — 주말/공휴일이면 토큰 낭비 없이 종료
if ! python -c "import datetime; from maps.market.trading_rules import is_krx_closed_date; raise SystemExit(1 if is_krx_closed_date(datetime.date.today()) else 0)"; then
  log "오늘은 KRX 휴장 — 블로그 생성 건너뜀"; exit 0
fi

REF_DATE="${MAPS_BLOG_REF_DATE:-$(date '+%F')}"
DIGEST="$LOG_DIR/digest_${REF_DATE}.json"

# ── 1단계: 결정적 다이제스트 생성 ───────────────────────────────────────────
log "다이제스트 생성 시작 (ref_date=$REF_DATE) → $DIGEST"
if ! python -c "
import datetime, json, sys
from maps.common.db import SessionLocal
from maps.common.settings import get_settings
from maps.ops.daily_digest import build_daily_digest

ref = datetime.date.fromisoformat('$REF_DATE')
db = SessionLocal()
try:
    digest = build_daily_digest(db, get_settings(), ref)
finally:
    db.close()
with open('$DIGEST', 'w', encoding='utf-8') as fh:
    json.dump(digest.model_dump(), fh, ensure_ascii=False, indent=2, default=str)
for err in digest.errors:
    print(f'  [섹션 실패] {err}', file=sys.stderr)
print(f'  후보={digest.candidate_total} 체결={len(digest.executions)} 오류={len(digest.errors)}')
" 2>&1 | tee -a "$LOG"; then
  log "다이제스트 생성 실패 — 블로그 생성 중단"; exit 1
fi

# ── 2단계: 글쓰기 ──────────────────────────────────────────────────────────
# 허용 도구를 Read/Write로 제한한다. Bash도 WebSearch도 없으므로 글쓰기 에이전트가
# 다이제스트 밖의 수치를 가져올 방법이 없다.
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

BLOG_TIMEOUT="${BLOG_TIMEOUT:-900}"
RAW_LOG="$LOG_DIR/blog_cron_${TS}.jsonl"
OUT="$BLOG_DIR/${REF_DATE}.md"

log "claude -p /blog 실행 시작 (timeout ${BLOG_TIMEOUT}s, raw=$RAW_LOG)"
timeout "${BLOG_TIMEOUT}s" "$CLAUDE_BIN" -p "/blog $DIGEST $OUT" \
    --allowedTools Read Write \
    --verbose --output-format stream-json 2>>"$LOG" \
  | tee -a "$RAW_LOG" \
  | python "$APP_DIR/scripts/analyze_stream_to_log.py" "$LOG"
rc=${PIPESTATUS[0]}

if [ "$rc" -ne 0 ]; then
  if [ "$rc" -eq 124 ]; then
    log "blog cron 시간초과(${BLOG_TIMEOUT}s) — claude 강제종료. 로그: $LOG"
  else
    log "blog cron 실패 (claude exit=$rc) — 로그 확인: $LOG"
  fi
  exit "$rc"
fi

if [ ! -s "$OUT" ]; then
  log "claude는 성공했으나 글이 비어 있음: $OUT"; exit 1
fi

log "blog cron 완료 — $OUT ($(wc -c <"$OUT") bytes)"
exit 0
