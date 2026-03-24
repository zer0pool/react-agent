# Airflow 에러 로그 배치 분석 사용법

## 사전 준비

### 1. 로그 디렉토리 구조

```
error_logs/
  01/          ← 월 폴더 (01 ~ 12)
    0001.log   ← 에러 1건 = .log 파일 1개
    0002.log
    ...
  02/
    ...
```

### 2. VertexAI 인증 설정

```bash
# 방법 A: gcloud CLI 로그인
gcloud auth application-default login

# 방법 B: 서비스 계정 키 파일 사용
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

`.env` 파일에 프로젝트 ID를 지정할 수 있습니다:

```
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

---

## 배치 실행

### 특정 월만 처리

```bash
python run_batch.py --month 01
python run_batch.py --month 03
```

### 전체 월 처리

```bash
python run_batch.py --all
```

### 모델 선택

```bash
# VertexAI (기본값)
python run_batch.py --month 01 --model google_vertexai/gemini-2.0-flash-001

# 로컬 Ollama
python run_batch.py --month 01 --model ollama/qwen2.5-coder:7b
```

### 기타 옵션

```bash
# 상세 로그 출력
python run_batch.py --month 01 --verbose

# 로그 디렉토리 경로 변경
python run_batch.py --month 01 --log-dir /data/airflow/error_logs

# DB 파일 경로 변경
python run_batch.py --month 01 --db results_2025.db
```

### 재실행 시 중복 처리 방지

이미 `success`로 처리된 파일은 자동으로 skip됩니다.
실패(`failed`)한 파일은 재실행 시 다시 처리됩니다.

```
Total files : 1000
Already done: 850     ← skip
Pending     : 150     ← 이번에 처리
```

---

## 결과 조회

결과는 `batch_results.db` (SQLite)에 저장됩니다.

### 테이블 컬럼

| 컬럼 | 설명 |
|------|------|
| `file_path` | 로그 파일 경로 (PK) |
| `month` | 월 (01 ~ 12) |
| `error_id` | 에러 분류 ID (예: `SCHEMA_MISMATCH`, `UNKNOWN`) |
| `category` | 에러 카테고리 (예: `SCHEMA_ISSUES`) |
| `confidence` | 분류 신뢰도 (0.0 ~ 1.0) |
| `result_json` | 전체 분석 결과 JSON |
| `status` | `success` / `failed` |
| `error_msg` | 실패 시 에러 메시지 |
| `processed_at` | 처리 시각 |

---

### sqlite3 CLI 로 조회

```bash
sqlite3 batch_results.db
```

```sql
-- 월별 처리 현황
SELECT month, status, COUNT(*) FROM results GROUP BY month, status ORDER BY month;

-- 에러 유형별 분포 (성공 건만)
SELECT error_id, category, COUNT(*) AS cnt
FROM results
WHERE status = 'success'
GROUP BY error_id, category
ORDER BY cnt DESC;

-- 신뢰도 높은 분석 결과 조회
SELECT file_path, error_id, confidence
FROM results
WHERE status = 'success' AND confidence >= 0.8
ORDER BY confidence DESC;

-- 실패 목록 확인
SELECT file_path, error_msg FROM results WHERE status = 'failed';

-- 특정 에러 ID 의 전체 분석 결과 출력
SELECT result_json FROM results WHERE error_id = 'SCHEMA_MISMATCH' LIMIT 1;
```

---

### Python 으로 조회

```python
import sqlite3
import json

conn = sqlite3.connect("batch_results.db")
conn.row_factory = sqlite3.Row

# 월별 현황
rows = conn.execute("""
    SELECT month, status, COUNT(*) as cnt
    FROM results GROUP BY month, status ORDER BY month
""").fetchall()
for r in rows:
    print(r["month"], r["status"], r["cnt"])

# 분석 결과 pandas DataFrame으로 로드
import pandas as pd
df = pd.read_sql("""
    SELECT month, file_path, error_id, category, confidence, processed_at
    FROM results WHERE status = 'success'
""", conn)
print(df.groupby("error_id").size().sort_values(ascending=False))

conn.close()
```

---

### DB Browser for SQLite (GUI)

[DB Browser for SQLite](https://sqlitebrowser.org/) 를 사용하면 GUI로 테이블을 탐색하거나 쿼리를 실행할 수 있습니다.
