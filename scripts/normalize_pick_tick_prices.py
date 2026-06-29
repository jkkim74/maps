"""기존 analysis_pick 행의 가격을 KRX 호가 단위로 일괄 정규화하는 일회성 스크립트.

trade-planner(LLM)가 호가 단위를 어긋나게 낸 과거 가격(예: 목표가 54912)을 결정론적
호가 그리드로 스냅한다. 신규 적재는 `scripts/load_analysis_picks.py` 가 이미 정규화하므로,
이 스크립트는 그 수정 이전에 저장된 행을 보정하는 용도다.

스냅 규칙(로더와 동일): 매수가=올림, 목표가·손절가=최근접 호가.

안전장치:
- ARMED/BOUGHT(무장·보유) 픽은 브래킷 정합성(라이브 주문) 보호를 위해 건너뛰고 보고만 한다.
  (API도 무장·보유 중 가격 수정을 금지한다 — 먼저 disarm 후 처리.)
- 기본은 dry-run(미반영). 실제 반영은 --apply 필요.

사용법 (프로젝트 루트 / 서버 venv 에서):
    python scripts/normalize_pick_tick_prices.py            # dry-run: 변경 대상만 출력
    python scripts/normalize_pick_tick_prices.py --apply    # 실제 DB 반영
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from maps.common.db import SessionLocal
from maps.common.models import AnalysisPick
from scripts.load_analysis_picks import _snap_price

# 무장·보유 픽은 라이브 브래킷과 묶여 있어 가격을 바꾸지 않는다.
_PROTECTED_STATES = {"ARMED", "BOUGHT"}


def normalize(*, apply: bool, include_protected: bool = False) -> int:
    """모든 analysis_pick 가격을 호가 단위로 스냅한다. 변경(또는 예정) 건수를 반환한다.

    include_protected=True면 ARMED/BOUGHT 픽도 함께 정규화한다. 보유 픽의 목표·손절가는
    코드가 매 사이클 평가하는 값일 뿐 대기 주문이 아니므로(고아 주문 위험 없음) 스냅은
    안전하지만, 라이브 포지션을 건드리므로 명시적으로만 허용한다.
    """
    session = SessionLocal()
    changed = 0
    skipped_protected = 0
    try:
        rows = session.query(AnalysisPick).all()
        for p in rows:
            new_buy = _snap_price(p.buy_price, p.market, kind="buy")
            new_target = _snap_price(p.target_price, p.market, kind="target")
            new_stop = _snap_price(p.stop_price, p.market, kind="stop")
            if (new_buy, new_target, new_stop) == (p.buy_price, p.target_price, p.stop_price):
                continue  # 이미 호가 그리드 위 — 변경 없음
            if p.state in _PROTECTED_STATES and not include_protected:
                skipped_protected += 1
                print(
                    f"  [skip:{p.state}] #{p.id} {p.ticker} "
                    f"매수 {p.buy_price}→{new_buy} / 목표 {p.target_price}→{new_target} / "
                    f"손절 {p.stop_price}→{new_stop}  (무장·보유: --include-protected 로만 처리)",
                    file=sys.stderr,
                )
                continue
            print(
                f"  #{p.id} {p.ticker} {p.name} "
                f"매수 {p.buy_price}→{new_buy} / 목표 {p.target_price}→{new_target} / "
                f"손절 {p.stop_price}→{new_stop}"
            )
            if apply:
                p.buy_price = new_buy
                p.target_price = new_target
                p.stop_price = new_stop
            changed += 1
        if apply:
            session.commit()
    finally:
        session.close()

    verb = "반영 완료" if apply else "변경 예정(dry-run)"
    print(f"\n{verb}: {changed}건" + (f", 보호 상태 건너뜀: {skipped_protected}건" if skipped_protected else ""))
    if not apply and changed:
        print("실제 반영하려면 --apply 를 붙여 다시 실행하세요.")
    return changed


def main(argv: list[str]) -> int:
    """엔트리포인트."""
    parser = argparse.ArgumentParser(description="기존 analysis_pick 가격 호가단위 정규화")
    parser.add_argument("--apply", action="store_true", help="실제 DB 반영(기본=dry-run).")
    parser.add_argument(
        "--include-protected", action="store_true",
        help="ARMED/BOUGHT(무장·보유) 픽도 정규화(기본=건너뜀).",
    )
    args = parser.parse_args(argv)
    normalize(apply=args.apply, include_protected=args.include_protected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
