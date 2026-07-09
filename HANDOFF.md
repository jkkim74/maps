# HANDOFF

> 작성일: 2026-07-09 (KST) · 작성자: 이전 세션 에이전트
> 주제: 운영 서버 상태 점검 + stock-report KQ150.KS 심볼 교체
> 이전 핸드오프(analysis-picks 500 + KIS 예외 래핑 수정, 7/05 집 PC 세션): git 이력 `f1e2db2`의 HANDOFF.md 참고.

## Goal

운영 서버(magable.kr)가 정상 구동 중인지 점검하고, 점검에서 발견된 유일한 조치 대상이던
**yfinance `KQ150.KS` 404 에러**(매일 15:01 stock_report 잡)를 해결한다.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://magable.kr`.
브로커는 **KIS 모의투자(VTS)** 계좌 `50185813`. 운영 DB는 PostgreSQL(`sudo -u postgres psql -d maps`).
**SSH 키 경로는 PC마다 다름**: 이 PC(회사) `D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem`,
집 PC `D:\maps\...`. CLAUDE.md에는 집 PC 기준으로 적혀 있음.

## Current Progress

### 운영 점검 결과 (2026-07-09 오전) — 정상

- `maps` systemd `active (running)`, 메모리 ~240MB, load ~0. `https://magable.kr` HTTP 200.
- 일일 파이프라인(수집 2,765종목 → 후보 → 검증 → 08:55 order_cycle → 15:35 EOD) 최근 2거래일 전부 success.
- KIS paper 계좌 총자산 ~8,560만원, 보유 004490 전략매매 추적 중(목표 54,900 / 손절 46,800).
- **7/05 핸드오프의 "월요일 장중 KIS 복구 확인" 항목은 사실상 확인됨** — 장중 broker_sync
  `sync_errors: 0`, 현재가 갱신 정상. 장외 시간대에만 KIS 모의투자 지연(90020000)으로 `sync_errors: 1`.
- **weak 장세 지속**: 후보는 저장되나 8개 전략 전부 주문 차단(`preferred_regime_mismatch:weak` 등)
  — `d5f2fa8`(regime gate 개편: 관찰은 항상, 주문만 차단) 의도대로 동작. 검증 승격 통과 0건도 게이트 정상 작동 결과.

### KQ150.KS → 229200.KS 교체 (완료, 운영 반영됨)

- 위치는 **로컬 저장소가 아니라 서버의 외부 소스 `/opt/stock_report`** — `report_generator.py:734`,
  `getUpAndDownReport.py:30`의 `KOSDAQ150_TICKER`. **이 디렉터리는 git 저장소가 아님** → 서버 직접
  편집(사용자 승인 받음), 백업 `*.bak-20260709` 생성.
- 대체 심볼 `229200.KS`(KODEX 코스닥150 ETF)는 같은 소스 `marketSummary.py`가 이미 코스닥150 대용으로
  사용 중. 신호 계산이 일간수익률·ATR%·장대음봉이라 ETF OHLC로 충분.
- `maps/stock_report/runner.py`가 이 모듈을 **in-process import(캐시)** 하므로 수정 후
  `sudo systemctl restart maps` 필요 → 10:58 KST 재시작, 1분 뒤 broker_sync `sync_errors: 0` 재개 확인.
- 교체 전 `229200.KS` yfinance 조회 검증(30행), 교체 후 `compute_kosdaq_signals()` 실값 반환 확인
  (이전에는 404로 전부 NaN 폴백). `KOSPI200.KS`는 에러 없어 유지.

### 그 외 (이전 handoff들 정리)

2026-06-29 handoff의 모바일 Next Steps 5건은 전부 완료(APK 서명 `8db0de7`, FCM `39c8e68`,
추이 차트 `b1a59e4`, 드릴다운·App.tsx 분리 `224cd9e`). 7/05 집 PC 세션이 KIS 예외 래핑
수정(`613c340`)·브로커 장애 폴백(`df44d04`)을 배포함.

## What Worked

- **로그 grep으로 에러 발생 시각(매일 15:01)과 stock_report 잡을 연결** → 로컬 repo에 KQ150이 없어도
  `/opt/stock_report`를 grep해서 위치 특정.
- **교체 전 검증 → 편집 → 기능 검증 → 재시작 → broker_sync 재개 확인**의 순서. 임시 셸 검증 시
  `MPLBACKEND=Agg`로 report_generator import 가능.
- 서버 직접 수정은 자동 승인 모드에서 권한 정책상 차단됨 → 사용자에게 옵션 제시 후 승인 받아 진행.

## What Didn't Work / 주의

- **PowerShell로 원격 python -c 인라인 실행은 이스케이프 지옥** — Bash 도구 + 작은따옴표/heredoc 사용.
- `/opt/stock_report`에서 임시 셸로 import 시 "KRX 로그인 실패(KRX_ID/KRX_PW 미설정)" 출력 — 모듈
  import 부수효과일 뿐 이번 수정과 무관. 해당 자격증명 env var는 로컬 `.env`에 있음(값은 커밋 금지).
- `apps/mobile/google-services.json`이 untracked로 존재(Firebase 키 포함) — **커밋 금지**.
  파일 지정 add 또는 `git add -u`만 사용.
- 이 저장소는 **집 PC와 회사 PC 두 곳에서 작업됨** — 푸시 전 `git fetch`로 원격 선행 커밋 확인
  (이번에도 non-fast-forward로 rebase 병합이 필요했음).

## Next Steps

1. **오늘 15:01 stock_report 잡 로그 확인** — `sudo journalctl -u maps --since "15:00" | grep -i "yfinance\|KQ150\|229200"`
   으로 404 소멸 최종 확인 (교체 검증은 끝났으니 형식적 확인).
2. **stock_report supply 타입 실패 조사** — run_id 163 "리포트 생성 실패: 조건 미충족 또는 데이터 없음"
   이 반복성인지 확인 (`stock_report_runs` 테이블).
3. **KIS 모의투자 지연(90020000) 경고** — 장외 시간 일별 체결 동기화에서 `sync_errors: 1` 반복.
   실질 영향 없으나 장외 시간대 호출 스킵 처리 여지 (`df44d04`가 폴백·24h 알림은 이미 추가함).
4. **매도 만료율 높음 조사** (이월, 미착수) — order_log에서 매도 주문 만료 패턴 분석.
5. `/opt/stock_report`가 git 관리 밖이므로 **백업/버전 관리 방안 검토** (예: 서버에서 git init 또는 로컬 미러).
6. **네트워크 의존 테스트 mock 처리** (7/05 handoff 이월) — conftest에서 broker mock 강제로 환경 편차 제거.
7. **펀더멘털 백필 재개 예정일(2026-06-22) 경과** (이월) — 상태 확인 필요(메모리 노트 참고).

## 핵심 파일 맵

- stock_report 연동: `maps/stock_report/runner.py`(sys.path로 `/opt/stock_report` import, `MAPS_STOCK_REPORT_PATH`).
- 서버 소스(서버에만 존재): `/opt/stock_report/report_generator.py`, `getUpAndDownReport.py`, `marketSummary.py`.
- regime gate: `maps/market/regime.py`, 주문 차단 사유는 candidate_generation 잡 로그의 `strategies_blocked`.
- 직전 코드 변경(7/05 세션): `maps/execution/kis_adapter.py`(토큰/해시키 예외 래핑),
  `maps/api/analysis_picks.py`(except 확대), `tests/test_kis_adapter.py`(회귀 테스트).
- 운영 접속: `ssh -i <PC별 키 경로> ubuntu@3.37.117.246`, 앱 루트 `/opt/maps`.
