"""전략 설명 카탈로그 — 화면에 보여줄 사람이 읽는 설명.

전략관리 화면은 `strategy_id` 만 보여줘서 `donchian_v2` 가 무엇을 하는 전략인지
화면 안에서 알 방법이 없었다. 이 모듈이 그 빈칸을 채운다.

**여기에는 산문만 둔다.** 손절률·ATR 배수·기본 파라미터·선호 장세·허용 MDD 는
이미 코드가 단일 출처이므로 :func:`describe_strategy` 가 그때그때 읽어서 조합한다.
숫자를 여기에 복사하면 전략을 손볼 때마다 설명이 조용히 어긋난다.

출처:
  * 손절률·ATR 배수 — :mod:`maps.strategy.live_rules`
  * 기본 파라미터 — 각 전략 클래스의 ``default_params``
  * 선호 장세 — 각 전략 클래스의 ``preferred_regimes``
  * 전략군·허용 MDD — :mod:`maps.common.constants`
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maps.common.constants import ALLOWED_MDD, STRATEGY_GROUP_MAP
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.base import BaseStrategy
from maps.strategy.contrarian_quality_v1 import ContrarianQualityAccumulationV1Strategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.strategy.live_rules import atr_multiplier, stop_loss_pct
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy


@dataclass(frozen=True)
class StrategyProse:
    """전략 1개의 사람이 읽는 설명 (숫자 없음)."""

    display_name: str
    summary: str
    idea: str
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    guide_file: str


@dataclass(frozen=True)
class StrategyDescription:
    """산문 + 코드에서 읽어온 숫자를 합친 화면용 설명."""

    strategy_id: str
    display_name: str
    summary: str
    idea: str
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    guide_file: str
    strategy_group: str | None
    preferred_regimes: tuple[str, ...]
    stop_loss_pct: float | None
    atr_multiplier: float | None
    mdd_limit: float | None
    default_params: dict = field(default_factory=dict)


#: strategy_id → 전략 클래스. 숫자를 클래스에서 직접 읽기 위한 매핑이다.
STRATEGY_CLASSES: dict[str, type[BaseStrategy]] = {
    "pullback_v3": PullbackV3Strategy,
    "pullback_v2": PullbackV2Strategy,
    "ath_breakout_v1": ATHBreakoutV1Strategy,
    "ath_breakout_v2": ATHBreakoutV2Strategy,
    "donchian_v1": DonchianV1Strategy,
    "donchian_v2": DonchianV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "contrarian_quality_accumulation_v1": ContrarianQualityAccumulationV1Strategy,
}


STRATEGY_PROSE: dict[str, StrategyProse] = {
    "pullback_v3": StrategyProse(
        display_name="눌림목 매수 V3",
        summary="오르는 종목이 잠깐 쉴 때 사서, 반등이 시작되면 판다",
        idea=(
            "상승하는 주가는 직선이 아니라 계단처럼 움직인다. 오를 때 따라 사면 비싸게 "
            "사게 되므로, 잠깐 숨 고르는 구간을 노려 조금 싸게 담는다. 핵심은 "
            "'떨어지는 종목'이 아니라 '오르는 종목이 잠깐 내려온 것'만 사는 것이다."
        ),
        entry_rules=(
            "MA5 가 MA20 위에 있다 (상승 흐름 확인)",
            "MA20 이 20거래일 전보다 높다 (하락·횡보 국면 차단)",
            "RSI(2) 가 기준값 아래로 떨어졌다 (짧고 날카로운 눌림)",
            "당일 저가가 전일 저가보다 낮다 (추가 눌림 확인)",
        ),
        exit_rules=("종가가 MA5 를 아래에서 위로 돌파하는 순간",),
        guide_file="01_pullback_v3.txt",
    ),
    "pullback_v2": StrategyProse(
        display_name="눌림목 매수 V2",
        summary="스토캐스틱으로 과매도 구간을 잡아 담는다",
        idea=(
            "V3 와 아이디어는 같지만 재는 자가 다르다. RSI 가 '얼마나 세게 밀렸나'를 "
            "본다면 스토캐스틱은 '최근 가격 범위에서 얼마나 낮은 자리인가'를 본다. "
            "국면 필터가 없어 V3 보다 공격적이고 그만큼 횡보장에 약하다."
        ),
        entry_rules=(
            "MA5 가 MA30 위에 있다 (상승 흐름 확인)",
            "스토캐스틱 %K 가 기준값 아래로 내려왔다 (과매도)",
            "당일 저가가 전일 저가보다 낮다 (추가 눌림 확인)",
        ),
        exit_rules=("종가가 MA5 이상으로 회복 (최소 1일 보유)",),
        guide_file="02_pullback_v2.txt",
    ),
    "ath_breakout_v1": StrategyProse(
        display_name="신고가 돌파 V1",
        summary="일정 기간 최고가를 뚫으면 사서, 이동평균선을 깨면 판다",
        idea=(
            "신고가 종목에는 물려 있는 사람이 없다. 위쪽에 '본전만 오면 팔겠다'는 "
            "대기 매물이 없다는 뜻이다. 승률은 낮지만 한 번 크게 나는 수익이 여러 번의 "
            "작은 손실을 덮는 구조라, 목표가를 두지 않는다."
        ),
        entry_rules=("종가가 전일까지의 N일 최고 종가를 상향 돌파",),
        exit_rules=("종가가 청산용 이동평균선 아래로 하락",),
        guide_file="03_ath_breakout_v1.txt",
    ),
    "ath_breakout_v2": StrategyProse(
        display_name="신고가 돌파 V2",
        summary="거래량이 함께 터진 돌파만 사고, 트레일링 스탑으로 수익을 지킨다",
        idea=(
            "가격은 소수의 물량으로 잠깐 밀어 올릴 수 있지만 거래량은 속이기 어렵다. "
            "그래서 V1 의 약점인 가짜 돌파를 거래량 급증과 국면 필터로 걸러낸다. "
            "여기에 최고점 대비 트레일링 스탑을 더해 낸 수익을 되돌려주지 않는다."
        ),
        entry_rules=(
            "종가가 전일까지의 N일 최고 종가를 상향 돌파",
            "당일 거래량이 평균 거래량의 배수 기준을 초과 (돌파 신뢰도)",
            "청산용 이동평균선이 20거래일 전보다 높다 (상승 국면 필터)",
        ),
        exit_rules=(
            "종가가 청산용 이동평균선 아래로 하락",
            "종가가 보유 중 최고점 대비 트레일링 스탑 폭만큼 하락",
        ),
        guide_file="04_ath_breakout_v2.txt",
    ),
    "donchian_v1": StrategyProse(
        display_name="돈치안 채널 V1",
        summary="채널 상단을 뚫으면 사고, 하단을 깨면 판다",
        idea=(
            "1980년대 터틀 트레이더 실험에서 쓰인 규칙의 원형이다. 설정값이 둘뿐이라 "
            "과거 데이터에 맞춰 외울 답이 없고, 그래서 40년이 지나도 작동한다. "
            "매수는 긴 기간, 매도는 짧은 기간을 봐서 '신중하게 사고 빠르게 판다'."
        ),
        entry_rules=("종가가 전일까지의 상단 채널(N일 최고 종가)을 상향 돌파",),
        exit_rules=("종가가 전일까지의 하단 채널(M일 최저 종가)을 하향 이탈",),
        guide_file="05_donchian_v1.txt",
    ),
    "donchian_v2": StrategyProse(
        display_name="돈치안 채널 V2",
        summary="돌파에 모멘텀·국면 확인을 더해 횡보장 톱니 손실을 줄인다",
        idea=(
            "횡보장에서는 채널을 뚫었는데 실제로는 제자리걸음인 경우가 생긴다. "
            "그래서 '돌파했는가' 하나만 믿지 않고 '최근 실제로 올랐는가(ROC)'와 "
            "'큰 흐름도 위인가(MA40)'를 함께 묻는다. 청산 채널은 진입 채널의 "
            "절반으로 자동 계산해 정해야 할 설정값을 하나 줄였다."
        ),
        entry_rules=(
            "종가가 전일까지의 N일 최고 종가를 상향 돌파",
            "ROC 가 0보다 크다 (최근 상승 확인)",
            "MA40 이 20거래일 전보다 높다 (상승 국면 필터)",
        ),
        exit_rules=("종가가 청산 채널(진입 채널의 50% 기간) 최저 종가를 하향 이탈",),
        guide_file="06_donchian_v2.txt",
    ),
    "multi_asset_trend_v1": StrategyProse(
        display_name="이중 이동평균 추세추종",
        summary="골든크로스 상태에서 사되, 장기선이 우상향일 때만 산다",
        idea=(
            "골든크로스만 보고 사면 횡보장에서 교차가 반복되며 계좌가 깎인다. "
            "그래서 교차하는 '순간'이 아니라 '상태'를 보고, 장기 이동평균선 자체가 "
            "우상향인지를 반드시 함께 확인한다. 바닥과 꼭대기를 노리지 않고 허리를 먹는다."
        ),
        entry_rules=(
            "단기 이동평균선이 장기 이동평균선 위에 있다 (골든크로스 상태)",
            "종가가 단기 이동평균선 위에 있다",
            "장기 이동평균선이 20거래일 전보다 높다 (상승 국면 필터)",
        ),
        exit_rules=(
            "단기 이동평균선이 장기 이동평균선 아래로 하락 (데드크로스)",
            "종가가 단기 이동평균선 아래로 이탈",
        ),
        guide_file="07_multi_asset_trend_v1.txt",
    ),
    "contrarian_quality_accumulation_v1": StrategyProse(
        display_name="역발상 분할매수",
        summary="가치 대비 싸진 우량주를 공포 구간에서 3단계로 나눠 담는다",
        idea=(
            "코스톨라니의 '주인과 개' 비유처럼 주가는 가치보다 크게 흔들리다 결국 "
            "가치를 따라간다. 다만 '많이 빠졌다'는 매수 이유가 아니므로 안전마진 "
            "점수를 최우선 관문으로 둔다. 바닥을 맞히는 건 포기하고 25% / 35% / 40% "
            "로 나눠 담으며, 손절도 가격이 아니라 '산 이유가 깨졌는가'로 판단한다."
        ),
        entry_rules=(
            "안전마진 점수가 기준선 이상 (가치 대비 저평가 확인)",
            "52주 최고가 대비 충분히 하락 (공포 구간)",
            "최근 거래량이 완전히 소멸하지 않음 (유동성 확보)",
            "추세 점수가 낮다 (아직 바닥 또는 하락 구간)",
            "RSI(14) 가 과매도 수준",
            "상장 180일 이상 경과",
        ),
        exit_rules=(
            "논리 무효화 — 실적 급락·부채 급등 등 매수 근거가 깨지면 가격과 무관하게 청산",
            "기술적 긴급 손절 — 고점 대비 큰 폭 하락 또는 ATR 기준 이탈",
        ),
        guide_file="08_contrarian_quality_v1.txt",
    ),
}


def describe_strategy(strategy_id: str) -> StrategyDescription | None:
    """산문과 코드상의 숫자를 합쳐 화면용 설명을 만든다.

    :param strategy_id: 전략 ID.
    :return: 설명. 카탈로그에 없는 전략이면 ``None``.
    """
    prose = STRATEGY_PROSE.get(strategy_id)
    if prose is None:
        return None

    strategy_cls = STRATEGY_CLASSES.get(strategy_id)
    group = STRATEGY_GROUP_MAP.get(strategy_id)
    return StrategyDescription(
        strategy_id=strategy_id,
        display_name=prose.display_name,
        summary=prose.summary,
        idea=prose.idea,
        entry_rules=prose.entry_rules,
        exit_rules=prose.exit_rules,
        guide_file=prose.guide_file,
        strategy_group=group,
        preferred_regimes=(
            tuple(sorted(strategy_cls.preferred_regimes)) if strategy_cls else ()
        ),
        stop_loss_pct=stop_loss_pct(strategy_id),
        atr_multiplier=atr_multiplier(strategy_id),
        mdd_limit=ALLOWED_MDD.get(group, {}).get("mc_p95_limit") if group else None,
        default_params=dict(strategy_cls().default_params) if strategy_cls else {},
    )


def display_name(strategy_id: str) -> str:
    """전략의 한글명. 카탈로그에 없으면 ID 를 그대로 돌려준다.

    :param strategy_id: 전략 ID.
    :return: 화면에 표시할 이름.
    """
    prose = STRATEGY_PROSE.get(strategy_id)
    return prose.display_name if prose else strategy_id
