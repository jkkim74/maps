"""stock-report 생성 실행기.

/opt/stock-report 소스를 sys.path에 추가하여 report_generator의 함수들을 호출하고,
결과 HTML을 MAPS DB(stock_report_runs)에 저장한다.
발송(Telegram/Slack/GitHub)은 일절 하지 않는다.
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from maps.common.models import StockReportRun
from maps.common.settings import get_settings

logger = logging.getLogger(__name__)

# 리포트 유형 정의
REPORT_TYPES = {
    "premium": "프리미엄 주식 리포트",
    "updown":  "Gap Up & Down 리스크 리포트",
    "summary": "Market Summary 리포트",
    "supply":  "Market Supply 리포트",
}


def _validate_summary_metadata(meta: dict) -> None:
    """Market Summary가 올바른 국내 지수와 연속 데이터로 계산됐는지 검증한다."""
    errors: list[str] = []
    inputs = meta.get("index_inputs") if isinstance(meta, dict) else None
    if (
        not isinstance(meta, dict)
        or meta.get("data_valid") is not True
        or not isinstance(inputs, dict)
    ):
        errors.append("missing validity metadata")
    else:
        for name, expected_ticker in (("kospi", "^KS11"), ("kosdaq", "^KQ11")):
            item = inputs.get(name)
            if not isinstance(item, dict):
                errors.append(f"{name}: missing")
                continue
            if item.get("ticker") != expected_ticker:
                errors.append(f"{name}: ticker={item.get('ticker')}")
            checks = (
                ("row_count", 60, lambda value, limit: value >= limit),
                ("latest_age_days", 7, lambda value, limit: 0 <= value <= limit),
                ("max_gap_days", 7, lambda value, limit: 0 <= value <= limit),
            )
            for field, limit, valid in checks:
                value = item.get(field)
                if not isinstance(value, (int, float)) or not valid(value, limit):
                    errors.append(f"{name}: {field}={value}")
    if errors:
        raise RuntimeError("index metadata invalid: " + "; ".join(errors))


def _add_stock_report_to_path() -> None:
    """stock-report 소스 디렉토리를 Python 경로에 추가한다."""
    settings = get_settings()
    src_path = Path(settings.maps_stock_report_path)
    src_str = str(src_path)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
        logger.info("stock-report 경로 추가: %s", src_str)


def _apply_linux_font() -> None:
    """리눅스 서버 환경에서 NanumGothic 폰트를 적용한다 (Malgun Gothic 대체)."""
    import matplotlib
    import matplotlib.pyplot as plt

    if sys.platform != "win32":
        try:
            matplotlib.rcParams["font.family"] = "NanumGothic"
        except Exception:
            pass
        matplotlib.rcParams["axes.unicode_minus"] = False


def _import_generators():
    """report_generator 모듈에서 생성 함수들을 임포트한다."""
    _add_stock_report_to_path()
    _apply_linux_font()
    try:
        from report_generator import (  # type: ignore[import]
            generate_market_summary_report,
            generate_market_supply_report,
            generate_premium_stock_report,
            getUpAndDownReport,
        )
        return {
            "premium": generate_premium_stock_report,
            "updown":  getUpAndDownReport,
            "summary": generate_market_summary_report,
            "supply":  generate_market_supply_report,
        }
    except ImportError as exc:
        raise RuntimeError(
            f"stock-report 임포트 실패. "
            f"경로({get_settings().maps_stock_report_path})를 확인하세요: {exc}"
        ) from exc


def run_report(db: Session, report_type: str) -> int:
    """지정된 리포트를 생성하고 DB에 저장한다. 생성된 run_id를 반환한다."""
    if report_type not in REPORT_TYPES:
        raise ValueError(f"지원하지 않는 리포트 유형: {report_type}. 가능: {list(REPORT_TYPES)}")

    run = StockReportRun(
        report_type=report_type,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id
    logger.info("[stock-report] 시작: type=%s run_id=%d", report_type, run_id)

    try:
        generators = _import_generators()
        gen_fn = generators[report_type]

        report_data = gen_fn()

        if report_data is None:
            raise RuntimeError("리포트 생성 실패: 조건 미충족 또는 데이터 없음")

        meta = report_data.metadata if hasattr(report_data, "metadata") else {}

        if report_type == "summary":
            # 실패하더라도 어떤 입력 검증에서 막혔는지 감사할 수 있게 메타데이터는 남긴다.
            run.meta_json = json.dumps(meta, ensure_ascii=False, default=str)
            _validate_summary_metadata(meta)

        row = db.query(StockReportRun).filter(StockReportRun.id == run_id).first()
        if row:
            row.status = "completed"
            row.html_content = report_data.html_content
            row.trade_date = str(report_data.trade_date) if hasattr(report_data, "trade_date") else None
            row.meta_json = json.dumps(meta, ensure_ascii=False, default=str)
            row.completed_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        logger.info("[stock-report] 완료: type=%s run_id=%d", report_type, run_id)

    except Exception as exc:
        logger.exception("[stock-report] 오류: type=%s run_id=%d", report_type, run_id)
        row = db.query(StockReportRun).filter(StockReportRun.id == run_id).first()
        if row:
            row.status = "failed"
            row.error_message = str(exc)
            row.completed_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()

    return run_id


def run_all_reports(db: Session) -> list[int]:
    """4종 리포트를 순서대로 생성한다. 생성된 run_id 목록을 반환한다."""
    run_ids = []
    for report_type in REPORT_TYPES:
        try:
            run_id = run_report(db, report_type)
            run_ids.append(run_id)
        except Exception as exc:
            logger.error("[stock-report] %s 건너뜀: %s", report_type, exc)
    return run_ids


def run_all_reports_if_idle(db: Session) -> list[int]:
    """Generate all reports unless a previous report run is still active."""
    running = (
        db.query(StockReportRun)
        .filter(StockReportRun.status == "running")
        .first()
    )
    if running:
        logger.info("[stock-report] 실행 중인 작업이 있어 자동 생성을 건너뜀: run_id=%d", running.id)
        return []
    return run_all_reports(db)
