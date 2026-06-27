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

최종 출력: 종목별 목표가 / 매수가 / 손절가 / 손익비 / 포지션 사이즈 표.
세 가격(목표가·매수가·손절가)이 모두 있는 종목만 최종 리스트에 포함한다.