# 전략 가이드 (네이버 블로그용) + 전략관리 화면 구성 검토

## 1. 원고

전략 1개 = 글 1편. `.txt` 파일 전체를 복사해 네이버 블로그 에디터에 붙여넣으면
그대로 발행할 수 있다. **마크다운 문법(`##`, `**`, 표)은 쓰지 않았다** — 네이버
스마트에디터는 마크다운을 렌더링하지 않아 기호가 그대로 노출되기 때문이다.
구분선·들여쓰기만으로 구조를 만들었다.

**이모지와 em dash(—)도 쓰지 않는다.** 투자 글이 AI 생성물로 읽히면 숫자의 신뢰까지
같이 떨어진다. 규약 전문은 `docs/blog_style_naver.md`, 검사는
`python scripts/check_naver_format.py docs/strategy_guides/*.txt`.

| 파일 | 전략 ID | 제목 |
|---|---|---|
| `00_전략_시작하기.txt` | — | 매매 전략이란 무엇인가 (공통 도입부·용어) |
| `01_pullback_v3.txt` | `pullback_v3` | 눌림목 매수 V3 |
| `02_pullback_v2.txt` | `pullback_v2` | 눌림목 매수 V2 (스토캐스틱) |
| `03_ath_breakout_v1.txt` | `ath_breakout_v1` | 신고가 돌파 V1 |
| `04_ath_breakout_v2.txt` | `ath_breakout_v2` | 신고가 돌파 V2 (거래량·트레일링) |
| `05_donchian_v1.txt` | `donchian_v1` | 돈치안 채널 V1 |
| `06_donchian_v2.txt` | `donchian_v2` | 돈치안 채널 V2 (ROC·국면 필터) |
| `07_multi_asset_trend_v1.txt` | `multi_asset_trend_v1` | 이중 이동평균 추세추종 |
| `08_contrarian_quality_v1.txt` | `contrarian_quality_accumulation_v1` | 역발상 분할매수 (플래그 OFF) |

각 편의 숫자는 아래 코드에서 그대로 가져왔다. **코드가 바뀌면 원고도 고쳐야 한다.**

- 진입·청산 조건 — 각 `maps/strategy/*.py` 모듈 docstring
- 손절률 — `maps/strategy/live_rules.py:_STOP_LOSS_PCTS`
- ATR 배수 — `maps/strategy/live_rules.py:_ATR_MULTIPLIERS`
- 기본 파라미터·조정 범위 — 각 전략의 `default_params` / `param_grid`
- 선호 장세 — 각 전략 클래스의 `preferred_regimes`
- 허용 MDD — `maps/common/constants.py:ALLOWED_MDD`

> ✅ **손절 문구 확정됨.** 원고의 "고정 %와 ATR 중 더 여유 있는 쪽"이 정본이며,
> `maps/strategy/live_rules.py:effective_stop_price` 가 유일한 구현이다.
> (이전에는 실거래 경로가 `atr_stop_price(...) or stop_loss_price(...)` 라서 ATR 이
> 있으면 무조건 ATR 을 썼다 — HANDOFF Next Steps 3. 지금은 통일돼 있다.)

---

## 2. 전략관리 화면 구성 검토

### 2.1 현재 상태

- 화면: `templates/strategies.html` — 카드 하나에 `<div id="strategies-area">` 뿐
- 렌더링: `static/js/app.js` 의 `loadStrategies()` — 표 1개
- 컬럼: 전략 ID · 단계 · Tradeability · Plateau · MC MDD p95 · WFA · 승격
- API: `GET /api/v1/strategies` (`maps/api/strategies.py`)

문제는 **`name` 필드가 `strategy_id` 를 그대로 복사**한다는 것이다
(`strategies.py:_to_strategy_item` 의 `name=strategy_id`). 화면에는 `donchian_v2`
같은 식별자만 뜨고, 그 전략이 무엇을 하는지 알 방법이 화면 안에 없다.

### 2.2 제안 — 2단 구조

```
전략 관리
┌───────────────────────────────────────────────────────────────┐
│ 전체 8 · 실전 0 · 모의후보 2 · 승격 대기 0                       │
├───────────────────────────────────────────────────────────────┤
│ [ 운용 현황 ]  [ 전략 설명 ]        ← 탭                        │
└───────────────────────────────────────────────────────────────┘

▸ 탭 1 "운용 현황" = 지금 표를 유지하되 두 컬럼만 추가
   전략              | 단계 | Tradeability | Plateau | MC MDD | WFA | 선호장세
   눌림목 매수 V3     | 모의 | 34.7         | 100     | 12.1%  | ✓   | 강세·혼조
   pullback_v3       |      |              |         |        |     |
   ↑ 한글명을 위, 식별자를 아래 작은 글씨로. 행 클릭 → 상세 패널

▸ 탭 2 "전략 설명" = 카드 그리드 (초보자·외부 공유용)
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │ 눌림목 매수 V3 │ │ 신고가 돌파 V1 │ │ 돈치안 채널 V1 │
   │ 오르는 종목이  │ │ 1년 최고가를   │ │ 40일 최고가를  │
   │ 쉴 때 산다     │ │ 뚫으면 산다    │ │ 뚫으면 산다    │
   │ 강세·혼조 -5%  │ │ 강세 -10%     │ │ 강세·혼조 -8% │
   └──────────────┘ └──────────────┘ └──────────────┘
```

### 2.3 상세 패널 (행/카드 클릭 시) — 3탭

| 탭 | 내용 | 데이터 출처 |
|---|---|---|
| ① 개요 | 한 줄 요약 · 핵심 아이디어 · 진입/청산/손절 조건 · 선호 장세 | 전략 클래스 + 신규 카탈로그 |
| ② 검증 | Tradeability·Plateau·MC·WFA + 승격 단계 진행바(research→live) | 기존 `GET /strategies` |
| ③ 가이드 | 블로그 원고 전문 + **[전체 복사]** 버튼 | `docs/strategy_guides/*.txt` |

③ 탭의 복사 버튼이 핵심이다. 화면에서 바로 복사해 네이버 블로그에 붙여넣을 수 있으면
원고 관리처가 하나로 끝난다(파일 → 화면 → 블로그).

### 2.4 설계 원칙 — 숫자는 코드에서, 산문만 문서에서

가이드 문서를 통째로 하드코딩하면 전략 파라미터가 바뀔 때마다 문서가 어긋난다.
블로그 원고에서 겪은 것과 같은 문제다. 그래서 화면에서는 **숫자를 코드에서 렌더링**한다.

이미 코드가 단일 출처인 값들:

```
strategy_id, preferred_regimes  → 전략 클래스 속성
default_params, param_grid      → 전략 클래스 메서드
손절 %, ATR 배수                 → live_rules.py
허용 MDD                        → constants.ALLOWED_MDD
전략군                          → constants.STRATEGY_GROUP_MAP
```

새로 작성이 필요한 것은 **산문뿐**이다: 한글명, 한 줄 요약, 비유, 초보자용 설명.
이걸 `maps/strategy/catalog.py` 같은 곳에 전략 ID → 산문 메타로 두고,
숫자는 위 출처에서 조합해 응답한다.

### 2.5 구현 시 손댈 파일

| 파일 | 작업 |
|---|---|
| `maps/strategy/catalog.py` | 신규. 전략 ID → 한글명·한 줄 요약·핵심 아이디어·가이드 파일명 |
| `maps/api/strategies.py` | `GET /api/v1/strategies/{id}/guide` 추가, `_to_strategy_item` 의 `name` 을 한글명으로 |
| `maps/api/schemas.py` | `StrategyItem` 에 `display_name`·`summary`·`preferred_regimes` 추가 |
| `templates/strategies.html` | 탭 2개 + 상세 패널 컨테이너 |
| `static/js/app.js` | `loadStrategies()` 개편, 상세 패널 렌더러, 복사 버튼 |
| `tests/test_strategy_catalog.py` | 신규. **`STRATEGY_GROUP_MAP` 의 모든 전략이 카탈로그에 있는지** 검증 |

마지막 항목이 중요하다. 새 전략을 추가하고 설명을 빼먹으면 테스트가 실패하도록
묶어두면, 화면에 식별자만 덩그러니 뜨는 지금 상태가 재발하지 않는다.

### 2.6 평문 표시·복사 재사용

③ 가이드 탭은 이미 있는 것을 그대로 쓸 수 있다.
`maps/api/blog.py` + `templates/blog.html` 에 **경로순회 차단과 평문 표시·전체 복사**가
구현되어 있다(일일 블로그용). 같은 방식으로 `docs/strategy_guides/` 를 읽으면 된다.

원고가 `.txt` 순수 텍스트이므로 마크다운 변환 없이 `pre-wrap` 으로 그대로 보여준다 —
화면에서 가공하면 복사한 내용과 네이버에 발행될 내용이 달라진다.
복사 버튼은 `static/js/app.js` 의 `copyGuideText(btn, sourceId)` 하나를 공유한다.
