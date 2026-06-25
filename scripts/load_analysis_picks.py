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
from typing import Any

from sqlalchemy.orm import Session

from maps.common.db import SessionLocal
from maps.common.models import AnalysisPick


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="/analyze 결과 → 분석 워치리스트 적재")
    parser.add_argument("--file", default=None, help="trade-planner JSON 파일 경로. 미지정 시 stdin.")
    parser.add_argument("--regime", default=None, help="시장국면(strong|mixed|weak). 1단계 결과.")
    parser.add_argument("--context", default=None, help="선정 전략/섹터 등 맥락 메모(2·3단계).")
    parser.add_argument("--source", default="analyze", help="픽 출처 라벨. 기본=analyze.")
    parser.add_argument("--ref-date", default=None, help="기준일(YYYY-MM-DD). 기본=오늘.")
    parser.add_argument("--dry-run", action="store_true", help="DB 기록 없이 적재 대상만 출력.")
    return parser.parse_args(argv)


def _load_payload(file_path: str | None) -> list[dict[str, Any]]:
    """stdin 또는 파일에서 trade-planner JSON을 읽어 종목 배열을 반환한다."""
    if file_path:
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read().strip()
    else:
        # stdin은 로케일(예: cp949)이 아니라 UTF-8로 명시 디코딩한다.
        raw = sys.stdin.buffer.read().decode("utf-8").strip()
    if not raw:
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
    session_factory: Callable[[], Session] = SessionLocal,
) -> list[dict[str, Any]]:
    """종목 배열을 analysis_pick 으로 적재한다. 적재된(또는 예정) 항목 요약을 반환한다.

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

    if dry_run or not picks:
        return prepared

    session = session_factory()
    try:
        session.add_all(picks)
        session.commit()
        for pick in picks:
            session.refresh(pick)
        for prep, pick in zip(prepared, picks):
            prep["id"] = pick.id
    finally:
        session.close()
    return prepared


def main(argv: list[str]) -> int:
    """엔트리포인트."""
    args = _parse_args(argv)
    ref_date = (
        datetime.date.fromisoformat(args.ref_date) if args.ref_date else datetime.date.today()
    )
    plan = _load_payload(args.file)
    result = load_picks(
        plan,
        regime=args.regime,
        context=args.context,
        source=args.source,
        ref_date=ref_date,
        dry_run=args.dry_run,
    )
    verb = "적재 대상" if args.dry_run else "적재 완료"
    print(f"{verb}: {len(result)}건 (ref_date={ref_date.isoformat()}, source={args.source})")
    for prep in result:
        marker = f"#{prep['id']} " if "id" in prep else ""
        print(f"  {marker}{prep['ticker']} {prep['name']} "
              f"매수 {prep['buy_price']} / 목표 {prep['target_price']} / 손절 {prep['stop_price']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
