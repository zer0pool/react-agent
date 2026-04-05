"""DBSCAN clustering engine. No Streamlit imports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer


@dataclass
class ClusterResult:
    """Everything the UI needs — no numpy/scipy types leaked out."""
    labels: list[int]               # cluster_id per log (-1 = noise)
    n_clusters: int
    n_noise: int
    coords_2d: list[tuple[float, float]]   # PCA projection for scatter plot
    cluster_summaries: list[dict]          # per-cluster stats
    coverage_rate: float                   # fraction covered by definitions
    uncovered_clusters: list[int]          # cluster ids with no definition match
    definition_coverage: list[dict]        # per-definition match info

    # Fields for "uncovered but similar to existing" analysis
    # cluster_summaries entries include:
    #   matched_definition      : str | None  — exact pattern match (any member)
    #   match_hit_count         : int          — how many members matched
    #   match_ratio             : float        — hit_count / cluster_size
    #   closest_definition      : str | None  — nearest definition by TF-IDF cosine (even if no exact match)
    #   closest_similarity      : float        — cosine similarity score 0~1


def _load_definitions(definitions_path: str) -> list[dict]:
    with open(definitions_path, encoding="utf-8") as f:
        return json.load(f)


def _match_definition(text: str, defn: dict) -> bool:
    """Return True if text matches the definition's pattern."""
    pattern_type = defn.get("pattern_type", "simple_string")
    pattern = defn.get("pattern", "")
    try:
        if pattern_type == "regex":
            # Replace [VAR] placeholder with a broad wildcard
            regex = pattern.replace("[VAR]", r"[\w.\-]+")
            return bool(re.search(regex, text, re.IGNORECASE))
        else:
            return pattern.lower() in text.lower()
    except re.error:
        return False


def run_clustering(
    records: list[dict],
    eps: float = 0.3,
    min_samples: int = 2,
    max_features: int = 1000,
    definitions_path: str = "data/error_definitions.json",
    pca_sample_limit: int = 5000,
) -> ClusterResult:
    """Vectorize logs with TF-IDF, cluster with DBSCAN, map to definitions."""

    texts = [r["normalized"] for r in records]
    n = len(texts)

    # ── 1. TF-IDF vectorization ───────────────────────────────────────────────
    vectorizer = TfidfVectorizer(max_features=max_features)
    X = vectorizer.fit_transform(texts)

    # ── 2. DBSCAN ────────────────────────────────────────────────────────────
    dbscan = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine", algorithm="brute")
    labels: np.ndarray = dbscan.fit_predict(X)

    unique_clusters = sorted(set(labels))
    cluster_ids = [c for c in unique_clusters if c != -1]
    n_clusters = len(cluster_ids)
    n_noise = int(np.sum(labels == -1))

    # ── 3. PCA 2D (sampled for large datasets) ────────────────────────────────
    sample_idx = list(range(n))
    if n > pca_sample_limit:
        rng = np.random.default_rng(42)
        sample_idx = rng.choice(n, pca_sample_limit, replace=False).tolist()

    pca = PCA(n_components=2, random_state=42)
    coords_full = pca.fit_transform(X[sample_idx].toarray())
    # Build full coords array (non-sampled points get NaN — UI can skip them)
    coords_2d_arr = np.full((n, 2), np.nan)
    for new_i, orig_i in enumerate(sample_idx):
        coords_2d_arr[orig_i] = coords_full[new_i]
    coords_2d = [(float(r[0]), float(r[1])) for r in coords_2d_arr]

    # ── 4. Per-cluster representative log & metadata ──────────────────────────
    cluster_summaries: list[dict] = []
    for cid in cluster_ids:
        mask = labels == cid
        member_indices = [i for i, m in enumerate(mask) if m]
        members = [records[i] for i in member_indices]
        # Cluster centroid vector (mean of member TF-IDF vectors)
        centroid = np.asarray(X[member_indices].mean(axis=0))
        cluster_summaries.append({
            "cluster_id": int(cid),
            "count": int(mask.sum()),
            "representative": members[0]["raw"][:500],
            "representative_path": members[0]["path"],
            "paths": [m["path"] for m in members],
            "all_raws": [m["raw"] for m in members],
            "centroid": centroid,          # used for similarity search, removed before return
            "matched_definition": None,
            "match_hit_count": 0,
            "match_ratio": 0.0,
            "closest_definition": None,
            "closest_similarity": 0.0,
        })

    # ── 5. Map clusters → definitions ────────────────────────────────────────
    definitions = _load_definitions(definitions_path)

    # Vectorize definition keywords for similarity fallback
    def_texts = [
        " ".join([
            d.get("pattern_name", ""),
            d.get("description", ""),
            " ".join(d.get("keywords", [])),
            " ".join(d.get("representative_examples", [])),
        ])
        for d in definitions
    ]
    def_vectorizer = TfidfVectorizer(max_features=max_features)
    X_def = def_vectorizer.fit_transform(def_texts)

    matched_cluster_ids: set[int] = set()

    for summary in cluster_summaries:
        all_raws = summary["all_raws"]
        hit_counts: dict[str, int] = {}

        # Test every member (not just representative) against every definition
        for raw_text in all_raws:
            for defn in definitions:
                if _match_definition(raw_text, defn):
                    eid = defn["error_id"]
                    hit_counts[eid] = hit_counts.get(eid, 0) + 1

        if hit_counts:
            # Pick definition with most hits (majority vote across cluster members)
            best_eid = max(hit_counts, key=lambda k: hit_counts[k])
            best_count = hit_counts[best_eid]
            summary["matched_definition"] = best_eid
            summary["match_hit_count"] = best_count
            summary["match_ratio"] = round(best_count / summary["count"], 2)
            matched_cluster_ids.add(summary["cluster_id"])
        else:
            # No exact match — find closest definition by TF-IDF cosine similarity
            centroid = summary["centroid"]
            # Project centroid into definition vector space
            centroid_in_def_space = def_vectorizer.transform(
                [" ".join([r[:300] for r in all_raws])]
            )
            from sklearn.metrics.pairwise import cosine_similarity
            sims = cosine_similarity(centroid_in_def_space, X_def)[0]
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])
            if best_sim > 0.01:
                summary["closest_definition"] = definitions[best_idx]["error_id"]
                summary["closest_similarity"] = round(best_sim, 3)

    # Remove centroid (numpy array) before returning — not JSON-serializable
    for s in cluster_summaries:
        s.pop("centroid", None)
        s.pop("all_raws", None)

    uncovered_clusters = [
        s["cluster_id"] for s in cluster_summaries
        if s["matched_definition"] is None
    ]

    coverage_rate = (
        len(matched_cluster_ids) / n_clusters if n_clusters > 0 else 0.0
    )

    # ── 6. Definition coverage report ─────────────────────────────────────────
    definition_coverage = []
    for defn in definitions:
        matched = [
            s["cluster_id"] for s in cluster_summaries
            if s["matched_definition"] == defn["error_id"]
        ]
        definition_coverage.append({
            "error_id": defn["error_id"],
            "pattern_name": defn["pattern_name"],
            "category": defn["category"],
            "matched_clusters": matched,
            "found_in_logs": len(matched) > 0,
        })

    return ClusterResult(
        labels=labels.tolist(),
        n_clusters=n_clusters,
        n_noise=n_noise,
        coords_2d=coords_2d,
        cluster_summaries=cluster_summaries,
        coverage_rate=coverage_rate,
        uncovered_clusters=uncovered_clusters,
        definition_coverage=definition_coverage,
    )
