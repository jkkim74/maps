"""예정 주문 화면이 유동성 축소·차단을 드러내는지 검사한다."""

from __future__ import annotations

from pathlib import Path


def test_orders_screen_shows_liquidity_cap() -> None:
    """축소된 예정 주문이 원래 수량과 사유를 보여야 한다.

    조용히 줄어들면 사용자는 왜 이만큼만 샀는지 알 방법이 없다.
    """
    template = Path("templates/orders.html").read_text(encoding="utf-8")
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "유동성" in template
    assert "liquidity_reason" in script
    assert "유동성 축소" in script
    assert "원래 수량" in script


def test_orders_screen_distinguishes_liquidity_block_from_cash() -> None:
    """유동성 차단을 '수량부족'과 같은 말로 뭉뚱그리면 안 된다."""
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "insufficient_liquidity" in script
    assert "거래대금 미확인" in script
