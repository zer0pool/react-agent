# 프롬프트 개선 계획

## 현재 문제점 요약

| # | 문제 | 영향 |
|---|------|------|
| 1 | 도메인 컨텍스트 없음 (notebook/BQ 구분 불가) | 분류 정확도 저하 |
| 2 | error_id 형식 불일치 (E001 vs ERR-GCP-403 vs SCHEMA_MISMATCH) | 결과 집계 불가 |
| 3 | category 목록 미정의 (`etc.`로 끝남) | LLM이 임의 카테고리 생성 |
| 4 | UNKNOWN 판정 기준 단순 (confidence < 0.7 → 무조건 UNKNOWN) | 원인 정보 손실 |
| 5 | 도구 사용 지침 모호 (언제, 어떤 쿼리로 검색할지 없음) | 검색 품질 저하 |
| 6 | Reviewer 기준 추상적 (항상 피드백 → refinement 루프 과잉) | 불필요한 LLM 호출 증가 |

---

## 개선 방향

### 1. category 고정 목록 정의 ★ 최우선

LLM이 아래 목록에서만 선택하도록 강제합니다.
집계/분석 시 일관성이 즉시 확보됩니다.

```
PERMISSION_ERROR    권한 부족 (403, IAM)
SCHEMA_MISMATCH     스키마 불일치 (BQ insert, DataFrame 컬럼)
RESOURCE_EXHAUSTED  OOM, 할당량 초과, Disk Full
NETWORK_ERROR       연결 실패, 타임아웃, DNS
CODE_ERROR          Python 예외, ImportError, AttributeError
QUERY_SYNTAX        BigQuery SQL 문법 오류
TIMEOUT             태스크/쿼리 실행 시간 초과
DATA_QUALITY        NULL, 타입 불일치, 잘못된 값
UNKNOWN             위 항목으로 분류 불가
```

---

### 2. error_id 체계 통일

현재 regex_rules, LLM 출력이 각각 다른 형식을 사용합니다.
아래 형식으로 통일합니다.

```
형식: {PREFIX}-{SEQ}

PREFIX 규칙:
  GCP   → Google Cloud / BigQuery 관련
  NB    → Notebook 실행 관련
  NET   → 네트워크 / 연결
  DATA  → 데이터 품질
  SYS   → 시스템 리소스

예시:
  GCP-001  BigQuery 403 Permission Denied
  GCP-002  BigQuery Schema Mismatch
  NB-001   Notebook OOM
  NB-002   Notebook Kernel Dead
  NET-001  Connection Timeout
```

regex_rules.py의 기존 패턴도 이 체계에 맞게 재정의 필요합니다.

---

### 3. 도메인 컨텍스트 섹션 추가

시스템 프롬프트 최상단에 서비스 설명을 추가합니다.

```
이 Airflow 환경에서 실행되는 job은 두 가지 유형입니다.

[Python Notebook Job]
- Jupyter kernel 위에서 Python 코드를 실행합니다.
- 주요 에러: OOM, kernel 강제 종료, ImportError, 타임아웃, 잘못된 파일 경로

[BigQuery SQL Job]
- Google BigQuery에 SQL을 실행하거나 데이터를 적재합니다.
- 주요 에러: 스키마 불일치, 403 권한 오류, 쿼리 문법 오류, 할당량 초과

job_type이 제공된 경우 반드시 해당 유형의 에러 관점에서 분석하세요.
```

---

### 4. UNKNOWN 세분화

단순 UNKNOWN 대신 이유를 기록하여 후속 분석에 활용합니다.

```json
{
  "error_id": "UNKNOWN",
  "unknown_reason": "LOG_TRUNCATED",
  "partial_finding": "Google API 호출 실패로 추정되나 에러 메시지가 잘려 확인 불가",
  "confidence": 0.2
}
```

`unknown_reason` 허용 값:
- `LOG_TRUNCATED` — 로그가 잘려서 핵심 메시지 없음
- `NEW_PATTERN`   — 알려진 패턴과 전혀 다른 새 에러
- `AMBIGUOUS`     — 여러 원인이 가능하나 특정 불가

---

### 5. 도구 사용 지침 구체화

```
[search_error_guide 사용 규칙]
- 반드시 에러 클래스명 + 핵심 메시지 조합으로 검색
  좋은 예: "Forbidden 403 BigQuery dataViewer"
  나쁜 예: "airflow error" (너무 추상적)
- 검색 결과와 현재 로그의 패턴이 일치하는지 반드시 교차 검증

[read_failed_source_code 사용 규칙]
- traceback에 구체적인 파일 경로와 라인 번호가 있을 때만 호출
- 없으면 호출하지 않음
```

---

### 6. Reviewer 기준 구체화

아래 조건 중 하나라도 해당하면 거절합니다.

```
거절 조건:
- category가 허용 목록 외의 값
- resolution_step이 "로그를 확인하세요" 수준의 일반론
- evidence_line이 실제 로그에 존재하지 않는 내용
- confidence >= 0.7인데 error_id가 UNKNOWN
- job_type이 제공됐는데 분석이 job_type을 전혀 반영하지 않음

승인 조건:
- category가 허용 목록 내 값
- evidence_line이 로그에서 직접 인용됨
- resolution_step이 구체적인 조치 (명령어, 설정값, 확인 경로 포함)
```

---

### 7. Few-shot 예시 추가

프롬프트에 좋은 분석 결과 예시 1개를 포함하면 출력 품질이 안정됩니다.

```
[예시 입력]
google.api_core.exceptions.BadRequest: 400 Braced constructors are not supported

[예시 출력]
{
  "error_id": "GCP-002",
  "category": "SCHEMA_MISMATCH",
  "technical_root_cause": "BigQuery가 지원하지 않는 Braced constructor 문법이 SQL 또는 데이터에 포함됨",
  "evidence_line": "google.api_core.exceptions.BadRequest: 400 Braced constructors are not supported",
  "resolution_step": "1. 데이터 파이프라인에서 중괄호 포함 값을 문자열로 변환\n2. BigQuery 테이블 스키마와 입력 데이터 타입 비교\n3. bq 명령어로 실제 스키마 확인: bq show --schema project:dataset.table",
  "confidence": 0.9
}
```

---

## 적용 우선순위

| 순서 | 항목 | 난이도 | 예상 효과 |
|------|------|--------|-----------|
| 1 | category 고정 목록 | 낮음 | 집계 일관성 즉시 확보 |
| 2 | error_id 체계 통일 | 낮음 | regex + LLM 결과 통합 |
| 3 | 도메인 컨텍스트 추가 | 낮음 | 분류 정확도 향상 |
| 4 | 도구 사용 지침 구체화 | 낮음 | 검색 품질 개선 |
| 5 | UNKNOWN 세분화 | 중간 | 미분류 에러 후속 분석 용이 |
| 6 | Reviewer 기준 구체화 | 중간 | refinement 루프 과잉 방지 |
| 7 | Few-shot 예시 추가 | 중간 | 출력 형식/품질 안정화 |

---

## 향후 추가될 때 반영할 내용 (job_id / task_id 연동 후)

- 시스템 프롬프트에 `logic_type` 동적 주입 (notebook / sql 분기)
- task_id 기반 사전 에러 힌트 목록 주입
- job_type별 category 가중치 조정 (notebook → RESOURCE_EXHAUSTED 우선, BQ → SCHEMA_MISMATCH 우선)
