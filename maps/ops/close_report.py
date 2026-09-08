"""장마감 텔레그램 리포트 — 하루치 다이제스트를 한 통의 요약으로 렌더한다.

숫자·사유의 출처는 :func:`maps.ops.daily_digest.build_daily_digest` 하나다. 여기서는
새로 계산하지 않고, 당일 실패 잡(``job_run_log``)만 얹어 텔레그램 HTML 로 만든다.
"확인 필요" 는 LLM 없이 데이터에서 규칙으로 뽑는다 — 근거 없는 문장은 넣지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from maps.api.schemas import DailyDigest
from maps.common.models import JobRunLog
from maps.common.settings import MapsSettings
from maps.ops.daily_digest import build_daily_digest
from maps.ops.notifications import _esc

logger = logging.getLogger(__name__)

JOB_NAME = "daily_close_report"
_WEEKDAYS = "월화수목금토일"
_MAX_HOLDINGS = 10
_MAX_LINES_PER_LIST = 12
_MESSAGE_CLIP = 80


def _failed_jobs_today(db: Session, ref_date: dt.date) -> list[tuple[str, int, str]]:
    """당일 실패 잡을 이름별 (건수, 마지막 message) 로 모은다.

    배치 모니터처럼 잡별 마지막 행만 보면 재실행 성공이 그날의 실패를 가린다.
    그래서 실패 행을 **전부** 센다. 이 리포트 잡 자신은 제외한다.
    """
    rows = (
        db.query(JobRunLog)
        .filter(
            JobRunLog.ref_date == ref_date,
            JobRunLog.status == "failed",
            JobRunLog.name != JOB_NAME,
        )
        .order_by(JobRunLog.id)
        .all()
    )
    counts: Counter[str] = Counter()
    last_message: dict[str, str] = {}
    for row in rows:
        counts[row.name] += 1
        last_message[row.name] = (row.message or "").strip()[:_MESSAGE_CLIP]
    return [(name, counts[name], last_message[name]) for name in sorted(counts)]


def _pct(value: float | None) -> str:
    """다이제스트의 비율은 소수(0.03125)다 — 표시만 % 로 바꾼다."""
    return "—" if value is None else f"{value * 100:+.2f}%"


def _won(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _skip_summary(reasons: list[str | None]) -> str:
    counted = Counter(r or "unknown" for r in reasons)
    return ", ".join(f"{_esc(reason)} {n}" for reason, n in counted.most_common(4))


def _attention_items(
    digest: DailyDigest,
    failed_jobs: list[tuple[str, int, str]],
    *,
    blog_written: bool,
) -> list[str]:
    """데이터가 뒷받침하는 '확인 필요' 한 줄들. 해당 없으면 빈 목록."""
    items: list[str] = []
    for name, count, _ in failed_jobs:
        if name == "broker_sync":
            continue
        items.append(f"{_esc(name)} 실패 {count}건 — 배치 모니터 확인")
    sync_failures = next((c for n, c, _ in failed_jobs if n == "broker_sync"), 0)
    if sync_failures >= 3:
        items.append(f"KIS 연결 불안정 (동기화 실패 {sync_failures}회)")
    if digest.errors:
        names = ", ".join(_esc(e.split(":", 1)[0]) for e in digest.errors)
        items.append(f"다이제스트 섹션 수집 실패: {names}")

    market = digest.market
    if market is not None:
        if market.entry_block_days >= 5:
            items.append(
                f"신규매수 한도 0% 가 {market.entry_block_days}거래일째 — "
                f"weekly_trend={_esc(market.weekly_trend)}"
            )
        if not market.score_ready and market.missing_factors:
            items.append(f"시장 점수 미측정 팩터: {_esc(', '.join(market.missing_factors))}")

    limit_up = digest.limit_up
    if limit_up is not None and limit_up.guard is not None:
        gaps = sum(
            1
            for r in limit_up.guard.scan_rejections
            if r.reason.endswith(("listing_unknown", "unknown_security"))
        )
        if gaps:
            items.append(f"상한가 감시 탈락에 데이터 공백 {gaps}건 (상장일·메타 결측)")
        if limit_up.guard.halted_reasons:
            items.append(
                f"상한가 일일 가드 발동: {_esc(', '.join(limit_up.guard.halted_reasons))}"
            )

    if digest.candidate_total and digest.candidate_incomplete_total / digest.candidate_total > 0.3:
        items.append(
            f"점수 미완성 후보 {digest.candidate_incomplete_total}/{digest.candidate_total} — "
            "피드 커버리지 확인"
        )
    if digest.liquidity_blocked_total > 0:
        items.append(f"유동성 때문에 제외된 후보 {digest.liquidity_blocked_total}")

    run = digest.analysis_run
    if run is None:
        items.append("16시 분석 파이프라인 실행 기록 없음")
    elif run.status == "failed":
        items.append(f"분석 파이프라인 실패: {_esc(run.error_message or '사유 미기록')}")

    stale = sum(1 for e in digest.conditional_entries if e.stale_reason)
    if stale:
        items.append(f"만료된 조건부 진입 {stale}건 — 재무장 또는 삭제")
    failed_reports = [r.report_type for r in digest.market_context if r.status == "failed"]
    if failed_reports:
        items.append(f"종목 리포트 실패: {_esc(', '.join(failed_reports))}")
    if not blog_written:
        items.append("블로그 원고 미생성")
    return items


def _render(
    digest: DailyDigest,
    failed_jobs: list[tuple[str, int, str]],
    ref_date: dt.date,
    *,
    blog_written: bool,
) -> str:
    lines: list[str] = [
        f"🏁 <b>MAPS 장마감 리포트</b> {ref_date.isoformat()} ({_WEEKDAYS[ref_date.weekday()]})"
    ]

    # 시스템
    total_failed = sum(c for _, c, _ in failed_jobs)
    lines.append(
        "🖥 <b>시스템</b> " + ("✅ 정상" if not total_failed else f"⚠️ 실패 {total_failed}건")
    )
    for name, count, message in failed_jobs:
        suffix = f" — {_esc(message)}" if message else ""
        lines.append(f" · {_esc(name)} ×{count}{suffix}")
    for error in digest.errors:
        lines.append(f" · 다이제스트 수집 실패: {_esc(error.split(':', 1)[0])}")

    # 장세
    market = digest.market
    if market is None:
        lines.append("📊 <b>장세</b> 수집 실패")
    else:
        raw = f" (raw {_esc(market.raw_regime)})" if market.raw_regime else ""
        lines.append(
            f"📊 <b>장세</b> {_esc(market.regime)}{raw} · 주간추세 {_esc(market.weekly_trend)}"
            f" · 변동성 {_esc(market.vol_regime or '—')}"
        )
        ratio = market.entry_limit_ratio
        limit = "—" if ratio is None else f"{ratio * 100:.0f}%"
        streak = (
            f" — {market.entry_block_since}부터 {market.entry_block_days}거래일째"
            if market.entry_block_since
            else ""
        )
        lines.append(f" · 신규매수 한도 {limit}{streak}")

    # 계좌
    portfolio = digest.portfolio
    if portfolio is None:
        lines.append("💰 <b>계좌</b> 스냅샷 없음")
    else:
        lines.append(
            f"💰 <b>계좌</b> 총 {_won(portfolio.total_assets)}원 · 현금 {_won(portfolio.cash)}"
            f" · 평가 {_won(portfolio.positions_value)} · 전일比 {_pct(portfolio.daily_pnl_pct)}"
        )
        holdings = sorted(
            portfolio.holdings,
            key=lambda h: (h.unrealized_pnl_pct is None, -(h.unrealized_pnl_pct or 0.0)),
        )
        for h in holdings[:_MAX_HOLDINGS]:
            lines.append(
                f" · <code>{_esc(h.ticker)}</code> {_esc(h.name or '')} {h.quantity:,}주"
                f" @{_won(h.avg_price)} 평가 {_pct(h.unrealized_pnl_pct)}"
            )
        if len(holdings) > _MAX_HOLDINGS:
            lines.append(f" · 외 {len(holdings) - _MAX_HOLDINGS}종목")

    # 매매
    buys = [e for e in digest.executions if e.side == "buy"]
    sells = [e for e in digest.executions if e.side == "sell"]
    lines.append(f"🛒 <b>매매</b> 매수 {len(buys)} · 매도 {len(sells)}")
    if not digest.executions:
        lines.append(" · 체결 없음")
    for e in digest.executions[:_MAX_LINES_PER_LIST]:
        side = "매수" if e.side == "buy" else "매도"
        price = _won(e.fill_price if e.fill_price is not None else e.order_price)
        tail = (
            f" — {_esc(e.exit_reason)}" if e.side == "sell" and e.exit_reason
            else f" ({_esc(e.strategy_id)})" if e.strategy_id else ""
        )
        lines.append(
            f" · {side} <code>{_esc(e.ticker)}</code> {_esc(e.name or '')} "
            f"{e.fill_qty or e.qty:,}주 @{price} [{_esc(e.status)}]{tail}"
        )

    # 상한가 전략
    limit_up = digest.limit_up
    if limit_up is None:
        lines.append("🚀 <b>상한가 전략</b> 수집 실패")
    elif not limit_up.enabled:
        lines.append("🚀 <b>상한가 전략</b> 꺼짐")
    else:
        sessions = limit_up.sessions
        entered = sum(1 for s in sessions if s.outcome != "no_trigger")
        rejections = limit_up.guard.scan_rejections if limit_up.guard else []
        halted = limit_up.guard.halted_reasons if limit_up.guard else []
        if not sessions and not rejections and not halted:
            lines.append(f"🚀 <b>상한가 전략</b> 후보 없음 · 모드 {_esc(limit_up.mode)}")
        else:
            reject_summary = _skip_summary([r.reason.split(":")[-1] for r in rejections])
            lines.append(
                f"🚀 <b>상한가 전략</b> 감시 {len(sessions)} · 진입 {entered}"
                f" · 감시제외 {len(rejections)}"
                + (f" ({reject_summary})" if reject_summary else "")
                + f" · 가드 {_esc(', '.join(halted)) if halted else '—'}"
            )
            for s in sessions[:_MAX_LINES_PER_LIST]:
                pnl = f" 실현 {s.realized_pnl:+,.0f}원" if s.realized_pnl is not None else ""
                lines.append(
                    f" · <code>{_esc(s.ticker)}</code> {_esc(s.name or '')} {_esc(s.outcome)}{pnl}"
                )

    # 분석 파이프라인
    run = digest.analysis_run
    if run is None:
        lines.append("🔍 <b>분석 파이프라인</b> 실행 기록 없음")
    elif run.status == "failed":
        lines.append(f"🔍 <b>분석 파이프라인</b> 실패 — {_esc(run.error_message or '사유 미기록')}")
    else:
        note = f" · {_esc(run.note)}" if run.note else ""
        lines.append(f"🔍 <b>분석 파이프라인</b> 픽 {run.picks_count}{note}")
        for p in run.picks[:_MAX_LINES_PER_LIST]:
            lines.append(
                f" · <code>{_esc(p.ticker)}</code> {_esc(p.name or '')} 매수 {_won(p.buy_price)}"
                f" / 목표 {_won(p.target_price)} / 손절 {_won(p.stop_price)}"
            )

    # 내일 예정
    preview = digest.tomorrow_orders
    if preview is None:
        lines.append("📋 <b>내일 예정</b> 미리보기 없음")
    else:
        planned = [i for i in preview.items if not i.skipped]
        skipped = [i for i in preview.items if i.skipped]
        summary = _skip_summary([i.skip_reason for i in skipped])
        lines.append(
            f"📋 <b>내일 예정</b> 주문 {len(planned)} · 제외 {len(skipped)}"
            + (f" ({summary})" if summary else "")
        )
        for i in planned[:_MAX_LINES_PER_LIST]:
            lines.append(
                f" · <code>{_esc(i.ticker)}</code> {_esc(i.name)} {i.estimated_qty:,}주"
                f" @{i.limit_price:,} ({_esc(i.strategy_id)})"
            )

    # 확인 필요
    attention = _attention_items(digest, failed_jobs, blog_written=blog_written)
    if attention:
        lines.append("⚠️ <b>확인 필요</b>")
        lines.extend(f" · {item}" for item in attention)
    else:
        lines.append("✅ <b>확인 필요</b> 없음")
    return "\n".join(lines)


def build_close_report(db: Session, settings: MapsSettings, ref_date: dt.date) -> str:
    """``ref_date`` 하루의 장마감 리포트를 텔레그램 HTML 문자열로 만든다."""
    digest = build_daily_digest(db, settings, ref_date)
    failed_jobs = _failed_jobs_today(db, ref_date)
    blog_written = any(
        (Path(settings.maps_blog_dir) / f"{ref_date.isoformat()}{suffix}").exists()
        for suffix in (".txt", ".md")
    )
    return _render(digest, failed_jobs, ref_date, blog_written=blog_written)
