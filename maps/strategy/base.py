"""전략 추상 베이스 클래스.

설계서 v2.6.3 §6 기준.
"""

from __future__ import annotations

import abc
import datetime

import pandas as pd


class BaseStrategy(abc.ABC):
    """모든 전략의 추상 베이스.

    클래스 변수:
        strategy_id:    고유 전략 ID (e.g. 'pullback_v3')
        strategy_group: STRATEGY_GROUP_MAP 참조 (e.g. 'pullback_short')
    """

    strategy_id: str
    strategy_group: str
    preferred_regimes: frozenset[str] = frozenset({"strong", "mixed", "weak"})

    @abc.abstractmethod
    def generate_signals(self, data: pd.DataFrame, params: dict) -> pd.DataFrame:
        """ref_date 까지의 데이터로 매매 신호를 생성한다.

        Args:
            data: date 인덱스 OHLCV DataFrame.
                  columns 최소 요건: open, high, low, close, volume
            params: 전략 파라미터 딕셔너리.

        Returns:
            원본 data에 아래 컬럼이 추가된 DataFrame:
                entry_signal (bool): 진입 신호
                exit_signal  (bool): 청산 신호
                stop_price   (float): 손절가
        """

    @abc.abstractmethod
    def param_grid(self) -> list[dict]:
        """Plateau 그리드 탐색 파라미터 조합 목록을 반환한다."""

    @property
    @abc.abstractmethod
    def default_params(self) -> dict:
        """기본 파라미터."""
        pass

    def required_bars(self, params: dict) -> int:
        """Return the minimum OHLCV bars needed to evaluate signals.

        Strategies with warm-up windows should override this so callers can
        filter history using the same lookback assumptions as generate_signals.
        """
        return 1
