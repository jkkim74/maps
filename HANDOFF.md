# HANDOFF

> 작성일: 2026-08-02 (일, KST 자정 직후 · 세션은 8/1 시작) · 작성자: 세션 에이전트 (집 PC, 키 `D:\maps\`)
> 주제 ①: **Korea weak guard 배포** — MIXED를 한국 실측 약세가 부정하면 WEAK로 하향 (마이그레이션 0014).
> 주제 ②: **백테스트 블로그 시리즈 10편** 신규 작성 (`docs/blog_series_backtest/`).
> 주제 ③: **백테스트 콘솔 정비** — 가짜 실행설정 패널 정직화 + 실행 결과 저장 (0015). 배포 사고 2건 수정 포함.
> 이전 핸드오프(매매기록 버그 2건·블로그 11편·구분선 20자, 7/31): git `bea757d` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
운영 DB PostgreSQL. **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.
서버는 **`8db50a9`** 배포 완료 — 00:30:48 KST 기동 `active (running)`, 마이그레이션 **0015까지 적용**.
테스트 592 passed. requirements 변경 없음.

## 이번 세션 커밋 (시간순)

| 해시 | 내용 |
|---|---|
| `312f5a0` | feat: Korea weak guard — MIXED + KOSPI 5·10주선 하회 + (추세강도 ≤35 또는 breadth WEAK) → WEAK 하향. `market_regime_log.korea_weak_guard_applied` 감사 플래그(0014). composite 점수는 미측정 팩터(유동성·심리) 제외 후 실측만 재정규화 |
| `f356baa` | docs: 백테스트 블로그 10편 — 사용법 우선 구성(기능 지도→데이터→전략별 실행→파라미터→결과 읽기→검증 화면→비용→생존자 편향→리플레이→자동 검증). `check_naver_format.py` 전편 통과 |
| `d24950d` | fix: 백테스트 콘솔 실행설정 패널 정직화 — 기간·유니버스·비용이 전부 하드코딩 장식이었다("2015-01~2026-04" 때문에 5~7월이 빠진다는 오해 유발). API가 DB 실측(min/max 일자)·비용 상수를 내려주고 화면이 그걸 표시. 동반 검증 배지·예상 시간 행 삭제 |
| `bea757d` | docs: 핸드오프에 APK 재빌드 완료 표기 (7/31 완료, 사용자 확인) |
| `d0a7197` | fix: 마이그레이션 0014 revision ID 35자 → 28자 (배포 사고 1, 아래) |
| `99d899d` | feat: 콘솔 백테스트 실행 결과 저장 — `backtest_run_log` 테이블(0015). 최근 실행 목록의 원천을 WFA 결과(NET CAGR·거래수 없음 → 영구 빈 컬럼)에서 이 로그로 교체. POST /run이 결과를 저장 |
| `8db50a9` | fix: numpy 스칼라 강제 변환 (배포 사고 2, 아래) |

미커밋으로 남긴 것: `apps/mobile/google-services.json` (Firebase 키 파일, 커밋 대상인지 사용자 결정 대기).

## 배포 사고 2건 (둘 다 해결, 기록 필수)

### 사고 1 — 마이그레이션 revision ID 32자 제한

`0014_market_regime_korea_weak_guard`(35자)가 `alembic_version.version_num`
**varchar(32)** 를 넘어 운영 Postgres에서 UPDATE 실패. 트랜잭션 DDL이라 전체
롤백돼 DB 무손상, 단 systemctl restart 전에 중단돼 구 코드가 계속 돌았다.
ID를 `0014_regime_korea_weak_guard`(28자)로 줄여 재배포.
**앞으로 revision ID는 32자 이내.** 0014·0015 파일에 주의 주석 있음.

### 사고 2 — np.float64는 psycopg2에 못 들어간다

콘솔 백테스트 첫 운영 실행이 INSERT에서
`InvalidSchemaName: schema "np" does not exist` 로 죽었다. 엔진 지표가
pandas 계산 결과(`np.float64`)인데 psycopg2가 변환 못 함.
**SQLite는 np.float64를 받아줘서 로컬 테스트가 못 잡는 부류다.**
집계 지점에서 `float()`/`int()` 강제(`api/backtest.py`), 회귀 테스트는
가짜 엔진 결과를 일부러 np.float64로 시드. DB에 넣는 수치가 pandas/numpy를
거쳤다면 항상 이 변환을 의심할 것.

## 운영 확인 (7/31 핸드오프의 "오늘 안에 확인" 3건 전부 해소)

1. `blog.md` 커밋·배포 — `798f83c`에 포함돼 있었음 (해소)
2. **17:10 검증 잡**: `mock_months=0.0` 사라짐 — donchian_v2 2.0 / pullback_v3 2.0 /
   donchian_v1 1.5 / multi_asset 0.1 (ath_breakout 0.0은 체결 없는 전략이라 정상).
   전 전략 `passed=f` 유지(점수 28.6~48.0 < 60) — 예고대로 정상
3. 18:30 배치: `blog/2026-07-31.txt` 생성, 구분선 정확히 20자 (해소)

콘솔 백테스트 운영 검증: 서버 localhost에서 로그인 쿠키로 실제 실행 —
pullback_v3 30종목, CAGR -0.13% / MDD -5.36% / 샤프 -5.85 / 거래 924건이
`backtest_run_log`에 저장되고 목록에 반환됨.

## What Worked / 주의

- **배포 실패가 alembic 단계에서 나면 서비스는 구 코드로 살아 있다.** 체인이
  `git pull && alembic && restart` 순서라 pull은 됐고 restart는 안 된 상태 —
  당황하지 말고 마이그레이션만 고쳐 재배포하면 된다.
- **PowerShell here-string 커밋은 한 호출에 하나만.** `'@; git add ...; git commit @'...`
  식으로 체이닝했다가 메시지 파싱이 깨져 백테스트 수정분이 HANDOFF 커밋에 섞였다.
  push 전이라 `git reset --soft HEAD~1` 로 분리 복구. 닫는 `'@` 뒤에는 아무것도 잇지 말 것.
- **한 파일에 두 작업이 섞이면 임시 되돌리기로 분리 커밋.** `schemas.py`에 weak guard와
  콘솔 패널 변경이 공존 — 패널 필드를 Edit로 잠시 뺐다가 커밋 후 되살리는 방식이
  hunk 스테이징 없는 환경에서 제일 단순했다.
- **화면의 정적 텍스트는 기능 질문으로 돌아온다.** "기간을 왜 못 바꾸나"의 답이
  "그건 그림이다"였다. 하드코딩 표시를 지금 고치지 않으면 다음 사용자 질문으로 돌아온다.
- 이월 주의(계속 유효): `date.today()`+UTC 저장 컬럼 함정, `order_log` 컬럼명,
  `journalctl | grep -v broker_sync`, analyze 픽과 스케줄러 주문은 다른 파이프라인,
  작업 트리에 다른 세션이 동시에 쓸 수 있으니 `git add -u` 전에 `git status`.

## Next Steps

### 새로 생긴 것

1. 콘솔 "최근 실행 목록"이 이제 콘솔 실행만 쌓는다(WFA 행 제외). 과거처럼 검증 잡
   실행도 보고 싶다는 요구가 나오면 `backtest_run_log`에 source 컬럼을 추가하는 방향.
2. `google-services.json` 커밋 여부 사용자 결정 (Firebase 설정 — 보통은 커밋해도
   되는 파일이지만 정책 확인).
3. 블로그 10편 발행 — 원고는 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.

### 이월 (7/31에서 그대로, 번호 유지)

5. 워치리스트·보유 화면 브라우저 CSS 확인 (네비 1줄, 카드 2열)
6. 픽 만료 가드 로그 — ARMED 픽 0건이라 아직 안 뜸
7. 🔴 **KIS 잔고·주문 페이지네이션 미구현** (`kis_adapter.py:372-394`) — 체결 누락
   유력 원인, `sync_broker_state`가 잘린 잔고로 미체결 SELL을 FILLED로 바꾼다.
   착수점 `scripts/diag_kis_balance.py`
8. 🟡 부분체결이 만료 처리된다 (`expire_pending_orders`)
9. 🟡 매매일지 페어링이 티커 단위 (`trade_review.py:119`)
10. 🔵 **후보 퍼널 재설계 + AI 스코어링** — 계획서 `docs/plans/candidate-funnel-ai-scoring.md`,
    구현 착수 전. 10-1(candidate_snapshot 387k행/143MB 용량)도 이걸로 해소
11. 🟡 후보 생성 누락일 2건 (7/01, 7/17) 잡 실패 로그 확인
12. 🟡 분석 워치리스트 누적 2건뿐 — 게이트 전량 탈락 중
13. `analysis_pick` id=1 CLOSED인데 exit_reason 빈 것
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨
15. ~2026-09-01 `mock_months ≥ 3` 충족 예정. 단 점수 28.6~48.0 < 60이라 승격은
    여전히 차단 — **점수가 실제 병목**
16. 업종 필터 활성화 (`earnings_revision` 0.25 자리표시자)
17. 애드센스 — `maps.magable.kr` 등록 (사용자 계정 작업)
18. 블로그 기획서 수정본 — 원고(시리즈 11편 + 백테스트 10편)를 정본으로 삼는 편이 빠름
19. 이월: KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트 mock화,
    서명 릴리스 APK, `order_log_backup_20260724` DROP 가능

## 핵심 파일 맵 (이번 세션 변경분)

- **Korea weak guard**: `maps/market/regime.py:korea_weak_guard_triggered`(판정),
  `regime_history.py:apply_hysteresis`(적용 지점 — buffer band보다 우선),
  플래그 `market_regime_log.korea_weak_guard_applied`,
  설정 `MAPS_KOREA_WEAK_GUARD_ENABLED`(기본 true)·`MAPS_KOREA_WEAK_TS_THRESHOLD`(35).
  테스트 `tests/test_regime_gate.py`(가드 7건).
- **백테스트 콘솔**: `maps/api/backtest.py` — GET이 패널 실측값
  (`data_start/data_end/max_tickers/cost_summary`)과 `backtest_run_log` 목록 반환,
  POST가 결과 저장(numpy 강제 변환 주의). 모델 `common/models.py:BacktestRunLog`.
  테스트 `tests/test_backtest_api.py` 3건.
- **블로그 원고**: `docs/blog_series_backtest/` 10편 (시스템 소개 11편·전략 9편과 별도 시리즈).
- **마이그레이션**: `0014_regime_korea_weak_guard`(컬럼 추가), `0015_backtest_run_log`
  (테이블 추가) — 둘 다 멱등, 운영 적용 완료. **revision ID 32자 제한.**
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
