---
description: 일일 다이제스트로 매매 기록 블로그 글 작성
---

오늘의 MAPS 매매 기록 블로그 글을 작성한다.

## 입력

`$ARGUMENTS`로 다이제스트 JSON 파일 경로가 넘어온다(예: `/opt/maps/logs/digest_2026-07-27.json`).
없으면 `logs/` 아래 가장 최근 `digest_*.json`을 쓴다.

**이 JSON이 유일한 사실 출처다.** Read로 읽어서 그 안의 값만 쓴다.

## 절대 규칙

1. **JSON에 없는 숫자를 쓰지 않는다.** 계산해서 만들어내지도 않는다. 단, JSON에 있는
   두 값의 차이·비율(예: `fill_price`와 `order_price`의 차이)은 계산해도 된다.
2. **`measured: false`인 팩터는 "미측정"으로만 적는다.** 점수를 인용하거나 해석하지 않는다.
   이 값들은 실제 데이터가 아니라 중립 자리표시자(50)다. 이걸 오늘의 투자심리인 것처럼
   쓰면 없는 분석을 지어낸 글이 된다.
3. **`null`은 "데이터 없음"이다.** 추정치로 메우지 않는다.
4. **`errors` 배열이 비어 있지 않으면** 해당 섹션을 "수집 실패"로 명시한다.
5. 뉴스·외부 정보를 검색하지 않는다. 시장 맥락은 `market_context`(외부 리포트 발췌)만 쓴다.
6. 종목 추천으로 읽히는 표현을 쓰지 않는다. 시스템이 무엇을 왜 했는지 기록할 뿐이다.

## 문체

객관적이고 냉철하게. 손실을 미화하지 않고 수익을 과장하지 않는다.
"~할 것으로 기대된다" 같은 전망 대신 "규칙이 X였고 조건이 Y여서 Z했다"로 쓴다.
시스템이 틀렸으면 틀렸다고 쓴다.

## 구성

```markdown
---
ref_date: <ref_date>
regime: <market.regime>
generated_at: <generated_at>
---

# <YYYY년 M월 D일> 매매 기록

<두세 문장 요약 — 오늘 무슨 일이 있었는지>

## 1. 시장 국면
regime / raw_regime / weekly_trend / vol_regime / breadth_pct / entry_limit_ratio,
그리고 `floor_applied`가 true면 "빌려온 MIXED"라는 뜻이므로 반드시 언급.
팩터는 `measured: true`인 것만 수치로 쓰고, 나머지는 "미측정(피드 미연결)"로 한 줄 처리.

## 2. 강세 업종
`sectors.selected`의 업종별 score·momentum·reason. `overheated`가 있으면 과열 경고를 적는다.

**`applied_to_trading: false`면 반드시 첫 줄에 "관측 전용 — 후보 선정에는 적용되지 않음"을
명시한다.** 적용된 것처럼 쓰면 오늘 매매를 잘못 설명하게 된다.

`placeholder_inputs`가 비어 있지 않으면 그 입력들이 중립값(50)이라 점수가 중앙으로
눌려 있다는 점을 한 줄로 적는다. `selector`가 `legacy`면 자리표시자 없이 기간 수익률
순위만 쓴 것이므로 그렇게 적는다.

## 3. 오늘의 전략
`strategies`에서 `active: true`인 전략과 그 이유(preferred_regimes에 오늘 국면이 포함),
`active: false`인 전략은 `block_reason`을 그대로 인용한다. `stage`도 함께 적는다.

## 4. 후보 종목
`candidates` 상위 몇 종목. final_score와 `score_reason`, ts_bucket을 쓴다.
`ai_analysis_memo`·`ai_contrarian_thesis`/`anti_thesis`·`valuation_margin_reason`이 있으면
근거로 인용한다. `excluded_reason`이 있는 종목은 왜 빠졌는지 적는다.
`candidate_total` 대비 `candidate_excluded` 비율도 한 줄 언급.

## 5. 오늘의 체결
`executions`를 매수/매도로 나눠 정리. 매수는 `entry_rationale`, 매도는 `exit_reason`을
반드시 적는다. `fill_price`와 `order_price` 차이가 크면 그것도 쓴다.
`exit_reason`이 null이면 "청산 사유 미기록"으로 적는다.
체결이 없으면 "체결 없음"이라고 쓰고 이유를 3번 섹션에서 연결한다.

## 6. 내일 주문 예정
`tomorrow_orders`의 `items`. `skipped: true`인 항목은 `skip_reason`을 적는다.
`data_stale: true`면 데이터가 오래됐다는 경고를 먼저 쓴다.
`live_eligible: false`는 모의 단계 전략이라는 뜻이다.

## 7. 시장 맥락
`market_context` 발췌를 인용하고 출처가 외부 리포트임을 밝힌다. 비어 있으면 섹션을 생략한다.
```

## 출력

Write로 `blog/<ref_date>.md`에 저장한다(경로는 `$ARGUMENTS`의 두 번째 인자가 있으면 그것).
저장 후 파일 경로와 글자 수만 보고한다.
