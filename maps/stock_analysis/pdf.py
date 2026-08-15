"""저장된 종목분석 이력을 PDF 바이트로 렌더링한다.

저장 시점의 원본(`snapshot`·`narrative`·`trade_plan`)만 그린다. 현재가 오버레이
(`latest_*`·`price_refreshed_at`)는 **읽지 않는다** — 이력은 불변이고 현재가만 따로
갱신한다는 `history.py` 의 경계를 PDF 에서도 지키기 위해서다. 저장 시점 값과 오늘 값이
한 장에 섞이면 무슨 기준의 문서인지 알 수 없어진다.

렌더러는 reportlab 이다. HTML→PDF 엔진(WeasyPrint·xhtml2pdf)은 운영 리눅스에서만 돌거나
개발 Windows 에서 깨져서 로컬 테스트를 할 수 없었다. reportlab 은 순수 파이썬이라 두 OS 가
동일하게 동작하고, 한글 TTF 를 직접 열어 **PDF 에 임베드**하므로 받는 쪽 뷰어에 한글 폰트가
없어도 글자가 보인다.
"""

from __future__ import annotations

import datetime
import importlib.resources
import io
import os
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, PolyLine, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from maps.common.models import StockAnalysisHistory

FONT_NAME = "MapsKR"
FONT_NAME_BOLD = "MapsKR-Bold"

# (일반, 볼드) 후보. 먼저 찾은 쌍을 쓴다. 볼드가 없으면 일반으로 대체한다.
_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    # 운영 서버 (Ubuntu, nanum 패키지)
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    # 개발 PC (Windows)
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
    # macOS
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf",
     "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
)

_TEXT = colors.HexColor("#1f2933")
_MUTED = colors.HexColor("#6b7280")
_LINE = colors.HexColor("#d5dbe1")
_HEAD_BG = colors.HexColor("#f1f4f7")
_ACCENT = colors.HexColor("#1d4ed8")


def _bundled_font() -> tuple[str, str]:
    """마지막 폴백 — pykrx 가 동봉한 한글 TTF.

    pykrx 는 이 저장소의 필수 의존성이라, 시스템에 한글 폰트가 하나도 없는 상자에서도
    이 경로는 있다. 볼드는 없어서 일반체로 대신한다.
    """
    try:
        path = str(importlib.resources.files("pykrx").joinpath("NanumBarunGothic.ttf"))
    except (ModuleNotFoundError, AttributeError, TypeError):  # pragma: no cover
        return "", ""
    return path, path


class FontUnavailableError(RuntimeError):
    """한글 폰트를 찾지 못했다 — 글자가 빈칸으로 나가느니 실패한다."""


def _try_register(regular: str, bold: str) -> bool:
    """폰트 쌍이 실재하면 등록하고 True 를 돌려준다."""
    if not regular or not os.path.exists(regular):
        return False
    pdfmetrics.registerFont(TTFont(FONT_NAME, regular))
    pdfmetrics.registerFont(
        TTFont(FONT_NAME_BOLD, bold if os.path.exists(bold) else regular)
    )
    pdfmetrics.registerFontFamily(
        FONT_NAME, normal=FONT_NAME, bold=FONT_NAME_BOLD,
        italic=FONT_NAME, boldItalic=FONT_NAME_BOLD,
    )
    return True


def _register_fonts() -> None:
    """한글 TTF 를 한 번만 등록한다.

    등록에 실패하면 PDF 를 만들지 않는다. 폰트 없이 만들면 한글이 전부 빈 네모로
    나가는데, 그건 깨진 걸 알아채기 어려운 실패다.
    """
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    for regular, bold in _FONT_CANDIDATES:
        if _try_register(regular, bold):
            return
    # 시스템 폰트를 못 찾았을 때만 pykrx 를 건드린다 — import 부수효과를 아낀다.
    if _try_register(*_bundled_font()):
        return
    raise FontUnavailableError(
        "한글 폰트를 찾지 못해 PDF 를 만들 수 없습니다. "
        f"확인한 경로: {[p for p, _ in _FONT_CANDIDATES]} + pykrx 동봉 폰트"
    )


def _style(name: str, size: float, *, bold: bool = False, color=_TEXT,
           space_before: float = 0, space_after: float = 0) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=FONT_NAME_BOLD if bold else FONT_NAME,
        fontSize=size,
        leading=size * 1.45,
        textColor=color,
        alignment=TA_LEFT,
        spaceBefore=space_before,
        spaceAfter=space_after,
    )


def _fmt(value: Any) -> str:
    """표에 넣을 사람이 읽는 문자열."""
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _kv_table(pairs: list[tuple[str, Any]], width: float) -> Table:
    """2열(항목·값)을 두 벌 나란히 놓은 표. 빈 값도 `-` 로 자리를 지킨다."""
    rows: list[list[str]] = []
    for i in range(0, len(pairs), 2):
        chunk = pairs[i:i + 2]
        row = []
        for label, value in chunk:
            row += [label, _fmt(value)]
        while len(row) < 4:
            row += ["", ""]
        rows.append(row)
    col = width / 4
    table = Table(rows, colWidths=[col * 1.1, col * 0.9, col * 1.1, col * 0.9])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), _TEXT),
        ("BACKGROUND", (0, 0), (0, -1), _HEAD_BG),
        ("BACKGROUND", (2, 0), (2, -1), _HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return table


def _grid_table(header: list[str], body: list[list[str]], width: float,
                *, first_col_is_label: bool = False) -> Table:
    """머리행이 있는 일반 격자표.

    `first_col_is_label` 이면 첫 열은 이름이라 왼쪽에 붙인다. 아니면 모든 칸이
    숫자이므로 전부 오른쪽 정렬한다 — 한 표 안에서 정렬이 갈리면 읽기 나쁘다.
    """
    table = Table([header] + body, colWidths=[width / len(header)] * len(header))
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTNAME", (0, 0), (-1, 0), FONT_NAME_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), _TEXT),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("ALIGN", (1 if first_col_is_label else 0, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return table


def _chart(points: list[dict], width: float, height: float = 46 * mm) -> Drawing | None:
    """6개월 종가 추이 선 그래프. 점이 2개 미만이면 그리지 않는다."""
    closes = [p.get("close") for p in points if isinstance(p.get("close"), (int, float))]
    if len(closes) < 2:
        return None

    pad_l, pad_r, pad_b, pad_t = 16 * mm, 4 * mm, 9 * mm, 5 * mm
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_b - pad_t
    low, high = min(closes), max(closes)
    span = (high - low) or 1

    drawing = Drawing(width, height)
    # 가로 눈금 3줄 + 값 라벨
    for frac in (0.0, 0.5, 1.0):
        y = pad_b + plot_h * frac
        drawing.add(Line(pad_l, y, pad_l + plot_w, y, strokeColor=_LINE, strokeWidth=0.4))
        drawing.add(String(
            pad_l - 2, y - 2.5, f"{int(low + span * frac):,}",
            fontName=FONT_NAME, fontSize=6.5, fillColor=_MUTED, textAnchor="end",
        ))

    step = plot_w / (len(closes) - 1)
    coords: list[float] = []
    for i, close in enumerate(closes):
        coords += [pad_l + step * i, pad_b + (close - low) / span * plot_h]
    drawing.add(PolyLine(coords, strokeColor=_ACCENT, strokeWidth=1.2))

    dates = [p.get("date", "") for p in points]
    for x, label in ((pad_l, dates[0]), (pad_l + plot_w, dates[-1])):
        drawing.add(String(
            x, pad_b - 7, str(label), fontName=FONT_NAME, fontSize=6.5,
            fillColor=_MUTED, textAnchor="start" if x == pad_l else "end",
        ))
    return drawing


def _section(title: str) -> Paragraph:
    return Paragraph(title, _style("h2", 11, bold=True, space_before=7 * mm,
                                   space_after=2.5 * mm))


def _analysed_at_kst(created_at: datetime.datetime | None) -> str:
    """DB 의 UTC naive 시각을 KST 문자열로 바꾼다.

    `created_at` 은 UTC naive 다. 그대로 찍으면 9시간 어긋난 시각이 문서에 박힌다.
    """
    if created_at is None:
        return "-"
    kst = created_at.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=9)
    return kst.strftime("%Y-%m-%d %H:%M KST")


def _trade_plan_flowables(plan: dict[str, Any], width: float) -> list:
    """매매계획. AI 가격이 없으면 수동 입력 안내로 닫는다."""
    entries = plan.get("entries") or []
    prices = [plan.get("target"), *entries, plan.get("stop")]
    if not any(isinstance(p, (int, float)) for p in prices):
        return [Paragraph(
            f"권고: {_fmt(plan.get('recommendation'))} — 저장된 실행 가격이 없습니다"
            " (수동 입력 필요).", _style("plain", 9))]

    labels = ["목표가", "1차 진입", "2차 진입", "3차 진입", "손절가"]
    values = [plan.get("target"), *(list(entries) + [None, None, None])[:3], plan.get("stop")]
    flowables = [
        _grid_table(labels, [[_fmt(v) for v in values]], width),
        Spacer(1, 2.5 * mm),
        Paragraph(f"권고: {_fmt(plan.get('recommendation'))}"
                  f" · 출처: {_fmt(plan.get('source'))}", _style("plain", 8.5, color=_MUTED)),
    ]
    rationale = plan.get("rationale")
    if rationale:
        flowables.append(Paragraph(str(rationale), _style("plain", 9, space_before=1.5 * mm)))
    return flowables


def render_history_pdf(row: StockAnalysisHistory) -> bytes:
    """저장된 분석 이력 한 건을 PDF 바이트로 만든다.

    같은 이력에 대해 항상 같은 바이트를 낸다(reportlab `invariant`). 결과가 달라지면
    입력이 달라졌다는 뜻이고, 현재가 오버레이 갱신은 입력이 아니다.
    """
    _register_fonts()

    snapshot: dict[str, Any] = row.snapshot or {}
    tech: dict[str, Any] = snapshot.get("기술적분석") or {}
    valuation: dict[str, Any] = snapshot.get("밸류에이션") or {}
    financials: dict[str, Any] = snapshot.get("재무제표_3개년") or {}
    plan: dict[str, Any] = row.trade_plan or {}

    buffer = io.BytesIO()
    margin = 16 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=14 * mm,
        title=f"{row.name} ({row.ticker}) 종목분석",
        author="MAPS",
        invariant=1,
    )
    width = doc.width
    story: list = []

    # ── 머리말 ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f"{row.name} ({row.ticker}) 종목분석",
        _style("h1", 16, bold=True, space_after=1.5 * mm),
    ))
    story.append(Paragraph(
        f"시장 {_fmt(row.market)} · 분석 시각 {_analysed_at_kst(row.created_at)}"
        f" · 기술 기준일 {_fmt(tech.get('기준일') or row.ref_date)}"
        f" · 분석 당시 주가 {_fmt(row.analyzed_price)}원",
        _style("meta", 8.5, color=_MUTED, space_after=1 * mm),
    ))
    story.append(Paragraph(
        "저장된 분석 원본입니다. 이후 갱신된 현재가는 포함하지 않습니다.",
        _style("note", 8, color=_MUTED),
    ))

    # ── 6개월 차트 ────────────────────────────────────────────────────────────
    chart = _chart(tech.get("차트_6개월") or [], width)
    if chart is not None:
        story.append(_section("6개월 주가 추이"))
        story.append(chart)

    # ── 기술적 분석 ───────────────────────────────────────────────────────────
    story.append(_section("기술적 분석"))
    if tech.get("error"):
        story.append(Paragraph(f"수집 실패: {tech['error']}", _style("plain", 9, color=_MUTED)))
    else:
        story.append(_kv_table([
            ("현재가", tech.get("현재가")),
            ("전일대비", f"{_fmt(tech.get('전일대비_pct'))}%"),
            ("52주 고가", tech.get("52주_고가")),
            ("52주 저가", tech.get("52주_저가")),
            ("RSI(14)", tech.get("RSI14")),
            ("RSI 상태", tech.get("RSI_상태")),
            ("MACD", tech.get("MACD")),
            ("MACD 방향", tech.get("MACD_방향")),
            ("MACD signal", tech.get("MACD_signal")),
            ("MACD 히스토그램", tech.get("MACD_히스토그램")),
            ("정배열 여부", tech.get("정배열_여부")),
            ("20/60 크로스", tech.get("20_60_크로스")),
        ], width))

    moving_avg = tech.get("이동평균선") or {}
    if moving_avg:
        story.append(_section("이동평균선"))
        keys = list(moving_avg)
        story.append(_grid_table(keys, [[_fmt(moving_avg[k]) for k in keys]], width))

    # ── 밸류에이션 ────────────────────────────────────────────────────────────
    story.append(_section("밸류에이션"))
    if valuation.get("error") or not valuation:
        story.append(Paragraph(
            f"수집 실패: {_fmt(valuation.get('error'))}", _style("plain", 9, color=_MUTED)))
    else:
        story.append(_kv_table(
            [(key, value) for key, value in valuation.items()], width))

    # ── 재무제표 ──────────────────────────────────────────────────────────────
    story.append(_section("재무제표 (최근 3개년)"))
    years = sorted((y for y in financials if str(y).isdigit()), reverse=True)
    if not years:
        story.append(Paragraph(
            f"수집 실패: {_fmt(financials.get('error'))}", _style("plain", 9, color=_MUTED)))
    else:
        accounts: list[str] = []
        for year in years:
            for account in financials[year]:
                if account not in accounts:
                    accounts.append(account)
        story.append(_grid_table(
            ["계정", *(f"{y}년" for y in years)],
            [[account, *(_fmt(financials[y].get(account)) for y in years)]
             for account in accounts],
            width,
            first_col_is_label=True,
        ))

    # ── 매매계획 ──────────────────────────────────────────────────────────────
    story.append(KeepTogether([_section("매매계획"), *_trade_plan_flowables(plan, width)]))

    # ── AI 원고 ───────────────────────────────────────────────────────────────
    if (row.narrative or "").strip():
        story.append(_section("AI 종합 의견"))
        for block in row.narrative.split("\n"):
            text = block.strip()
            if text:
                story.append(Paragraph(
                    text.replace("&", "&amp;").replace("<", "&lt;"),
                    _style("plain", 9, space_after=1.2 * mm),
                ))

    doc.build(story)
    return buffer.getvalue()
