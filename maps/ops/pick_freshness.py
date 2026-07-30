"""분석 워치리스트 픽(`analysis_pick`)의 신선도 판정.

`/analyze` 가 만든 픽은 만료 개념 없이 영구히 남는다. 2026-07-30 에 6/30 픽이
한 달째 "관찰"로 떠 있었고, 운영자가 그 한 달 된 매수가를 유효한 계획으로 읽고
무장해 17초 만에 실주문이 나갔다(그사이 주가는 -39% 하락해 매수가를 관통한 상태).

여기 있는 함수들은 **상태 전이가 아니라 파생 계산**이다. 만료 잡이 멈추거나
배포·DB 복원 직후여도 가드가 살아 있어야 하기 때문이다 — 애초 사고 원인이
"만료시키는 주체가 없다"였는데, 가드를 잡에 의존시키면 같은 실패를 반복한다.

.. warning::
   신선도는 **`ref_date` 로만** 계산한다. `created_at`/`updated_at`/`last_action_at`
   은 UTC naive 로 저장되는데 `ref_date` 는 KST `Date` 다(`common/models.py` 참고).
   `created_at` 으로 나이를 재면 매일 09:00 KST 이전에 하루씩 어긋난다.
   같은 함정의 기록은 `execution/order_manager.kst_day_bounds_utc` docstring 에 있다.
"""

from __future__ import annotations

import datetime as dt

from maps.common.models import AnalysisPick
from maps.common.settings import MapsSettings
from maps.market.trading_rules import previous_trading_day, trading_days_ago

# 만료 사유 코드. 응답 스키마의 stale_reason 으로 그대로 나가고, 프런트는 이 문자열을
# 한글 라벨로만 바꾼다(order_preview 의 stale_reason 과 같은 규약).
STALE_REASON_EXPIRED = "expired"

# 나이 계산 상한. 정확한 숫자보다 "아주 오래됨"이라는 사실이 중요하고, 상한이 없으면
# 오래된 픽 하나가 요청마다 무제한 캘린더 순회를 유발한다.
_MAX_AGE_TRADING_DAYS = 60


def pick_cutoff_date(
    settings: MapsSettings,
    *,
    today: dt.date | None = None,
) -> dt.date:
    """픽이 신선하다고 인정되는 가장 오래된 `ref_date` 를 반환한다.

    :param settings: `maps_analysis_pick_max_age_trading_days` 를 읽는다.
    :param today: 기준일. 테스트 결정성을 위한 주입 지점이며 운영 호출부는 넘기지 않는다.
    :return: 이 날짜 **이상**의 `ref_date` 를 가진 픽이 신선하다(경계 포함).
    """
    base = today or dt.date.today()
    return trading_days_ago(
        base,
        settings.maps_analysis_pick_max_age_trading_days,
        extra_closed_dates=settings.krx_closed_dates,
    )


def is_pick_stale(pick: AnalysisPick, cutoff: dt.date) -> bool:
    """픽의 기준일이 만료됐는지 판정한다. `ref_date == cutoff` 는 아직 신선하다.

    :param pick: 판정 대상 픽.
    :param cutoff: :func:`pick_cutoff_date` 결과.
    :return: 만료면 True. `ref_date` 가 없으면 판정 불가로 보고 False.
    """
    if pick.ref_date is None:
        return False
    return pick.ref_date < cutoff


def pick_stale_reason(pick: AnalysisPick, cutoff: dt.date) -> str | None:
    """만료 사유 코드를 반환한다. 신선하면 None."""
    return STALE_REASON_EXPIRED if is_pick_stale(pick, cutoff) else None


def pick_age_trading_days(
    pick: AnalysisPick,
    *,
    settings: MapsSettings,
    today: dt.date | None = None,
) -> int | None:
    """픽의 기준일이 몇 거래일 지났는지 센다.

    화면이 "만료 N거래일"을 날짜 연산 없이 찍을 수 있게 하는 값이다. lookback 상한
    (`trading_days_ago` 의 60)을 넘으면 상한값을 반환한다 — 정확한 숫자보다
    "아주 오래됨"이라는 사실이 중요하고, 상한 없이 세면 비용이 무제한이 된다.

    :return: 경과 거래일 수. `ref_date` 가 없으면 None.
    """
    if pick.ref_date is None:
        return None
    base = today or dt.date.today()
    if pick.ref_date >= base:
        return 0
    # 뒤로 한 번만 걸어간다. age 마다 trading_days_ago(base, age) 를 부르면 O(n²) 이 되고,
    # is_krx_closed_date 는 호출마다 holidays.KR(...) 을 만들기 때문에 그 비용이 그대로 곱해진다.
    closed = settings.krx_closed_dates
    cursor = base
    for age in range(1, _MAX_AGE_TRADING_DAYS + 1):
        cursor = previous_trading_day(cursor, extra_closed_dates=closed)
        if cursor <= pick.ref_date:
            return age
    return _MAX_AGE_TRADING_DAYS
