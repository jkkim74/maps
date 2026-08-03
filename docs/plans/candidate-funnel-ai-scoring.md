# 후보 퍼널을 신호 기반으로 재설계하고 AI 스코어링을 붙인다

> 상태: **Phase 1(퍼널) 구현 완료 — Phase 2(AI) 착수 전** (2026-08-03 개정)
> 최초 작성 2026-07-31, 기준 커밋 `6395666` / 테스트 기준선 581.
> 개정 시점 기준 커밋 `d913cf5`, 테스트 기준선 **634 → 640 passed**.
> 승인 범위: 퍼널 재설계 + AI 재설계. 저장은 신호 종목 전수 + 전략별 상위 N.
> 모델은 Claude Opus 5. 성과 A/B 검증은 이번 범위에서 제외.

## 2026-08-03 재검토에서 정정한 것

원안을 `d913cf5` 기준으로 대조한 결과. **진단은 유효했고**(아래 Context의 근거를 7/31
실측으로 재확인), 다음 넷을 고쳤다:

1. **마이그레이션 체인** — 원안의 `down_revision = "0013_order_log_entry_atr"`는 틀렸다.
   실제 head는 `0017_bt_run_period_verdict`. 새 마이그레이션은 `0018_candidate_entry_signal`.
2. **테스트 기준선** — 581 → 634.
3. **"AI를 켜면 유니버스를 두 번 훑는다 → 하루 20,160회"** — AI가 꺼진 현재
   `ai_candidate_top_n=0`이라 pre-scoring 루프를 통째로 건너뛴다. 실제는 **10,080회**였다.
   이중 순회 제거는 "지금 절반"이 아니라 "AI를 켤 때 2배가 되는 구조를 미리 없앰"이다.
4. **Bedrock 호출자가 둘이다** — 원안은 `technical_scorer.py`만 다뤘으나
   `contrarian_analyzer.py`도 같은 `boto3.invoke_model` + 같은 `aws_bedrock_model_id`를
   공유한다. 한쪽만 옮기면 모델 ID 형식이 갈린다(`us.anthropic.*` vs Mantle `anthropic.*`).
   → **Phase 2에서 두 모듈을 함께** 옮긴다.

착수 전 확인사항이던 "운영 `.env`의 `MAPS_AI_CANDIDATE_TOP_N`"은 **미설정**으로 확인됐다
(기본 5). 의미를 바꿔도 운영 오버라이드 조정은 필요 없다.

## Phase 1 구현 결과 (완료)

| 변경 | 위치 |
|---|---|
| 신호 계산 정본 분리 | `scheduler.py` `_signal_from_frame(strategy_id, frame)` 신규, `_latest_strategy_signal`은 DB 래퍼로. 봉 수는 `_SIGNAL_LOOKBACK_BARS=400` 상수로 공유 |
| 종목 컨텍스트 1회 계산 | `scheduler.py` `TickerContext` + `_build_ticker_contexts()`. `generate_candidates`가 전략 루프 **밖에서** 호출 |
| 저장 정책 | 신호 종목 전수 ∪ 나머지 중 `final_score` 상위 N (`maps_candidate_snapshot_top_n`, 기본 50) |
| 스키마 | `candidate_snapshot.entry_signal` (nullable bool) + `0018_candidate_entry_signal` |
| 단계별 카운터 | 전략별 INFO 로그 `후보 저장 [id]: universe=.. signals=.. stored=.. dropped=..` + 잡 details 의 `universe_size`/`signal_count`/`dropped_count` |

테스트: `tests/test_candidate_funnel.py` 6건 신규. `_save_candidate_snapshot` 반환값이
`len(universe)` → **저장된 행 수**로 바뀌었다(`saved_count` 의미가 정확해짐).

## Context

"후보에 종목이 너무 많다"는 지적에서 출발해 후보 생성 알고리즘을 읽었다.
문제는 개수가 아니라 **후보의 정의**였다.

현재 후보 생성은 **전략 신호를 전혀 보지 않는다.** 유니버스 1,260 종목 전체에
대해 유동성·추세 점수만 매겨 저장하고, 진입 신호(`entry_signal`)는 다음 날
08:55 주문 시점에야 계산한다. 그래서 하루 **1,260 × 8전략 = 10,080행**이 쌓이고
(누적 387,141행 / 143MB), 그중 실제로 살 수 있는 종목이 몇 개인지는 저장된
어디에도 없다.

점수 산식도 전략과 무관하다. 2026-07-30 donchian_v2 상위를 보면:

```
000660 SK하이닉스   점수 59.4  (유동성 100.0, 추세  18.7)
051900 LG생활건강   점수 50.6  (유동성   1.2, 추세 100.0)
051905 LG생활건강우 점수 50.0  (유동성   0.0, 추세 100.0)   ← 우선주
246710 티앤알바이오팹 점수 50.0  (유동성   0.0, 추세 100.0)
```

**AI 스코어링 대상(`maps_ai_candidate_top_n`, 기본 5)은 이 랭킹의 상위 5개다.**
즉 지금 AI를 켜면 초대형주 1개와 유동성이 사실상 0인 종목 4개를 분석한다.
"제대로 된 스코어링"이 안 되는 원인은 모델이 아니라 **대상 선정**이다.

비용 구조도 확인했다. AI를 켜면 `_save_candidate_snapshot`이 **유니버스를 두 번**
훑는다(대상 선정용 pre-scoring 루프 + 본 루프). 그리고 이 함수는 전략마다 한 번씩
호출되는데 매번 종목별 OHLCV를 **전체 기간** 다시 읽는다(`to_dataframe`에 `start`
없음). 하루 OHLCV 로드가 **10,080회**, AI를 켜면 20,160회다. Bedrock 요금보다
이쪽이 크다.

---

## 설계

퍼널을 이렇게 바꾼다.

```
유니버스 1,260
  ① OHLCV 1회 로드 (전략 8개가 공유, 400봉으로 제한)   → DB 작업 8분의 1
  ② 종목별 추세강도·밸류에이션 1회 계산               → 중복 계산 제거
  ③ 전략별 진입 신호 계산 (신호 게이트)               → 후보 10,080 → 수십 건
  ④ 신호 종목 전수 + 전략별 상위 N 저장               → 하루 수백 행
  ⑤ 신호 종목 중 상위 K개만 AI 스코어링               → 대상이 비로소 의미를 갖는다
```

핵심은 ③이다. 지금은 "유동성 좋고 추세 강한 종목"을 후보라고 부르는데,
바꾸면 "이 전략이 오늘 사겠다고 말한 종목"이 후보가 된다.

> 📌 신호를 후보 생성 시점으로 옮겨도 주문 시점 재검사는 **그대로 둔다.**
> 스냅샷 신선도 가드(`_order_candidates`)와 08:55 재계산은 오래된 신호로
> 주문이 나가는 것을 막는 장치라 신호 게이트와 목적이 다르다.

---

## 변경 내용

> 아래 1~3은 **Phase 1 로 구현 완료**. 4~5(AI)는 착수 전이며, 위 "재검토에서 정정한 것"의
> 4번(호출자 둘)과 Opus 5 관련 주의사항을 반드시 함께 적용할 것.

### 1. OHLCV·지표 계산을 한 번으로 (`maps/ops/scheduler.py`)

`generate_candidates`(380~430행)가 `for strategy_id in _RUNNABLE_STRATEGIES`
루프 안에서 `_save_candidate_snapshot(..., result.universe, ...)`를 호출한다.
루프 **밖에서** 종목별 컨텍스트를 한 번 만들어 넘긴다.

- 신규 `_build_ticker_contexts(db, universe, ref_date)` → `dict[str, TickerContext]`
  (OHLCV 프레임, 추세강도, ts_bucket, ATR14, 종가, 밸류에이션 결과)
- `to_dataframe`에 **`start`를 넘긴다.** 지금은 전 기간을 읽는다.
  `_latest_strategy_signal`이 쓰는 400봉과 맞춘다 — 워밍업 차이로 ATR이
  어긋나는 함정을 이미 겪었다(CLAUDE.md 손절 항목).
- `_save_candidate_snapshot` 시그니처에 `contexts` 추가. 내부의 OHLCV 로드·
  추세강도·밸류에이션 계산 블록을 컨텍스트 조회로 대체한다.
- **AI 대상 선정용 pre-scoring 루프(1282~1344행)는 삭제한다.** 같은 계산을
  두 번 하는 구조 자체가 없어진다.

### 2. 신호 게이트

`_latest_strategy_signal`(2370~)이 이미 400봉을 읽어 `generate_signals`를 돌리고
마지막 행에서 `entry_signal`/`atr14`를 꺼낸다. 이걸 **프레임을 받는 함수와 DB에서
읽는 래퍼로 분리**해 후보 생성과 주문 시점이 같은 코드를 쓰게 한다.

```
_signal_from_frame(strategy_id, frame)  ← 신규(정본). 후보 생성이 호출
_latest_strategy_signal(db, ...)        ← 기존 시그니처 유지, 내부에서 위를 호출
```

값이 두 곳에서 계산되면 조용히 어긋난다 — 손절가에서 이미 겪었다.

### 3. 저장 정책

- `candidate_snapshot`에 `entry_signal` 컬럼 추가(nullable bool).
  마이그레이션은 `alembic/versions/0013_order_log_entry_atr.py`를 본뜬다
  (같은 nullable 추가 + `sa.inspect` 멱등 가드 + `batch_alter_table` downgrade).
  `down_revision = "0013_order_log_entry_atr"`.
- 저장 대상 = **신호 있는 종목 전수** ∪ **전략별 `final_score` 상위 N**
  (신규 설정 `maps_candidate_snapshot_top_n`, 기본 50).
- 제외된 종목 수를 `CollectionLog` note에 남긴다. "오늘 왜 후보가 없었나"를
  사후에 답할 수 없게 되면 6/23 약세장 때와 같은 오진이 반복된다.
- 업종 필터 제외 행(`score_type="SECTOR_FILTER"`)은 지금 유니버스 전량을
  저장한다. 이것도 상위 N 규칙에 포함시켜 건수만 로그로 남긴다.

### 4. AI 대상 선정

`maps_ai_candidate_top_n`(기본 5)은 지금 **전략마다** 적용돼 하루 최대 40콜이다.
의미를 바꾼다.

- 대상 = **신호 있는 후보** 중 `final_score` 상위, **하루 전체 기준** 상한.
- 설정 이름을 `maps_ai_daily_call_limit`으로 바꾸고 기본 10. 옛 이름은
  하위호환으로 읽되 경고를 남긴다.
- 신호가 0건인 날은 AI 호출도 0건이다. 지금은 신호와 무관하게 항상 40콜이었다.

### 5. AI 스코어링 재설계 (`maps/ai/technical_scorer.py`)

**a. 프롬프트를 정적/동적으로 분리한다.** 현재 `_PROMPT_TEMPLATE`은 2행에
`{ticker}({name})`를 끼워 넣는다. 프롬프트 캐싱은 **접두사 일치**라 종목명이 앞에
오면 재사용 가능한 접두사가 **한 글자도 없다.**

```
system  : 역할, 채점 기준, 판정 규칙, JSON 스키마      ← 종목과 무관, 캐시 대상
user    : 티커·종목명·지표·최근 30봉                    ← 매 호출 변동
```

**b. 캐시 최소 길이를 넘겨야 한다.** 현재 정적부는 약 500자다. Opus 5의 캐시
최소 프리픽스는 **512토큰**이라 지금 그대로면 캐싱이 조용히 안 걸린다(에러 없이
`cache_creation_input_tokens: 0`). 채점 기준을 실제로 채워 넘긴다 — 점수 구간
정의, 지지·저항 판정 규칙, 한국 시장 특성(상하한가·호가단위), 데이터가 부족할 때의
반환 규칙. 이건 캐싱을 위한 패딩이 아니라 **스코어링 품질 자체를 위한 내용**이다.
작성 후 `count_tokens`로 512 초과를 확인한다.

**c. `cache_control`을 명시한다.** Bedrock은 **자동 캐싱을 지원하지 않는다.**
마지막 system 블록에 `{"type": "ephemeral"}`를 직접 붙인다.

**d. 구조화 출력으로 바꾼다.** 지금은 "JSON만 응답하세요" 지시 + 수동 파싱이라
파싱 실패가 곧 점수 없음이다. `output_config.format`(json_schema)은 Bedrock에서
지원되므로 스키마로 강제한다.

**e. 클라이언트·모델.** 현재 boto3 `invoke_model`에 body를 직접 조립한다.
Anthropic SDK의 Bedrock 클라이언트로 바꾼다.

```
from anthropic import AnthropicBedrockMantle
client = AnthropicBedrockMantle(aws_region=...)
model  = "anthropic.claude-opus-5"        # Bedrock은 anthropic. 접두사
```

`requirements.txt`에 `anthropic[bedrock]` 추가 → **배포 시 `pip install -r
requirements.txt` 필수**(CLAUDE.md 배포 노트).

> 🔴 **Opus 5는 thinking이 기본 ON이고 `max_tokens`가 thinking과 응답을 함께
> 제한한다.** 현재 `max_tokens=512`를 그대로 두면 응답이 잘린다.
> `max_tokens`를 4000으로 올리고 `output_config={"effort": "low"}`로 깊이를
> 조절한다. `thinking: {"type": "disabled"}`로 끄는 쪽은 택하지 않는다 —
> Opus 5에서 thinking을 끄면 `<thinking>` 태그가 응답에 새는 사례가 있고,
> 하루 수십 콜 규모에서 아낄 금액이 몇 달러다.

`temperature=0.1`은 **삭제한다.** Opus 5는 샘플링 파라미터를 받지 않고 400을 낸다.

---

## 테스트

수정 전 소스에서 실패하는지 `git stash`로 확인한다.

| 파일 | 추가할 테스트 |
|---|---|
| `tests/test_candidate_snapshot_scheduler.py` | 신호 없는 종목이 저장되지 않는다 / 신호 있는 종목은 상위 N 밖이어도 저장된다 / 제외 건수가 로그에 남는다 |
| 신규 `tests/test_candidate_funnel.py` | 유니버스 N종목·전략 8개에서 `to_dataframe` 호출이 **N회**다(8N회가 아니라) — 카운팅 스텁으로 고정 |
| `tests/test_ai_technical_scorer.py` | system 프리픽스가 **종목이 달라도 바이트 단위로 동일**하다(티커가 프리픽스로 새어 들어오는 회귀를 잡는다) / `cache_control`이 마지막 system 블록에 붙는다 / 샘플링 파라미터를 보내지 않는다 |
| 〃 | AI 호출 실패 시 기존대로 점수 유지(None 반환) — 기존 동작 고정 |

기준선: **581 passed** (2026-07-31).

---

## 검증

1. **로컬**: `alembic upgrade head`를 빈 SQLite와 컬럼 선반영 상태 양쪽에서 확인.
2. **후보 생성 실측** — 배포 후 수동 실행하고 행 수·소요시간을 이전과 대조한다.
   ```
   sudo -u postgres psql -d maps -c "SELECT ref_date, count(*), \
     count(*) FILTER (WHERE entry_signal) AS signals \
     FROM candidate_snapshot GROUP BY 1 ORDER BY 1 DESC LIMIT 3;"
   ```
   직전 거래일 10,080행 → 수백 행이어야 한다. 신호 건수가 **0이면 게이트가
   과하게 잠긴 것**이므로 롤백 판단 지점이다.
3. **AI는 스테이징 성격으로 먼저 켠다.** `maps_ai_technical_scoring_enabled=true`
   + `maps_ai_daily_call_limit=3`으로 하루 돌려보고, 로그에서
   `cache_read_input_tokens`가 **2번째 호출부터 0이 아닌지** 확인한다.
   0이면 캐싱이 안 걸린 것이다(프리픽스가 512토큰 미만이거나 접두사가 흔들린다).
4. **점수가 실제로 바뀌는지** — `candidate_snapshot.ai_technical_score`가 채워지고
   `final_score`가 AI 가중치(상한 0.20)만큼 움직이는지 대조한다.
5. 08:55 주문 사이클이 신호 게이트 이후에도 정상 동작하는지 다음 거래일에 확인한다.

---

## 주의

- 🔴 **신호 게이트가 후보를 0으로 만들 수 있다.** 6/23~ 약세장에서 후보가 0건이던
  전례가 있고, 그때는 국면 차단이 원인이었다. 이번엔 게이트가 하나 더 늘어나므로
  0건일 때 **어느 단계에서 0이 됐는지**(유니버스 / 국면 / 신호 / 점수) 로그로
  구분되게 해야 한다. 구분이 안 되면 또 "수집이 고장 났나"부터 뒤지게 된다.
- 🔴 **`maps_ai_candidate_top_n`의 의미가 바뀐다.** 전략별 → 하루 전체.
  운영 `.env`에 값이 설정돼 있으면 의도와 다르게 동작하므로 배포 전 확인한다.
- ⚠️ **`requirements.txt`가 바뀐다.** 배포 시 `pip install -r requirements.txt`를
  빠뜨리면 기동이 깨진다.
- ⚠️ Bedrock은 **자동 프롬프트 캐싱과 Batch API를 지원하지 않는다.** 캐싱은
  `cache_control` 명시로만 되고, 배치 50% 할인은 선택지가 아니다.
- ⚠️ 저장 정책을 바꿔도 **과거 387K행은 그대로 둔다.** 보존 정책은 별건이다.
- 📌 이번 변경은 **AI가 좋은 종목을 고르는지까지는 검증하지 않는다.** 대상 선정과
  호출 구조만 바로잡는다. 성과 검증(A/B)은 범위에서 뺐다.
