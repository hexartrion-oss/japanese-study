# MANUAL

이 리포 운영과 관련된 참고 문서입니다.

## 문제 발생 시 대응 절차

워크플로우가 실패하면 `kwonyh000@naver.com`으로 결과 알림 메일이 오며,
본문에 실행 정보(`run_meta.txt`)와 생성 로그(`run_log.txt`)가 첨부된다.
이 로그의 마지막 `[경고]`/`[오류]` 메시지로 원인을 좁힌다.

### 실패 사유별 대응

- **`[중단] 유효한 문장이 없어 PDF/이메일 전송을 건너뜁니다.`**
  (`get_japanese.py`의 `RuntimeError`) — `fetch_study_lines`가 4회 재시도
  후에도 검증(`validate_sentences`)을 통과한 문장을 얻지 못한 경우다.
  로그에서 반복되는 `[경고]` 사유(문장 수 부족 / 불완전 문장 / A·B 분량
  부족 / 경어 상투구 과다)를 확인한다. 특정 레벨·주제에서만 반복되면
  일회성 Gemini 응답 품질 문제일 가능성이 높으므로 재실행으로 대개
  해결된다.
- **`[Gemini] 모든 모델 실패`** — `_GEMINI_MODELS`의 세 모델(현재
  `gemini-2.5-flash` → `gemini-2.5-flash-lite` → `gemini-3.5-flash`) 모두
  429(quota)/503/기타 오류로 실패한 경우다. quota 초과면 시간을 두고
  재실행, 모델 자체가 폐기·이름 변경되었다면 `_GEMINI_MODELS` 목록을
  현재 사용 가능한 모델명으로 갱신해야 한다.
- **`RSS crawl failed (...)`** — NHK RSS 피드 접근 실패. `crawl_titles`는
  실패한 피드를 건너뛰고 나머지로 계속 진행하며, 전부 실패하면
  `fetch_study_lines`가 레벨별 폴백 주제로 넘어가므로 대부분 자동
  복구된다. 모든 피드가 지속적으로 실패하면 NHK RSS URL 변경 여부를
  확인한다.
- **`Japanese font not found`** (`FileNotFoundError`, `find_font()`) —
  CI에서는 `fonts-ipafont-gothic` 설치 스텝이 실패했거나 폰트 파일 경로가
  바뀐 경우다. 로컬 실행이면 `JAPANESE_FONT_PATH` 환경변수로 `.ttf` 경로를
  직접 지정한다.
- **`Email credentials not set` / `App password placeholder`** — Gmail
  관련 Secrets(`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`)가 비어 있거나
  플레이스홀더 값(10자 미만 또는 "입력" 포함)인 경우다. 리포지토리
  Secrets 설정을 확인한다.
- **결과 알림 메일 자체가 오지 않음** — 워크플로우 실행이 아예 시작되지
  못한 경우(예: 스케줄 트리거 자체가 실패)다. Actions 탭에서 워크플로우
  실행 이력을 직접 확인해야 한다.

### 자주 겪을 수 있는 문제

- **경어 카테고리에서 자사 인물에게 존경어를 쓰는 오류**가 보이면
  `write_story_with_gemini`의 사내향(`doc_kind == "internal"`) 지시문이
  제대로 전달됐는지, 또는 Gemini가 지시를 어겼는지 로그의 `[시드] 경어
  문서` 라인으로 사내/사외 구분이 맞게 뽑혔는지 먼저 확인한다.
- **같은 레벨이 며칠 연속 나오는 것처럼 보임** — `pick_level`의 순환
  로직은 날짜만으로 결정되므로 버그가 아니라면 대개 주 경계(JPT주/JLPT주
  전환) 근처의 정상 동작이다. `get_week_of_month` 계산이 의도한 주와
  일치하는지부터 확인한다.
- **수동 실행 결과 메일이 안 옴** — 수동 실행 시 학습 자료 메일은
  `hexartrion@gmail.com` 단독 수신(`MANUAL_MAIL_TO`)이다. 스팸함도
  확인한다. 실패 여부는 항상 `kwonyh000@naver.com` 알림 메일로 확인
  가능하다(`if: always()`).

## 작업 지시서 작성 원칙

이 리포에 대한 작업 지시서(claude.ai 대화에서 Claude Code로 전달하는 문서)는
토큰을 최소화하는 방향으로 작성한다.

- 배경은 한두 줄 요약으로 끝낸다. 이미 나눈 대화 내용을 다시 풀어 쓰지 않는다
- 코드는 변경되는 부분만 제시한다. 새 함수를 통째로 재기재하지 않는다
- 체크리스트·검증 절차는 핵심 1~2개만 남긴다
- 소규모 변경에는 매 작업 승인을 요구하지 않는다. 여러 작업이 얽힌 큰
  변경에만 단계별 승인을 명시한다
