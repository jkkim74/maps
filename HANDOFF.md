# HANDOFF

> 작성일: 2026-06-29 (KST) · 작성자: 이전 세션 에이전트
> 대상 화면: 분석 워치리스트(SCR-19), 거래리뷰(SCR-17), 전략매매 추적

## Goal

운영 중인 MAPS의 **분석 워치리스트 / 거래리뷰 화면 정확성·가시성 개선** 묶음 작업.
이번 세션은 사용자가 화면에서 발견한 표시/계산 이슈들을 순차적으로 수정·배포했다.

## Current Progress (이번 세션 완료, 전부 master 푸시 + 운영 배포 완료)

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`. **SSH 키 실제 경로는 `D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem`** (CLAUDE.md의 `D:\maps\...`는 틀림). 운영 DB는 **PostgreSQL**(sqlite 아님), `sudo -u postgres psql -d maps`.

완료된 커밋(시간순):
1. `e65c4c3` 전략매매 추적 로그 — `scheduler._process_strategy_trades`에 매 사이클 ARMED/BOUGHT 추적 로그(현재가 vs 매수가/목표가/손절가). 단, broker_sync 루프가 돌려면 `MAPS_SCHEDULER_ENABLED`+`MAPS_LIVE_TRADING_ENABLED`(장중)+`MAPS_STRATEGY_TRADE_ENABLED` 모두 ON 필요(기본 OFF).
2. `2d11a03` 워치리스트 현재가 — 보유 종목은 리스크모니터와 동일하게 브로커 라이브 현재가(`_broker_live_prices`) 우선, 미보유는 일봉 종가 폴백.
3. `1a6fda2` 호가단위 스냅 — `trading_rules.round_to_krx_tick()` 신규. `load_analysis_picks.py`(신규 픽)·`scheduler.py`(plan_target/stop, AI 가격)에서 호가 그리드로 스냅(54912→54900).
4. `c4028ef`/`afa77e8` 기존 데이터 정규화 — `scripts/normalize_pick_tick_prices.py`(dry-run 기본, `--apply`, `--include-protected`). 운영에서 1건(세방전지 004490 목표가 54912→54900) 반영 완료.
5. `8f5214c` 체결가 컬럼 — 워치리스트에 "체결가" 컬럼 추가. `entry_order_id → OrderLog.fill_price`(없으면 order_price). `_fill_prices()` 헬퍼.
6. `48d9c81` 체결가 정수화 + 손익비 기준 — 체결가 `round()` 정수, `rr_ratio`를 **체결가 우선**(미체결 시 계획 매수가)으로 계산.
7. `74afce2` 거래리뷰 보유중 행 — `status==='open'`이면 매도가/손익/수익률 `—` 표시(프론트). 비고 "보유 중". 미실현 손익은 상단 KPI에만 유지(데이터/집계 불변).

테스트 전체 통과: **329 passed**. 워크플로: 코드 수정 → `pytest` → `git push` → SSH 배포(`git pull && systemctl restart maps`).

## What Worked

- **리스크모니터 보유종목(`maps/api/risk.py:_broker_holdings`) 패턴 재사용**: 라이브 현재가·체결가(fill_price or order_price) 산출에 그대로 차용.
- **표시 계층만 수정 원칙**: 거래리뷰 보유중 이슈는 백엔드 데이터(KPI·전략집계가 사용)를 건드리지 않고 프론트(`static/js/app.js`)에서만 숨김 → 블라스트 반경 최소.
- **운영 PostgreSQL 직접 조회로 근거 확정**(holdings, order_log) 후 수정 → 추측 방지.
- **결정론적 호가 스냅**: LLM(trade-planner)이 호가단위 안 지키는 값을 내므로 코드에서 강제(`round_to_krx_tick`).

## What Didn't Work / 주의

- **CLAUDE.md의 SSH 키 경로(`D:\maps\...`)는 틀림** → `D:\ssh_maps\...` 사용.
- `!deploy`는 클린 트리 요구 → dirty면 먼저 커밋/푸시 후 배포해야 함(매번 그렇게 처리).
- 호가단위 코드 수정만으로는 **기존 DB 행이 안 바뀜** → 별도 정규화 스크립트 필요했음.
- 보유 픽(BOUGHT)은 API가 가격 수정을 막지만, 목표/손절은 코드가 매 사이클 평가하는 값(대기 주문 없음)이라 정규화는 안전 → `--include-protected`로 처리.

## Next Steps (미착수 / 후속 후보)

1. **미보유(WATCH/ARMED) 픽의 장중 현재가 미반영** — 현재 일봉 종가만. KIS는 임의 종목 단건 시세 API 없음. pykrx 배치(`scheduler._fetch_intraday_prices`) 활용 또는 추적 루프에서 픽에 last_price 저장 방식 검토.
2. **거래리뷰 `by_strategy` 승/패 집계 버그(범위 밖, 미수정)** — `maps/api/trade_review.py:225-227`의 `known`이 open 포지션 미실현 pnl을 승/패로 포함. 실현 거래만 집계하도록 분리 필요.
3. 사용자가 하이라이트했던 `kis_adapter.py:48` `inquire-balance` 엔드포인트 — 이번 작업과 무관했으나 추가 요청 가능성 있음(잔고/평단 관련). 미확인.
4. 운영 화면 최종 육안 확인(Ctrl+F5) — 체결가/손익비/거래리뷰 변경이 실제로 보이는지 사용자 확인 대기.

## 핵심 파일 맵

- 워치리스트 API: `maps/api/analysis_picks.py` (`_current_prices`, `_broker_live_prices`, `_fill_prices`, `_to_item`, `_rr_ratio`)
- 워치리스트 화면: `templates/analysis_picks.html`
- 거래리뷰: `maps/api/trade_review.py` + `static/js/app.js`(`loadTradeReview`, ~L1490)
- 호가단위: `maps/market/trading_rules.py` (`krx_tick_size`, `round_up_krx_price`, `round_to_krx_tick`)
- 전략매매 브래킷/추적: `maps/ops/scheduler.py` (`_process_strategy_trades`, `sync_broker_state`)
- 정규화 스크립트: `scripts/normalize_pick_tick_prices.py`, `scripts/load_analysis_picks.py`
