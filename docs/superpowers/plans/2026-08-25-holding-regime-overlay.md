# Holding Regime Overlay v1 — Shadow 전용 구현 계획

> 2026-08-26 설계 검토에서 자동매도(`enforce`) 범위를 폐기했다. v1은
> `off|shadow` 판정·감사·다이제스트만 제공하며 어떤 경우에도 주문을 만들지 않는다.

## 목표

자동후보로 매수한 보유 종목의 진입 당시 장세와 최근 확정 장세를 비교해 `HOLD`,
`WATCH`, `EXIT` 감사 판정을 남긴다. `EXIT`는 운영자 검토용 신호일 뿐 매도 지시가 아니다.
기존 손절·익절·트레일링·전략 청산은 독립적으로 먼저 동작한다.

## 확정 정책

- 설정은 `off|shadow`, 기본값은 `shadow`다. `enforce`는 유효하지 않다.
- 현재 장세는 `source="candidate_generation"`인 최근 장마감 관측만 사용한다.
- 서로 다른 두 관측에서 같은 불리 원인이 지속될 때만 confirmed다.
  - `weekly_fail`: 주간추세가 `fail`
  - `weak_transition`: 진입 `strong|mixed`에서 현재 `weak`로 전환
- 최신 관측과 두 관측 사이 간격은 각각 최대 3일이다.
- confirmed이고 전략군이 `pullback_short`, `ath_outlier`, `donchian_research`면
  감사 액션은 `EXIT`다.
- `multi_asset`, `contrarian_quality`는 confirmed여도 `WATCH`다.
- 고변동성 단독, 선호 장세 이탈, 한 번만 나타난 불리 원인은 `WATCH`다.
- 입력 누락·노후화·불일치·미등록 전략은 `HOLD`로 실패 개방한다.
- 부분매도, 손절선 변경, 전략 교체, 재생 도구, UI는 v1 범위가 아니다.

## 진입 포지션 신뢰 조건

오버레이는 다음 조건을 모두 만족하는 자동 BUY만 신뢰한다.

- `filled|partially_filled`이고 `fill_qty > 0`
- `decision_context.version == 1`, `origin == "live"`
- `candidate.snapshot_id`가 양수 정수이며 실제 `CandidateSnapshot`과 종목·전략·기준일이 일치
- `market.source == "order_cycle"`
- 주문 시장일이 후보일보다 빠르지 않고 평가 기준일보다 미래가 아님
- 진입 이후 양수 체결 SELL이 없고 현재 수량이 진입 체결수량을 초과하지 않음

수동·외부·만료·변조 컨텍스트와 청산 뒤 외부 재취득처럼 출처가 모호한 보유분은
`HOLD` 또는 감사 제외로 처리한다. 현재 `AnalysisPick.state == "BOUGHT"`인 종목은 설정과
무관하게 오버레이와 다이제스트 집계에서 제외한다.

## 구현 구조

- `maps/risk/holding_regime_overlay.py`
  - 부작용 없는 `evaluate_holding_regime()` 판정기
- `maps/common/models.py`, `alembic/versions/0028_holding_regime_audit.py`
  - 일자·포지션별 감사 행, `(ref_date, position_key)` 유일 제약
- `maps/ops/scheduler.py`
  - 기존 청산 처리 뒤 별도 예외 경계에서 shadow 감사 실행
  - 최신 BUY의 `position_key=order:<OrderLog.id>` 사용
  - 같은 날 같은 값은 다시 저장하지 않음
- `maps/ops/daily_digest.py`, `maps/api/schemas.py`
  - 보유 종목별 판정과 `hold|watch|exit` 합계 노출

## 완료 기준

- [x] 순수 판정기와 정책 단위 테스트
- [x] `off|shadow` 설정 및 `enforce` 거부 테스트
- [x] 감사 모델과 Alembic 0028
- [x] broker sync shadow 감사와 기존 청산 오류 격리
- [x] 자동 BUY 컨텍스트·후보 스냅샷·현재 포지션 출처 검증
- [x] BOUGHT 분석픽 제외와 다이제스트 액션 집계
- [x] 동일 일자·동일 입력 무변경 보장
- [x] 전체 회귀 테스트 및 배포 전 인계 (`957 passed`, 2026-08-26)

## 운영 경계

이 변경은 로컬 기능 브랜치 구현이다. 운영 DB 마이그레이션, 서비스 재시작, `.env` 변경,
배포는 별도 승인 없이는 수행하지 않는다. 배포하더라도 모드는 `shadow`로만 운용한다.
