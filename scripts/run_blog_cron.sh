#!/usr/bin/env bash
# run_blog_cron.sh — 매 거래일 저녁, 그날의 매매 기록 블로그 글을 무인 생성한다.
#
# 2단계 구조:
#   1) maps.ops.daily_digest 가 DB에서 하루치 JSON(digest)을 만든다 — 수치의 유일한 출처
#   2) claude -p /blog 가 그 JSON만 읽고 원고를 쓴다 — DB 조회 권한 없음
#      출력은 마크다운이 아니라 네이버 스마트에디터 붙여넣기용 평문(.txt)이다.
#      규약: docs/blog_style_naver.md
#   3) verify_blog_numbers.py 가 글의 숫자를 다이제스트와 대조해 보고한다
#
# 2단계의 도구 제한이 핵심이지만 그것만 믿지는 않는다. 실측 결과:
#   - `--allowedTools Read Write` 만으로는 **Bash 가 막히지 않는다**(자동승인 목록일 뿐).
#   - `--disallowedTools Bash` 만 걸면 `ToolSearch` 로 `Monitor` 를 불러와 셸을 우회한다.
# 그래서 실행·조회 계열을 전부 명시적으로 차단한다. 차단 목록은 본질적으로
# 빈틈이 생길 수 있으므로 3단계 검증을 함께 둔다.
#
# /etc/cron.d/maps-blog 에서 호출 (stock_report 18:00 완료 후):
#   30 18 * * 1-5 ubuntu /opt/maps/scripts/run_blog_cron.sh
#
# 사전조건은 run_analyze_cron.sh 와 동일하다 (claude CLI + ubuntu 사용자 OAuth 인증).
set -uo pipefail
umask 077

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
# Read/Write 만 남기고 실행·조회·위임 계열을 전부 차단한다. ToolSearch 를 빼먹으면
# 지연 도구(Monitor 등)를 불러와 셸을 되찾으므로 반드시 포함해야 한다.
BLOG_DENY=(
  Bash Edit Agent Task Skill ToolSearch Workflow
  Monitor WebFetch WebSearch Glob Grep NotebookEdit
  ScheduleWakeup ReportFindings
)
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

redact_args=(--env-file "$APP_DIR/.env")
if [ -f "$SECRETS_FILE" ]; then
  redact_args+=(--env-file "$SECRETS_FILE")
fi
if ! python "$APP_DIR/scripts/redact_stream_secrets.py" "${redact_args[@]}" --check; then
  log "비밀 마스킹 초기화 실패 — 블로그 생성 중단"; exit 1
fi

BLOG_TIMEOUT="${BLOG_TIMEOUT:-900}"
RAW_LOG="$LOG_DIR/blog_cron_${TS}.jsonl"
OUT="$BLOG_DIR/${REF_DATE}.txt"

log "claude -p /blog 실행 시작 (timeout ${BLOG_TIMEOUT}s, raw=$RAW_LOG)"
timeout "${BLOG_TIMEOUT}s" "$CLAUDE_BIN" -p "/blog $DIGEST $OUT" \
    --allowedTools Read Write \
    --disallowedTools "${BLOG_DENY[@]}" \
    --verbose --output-format stream-json 2>>"$LOG" \
  | python "$APP_DIR/scripts/redact_stream_secrets.py" "${redact_args[@]}" \
  | tee -a "$RAW_LOG" \
  | python "$APP_DIR/scripts/analyze_stream_to_log.py" "$LOG"
pipeline_status=("${PIPESTATUS[@]}")
rc=${pipeline_status[0]}
if [ "${pipeline_status[1]}" -ne 0 ]; then
  log "비밀 마스킹 스트림 실패 — 로그 저장 중단"; rc=70
elif [ "${pipeline_status[2]}" -ne 0 ] || [ "${pipeline_status[3]}" -ne 0 ]; then
  log "blog 로그 파이프라인 실패"; rc=74
fi

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

# ── 3단계: 숫자 대조 ───────────────────────────────────────────────────────
# 도구 차단은 블랙리스트라 빈틈이 남는다. 결과물을 직접 검증해 로그에 남긴다.
# 파생값(차이·비율)은 정상이므로 실패로 처리하지 않고 사람이 훑도록 보고만 한다.
python "$APP_DIR/scripts/verify_blog_numbers.py" "$DIGEST" "$OUT" 2>&1 | tee -a "$LOG"

# ── 4단계: 네이버 포맷·문체 검사 ───────────────────────────────────────────
# 붙여넣기(마크다운 잔재)와 문체(이모지·em dash·상투구)를 함께 본다.
# 전자는 글을 깨뜨리고, 후자는 AI 생성물처럼 보이게 해 숫자의 신뢰까지 깎는다.
# 발행 전에 사람이 고칠 수 있도록 목록만 남긴다.
python "$APP_DIR/scripts/check_naver_format.py" "$OUT" 2>&1 | tee -a "$LOG"

log "blog cron 완료 — $OUT ($(wc -c <"$OUT") bytes)"
exit 0
