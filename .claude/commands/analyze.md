---
description: MAPS 전체 매매 분석 파이프라인 실행
---

MAPS 매매 분석을 다음 순서로 실행한다. 각 단계는 해당 subagent를
호출하고, 출력 JSON을 다음 단계 입력으로 넘긴다. 한 단계라도
JSON 스키마 검증에 실패하면 즉시 중단하고 원인을 보고한다.

1. market-regime subagent 호출 → 시장 국면 판단
2. strategy-selector subagent 호출 (1의 결과 입력)
3. sector-rotation subagent 호출
4. stock-screener subagent 호출
5. margin-of-safety subagent 호출 (게이트: 미통과 종목 제거)
6. technical-analysis subagent 호출
7. trade-planner subagent 호출
8. **분석 워치리스트 적재** — trade-planner JSON을 SCR19 워치리스트(`analysis_pick`)에
   적재한다. 자동주문 파이프라인(candidate_snapshot)과 분리된 별도 보관소이며,
   화면에서 종목 클릭 시 종합분석 딥다이브를 재실행하는 출발점이 된다.
   프로젝트 루트에서 trade-planner JSON을 stdin으로 넘겨 실행한다:

   ```bash
   echo '<trade-planner JSON>' | python scripts/load_analysis_picks.py \
       --regime <1단계 regime: strong|mixed|weak> \
       --context "<2·3단계 선정 전략 / 섹터>"
   ```

   - 1단계 market-regime의 regime, 2·3단계의 선정 전략·섹터를 `--regime`/`--context`로 전달한다.
   - 적재 결과(생성 ID·건수)를 사용자에게 보고한다.
   - 서버가 떠 있으면 동등하게 `POST /api/v1/analysis-picks`(body `{"picks":[...]}`)로도 적재 가능하다.

   **최종 종목이 0개여도 반드시 로더를 호출해 실행기록을 남긴다.** (0종목 정상완료와
   cron 실패를 구분하기 위함 — `analysis_run` 감사 테이블에 `picks_count=0` row가 남는다.)
   빈 plan은 `--allow-empty`로 넘기고, 어떤 게이트에서 전량 탈락했는지 `--note`에 요약한다:

   ```bash
   echo '{"trade_plan": []}' | python scripts/load_analysis_picks.py \
       --allow-empty \
       --regime <1단계 regime> \
       --context "<2·3단계 선정 전략 / 섹터>" \
       --candidates-count <4단계 스크리닝 후보 수> \
       --note "<예: 안전마진 14→2, R:R 게이트 2→0 전량 탈락>"
   ```

최종 출력: 종목별 목표가 / 매수가 / 손절가 / 손익비 / 포지션 사이즈 표.
세 가격(목표가·매수가·손절가)이 모두 있는 종목만 최종 리스트에 포함한다.
적재 후 사용자에게 "분석 워치리스트(SCR19)에서 종목 클릭 시 딥다이브 확인 가능"을 안내한다.
0종목인 경우 "워치리스트 0종목(실행기록 적재 완료)"임을 명시한다.
