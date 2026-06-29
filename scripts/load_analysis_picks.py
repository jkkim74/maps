"""/analyze 파이프라인 결과를 분석 워치리스트(SCR19)에 적재하는 로더.

`/analyze` 의 최종 단계인 trade-planner 가 산출한 JSON
(`{"trade_plan": [{ticker, name, entry, target, stop_loss, risk_reward,
position_size_pct, max_loss_pct}, ...]}`)을 받아 `analysis_pick` 테이블에 적재한다.

자동주문 소스(candidate_snapshot)와 분리된 별도 보관소이므로, 검증 게이트를
거치지 않은 재량 선정 종목을 여기에 넣어도 '검증-우선' 철학을 깨지 않는다.

사용법 (프로젝트 루트에서):
    echo '<trade-planner JSON>' | python scripts/load_analysis_picks.py \
        --regime mixed --context "pullback_v3, ath_breakout_v1 / 반도체"
    python scripts/load_analysis_picks.py --file plan.json --regime strong
    python scripts/load_analysis_picks.py --file plan.json --dry-run

trade-planner JSON은 stdin 또는 --file 로 전달한다. 최상위가
``{"trade_plan": [...]}`` 형태이거나, 종목 배열 ``[...]`` 자체여도 처리한다.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# `python scripts/load_analysis_picks.py` 로 직접 실행하면 sys.path[0]이 scripts/라서
# 최상위 `maps` 패키지를 못 찾는다. 리포지토리 루트(이 파일의 부모의 부모)를 추가해
# 어느 cwd에서 실행하든 import가 동작하게 한다(편집 가능 설치 없이도).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy.orm import Session

from maps.common.db import SessionLocal
from maps.common.models import AnalysisPick, AnalysisRun
from maps.market.trading_rules import round_to_krx_tick, round_up_krx_price


def _snap_price(value: Any, market: str | None, *, kind: str) -> Any:
    """가격을 KRX 호가 단위로 스냅한다. 숫자가 아니거나 0 이하면 원값 유지.

    trade-planner(LLM)가 호가 단위를 어긋나게 낸 값(예: 54912)을 결정론적으로 정규화한다.
    매수가는 올림(체결 우선, 자동 파이프라인 plan_buy와 일관), 목표가·손절가는 최근접 호가.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return value
    mkt = (market or "KOSPI").upper()
    if kind == "buy":
        return round_up_krx_price(float(value), market=mkt)
    return round_to_krx_tick(float(value), market=mkt)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="/analyze 결과 → 분석 워치리스트 적재")
    parser.add_argument("--file", default=None, help="trade-planner JSON 파일 경로. 미지정 시 stdin.")
    parser.add_argument("--regime", default=None, help="시장국면(strong|mixed|weak). 1단계 결과.")
    parser.add_argument("--context", default=None, help="선정 전략/섹터 등 맥락 메모(2·3단계).")
    parser.add_argument("--source", default="analyze", help="픽 출처 라벨. 기본=analyze.")
    parser.add_argument("--ref-date", default=None, help="기준일(YYYY-MM-DD). 기본=오늘.")
    parser.add_argument("--dry-run", action="store_true", help="DB 기록 없이 적재 대상만 출력.")
    parser.add_argument(
        "--status", default="completed", choices=["completed", "failed"],
        help="실행 기록 상태. 기본=completed. cron 실패 기록 시 failed.",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="입력 JSON이 비어도 에러 없이 0종목 실행기록만 남긴다(0종목/실패 기록용).",
    )
    parser.add_argument("--note", default=None, help="실행 요약/사유 한 줄 메모.")
    parser.add_argument("--error", default=None, help="실패 사유(status=failed 시).")
    parser.add_argument(
        "--candidates-count", type=int, default=None, help="스크리닝 후보 수(선택적 퍼널 메모).",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="텔레그램 편입 알림을 보내지 않는다(수동 적재·테스트용).",
    )
    return parser.parse_args(argv)


def _load_payload(file_path: str | None, allow_empty: bool = False) -> list[dict[str, Any]]:
    """stdin 또는 파일에서 trade-planner JSON을 읽어 종목 배열을 반환한다.

    allow_empty=True면 입력이 비어도 에러 대신 빈 리스트를 반환한다
    (0종목/실패 실행기록만 남기는 경로용).
    """
    if file_path:
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read().strip()
    else:
        # stdin은 로케일(예: cp949)이 아니라 UTF-8로 명시 디코딩한다.
        raw = sys.stdin.buffer.read().decode("utf-8").strip()
    if not raw:
        if allow_empty:
            return []
        raise SystemExit("입력 JSON이 비어 있습니다 (stdin 또는 --file).")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 파싱 실패: {exc}")
    plan = data.get("trade_plan", data) if isinstance(data, dict) else data
    if not isinstance(plan, list):
        raise SystemExit("trade_plan 배열을 찾을 수 없습니다.")
    return plan


def _build_rationale(item: dict[str, Any]) -> str | None:
    """R:R·포지션 사이즈 등 거래계획 메타를 한 줄 메모로 만든다."""
    parts: list[str] = []
    if item.get("risk_reward") is not None:
        parts.append(f"R:R {item['risk_reward']}")
    if item.get("position_size_pct") is not None:
        parts.append(f"size {item['position_size_pct']}%")
    if item.get("max_loss_pct") is not None:
        parts.append(f"maxloss {item['max_loss_pct']}%")
    return " · ".join(parts) if parts else None


def load_picks(
    plan: list[dict[str, Any]],
    *,
    regime: str | None,
    context: str | None,
    source: str,
    ref_date: datetime.date,
    dry_run: bool,
    status: str = "completed",
    note: str | None = None,
    error_message: str | None = None,
    candidates_count: int | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    notify_telegram: bool = True,
    notifier: Any | None = None,
) -> list[dict[str, Any]]:
    """종목 배열을 analysis_pick 으로 적재한다. 적재된(또는 예정) 항목 요약을 반환한다.

    dry_run이 아니면 픽 적재 여부와 무관하게 analysis_run 실행기록 1건을 함께 남긴다
    (0종목이어도 picks_count=0 row를 기록 → cron 실패와 구분 가능).

    session_factory 를 주입하면(테스트용) 임의의 DB 세션으로 적재할 수 있다.
    """
    prepared: list[dict[str, Any]] = []
    picks: list[AnalysisPick] = []
    for item in plan:
        ticker = str(item.get("ticker", "")).strip()
        if not ticker:
            continue
        entry = item.get("entry")
        target = item.get("target")
        stop = item.get("stop_loss")
        if entry is None or target is None or stop is None:
            print(f"  [skip] {ticker}: entry/target/stop_loss 누락", file=sys.stderr)
            continue
        # LLM이 호가 단위를 어긋나게 낸 가격(예: 목표가 54912)을 KRX 호가 그리드로 정규화한다.
        market = item.get("market")
        entry = _snap_price(entry, market, kind="buy")
        target = _snap_price(target, market, kind="target")
        stop = _snap_price(stop, market, kind="stop")
        prepared.append(
            {"ticker": ticker, "name": item.get("name") or ticker,
             "buy_price": entry, "target_price": target, "stop_price": stop}
        )
        picks.append(
            AnalysisPick(
                ref_date=ref_date,
                ticker=ticker,
                name=str(item.get("name") or ticker).strip(),
                market=item.get("market"),
                source=source,
                buy_price=entry,
                target_price=target,
                stop_price=stop,
                rationale=_build_rationale(item),
                regime=regime,
                strategy_context=context,
            )
        )

    if dry_run:
        return prepared

    run = AnalysisRun(
        ref_date=ref_date,
        status=status,
        source=source,
        regime=regime,
        strategy_context=context,
        picks_count=len(picks),
        candidates_count=candidates_count,
        note=note,
        error_message=error_message,
    )
    session = session_factory()
    try:
        if picks:
            session.add_all(picks)
        session.add(run)
        session.commit()
        for pick in picks:
            session.refresh(pick)
        for prep, pick in zip(prepared, picks):
            prep["id"] = pick.id
    finally:
        session.close()

    # 편입 결과를 텔레그램으로 푸시(무장/무장해제 버튼 포함). best-effort —
    # 전송 실패가 적재 성공을 무효화하지 않는다. completed + 1종목 이상일 때만.
    if notify_telegram and status == "completed" and prepared:
        try:
            tg = notifier
            if tg is None:
                from maps.ops.notifications import get_telegram_notifier
                tg = get_telegram_notifier()
            tg.send_analysis_picks(
                prepared, regime=regime, context=context, ref_date=ref_date.isoformat()
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] 텔레그램 알림 실패(무시): {exc}", file=sys.stderr)

    return prepared


def main(argv: list[str]) -> int:
    """엔트리포인트."""
    args = _parse_args(argv)
    ref_date = (
        datetime.date.fromisoformat(args.ref_date) if args.ref_date else datetime.date.today()
    )
    plan = _load_payload(args.file, allow_empty=args.allow_empty)
    result = load_picks(
        plan,
        regime=args.regime,
        context=args.context,
        source=args.source,
        ref_date=ref_date,
        dry_run=args.dry_run,
        status=args.status,
        note=args.note,
        error_message=args.error,
        candidates_count=args.candidates_count,
        notify_telegram=not args.no_telegram,
    )
    verb = "적재 대상" if args.dry_run else "적재 완료"
    print(f"{verb}: {len(result)}건 (ref_date={ref_date.isoformat()}, source={args.source})")
    if not args.dry_run:
        print(f"실행기록: status={args.status}, picks={len(result)}, ref_date={ref_date.isoformat()}")
    for prep in result:
        marker = f"#{prep['id']} " if "id" in prep else ""
        print(f"  {marker}{prep['ticker']} {prep['name']} "
              f"매수 {prep['buy_price']} / 목표 {prep['target_price']} / 손절 {prep['stop_price']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
