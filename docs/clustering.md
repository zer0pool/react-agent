# Cluster Analysis Guide

Error log clustering using DBSCAN to validate `error_definitions.json` coverage
and discover previously unknown error types.

---

## Overview

The **Cluster Analysis** tab groups raw log files by textual similarity,
then compares each cluster against the 20 definitions in `error_definitions.json`.

This answers two questions:

| Question | How it's answered |
|----------|-------------------|
| Are the current 20 definitions accurate? | Logs in the same cluster should map to the same definition. If they don't, the definition pattern may be too broad or too narrow. |
| Are there error types not yet defined? | Clusters that match **no** definition are highlighted as new error type candidates. |

---

## How It Works

```
error_logs/**/*.log
        │
        ▼
  [1] Preprocessor
      • Strip Airflow boilerplate (INFO lines, JVM stack frames)
      • Focus on the last traceback / error section (last 30 lines)
      • Normalize variable parts:
          2024-05-20 14:32:01  →  [TIME]
          127.0.0.1            →  [IP]
          42, 1024             →  [NUM]
          /home/airflow/dags   →  [PATH]
          0xdeadbeef           →  [HEX]
          uuid-xxxx-...        →  [UUID]
        │
        ▼
  [2] TF-IDF Vectorization
      • Convert normalized text to sparse numeric vectors
      • max_features controls vocabulary size (default: 1000)
        │
        ▼
  [3] DBSCAN Clustering
      • Metric: cosine similarity (direction, not magnitude)
      • eps: maximum cosine distance to be considered a neighbor
      • min_samples: minimum points to form a cluster core
      • Logs that fit no cluster → label -1 (noise)
        │
        ▼
  [4] Definition Mapping
      • Each cluster's representative log is tested against
        every pattern in error_definitions.json
      • Match  → cluster labeled with that error_id (covered ✅)
      • No match → cluster flagged as uncovered ❌ (new type candidate)
        │
        ▼
  [5] Results
      • Coverage rate: % of clusters matched to a definition
      • Uncovered clusters: new error type candidates
      • Definition gaps: definitions with no matching log in the dataset
```

---

## Step-by-Step Usage

### 1. Open the Cluster Analysis Tab

Launch the Streamlit app and click the **Cluster Analysis** tab.

```bash
make ui
# or
streamlit run app.py --server.headless true
```

---

### 2. Set the Log Directory

The field defaults to `error_logs/`. Change it to point at your actual log root.

**Supported directory layouts:**

```
# error logs layout (yyyy/mm)
error_logs/
  2025/
    01/
      0001.log
      0002.log
    02/
      0001.log


```

Both layouts are scanned automatically via `**/*.log` glob.

---

### 3. Tune DBSCAN Parameters

| Parameter | Default | Effect |
|-----------|---------|--------|
| **eps** | 0.30 | Max cosine distance between neighbors. Lower = stricter, more clusters. Higher = looser, fewer clusters. |
| **min_samples** | 2 | Min points to form a cluster. Set to 1 to cluster every log individually. Set higher to ignore rare one-off errors. |
| **TF-IDF max_features** | 1000 | Vocabulary size. Increase for very diverse log datasets. |

**Recommended starting points by dataset size:**

| Dataset size | eps | min_samples |
|-------------|-----|-------------|
| < 1,000 logs | 0.25 – 0.35 | 2 |
| 1,000 – 10,000 logs | 0.20 – 0.30 | 3 – 5 |
| > 10,000 logs | 0.15 – 0.25 | 5 – 10 |

> **Tip:** Start with the defaults. If too many logs end up as noise (cluster -1),
> increase `eps` or decrease `min_samples`. If unrelated errors are merged into
> one cluster, decrease `eps`.

---

### 4. Run and Read the Coverage Summary

After clicking **Run Clustering**, four metrics appear:

```
Total Logs   │  Clusters Found  │  Covered by Definitions  │  Noise
   1,240     │       18         │       14 / 18  (78%)     │   156
```

| Metric | Meaning |
|--------|---------|
| **Total Logs** | Number of `.log` files successfully preprocessed |
| **Clusters Found** | Number of distinct error groups (excluding noise) |
| **Covered by Definitions** | Clusters matched to at least one of the 20 definitions |
| **Noise** | Logs that did not belong to any cluster — rare or highly variable errors |

---

### 5. Interpret the Cluster Map

The 2D scatter plot projects all log vectors via PCA into two dimensions.

| Color | Meaning |
|-------|---------|
| 🟢 Green | Cluster covered by an existing definition |
| 🔴 Red | Cluster **not** covered — new error type candidate |
| ⚫ Gray | Noise points (cluster = -1) |

- Hover over a point to see its cluster ID and matched definition.
- Tight, well-separated clusters indicate clear error boundaries.
- Overlapping clusters suggest similar error patterns — consider merging definitions.

> Note: For datasets > 5,000 logs, the plot shows a random 5,000-point sample
> for performance. Clustering itself runs on all logs.

---

### 6. Investigate Uncovered Clusters

Each uncovered cluster is shown with:

- **Log count** — how many logs are in this group
- **Representative log** — the first log from the cluster (after preprocessing)
- **Source file path** — where the representative log came from

**"Analyze with Agent" button**

Clicking this sends the representative log to the **Analyze tab** and runs the
full ReAct agent on it. The agent will attempt to classify it and suggest what
new error definition could be added.

Workflow for adding a new definition:

```
1. Click "Analyze with Agent" on an uncovered cluster
2. Review the agent's analysis result
3. If valid new error type:
   a. Note the suggested error_id, category, pattern
   b. Add a new entry to data/error_definitions.json
   c. Re-run vector_store.py to rebuild the search index
   d. Re-run clustering to verify the new definition covers the cluster
```

---

### 7. Check Definition Coverage Detail

The bottom table shows all 20 definitions and whether they matched any log:

| Column | Meaning |
|--------|---------|
| **Found in Logs ✅** | This definition matched at least one cluster in the dataset |
| **Found in Logs ❌ not found** | No log in this dataset triggered this definition |

A definition marked ❌ does **not** mean it's wrong — it may simply not appear
in the current 3-month sample. However, if it remains ❌ across multiple large
batches, consider whether:

- The pattern regex is too strict
- The error genuinely no longer occurs in production
- It has been superseded by another definition

---

## Interpreting Results

### Coverage rate is high (> 80%)

The existing 20 definitions cover most of the log space. Check the few
uncovered clusters manually — they may be noise or edge cases.

### Coverage rate is low (< 60%)

Many log patterns are not covered. Likely causes:

1. The dataset contains a new category of errors not yet defined
2. The `eps` value is too small, fragmenting one error type into many clusters
3. Log format has changed, breaking existing pattern matching

### Many noise points

- `eps` may be too small — try increasing to 0.35 or 0.40
- `min_samples` may be too high — try reducing to 2
- The dataset may genuinely contain many unique one-off errors

### Two definitions matching the same cluster

- The two definitions may describe the same underlying error
- Consider merging them or making the patterns more specific

---

## File Reference

| File | Role |
|------|------|
| `src/clustering/preprocessor.py` | Log normalization and loading |
| `src/clustering/engine.py` | TF-IDF vectorization, DBSCAN, definition mapping |
| `src/ui/pages/clustering.py` | Streamlit tab UI |
| `data/error_definitions.json` | The 20 error type definitions being validated |

---

## Example: Adding a New Definition After Clustering

Suppose Cluster #7 (23 logs) is uncovered. The representative log is:

```
ssl.SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
```

Steps:

1. Click **Analyze with Agent** → agent suggests `E021 | TLS_ERRORS | SSL Certificate Failure`
2. Add to `data/error_definitions.json`:

```json
{
  "error_id": "E021",
  "pattern_name": "SSL Certificate Failure",
  "pattern_type": "regex",
  "pattern": "SSL(?:Error|Exception).*CERTIFICATE_VERIFY_FAILED",
  "category": "INFRASTRUCTURE_ERRORS",
  "sub_category": "TLS_ERRORS",
  "severity": "HIGH",
  ...
}
```

3. Rebuild vector index:

```bash
PYTHONPATH=src python vector_store.py
```

4. Re-run clustering → Cluster #7 should now appear green (covered ✅)
