"""Operational scheduler for data, candidates, validation, orders, and EOD.

The scheduler deliberately keeps live ordering behind
MAPS_LIVE_TRADING_ENABLED.  In paper/mock mode the order job only syncs
broker state and records an audit log.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from maps.common.constants import STRATEGY_GROUP_MAP
from maps.common.db import SessionLocal
from maps.common.models import (
    CandidateSnapshot,
    CollectionLog,
    HistoricalOHLCV,
    MonteCarloSequenceResults,
    ParameterPlateauResults,
    PortfolioSnapshot,
    PromotionHistory,
    OrderLog,
    WalkForwardFoldResults,
    WalkForwardResults,
)
from maps.common.settings import MapsSettings, get_settings
from maps.backtest.engine import BacktestEngine, BacktestResult
from maps.common.exceptions import BacktestError, DuplicateOrderError, ValidationError
from maps.data.collector import DataCollector
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.data.krx_adapter import CollectionResult, KRXAdapter, MockKRXAdapter, SecurityMeta
from maps.data.security_repo import HaltPeriod, ManagedPeriod, Security
from maps.data_quality.universe_filter import DataQualityFilter, UniverseResult
from maps.execution.broker_adapter import Order, OrderSide, OrderType, Position, get_broker
from maps.execution.order_manager import OrderManager
from maps.market.trading_rules import is_krx_closed_date, round_up_krx_price
from maps.ops.notifications import SlackNotifier
from maps.ops.order_state import claimed_candidate_tickers
from maps.promotion.gate import PromotionGate, PromotionStage
from maps.risk.manager import RiskManager
from maps.strategy.ath_breakout_v1 import ATHBreakoutV1Strategy
from maps.strategy.ath_breakout_v2 import ATHBreakoutV2Strategy
from maps.strategy.base import BaseStrategy
from maps.strategy.donchian_v1 import DonchianV1Strategy
from maps.strategy.donchian_v2 import DonchianV2Strategy
from maps.strategy.multi_asset_trend_v1 import MultiAssetTrendV1Strategy
from maps.strategy.pullback_v2 import PullbackV2Strategy
from maps.strategy.pullback_v3 import PullbackV3Strategy
from maps.strategy.live_rules import stop_loss_price
from maps.stock_report.runner import run_all_reports_if_idle
from maps.validation.monte_carlo import MonteCarloValidator
from maps.validation.plateau import ParameterPlateauTester
from maps.validation.walk_forward import WalkForwardAnalyzer

logger = logging.getLogger(__name__)

# ── KRX 거래일 캐시 ────────────────────────────────────────────────────────────
_krx_market_day_cache: dict[dt.date, bool] = {}


def _is_krx_market_day(date: dt.date | None = None) -> bool:
    """주어진 날짜가 KRX 거래일인지 확인한다.

    1. 토/일 → 즉시 False (빠른 경로)
    2. 평일 → pykrx로 한국 공휴일 여부 확인
    3. pykrx 조회 실패 시 True 반환(폴백)으로 스케쥴러가 멈추지 않도록 한다.
    결과는 날짜 단위로 캐싱해 interval 잡의 반복 호출 비용을 낮춘다.
    """
    target = date or dt.date.today()
    if target in _krx_market_day_cache:
        return _krx_market_day_cache[target]

    # 주말 체크 (토=5, 일=6)
    if is_krx_closed_date(target, extra_closed_dates=get_settings().krx_closed_dates):
        _krx_market_day_cache[target] = False
        return False

    # pykrx로 한국 공휴일 체크
    # Live OHLCV does not exist before the session opens. Weekdays that are
    # not known closure dates must remain runnable for pre-open order jobs.
    if target >= dt.date.today():
        return True

    date_str = target.strftime("%Y%m%d")
    try:
        from pykrx import stock as _pykrx_stock  # noqa: PLC0415 — lazy import
        df = _pykrx_stock.get_index_ohlcv(date_str, date_str, "1001")  # KOSPI
        result = len(df) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("KRX 거래일 확인 실패, 거래일로 간주합니다: %s", exc)
        result = True

    _krx_market_day_cache[target] = result
    return result


_RUNNABLE_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "pullback_v3": PullbackV3Strategy,
    "pullback_v2": PullbackV2Strategy,
    "ath_breakout_v1": ATHBreakoutV1Strategy,
    "ath_breakout_v2": ATHBreakoutV2Strategy,
    "multi_asset_trend_v1": MultiAssetTrendV1Strategy,
    "donchian_v1": DonchianV1Strategy,
    "donchian_v2": DonchianV2Strategy,
}

_VALIDATION_SAMPLE_TICKERS = 5

# WFA 에 사용할 선호 ticker 우선순위.
# 알파벳순 첫 번째(소형주)가 아닌 유동성 높은 대형주로 WFA 를 실행하여
# 전략 성과의 대표성을 높인다.
_WFA_PREFERRED_TICKERS: list[str] = [
    "005930",  # 삼성전자 (KOSPI 시총 1위)
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "068270",  # 셀트리온
    "207940",  # 삼성바이오로직스
    "006400",  # 삼성SDI
]


@dataclass
class JobRun:
    name: str
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StrategySignal:
    """Latest live decision produced by a strategy."""

    entry_signal: bool
    exit_signal: bool
    close: float


class OperationalPipeline:
    """Runs the individual MAPS operational steps."""

    def __init__(
        self,
        *,
        settings: MapsSettings | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        notifier: SlackNotifier | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory
        self._notifier = notifier or SlackNotifier(self._settings)
        self._last_collection: CollectionResult | None = None
        self._last_universe: UniverseResult | None = None

    @property
    def last_collection(self) -> CollectionResult | None:
        return self._last_collection

    @property
    def last_universe(self) -> UniverseResult | None:
        return self._last_universe

    def collect_data(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            collector = DataCollector(self._make_krx_adapter(), db)
            result = collector.collect_daily(ref_date)
            self._last_collection = result
            return {
                "ref_date": ref_date.isoformat(),
                "ohlcv_count": len(result.ohlcv),
                "meta_count": len(result.meta),
                "halt_count": len(result.halts),
                "managed_count": len(result.managed),
            }

        return self._job("data_collection", _run)

    def generate_candidates(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            collection = self._last_collection
            if collection is None or collection.ref_date != ref_date:
                collector = DataCollector(self._make_krx_adapter(), db)
                collection = collector.collect_daily(ref_date)
                self._last_collection = collection

            candidates = self._to_securities(collection.meta, collection, ref_date)
            result = DataQualityFilter(db=db, mode="live").generate(ref_date, candidates)
            # 등록된 모든 전략에 동일한 유니버스 스냅샷을 저장한다.
            # DataQualityFilter 유니버스는 유동성·데이터 품질 기반 공통 후보군이므로
            # 전략별로 별도 필터링 없이 공유할 수 있다.
            # 각 전략의 진입 신호는 order_cycle 에서 generate_signals() 로 별도 계산된다.
            saved_count = 0
            for strategy_id in _RUNNABLE_STRATEGIES:
                saved_count += self._save_candidate_snapshot(
                    db, ref_date, strategy_id, result.universe
                )
            self._last_universe = result
            return {
                "ref_date": ref_date.isoformat(),
                "total_candidates": len(candidates),
                "kept_count": len(result.universe),
                "rejected_count": len(result.rejected),
                "rejection_ratio": round(result.rejection_ratio, 4),
                "saved_count": saved_count,
                "strategies_updated": list(_RUNNABLE_STRATEGIES.keys()),
            }

        return self._job("candidate_generation", _run)

    def backfill_ohlcv(self, start: dt.date, end: dt.date) -> JobRun:
        def _run(db: Session) -> dict:
            collector = DataCollector(self._make_krx_adapter(), db)
            return collector.collect_ohlcv_history(start, end)

        return self._job("ohlcv_backfill", _run)

    def run_validation(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            generated = self._generate_validation_metrics(db, ref_date)
            promotion = self._evaluate_promotions(db, ref_date)
            if promotion["evaluated"] > 0:
                self._write_log(
                    db,
                    ref_date=ref_date,
                    source="scheduler.validation",
                    status="success",
                    items=int(promotion["evaluated"]),
                    note=(
                        f"Promotion evaluation complete: passed={promotion['passed']}, "
                        f"failed={promotion['failed']}."
                    ),
                )
                return {
                    "ref_date": ref_date.isoformat(),
                    "status": "success",
                    "reason": "promotion_evaluation_completed",
                    "generated": generated,
                    **promotion,
                }

            # Full WFA/MC requires persisted OHLCV history. If no validation or
            # candidate data is available yet, record readiness without creating
            # synthetic promotion decisions.
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.validation",
                status="skipped",
                items=0,
                note="No candidate or validation metrics available for promotion evaluation.",
            )
            return {
                "ref_date": ref_date.isoformat(),
                "status": "skipped",
                "reason": "promotion_inputs_missing",
                "generated": generated,
                **promotion,
            }

        return self._job("validation", _run)

    def run_order_cycle(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            manager = OrderManager(broker=broker, risk=RiskManager(broker=broker, db=db), db=db)
            sync = manager.sync_broker_state()
            self._save_portfolio_snapshot(db, ref_date, sync)
            live_enabled = self._settings.maps_live_trading_enabled
            submitted_orders = 0
            skipped_orders = 0
            submitted_buy_orders = 0
            submitted_sell_orders = 0
            skipped_buy_orders = 0
            skipped_sell_orders = 0
            note = None

            if live_enabled:
                submitted_sell_orders, skipped_sell_orders, exit_tickers = self._submit_exit_orders(
                    db=db,
                    broker=broker,
                    manager=manager,
                    ref_date=ref_date,
                )
                submitted_buy_orders, skipped_buy_orders = self._submit_candidate_orders(
                    db=db,
                    broker=broker,
                    manager=manager,
                    ref_date=ref_date,
                    blocked_tickers=exit_tickers,
                )
                submitted_orders = submitted_sell_orders + submitted_buy_orders
                skipped_orders = skipped_sell_orders + skipped_buy_orders
                final_balance = broker.get_account_balance()
                self._save_portfolio_snapshot(db, ref_date, {
                    "cash": final_balance.cash,
                    "positions_value": final_balance.positions_value,
                    "total_assets": final_balance.total_value,
                })
            else:
                note = "Order submission disabled by MAPS_LIVE_TRADING_ENABLED=false."

            status = "success" if live_enabled else "skipped"
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.orders",
                status=status,
                items=submitted_orders,
                note=note,
            )
            return {
                "ref_date": ref_date.isoformat(),
                "live_trading_enabled": live_enabled,
                "cash": sync["cash"],
                "positions_value": sync["positions_value"],
                "open_orders": sync["open_orders"],
                "updated_orders": sync["updated_orders"],
                "submitted_orders": submitted_orders,
                "skipped_orders": skipped_orders,
                "submitted_buy_orders": submitted_buy_orders,
                "submitted_sell_orders": submitted_sell_orders,
                "skipped_buy_orders": skipped_buy_orders,
                "skipped_sell_orders": skipped_sell_orders,
            }

        return self._job("order_cycle", _run)

    def sync_broker_state(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            sync = OrderManager(broker=broker, risk=RiskManager(broker=broker, db=db), db=db).sync_broker_state()
            self._save_portfolio_snapshot(db, ref_date, sync)
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.broker_sync",
                status="success",
                items=int(sync["updated_orders"]),
                note=f"open_orders={sync['open_orders']}",
            )
            return {"ref_date": ref_date.isoformat(), **sync}

        return self._job("broker_sync", _run)

    def run_eod_cleanup(self, ref_date: dt.date | None = None) -> JobRun:
        ref_date = ref_date or dt.date.today()

        def _run(db: Session) -> dict:
            broker = get_broker(self._settings.maps_broker_mode)
            manager = OrderManager(broker=broker, risk=RiskManager(broker=broker, db=db), db=db)
            cancelled = 0
            try:
                open_orders = broker.get_open_orders()
            except NotImplementedError:
                open_orders = []
            for order in open_orders:
                if broker.cancel_order(order.order_id):
                    cancelled += 1
            if hasattr(broker, "eod_cleanup"):
                broker.eod_cleanup()  # type: ignore[attr-defined]
            expired = manager.expire_pending_orders()
            self._write_log(
                db,
                ref_date=ref_date,
                source="scheduler.eod",
                status="success",
                items=cancelled,
                note="Cancelled remaining open orders and ran broker EOD cleanup.",
            )
            return {
                "ref_date": ref_date.isoformat(),
                "open_orders_seen": len(open_orders),
                "cancelled_orders": cancelled,
                "expired_orders": expired,
            }

        return self._job("eod_cleanup", _run)

    def _job(self, name: str, fn: Callable[[Session], dict]) -> JobRun:
        started = dt.datetime.now(dt.timezone.utc)
        run = JobRun(name=name, status="running", started_at=started)
        db = self._session_factory()
        try:
            run.details = fn(db)
            run.status = "success"
            run.message = "ok"
        except Exception as exc:
            db.rollback()
            run.status = "failed"
            run.message = str(exc)
            logger.exception("Operational job failed: %s", name)
            self._notifier.send_job_failed(name, str(exc))
        finally:
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.close()
        return run

    def _make_krx_adapter(self):
        if self._settings.maps_data_provider == "mock":
            return MockKRXAdapter()
        return KRXAdapter()

    def _generate_validation_metrics(self, db: Session, ref_date: dt.date) -> dict[str, int | list[dict[str, str]]]:
        strategy_ids = self._candidate_strategy_ids(db, ref_date)
        generated = {"wfa": 0, "plateau": 0, "mc": 0, "skipped": []}
        if not strategy_ids:
            return generated

        repo = HistoricalOHLCVRepository(db)
        for strategy_id in strategy_ids:
            strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id)
            if strategy_cls is None:
                generated["skipped"].append({"strategy_id": strategy_id, "reason": "strategy_not_runnable"})
                continue

            strategy = strategy_cls()
            min_bars = self._wfa_required_bars()
            tickers = repo.list_tickers_with_history(end=ref_date, min_bars=min_bars)
            if not tickers:
                generated["skipped"].append({
                    "strategy_id": strategy_id,
                    "reason": f"insufficient_history: need {min_bars} bars",
                })
                continue

            # 백테스트 샘플: 유동성 대형주 우선 선택 (알파벳 첫 번째 소형주 회피)
            sample_tickers = self._pick_sample_tickers(tickers, _VALIDATION_SAMPLE_TICKERS)
            backtests = self._run_backtest_grid(db, repo, strategy, sample_tickers, ref_date)
            if not backtests:
                generated["skipped"].append({"strategy_id": strategy_id, "reason": "no_backtest_results"})
                continue

            if self._save_plateau_result(db, strategy, ref_date, backtests):
                generated["plateau"] += 1
            if self._save_mc_result(db, strategy, ref_date, backtests):
                generated["mc"] += 1
            wfa_ticker = self._pick_wfa_ticker(tickers)
            if self._save_wfa_result(db, repo, strategy, wfa_ticker, ref_date):
                generated["wfa"] += 1

        return generated

    @staticmethod
    def _pick_sample_tickers(tickers: list[str], n: int) -> list[str]:
        """백테스트 샘플 ticker 목록을 구성한다.

        _WFA_PREFERRED_TICKERS 를 우선 포함하고, 나머지를 원래 순서(알파벳)로 채운다.
        소형·비유동 종목이 plateau/MC 점수를 왜곡하는 것을 방지한다.
        """
        ticker_set = set(tickers)
        preferred = [t for t in _WFA_PREFERRED_TICKERS if t in ticker_set]
        others = [t for t in tickers if t not in set(_WFA_PREFERRED_TICKERS)]
        return (preferred + others)[:n]

    @staticmethod
    def _pick_wfa_ticker(tickers: list[str]) -> str:
        """WFA 에 사용할 대표 ticker 를 선택한다.

        알파벳순 첫 번째(보통 소형주) 대신 _WFA_PREFERRED_TICKERS 목록에서
        데이터가 충분한 첫 번째 ticker 를 우선 사용한다.
        목록에 없는 경우 tickers[0] 으로 폴백한다.
        """
        ticker_set = set(tickers)
        for preferred in _WFA_PREFERRED_TICKERS:
            if preferred in ticker_set:
                return preferred
        return tickers[0]

    @staticmethod
    def _wfa_required_bars() -> int:
        analyzer = WalkForwardAnalyzer()
        return (36 + (5 * 12)) * 21

    @staticmethod
    def _candidate_strategy_ids(db: Session, ref_date: dt.date) -> list[str]:
        rows = (
            db.query(CandidateSnapshot.strategy_id)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .distinct()
            .order_by(CandidateSnapshot.strategy_id.asc())
            .all()
        )
        return [row.strategy_id for row in rows]

    def _run_backtest_grid(
        self,
        db: Session,
        repo: HistoricalOHLCVRepository,
        strategy: BaseStrategy,
        tickers: list[str],
        ref_date: dt.date,
    ) -> list[dict]:
        engine = BacktestEngine()
        rows: list[dict] = []
        for params in strategy.param_grid():
            results: list[BacktestResult] = []
            min_bars = max(strategy.required_bars(params), 30)
            for ticker in tickers:
                df = repo.to_dataframe(ticker, end=ref_date)
                if len(df) < min_bars:
                    continue
                df.index.name = ticker
                try:
                    results.append(engine.run(strategy, params, df))
                except BacktestError as exc:
                    logger.debug("Validation backtest skipped [%s %s]: %s", strategy.strategy_id, ticker, exc)
            if not results:
                continue
            row = dict(params)
            row["sharpe"] = sum(r.sharpe for r in results) / len(results)
            row["mdd"] = min(r.mdd for r in results)
            row["daily_returns"] = self._average_daily_returns(results)
            rows.append(row)
        return rows

    @staticmethod
    def _average_daily_returns(results: list[BacktestResult]) -> list[float]:
        series = [pd.Series(r.daily_returns, dtype=float) for r in results if len(r.daily_returns) >= 30]
        if not series:
            return []
        frame = pd.concat(series, axis=1).fillna(0.0)
        return frame.mean(axis=1).tolist()

    @staticmethod
    def _save_plateau_result(db: Session, strategy: BaseStrategy, ref_date: dt.date, rows: list[dict]) -> bool:
        # param_keys: default_params 키 중 실제 row 에 존재하는 것만 사용한다.
        # param_grid() 에 포함되지 않은 파라미터(예: vol_period)가 default_params 에만
        # 있을 경우 KeyError 가 발생하므로 교집합으로 제한한다.
        _non_param = {"sharpe", "mdd", "daily_returns"}
        actual_param_keys = [k for k in (rows[0] if rows else {}) if k not in _non_param]
        param_keys = [k for k in strategy.default_params if k in set(actual_param_keys)]
        if not param_keys:
            logger.warning("Plateau validation skipped [%s]: no overlapping param keys", strategy.strategy_id)
            return False
        try:
            result = ParameterPlateauTester().run(rows, param_keys=param_keys)
        except ValueError as exc:
            logger.warning("Plateau validation skipped [%s]: %s", strategy.strategy_id, exc)
            return False

        grade_map = {"robust": "A", "moderate": "C", "fragile": "F"}
        db.add(ParameterPlateauResults(
            strategy_id=strategy.strategy_id,
            run_date=ref_date,
            total_combinations=len(rows),
            positive_combinations=result.passing_neighbors,
            positive_ratio=result.score / 100.0,
            grade=grade_map.get(result.grade, "F"),
            best_params_json=json.dumps(result.best_combo, ensure_ascii=False),
        ))
        db.commit()
        return True

    @staticmethod
    def _save_mc_result(db: Session, strategy: BaseStrategy, ref_date: dt.date, rows: list[dict]) -> bool:
        best = max(rows, key=lambda row: float(row.get("sharpe", 0.0)))
        daily_returns = list(best.get("daily_returns") or [])
        if len(daily_returns) < 30:
            logger.warning("Monte Carlo validation skipped [%s]: fewer than 30 returns", strategy.strategy_id)
            return False

        try:
            result = MonteCarloValidator(n_simulations=1000).validate(
                strategy.strategy_id,
                strategy.strategy_group,
                daily_returns,
            )
        except ValidationError as exc:
            logger.warning("Monte Carlo validation skipped [%s]: %s", strategy.strategy_id, exc)
            return False

        db.add(MonteCarloSequenceResults(
            strategy_id=result.strategy_id,
            strategy_group=result.strategy_group,
            run_date=ref_date,
            n_simulations=result.n_simulations,
            mdd_p95=result.mdd_p95,
            mdd_limit=result.mdd_limit,
            mc_within_limit=result.passed,
        ))
        db.commit()
        return True

    @staticmethod
    def _save_wfa_result(
        db: Session,
        repo: HistoricalOHLCVRepository,
        strategy: BaseStrategy,
        ticker: str,
        ref_date: dt.date,
    ) -> bool:
        df = repo.to_dataframe(ticker, end=ref_date)
        df.index.name = ticker
        result = WalkForwardAnalyzer().run(strategy, df, strategy.param_grid())
        summary = WalkForwardResults(
            strategy_id=strategy.strategy_id,
            run_date=ref_date,
            n_folds=len(result.folds),
            sharpe_mean=result.sharpe_mean,
            sharpe_std=result.sharpe_std,
            negative_folds=result.negative_folds,
            mean_g2p=result.mean_g2p,
            passed=result.passed,
            fail_reasons_json=json.dumps(result.fail_reasons, ensure_ascii=False) if result.fail_reasons else None,
        )
        db.add(summary)
        db.flush()
        for fold in result.folds:
            db.add(WalkForwardFoldResults(
                wfa_run_id=summary.id,
                strategy_id=strategy.strategy_id,
                fold_idx=fold.fold_idx,
                is_start=fold.is_start,
                is_end=fold.is_end,
                oos_start=fold.oos_start,
                oos_end=fold.oos_end,
                is_sharpe=fold.is_sharpe,
                oos_sharpe=fold.oos_sharpe,
                is_g2p=fold.is_g2p,
                oos_g2p=fold.oos_g2p,
                g2p_ratio=fold.g2p_ratio,
                best_params_json=None,
            ))
        db.commit()
        return True

    def _evaluate_promotions(self, db: Session, ref_date: dt.date) -> dict[str, int | list[str]]:
        latest_candidates = (
            db.query(CandidateSnapshot.strategy_id)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .distinct()
            .all()
        )
        latest_plateau = self._latest_rows_by_strategy(
            db.query(ParameterPlateauResults)
            .filter(ParameterPlateauResults.run_date <= ref_date)
            .order_by(ParameterPlateauResults.run_date.desc(), ParameterPlateauResults.id.desc())
            .all()
        )
        latest_mc = self._latest_rows_by_strategy(
            db.query(MonteCarloSequenceResults)
            .filter(MonteCarloSequenceResults.run_date <= ref_date)
            .order_by(MonteCarloSequenceResults.run_date.desc(), MonteCarloSequenceResults.id.desc())
            .all()
        )
        latest_wfa = self._latest_rows_by_strategy(
            db.query(WalkForwardResults)
            .filter(WalkForwardResults.run_date <= ref_date)
            .order_by(WalkForwardResults.run_date.desc(), WalkForwardResults.id.desc())
            .all()
        )
        latest_promotions = self._latest_promotion_rows(db)

        strategy_ids = sorted(
            {row.strategy_id for row in latest_candidates}
            | set(latest_plateau)
            | set(latest_mc)
            | set(latest_wfa)
        )
        if not strategy_ids:
            return {"evaluated": 0, "passed": 0, "failed": 0, "strategies": []}

        gate = PromotionGate(db=db)
        passed = 0
        failed = 0
        evaluated_strategies: list[str] = []
        for strategy_id in strategy_ids:
            current_stage = self._promotion_stage(latest_promotions.get(strategy_id))
            metrics = self._promotion_metrics(
                latest_plateau.get(strategy_id),
                latest_mc.get(strategy_id),
                latest_wfa.get(strategy_id),
            )
            decision = gate.evaluate(
                strategy_id,
                metrics,
                current_stage,
                strategy_group=STRATEGY_GROUP_MAP.get(strategy_id),
            )
            evaluated_strategies.append(strategy_id)
            if decision.passed:
                passed += 1
            else:
                failed += 1

        return {
            "evaluated": len(evaluated_strategies),
            "passed": passed,
            "failed": failed,
            "strategies": evaluated_strategies,
        }

    @staticmethod
    def _latest_rows_by_strategy(rows) -> dict[str, object]:
        latest: dict[str, object] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row
        return latest

    @staticmethod
    def _latest_promotion_rows(db: Session) -> dict[str, PromotionHistory]:
        """전략별 마지막 '성공' 승격 이력을 반환한다.

        passed=True 레코드만 읽는다.  실패한 평가(passed=False)는 현재 단계에
        영향을 주지 않는다 — 실패한 평가가 이전에 승격된 단계를 덮어쓰는
        '강제 강등' 버그를 방지하기 위해서다.

        예) pullback_v3 가 mock_candidate 로 승격된 후, 다음 평가 주기에서
        "점수 71.8 < 임계값 75 (live_candidate)" 로 실패하면 passed=False 레코드가
        추가되는데, 이 레코드를 읽으면 stage=RESEARCH 로 되돌아간다.
        passed=True 만 읽으면 마지막 성공 기록(mock_candidate)이 유지된다.
        """
        rows = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .all()
        )
        latest: dict[str, PromotionHistory] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row
        return latest

    @staticmethod
    def _promotion_stage(row: PromotionHistory | None) -> PromotionStage:
        if row is None or row.to_stage == PromotionStage.REJECTED.value:
            return PromotionStage.RESEARCH
        try:
            return PromotionStage(row.to_stage)
        except ValueError:
            return PromotionStage.RESEARCH

    @staticmethod
    def _promotion_metrics(
        plateau: ParameterPlateauResults | None,
        mc: MonteCarloSequenceResults | None,
        wfa: WalkForwardResults | None,
    ) -> dict[str, float]:
        metrics: dict[str, float] = {}
        if plateau is not None:
            metrics["robustness"] = max(0.0, min(float(plateau.positive_ratio), 1.0))
        if mc is not None:
            ratio = abs(float(mc.mdd_p95)) / float(mc.mdd_limit) if mc.mdd_limit else 1.0
            metrics["risk"] = max(0.0, min(1.0 - ratio, 1.0))
            metrics["mc_mdd_p95"] = float(mc.mdd_p95)
        if wfa is not None:
            # WFA 통과 여부와 무관하게 실제 측정치 기반으로 항상 설정한다.
            # 음수 값은 0 으로 클램프 — "메트릭 누락" 대신 실제 점수로 gate 가 판단한다.
            # 과거에는 passed=True 일 때만 설정했는데, 그러면 모든 미통과 전략이
            # robustness/risk 점수만으로 평가돼 임계값(60)을 절대 넘지 못했다.
            metrics["recovery"] = max(0.0, min(float(wfa.mean_g2p) / 2.0, 1.0))
            metrics["return"] = max(0.0, min(float(wfa.sharpe_mean) / 2.0, 1.0))
        return metrics

    def _to_securities(
        self,
        meta: list[SecurityMeta],
        collection: CollectionResult,
        ref_date: dt.date,
    ) -> list[Security]:
        ohlcv_by_ticker = {row.ticker: row for row in collection.ohlcv}
        halted = set(collection.halts)
        managed = set(collection.managed)
        securities: list[Security] = []
        for item in meta:
            ohlcv = ohlcv_by_ticker.get(item.ticker)
            turnover = (ohlcv.close * ohlcv.volume) if ohlcv else 0.0
            missing_fields = self._missing_ohlcv_fields(ohlcv)
            securities.append(
                Security(
                    ticker=item.ticker,
                    name=item.name,
                    market=item.market,
                    security_type=item.security_type,
                    listing_date=item.listing_date or dt.date(2000, 1, 1),
                    delisting_date=item.delisting_date,
                    # The current collector does not persist a separate
                    # adjusted-price history yet.  Treat present live OHLCV as
                    # usable so the daily candidate job remains operational.
                    has_adjusted_price=bool(ohlcv),
                    latest_ohlcv_date=ohlcv.date if ohlcv else None,
                    missing_ohlcv_fields=missing_fields,
                    halt_periods=(
                        [HaltPeriod(ticker=item.ticker, start=ref_date, end=ref_date)]
                        if item.ticker in halted else []
                    ),
                    managed_periods=(
                        [ManagedPeriod(ticker=item.ticker, start=ref_date, end=ref_date)]
                        if item.ticker in managed else []
                    ),
                    turnover_cache={ref_date: turnover},
                )
            )
        return securities

    @staticmethod
    def _missing_ohlcv_fields(ohlcv) -> set[str]:
        if ohlcv is None:
            return {"open", "high", "low", "close", "volume"}
        missing: set[str] = set()
        for field_name in ("open", "high", "low", "close"):
            value = getattr(ohlcv, field_name)
            if value is None or value <= 0:
                missing.add(field_name)
        if ohlcv.volume is None or ohlcv.volume < 0:
            missing.add("volume")
        return missing

    def _save_candidate_snapshot(
        self,
        db: Session,
        ref_date: dt.date,
        strategy_id: str,
        universe: list[Security],
    ) -> int:
        db.execute(
            delete(CandidateSnapshot).where(
                CandidateSnapshot.ref_date == ref_date,
                CandidateSnapshot.strategy_id == strategy_id,
            )
        )
        ranked = sorted(
            universe,
            key=lambda stock: stock.avg_turnover_20d_as_of(ref_date),
            reverse=True,
        )
        max_turnover = max((stock.avg_turnover_20d_as_of(ref_date) for stock in ranked), default=0.0)
        for stock in ranked:
            turnover = stock.avg_turnover_20d_as_of(ref_date)
            score = (turnover / max_turnover * 100.0) if max_turnover > 0 else 0.0
            db.add(
                CandidateSnapshot(
                    ref_date=ref_date,
                    strategy_id=strategy_id,
                    ticker=stock.ticker,
                    name=stock.name,
                    market=stock.market,
                    factor_score=round(score, 2),
                    trend_strength=50.0,
                    ts_bucket="S3",
                    final_score=round(score, 2),
                    weekly_pass=True,
                    estimated_qty=None,
                )
            )
        db.commit()
        return len(ranked)

    @staticmethod
    def _save_portfolio_snapshot(db: Session, ref_date: dt.date, sync: dict[str, float | int]) -> None:
        cash = float(sync.get("cash", 0.0))
        positions_value = float(sync.get("positions_value", 0.0))
        total_assets = float(sync.get("total_assets", cash + positions_value))
        row = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.ref_date == ref_date, PortfolioSnapshot.source == "broker")
            .first()
        )
        if row is None:
            db.add(
                PortfolioSnapshot(
                    ref_date=ref_date,
                    source="broker",
                    total_assets=total_assets,
                    cash=cash,
                    positions_value=positions_value,
                )
            )
        else:
            row.total_assets = total_assets
            row.cash = cash
            row.positions_value = positions_value
            row.updated_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

    def _submit_candidate_orders(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        ref_date: dt.date,
        blocked_tickers: set[str] | None = None,
    ) -> tuple[int, int]:
        account = broker.get_account_balance()
        remaining_cash = account.cash
        positions = self._broker_positions(broker)
        candidates = self._order_candidates(db, ref_date)
        submitted = 0
        skipped = 0
        max_orders = 3
        blocked_tickers = blocked_tickers or set()

        slippage_pct = self._settings.maps_order_slippage_pct
        max_gap_pct = self._settings.maps_order_max_gap_pct

        for candidate in candidates:
            if submitted >= max_orders:
                skipped += 1
                continue
            if candidate.ticker in blocked_tickers:
                skipped += 1
                continue
            if positions.get(candidate.ticker, 0) > 0:
                skipped += 1
                continue

            signal = self._latest_strategy_signal(
                db,
                ticker=candidate.ticker,
                strategy_id=candidate.strategy_id,
                ref_date=candidate.ref_date,
            )
            if signal is None or not signal.entry_signal:
                logger.info(
                    "Order skipped [%s %s]: strategy entry signal is not active",
                    candidate.strategy_id,
                    candidate.ticker,
                )
                skipped += 1
                continue

            # 신호 발생 시점 종가 (전략이 신호를 생성한 날 기준)
            signal_close = self._latest_close(db, candidate.ticker, candidate.ref_date)
            if signal_close <= 0:
                skipped += 1
                continue

            # 주문 시점 기준 DB 최신 종가 (당일 포함, 장 마감 후 수집된 전일 종가)
            current_close = self._latest_close(db, candidate.ticker, ref_date)
            if current_close <= 0:
                current_close = signal_close

            # 갭 체크: 신호 이후 시장이 MAX_GAP 이상 상승 → 신호 무효, 스킵
            gap_pct = (current_close - signal_close) / signal_close
            if gap_pct > max_gap_pct:
                logger.info(
                    "Order skipped [%s %s]: gap +%.1f%% since signal exceeds limit +%.1f%%",
                    candidate.strategy_id, candidate.ticker,
                    gap_pct * 100, max_gap_pct * 100,
                )
                skipped += 1
                continue

            # 지정가 = 최신 종가 × (1 + slippage) — 당일 소폭 상승 흡수
            limit_price = round_up_krx_price(
                current_close * (1 + slippage_pct),
                market=candidate.market,
            )

            remaining_slots = max(max_orders - submitted, 1)
            qty = self._order_qty(candidate, account.total_value, remaining_cash, limit_price, remaining_slots)
            if qty <= 0:
                skipped += 1
                continue

            order = Order(
                strategy_id=candidate.strategy_id,
                ticker=candidate.ticker,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=qty,
                limit_price=limit_price,
                current_price=current_close,
                memo=(
                    f"candidate_snapshot:{candidate.ref_date.isoformat()} "
                    f"signal={signal_close:.0f} gap={gap_pct:+.3f}"
                ),
            )
            try:
                manager.submit(order)
            except DuplicateOrderError:
                logger.info(
                    "Order skipped [%s %s]: already submitted",
                    candidate.strategy_id,
                    candidate.ticker,
                )
                skipped += 1
                continue
            submitted += 1
            remaining_cash = max(remaining_cash - qty * limit_price, 0.0)
            positions[candidate.ticker] = positions.get(candidate.ticker, 0) + qty

        return submitted, skipped

    def _submit_exit_orders(
        self,
        *,
        db: Session,
        broker,
        manager: OrderManager,
        ref_date: dt.date,
    ) -> tuple[int, int, set[str]]:
        positions = self._broker_position_details(broker)
        if not positions:
            return 0, 0, set()

        rows = (
            db.query(OrderLog)
            .filter(OrderLog.ticker.in_(set(positions)))
            .filter(OrderLog.side == OrderSide.BUY.value)
            .filter(OrderLog.status.in_(["filled", "partially_filled"]))
            .order_by(OrderLog.created_at.desc(), OrderLog.id.desc())
            .all()
        )
        entries: dict[str, OrderLog] = {}
        for row in rows:
            if row.ticker not in entries:
                entries[row.ticker] = row

        submitted = 0
        skipped = 0
        exit_tickers: set[str] = set()
        for ticker, position in positions.items():
            entry = entries.get(ticker)
            if entry is None or not entry.strategy_id:
                skipped += 1
                continue

            signal = self._latest_strategy_signal(
                db,
                ticker=ticker,
                strategy_id=entry.strategy_id,
                ref_date=ref_date,
            )
            current_price = (
                position.current_price
                if position.current_price is not None and position.current_price > 0
                else self._latest_close(db, ticker, ref_date)
            )
            entry_price = entry.fill_price or entry.order_price or position.avg_price
            stop_price = stop_loss_price(entry.strategy_id, entry_price)
            stop_triggered = (
                stop_price is not None
                and current_price > 0
                and current_price <= stop_price
            )
            strategy_exit = bool(signal and signal.exit_signal)
            if not stop_triggered and not strategy_exit:
                continue

            reason = "stop_loss" if stop_triggered else "strategy_exit"
            order = Order(
                strategy_id=entry.strategy_id,
                ticker=ticker,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=position.quantity,
                current_price=current_price,
                memo=(
                    f"{reason} entry={entry_price:.0f} "
                    f"current={current_price:.0f} stop={stop_price or 0:.0f}"
                ),
            )
            try:
                manager.submit_exit(order)
            except DuplicateOrderError:
                logger.info(
                    "Exit skipped [%s %s]: already submitted",
                    entry.strategy_id,
                    ticker,
                )
                skipped += 1
                exit_tickers.add(ticker)
                continue
            submitted += 1
            exit_tickers.add(ticker)
            logger.info(
                "Exit submitted [%s %s]: %s current=%.0f stop=%s",
                entry.strategy_id,
                ticker,
                reason,
                current_price,
                f"{stop_price:.0f}" if stop_price is not None else "n/a",
            )

        return submitted, skipped, exit_tickers

    def _order_candidates(self, db: Session, ref_date: dt.date) -> list[CandidateSnapshot]:
        latest_date = (
            db.query(CandidateSnapshot.ref_date)
            .filter(CandidateSnapshot.ref_date <= ref_date)
            .order_by(CandidateSnapshot.ref_date.desc())
            .limit(1)
            .scalar()
        )
        if latest_date is None:
            return []

        eligible_stages = {"mock_candidate", "live_candidate", "live"}
        latest_promotions = self._latest_promotions(db)
        rows = (
            db.query(CandidateSnapshot)
            .filter(CandidateSnapshot.ref_date == latest_date)
            .filter(CandidateSnapshot.weekly_pass.is_(True))
            .order_by(CandidateSnapshot.final_score.desc(), CandidateSnapshot.trend_strength.desc())
            .all()
        )
        claimed = claimed_candidate_tickers(db, since=latest_date)
        return [
            row for row in rows
            if latest_promotions.get(row.strategy_id) in eligible_stages
            and row.ticker not in claimed
        ]

    @staticmethod
    def _latest_promotions(db: Session) -> dict[str, str]:
        """주문 주기에서 참조할 전략별 현재 단계를 반환한다.

        passed=True 레코드만 읽는다.  실패 평가가 주문 자격(eligible_stages)을
        잘못 차단하는 것을 방지한다.
        """
        rows = (
            db.query(PromotionHistory)
            .filter(PromotionHistory.passed.is_(True))
            .order_by(PromotionHistory.evaluated_at.desc(), PromotionHistory.id.desc())
            .all()
        )
        latest: dict[str, str] = {}
        for row in rows:
            if row.strategy_id not in latest:
                latest[row.strategy_id] = row.to_stage
        return latest

    @staticmethod
    def _latest_close(db: Session, ticker: str, ref_date: dt.date) -> float:
        row = (
            db.query(HistoricalOHLCV)
            .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
            .order_by(HistoricalOHLCV.date.desc())
            .first()
        )
        return float(row.close) if row and row.close > 0 else 0.0

    @staticmethod
    def _latest_strategy_signal(
        db: Session,
        *,
        ticker: str,
        strategy_id: str,
        ref_date: dt.date,
    ) -> StrategySignal | None:
        strategy_cls = _RUNNABLE_STRATEGIES.get(strategy_id)
        if strategy_cls is None:
            return None
        rows = (
            db.query(HistoricalOHLCV)
            .filter(HistoricalOHLCV.ticker == ticker, HistoricalOHLCV.date <= ref_date)
            .order_by(HistoricalOHLCV.date.desc())
            .limit(400)
            .all()
        )
        if not rows:
            return None
        frame = pd.DataFrame([
            {
                "date": row.date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in reversed(rows)
        ]).set_index("date")
        strategy = strategy_cls()
        signals = strategy.generate_signals(frame, strategy.default_params)
        if signals.empty:
            return None
        latest = signals.iloc[-1]
        return StrategySignal(
            entry_signal=bool(latest.get("entry_signal", False)),
            exit_signal=bool(latest.get("exit_signal", False)),
            close=float(latest.get("close", 0.0)),
        )

    def _order_qty(
        self,
        candidate: CandidateSnapshot,
        total_value: float,
        remaining_cash: float,
        price: float,
        remaining_slots: int,
    ) -> int:
        max_position_value = total_value * self._settings.max_single_exposure
        cash_budget = remaining_cash / max(remaining_slots, 1)
        budget = min(max_position_value, cash_budget)
        max_qty = int(budget // price)
        if candidate.estimated_qty and candidate.estimated_qty > 0:
            return min(int(candidate.estimated_qty), max_qty)
        return max_qty

    @staticmethod
    def _broker_positions(broker) -> dict[str, int]:
        try:
            return broker.get_positions()
        except NotImplementedError:
            return {}

    @staticmethod
    def _broker_position_details(broker) -> dict[str, Position]:
        fetch_positions = getattr(broker, "_fetch_positions_and_balance", None)
        if callable(fetch_positions):
            positions, _balance = fetch_positions()
            return positions
        return {
            ticker: position
            for ticker, qty in OperationalPipeline._broker_positions(broker).items()
            if qty > 0 and (position := broker.get_position(ticker)) is not None
        }

    @staticmethod
    def _write_log(
        db: Session,
        *,
        ref_date: dt.date,
        source: str,
        status: str,
        items: int,
        note: str | None = None,
    ) -> None:
        db.add(CollectionLog(ref_date=ref_date, source=source, status=status, items=items, note=note))
        db.commit()


class MapsOperationalScheduler:
    """APScheduler wrapper for MAPS operational jobs."""

    def __init__(
        self,
        *,
        settings: MapsSettings | None = None,
        pipeline: OperationalPipeline | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline or OperationalPipeline(settings=self._settings)
        self._scheduler = BackgroundScheduler(timezone=self._settings.maps_scheduler_timezone)
        self._last_runs: dict[str, JobRun] = {}

    @property
    def running(self) -> bool:
        return self._scheduler.running

    def start(self) -> None:
        if self.running:
            return
        self._register_jobs()
        self._scheduler.start()
        logger.info("MAPS operational scheduler started")

    def shutdown(self) -> None:
        if self.running:
            self._scheduler.shutdown(wait=False)
            logger.info("MAPS operational scheduler stopped")

    def status(self) -> dict:
        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]
        return {
            "enabled": self._settings.maps_scheduler_enabled,
            "running": self.running,
            "timezone": self._settings.maps_scheduler_timezone,
            "jobs": jobs,
            "last_runs": {name: self._serialize_run(run) for name, run in self._last_runs.items()},
        }

    def run_once(self, job_name: str) -> JobRun:
        mapping = {
            "data_collection": self._pipeline.collect_data,
            "candidate_generation": self._pipeline.generate_candidates,
            "validation": self._pipeline.run_validation,
            "order_cycle": self._pipeline.run_order_cycle,
            "broker_sync": self._pipeline.sync_broker_state,
            "eod_cleanup": self._pipeline.run_eod_cleanup,
        }
        if job_name not in mapping:
            raise ValueError(f"Unknown scheduler job: {job_name}")
        return self._record(job_name, mapping[job_name])

    def backfill_ohlcv(self, start: dt.date, end: dt.date) -> JobRun:
        return self._record("ohlcv_backfill", lambda: self._pipeline.backfill_ohlcv(start, end))

    def _make_krx_job(self, name: str) -> Callable:
        """KRX 거래일에만 실행되는 잡 콜러블을 반환한다.

        비거래일(주말·한국 공휴일)에 트리거되면 실행을 건너뛰고
        로그만 남긴다.
        """
        def _job() -> None:
            if not _is_krx_market_day():
                logger.info("Scheduler job [%s] skipped: KRX 비거래일", name)
                return
            self.run_once(name)
        return _job

    def _register_jobs(self) -> None:
        self._add_weekday_job("data_collection", self._settings.maps_data_collection_time)
        self._add_weekday_job("candidate_generation", self._settings.maps_candidate_time)
        self._add_weekday_job("validation", self._settings.maps_validation_time)
        self._add_weekday_job("order_cycle", self._settings.maps_order_time)
        self._scheduler.add_job(
            self._make_krx_job("broker_sync"),
            IntervalTrigger(seconds=self._settings.maps_broker_sync_interval_seconds),
            id="broker_sync",
            name="broker_sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # KIS 모의서버 timeout(30s) × 재시도(3회) = 최대 ~90s 소요 가능.
            # misfire_grace_time을 동일하게 설정해 정상 지연 시 missed 처리 방지.
            misfire_grace_time=self._settings.maps_broker_sync_interval_seconds * 2,
        )
        self._add_weekday_job("eod_cleanup", self._settings.maps_eod_time)
        hour, minute = _parse_hhmm(self._settings.maps_stock_report_time)
        self._scheduler.add_job(
            self._run_stock_report,
            CronTrigger(hour=hour, minute=minute),
            id="stock_report",
            name="stock_report",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    @staticmethod
    def _run_stock_report() -> None:
        """Generate all stock reports once per day, including weekends."""
        db = SessionLocal()
        try:
            run_ids = run_all_reports_if_idle(db)
            logger.info("Scheduler job stock_report: generated run_ids=%s", run_ids)
        finally:
            db.close()

    def _add_weekday_job(self, name: str, hhmm: str) -> None:
        """월~금 KRX 거래일(공휴일 제외)에만 실행되는 잡을 등록한다."""
        hour, minute = _parse_hhmm(hhmm)
        self._scheduler.add_job(
            self._make_krx_job(name),
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            id=name,
            name=name,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def _record(self, name: str, fn: Callable[[], JobRun]) -> JobRun:
        run = fn()
        self._last_runs[name] = run
        logger.info("Scheduler job %s: %s %s", name, run.status, json.dumps(run.details, ensure_ascii=False))
        return run

    @staticmethod
    def _serialize_run(run: JobRun) -> dict:
        return {
            "name": run.name,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "message": run.message,
            "details": run.details,
        }


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid HH:MM scheduler time: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid HH:MM scheduler time: {value!r}")
    return hour, minute


_scheduler: MapsOperationalScheduler | None = None


def get_operational_scheduler() -> MapsOperationalScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MapsOperationalScheduler()
    return _scheduler


def start_operational_scheduler_if_enabled() -> None:
    scheduler = get_operational_scheduler()
    if scheduler._settings.maps_scheduler_enabled:
        scheduler.start()


def shutdown_operational_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown()
