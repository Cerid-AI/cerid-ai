#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""C3.1 BGE-M3 ONNX validation spike (Workstream E, Cycle 3).

Standalone benchmark: download the BAAI/bge-m3 ONNX export, encode the
seed eval-corpus markdown files, and emit measurements the C3.2 sparse
retrieval implementation will need to commit to a design.

Measures:
    - ONNX model disk size (FP32 vs INT8 quantized)
    - Encode latency on CPU (warm) over ~20 short documents
    - Sparse vector density (typical non-zeros per chunk @ 512 tokens)
    - Per-chunk storage projection (10K / 100K chunk fleets)
    - ONNX ``session.get_outputs()`` tensor names + shapes
      (critical: if outputs[1] is not the sparse-weight tensor, the
      C3.2 blueprint must be adjusted)
    - Correctness sanity: two paraphrased short texts must have
      sparse cosine similarity > 0

The spike runs ENTIRELY off the local filesystem and the HF model
cache. No Cerid services need to be up. The eval corpus is read
directly from ``data/eval-corpus/v1/`` rather than queried from
ChromaDB.

Idempotent — re-running uses the cached HF download.

Usage:
    PYTHONPATH=src/mcp .venv/bin/python scripts/bge_m3_spike.py --use-int8
    PYTHONPATH=src/mcp .venv/bin/python scripts/bge_m3_spike.py --use-fp32

Default is ``--use-int8`` (~580 MB) to keep download cost low. Pass
``--both`` to fetch both variants and report the FP32 size for the
recommendation table.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean, median, stdev

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("bge-m3-spike")

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_CORPUS_DIR = REPO_ROOT / "data" / "eval-corpus" / "v1"

# BAAI/bge-m3 ships only an FP32 ONNX (XLMRoberta base encoder, no
# quantized variant). The full quantized matrix lives in Xenova's
# transformers.js mirror of the same checkpoint — same weights, same
# backbone, just additionally quantized. Use BAAI for FP32 and Xenova
# for INT8 unless the operator overrides via env.
HF_REPO_ID_FP32 = "BAAI/bge-m3"
HF_REPO_ID_INT8 = "Xenova/bge-m3"
ONNX_FP32_FILENAME = "onnx/model.onnx"
ONNX_FP32_DATA_FILENAME = "onnx/model.onnx_data"  # external-weights blob
ONNX_INT8_FILENAME = "onnx/model_quantized.onnx"
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")

MAX_SEQ_LEN = 512
WARMUP_RUNS = 2
BENCH_RUNS = 3  # per document — we average


def fetch_model_files(use_int8: bool, fetch_both: bool) -> dict[str, Path]:
    """Download (or return cached) ONNX model + tokenizer files from HF Hub."""
    from huggingface_hub import hf_hub_download

    files: dict[str, Path] = {}

    if fetch_both or not use_int8:
        log.info("downloading (or using cached) FP32 %s from %s ...", ONNX_FP32_FILENAME, HF_REPO_ID_FP32)
        files[ONNX_FP32_FILENAME] = Path(hf_hub_download(repo_id=HF_REPO_ID_FP32, filename=ONNX_FP32_FILENAME))
        # FP32 export uses external weights — fetch the sidecar blob too.
        try:
            blob = hf_hub_download(repo_id=HF_REPO_ID_FP32, filename=ONNX_FP32_DATA_FILENAME)
            files[ONNX_FP32_DATA_FILENAME] = Path(blob)
        except Exception as exc:  # noqa: BLE001 - spike-only
            log.warning("FP32 external-weights blob not fetched: %s", exc)

    if fetch_both or use_int8:
        log.info("downloading (or using cached) INT8 %s from %s ...", ONNX_INT8_FILENAME, HF_REPO_ID_INT8)
        files[ONNX_INT8_FILENAME] = Path(hf_hub_download(repo_id=HF_REPO_ID_INT8, filename=ONNX_INT8_FILENAME))

    # Tokenizer can come from BAAI (canonical) — same SentencePiece for both variants.
    for tok_file in TOKENIZER_FILES:
        log.info("downloading (or using cached) tokenizer file %s ...", tok_file)
        path = hf_hub_download(repo_id=HF_REPO_ID_FP32, filename=tok_file)
        files[tok_file] = Path(path)

    return files


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def load_corpus_texts() -> list[tuple[str, str]]:
    """Read all .md files under data/eval-corpus/v1 (skip MANIFEST)."""
    docs: list[tuple[str, str]] = []
    for md in sorted(EVAL_CORPUS_DIR.rglob("*.md")):
        if md.name == "MANIFEST.md":
            continue
        rel = md.relative_to(EVAL_CORPUS_DIR).as_posix()
        docs.append((rel, md.read_text(encoding="utf-8")))
    log.info("loaded %d corpus docs from %s", len(docs), EVAL_CORPUS_DIR)
    return docs


def load_tokenizer(tokenizer_json_path: Path):
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer_json_path))
    tok.enable_truncation(max_length=MAX_SEQ_LEN)
    tok.enable_padding(length=MAX_SEQ_LEN, pad_id=1, pad_token="<pad>")
    return tok


def tokenize_batch(tokenizer, texts: list[str]) -> dict[str, np.ndarray]:
    """Return (input_ids, attention_mask) ndarrays shaped [B, MAX_SEQ_LEN]."""
    encodings = tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    return {"input_ids": input_ids, "attention_mask": attention_mask}


def build_session(onnx_path: Path):
    import onnxruntime as ort

    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_opts.intra_op_num_threads = 0  # let ORT pick
    providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(str(onnx_path), sess_opts, providers=providers)
    return sess


def describe_session_io(sess) -> dict:
    inputs = [{"name": i.name, "shape": list(i.shape), "dtype": i.type} for i in sess.get_inputs()]
    outputs = [{"name": o.name, "shape": list(o.shape), "dtype": o.type} for o in sess.get_outputs()]
    return {"inputs": inputs, "outputs": outputs}


def run_inference(sess, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    # Filter feeds to only the inputs ORT expects (token_type_ids may be unused).
    expected = {i.name for i in sess.get_inputs()}
    feeds_clean = {k: v for k, v in feeds.items() if k in expected}
    if "token_type_ids" in expected and "token_type_ids" not in feeds_clean:
        # XLM-RoBERTa uses all-zero token_type_ids; supply if model demands it.
        bsz, seq = feeds["input_ids"].shape
        feeds_clean["token_type_ids"] = np.zeros((bsz, seq), dtype=np.int64)
    return sess.run(None, feeds_clean)


def sparse_from_outputs(
    outputs: list[np.ndarray],
    sparse_output_idx: int,
    attention_mask: np.ndarray,
) -> list[dict[int, float]]:
    """Convert the sparse-logits tensor into per-doc {token_id: weight} dicts.

    BGE-M3 sparse head produces a [B, T, 1] (or [B, T]) tensor of
    non-negative weights per token. The canonical aggregation (per the
    upstream FlagEmbedding ``compute_lexical_matching_score``) is:
    max-pool over sequence positions per vocab id, after masking PAD.
    But since BGE-M3 emits one scalar weight per ACTUAL token (the
    weight is keyed by the token's input id), aggregation is "for each
    position p where mask=1, accumulate max(weight[p]) at key
    input_ids[p]".
    """
    raise NotImplementedError  # populated inline below — kept for clarity


def aggregate_sparse(
    sparse_logits: np.ndarray,
    input_ids: np.ndarray,
    attention_mask: np.ndarray,
    special_token_ids: set[int],
) -> list[dict[int, float]]:
    """Per-doc {token_id: weight} via max-pool over positions, PAD-masked.

    BGE-M3 reference: take ReLU(logits), drop PAD + CLS/SEP tokens,
    then per (input_id) keep the max weight across positions.
    """
    if sparse_logits.ndim == 3 and sparse_logits.shape[-1] == 1:
        weights = sparse_logits.squeeze(-1)
    else:
        weights = sparse_logits  # shape [B, T]
    weights = np.maximum(weights, 0.0)
    results: list[dict[int, float]] = []
    bsz, _ = input_ids.shape
    for b in range(bsz):
        per_doc: dict[int, float] = {}
        ids = input_ids[b]
        mask = attention_mask[b]
        w = weights[b]
        for pos in range(len(ids)):
            if mask[pos] == 0:
                continue
            tid = int(ids[pos])
            if tid in special_token_ids:
                continue
            wt = float(w[pos])
            if wt <= 0.0:
                continue
            prev = per_doc.get(tid)
            if prev is None or wt > prev:
                per_doc[tid] = wt
        results.append(per_doc)
    return results


def sparse_cosine(a: dict[int, float], b: dict[int, float]) -> float:
    """Cosine similarity of two sparse {tid: weight} dicts."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = float(np.sqrt(sum(v * v for v in a.values())))
    nb = float(np.sqrt(sum(v * v for v in b.values())))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def project_storage_bytes(median_nnz: int) -> dict[str, int]:
    """Estimate per-chunk + fleet storage for sparse-only vectors.

    Wire format assumption: {token_id: weight} stored as a packed
    array of (uint32, float32) pairs = 8 bytes per non-zero entry.
    Round up for JSON/Pyserini-style overhead at 12 B/nnz.
    """
    per_chunk_packed = median_nnz * 8
    per_chunk_json = median_nnz * 14  # rough JSON `"12345":0.4321,` per entry
    return {
        "per_chunk_packed_bytes": per_chunk_packed,
        "per_chunk_json_bytes": per_chunk_json,
        "fleet_10k_packed_mb": (per_chunk_packed * 10_000) / (1024 * 1024),
        "fleet_100k_packed_mb": (per_chunk_packed * 100_000) / (1024 * 1024),
        "fleet_10k_json_mb": (per_chunk_json * 10_000) / (1024 * 1024),
        "fleet_100k_json_mb": (per_chunk_json * 100_000) / (1024 * 1024),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 ONNX C3.1 spike")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--use-int8", action="store_true", help="encode with the INT8 quantized export (default)")
    group.add_argument("--use-fp32", action="store_true", help="encode with the FP32 export")
    parser.add_argument("--both", action="store_true", help="fetch both INT8 + FP32 so the report records both sizes")
    args = parser.parse_args()

    use_int8 = not args.use_fp32  # default True
    fetch_both = args.both

    log.info("=" * 60)
    log.info("BGE-M3 C3.1 spike — variant=%s, fetch_both=%s", "INT8" if use_int8 else "FP32", fetch_both)
    log.info("=" * 60)

    files = fetch_model_files(use_int8=use_int8, fetch_both=fetch_both)

    sizes: dict[str, float] = {}
    if ONNX_INT8_FILENAME in files:
        sizes["int8_mb"] = file_size_mb(files[ONNX_INT8_FILENAME])
    if ONNX_FP32_FILENAME in files:
        fp32_total = file_size_mb(files[ONNX_FP32_FILENAME])
        if ONNX_FP32_DATA_FILENAME in files:
            fp32_total += file_size_mb(files[ONNX_FP32_DATA_FILENAME])
        sizes["fp32_mb"] = fp32_total
    log.info("model disk sizes (MB): %s", json.dumps(sizes, indent=2))

    onnx_path = files[ONNX_INT8_FILENAME if use_int8 else ONNX_FP32_FILENAME]
    sess = build_session(onnx_path)
    io = describe_session_io(sess)
    log.info("session inputs: %s", json.dumps(io["inputs"], indent=2))
    log.info("session outputs: %s", json.dumps(io["outputs"], indent=2))

    tokenizer = load_tokenizer(files["tokenizer.json"])
    # Identify special tokens to exclude from sparse aggregation
    tok_cfg_path = files["special_tokens_map.json"]
    tok_cfg = json.loads(tok_cfg_path.read_text())
    special_tokens = {v if isinstance(v, str) else v.get("content") for v in tok_cfg.values()}
    special_tokens.discard(None)
    special_token_ids: set[int] = set()
    for st in special_tokens:
        if st is None:
            continue
        tid = tokenizer.token_to_id(st)
        if tid is not None:
            special_token_ids.add(tid)
    log.info("special token ids excluded from sparse: %s", sorted(special_token_ids))

    docs = load_corpus_texts()
    texts = [t for _, t in docs]

    # Encode one-by-one to time per-doc and to keep memory bounded.
    # Warmup
    for i in range(WARMUP_RUNS):
        feeds = tokenize_batch(tokenizer, [texts[0]])
        run_inference(sess, feeds)
    log.info("warmup complete (%d runs)", WARMUP_RUNS)

    per_doc_latency_ms: list[float] = []
    nnz_counts: list[int] = []
    all_sparse: list[dict[int, float]] = []
    output_names = [o.name for o in sess.get_outputs()]
    # Heuristic: sparse output is the 1-d-per-token tensor (last dim 1 or
    # equals seq len). Fall back to index 1 per the blueprint.
    sparse_idx: int | None = None
    for i, o in enumerate(sess.get_outputs()):
        shape = list(o.shape)
        if len(shape) == 3 and (shape[-1] == 1):
            sparse_idx = i
            break
    if sparse_idx is None and len(output_names) > 1:
        sparse_idx = 1  # blueprint default
    if sparse_idx is None:
        log.warning(
            "*** BLOCKING: ONNX session emits %d output(s) — no sparse-weights tensor present. "
            "Outputs: %s. The published BGE-M3 ONNX export (BAAI/bge-m3 + Xenova mirror) is "
            "XLMRoberta-backbone-only — the sparse and ColBERT heads are NOT in the graph. "
            "C3.2 cannot consume sparse vectors from this artifact. "
            "Continuing the spike with a SYNTHETIC sparse approximation derived from token-level "
            "L2 norms of the last_hidden_state, purely so the latency/density/storage envelope "
            "is still measured for the operator's decision.",
            len(output_names),
            output_names,
        )
    else:
        log.info("inferred sparse output index = %d (name=%s)", sparse_idx, output_names[sparse_idx])

    for doc_idx, (rel, text) in enumerate(docs):
        feeds = tokenize_batch(tokenizer, [text])
        runs_ms: list[float] = []
        outs_last: list[np.ndarray] | None = None
        for _ in range(BENCH_RUNS):
            t0 = time.perf_counter()
            outs_last = run_inference(sess, feeds)
            runs_ms.append((time.perf_counter() - t0) * 1000.0)
        per_doc_latency_ms.append(mean(runs_ms))

        assert outs_last is not None
        if sparse_idx is None:
            # Synthetic envelope: per-token L2 norm of the hidden state,
            # max-pooled into a {token_id: weight} dict. This is NOT the
            # real BGE-M3 sparse output — it's a proxy that gives the
            # operator a plausible nnz envelope per chunk.
            hidden = outs_last[0]  # [B, T, 1024]
            synth = np.linalg.norm(hidden, axis=-1)[:, :, None]  # [B, T, 1]
            sparse = aggregate_sparse(
                synth,
                feeds["input_ids"],
                feeds["attention_mask"],
                special_token_ids,
            )[0]
        else:
            sparse_logits = outs_last[sparse_idx]
            sparse = aggregate_sparse(
                sparse_logits,
                feeds["input_ids"],
                feeds["attention_mask"],
                special_token_ids,
            )[0]
        nnz_counts.append(len(sparse))
        all_sparse.append(sparse)
        log.info(
            "doc %02d %-44s | mean_ms=%6.1f nnz=%4d top_w=%.3f",
            doc_idx + 1,
            rel,
            per_doc_latency_ms[-1],
            len(sparse),
            max(sparse.values()) if sparse else 0.0,
        )

    # Correctness check
    log.info("-" * 60)
    log.info("correctness check: paraphrase pair must yield sparse_cos > 0")
    pair = ["python coding guide", "guide to python coding"]
    p_feeds = tokenize_batch(tokenizer, pair)
    p_outs = run_inference(sess, p_feeds)
    if sparse_idx is None:
        hidden = p_outs[0]
        synth = np.linalg.norm(hidden, axis=-1)[:, :, None]
        p_sparse = aggregate_sparse(
            synth,
            p_feeds["input_ids"],
            p_feeds["attention_mask"],
            special_token_ids,
        )
    else:
        p_sparse = aggregate_sparse(
            p_outs[sparse_idx],
            p_feeds["input_ids"],
            p_feeds["attention_mask"],
            special_token_ids,
        )
    pair_cos = sparse_cosine(p_sparse[0], p_sparse[1])
    log.info("paraphrase sparse cosine = %.4f (a=%d nnz, b=%d nnz)", pair_cos, len(p_sparse[0]), len(p_sparse[1]))

    # Aggregate stats
    median_nnz = int(median(nnz_counts))
    mean_nnz = int(round(mean(nnz_counts)))
    nnz_stdev = stdev(nnz_counts) if len(nnz_counts) > 1 else 0.0
    mean_latency_ms = mean(per_doc_latency_ms)
    median_latency_ms = median(per_doc_latency_ms)
    p95_latency_ms = sorted(per_doc_latency_ms)[int(0.95 * (len(per_doc_latency_ms) - 1))]

    storage = project_storage_bytes(median_nnz)

    summary = {
        "variant_used": "INT8" if use_int8 else "FP32",
        "model_sizes_mb": sizes,
        "session_io": io,
        "inferred_sparse_output_index": sparse_idx,
        "inferred_sparse_output_name": output_names[sparse_idx] if sparse_idx is not None else None,
        "sparse_head_present_in_onnx": sparse_idx is not None,
        "density_source": "real_sparse_head" if sparse_idx is not None else "synthetic_token_l2_envelope",
        "num_docs": len(docs),
        "max_seq_len": MAX_SEQ_LEN,
        "latency_ms": {
            "mean": round(mean_latency_ms, 2),
            "median": round(median_latency_ms, 2),
            "p95": round(p95_latency_ms, 2),
            "min": round(min(per_doc_latency_ms), 2),
            "max": round(max(per_doc_latency_ms), 2),
        },
        "sparse_density": {
            "median_nnz": median_nnz,
            "mean_nnz": mean_nnz,
            "stdev_nnz": round(nnz_stdev, 2),
            "min_nnz": min(nnz_counts),
            "max_nnz": max(nnz_counts),
        },
        "storage_projection": {k: round(v, 2) if isinstance(v, float) else v for k, v in storage.items()},
        "correctness": {
            "paraphrase_pair": pair,
            "sparse_cosine": round(pair_cos, 4),
            "passes_threshold_gt_zero": pair_cos > 0.0,
        },
    }
    # Surface common-token overlap as a sanity signal too.
    common_token_counter = Counter()
    for s in all_sparse:
        for tid in s:
            common_token_counter[tid] += 1
    summary["sparse_density"]["docs_containing_top_token"] = common_token_counter.most_common(1)[0][1] if common_token_counter else 0

    log.info("-" * 60)
    log.info("SUMMARY")
    log.info(json.dumps(summary, indent=2))

    # Write JSON summary next to the markdown report — handy for re-runs.
    out_path = REPO_ROOT / "tasks" / "2026-05-12-bge-m3-spike.json"
    out_path.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("wrote machine-readable summary to %s", out_path)

    if not summary["correctness"]["passes_threshold_gt_zero"]:
        log.error("CORRECTNESS FAIL — paraphrase pair has zero sparse overlap")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
