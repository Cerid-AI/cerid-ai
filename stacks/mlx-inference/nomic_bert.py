# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""nomic-bert on MLX, reading the same GGUF quenchforge serves (nomic-embed-text-v1.5 Q8_0).

Reproduces llama.cpp's path for that file: its WPM tokenizer, Q8_0 weights
used as MLX 8-bit groups of 32, post-norm BERT with NeoX rotary and SwiGLU,
then CLS pooling and L2 normalisation. CLS is deliberate: quenchforge starts
its embed slot with ``--pooling cls`` (the GGUF itself says mean), and the
indexes built through it hold those vectors. Mean pooling scores
0.81-0.94 cosine against them; CLS scores 0.99999+.

No query/document prefix is added. Callers send raw text, as they did to
quenchforge.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import mlx.core as mx

_WHITESPACE = set(range(0x09, 0x0E)) | set(range(0x2000, 0x200B)) | {
    0x20, 0x85, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
}
_CJK = (
    (0x04E00, 0x09FFF), (0x03400, 0x04DBF), (0x20000, 0x2A6DF), (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F), (0x2B920, 0x2CEAF), (0x0F900, 0x0FAFF), (0x2F800, 0x2FA1F),
)


def _nfd_first(cpt: int) -> int:
    # llama.cpp's NFD table keeps only the FIRST codepoint of a decomposition,
    # so "é" becomes "e" and the combining accent is dropped.
    return ord(unicodedata.normalize("NFD", chr(cpt))[0])


def _lower(cpt: int) -> int:
    # Simple (single-codepoint) lowercase mapping, as in UnicodeData.txt.
    low = chr(cpt).lower()
    return ord(low[0]) if low else cpt


def _wpm_words(text: str) -> list[str]:
    words = [""]
    for ch in text:
        cpt = _nfd_first(ord(ch))
        if cpt in _WHITESPACE:
            if words[-1]:
                words.append("")
            continue
        cat = unicodedata.category(chr(cpt))
        if cpt == 0 or cpt == 0xFFFD or cat in ("Cc", "Cf", "Co", "Cs"):
            continue
        s = chr(_lower(cpt))
        if cat[0] == "P" or (cpt < 0x7F and cat[0] == "S") or any(a <= cpt <= b for a, b in _CJK):
            if words[-1]:
                words.append("")
            words[-1] = s
            words.append("")
        else:
            words[-1] += s
    if not words[-1]:
        words.pop()
    return words


class NomicBertEmbedder:
    def __init__(self, gguf: Path, pooling: str = "cls", max_tokens: int = 2048) -> None:
        weights, meta = mx.load(str(gguf), return_metadata=True)
        if str(meta.get("general.architecture")) != "nomic-bert":
            raise ValueError(f"{gguf} is not a nomic-bert GGUF")
        self.w = weights
        self.pooling = pooling
        self.max_tokens = max_tokens
        arch = "nomic-bert"
        self.n_layer = int(meta[f"{arch}.block_count"].item())
        self.n_head = int(meta[f"{arch}.attention.head_count"].item())
        self.n_embd = int(meta[f"{arch}.embedding_length"].item())
        self.eps = float(meta[f"{arch}.attention.layer_norm_epsilon"].item())
        self.rope_base = float(meta[f"{arch}.rope.freq_base"].item())
        tokens: list[str] = meta["tokenizer.ggml.tokens"]
        types = meta["tokenizer.ggml.token_type"].tolist()
        # Token text is compared as UTF-8 bytes, the way llama.cpp slices words.
        self.vocab = {t.encode(): i for i, t in enumerate(tokens)}
        self.max_len = max(len(t.encode()) for t in tokens)
        self.special = {t: i for i, (t, ty) in enumerate(zip(tokens, types)) if ty == 3}
        self.cls = int(meta["tokenizer.ggml.bos_token_id"].item())
        self.sep = int(meta["tokenizer.ggml.seperator_token_id"].item())
        self.unk = int(meta["tokenizer.ggml.unknown_token_id"].item())
        self.head_dim = self.n_embd // self.n_head
        mx.eval(self.w)

    # -- tokenizer ---------------------------------------------------------

    def _wpm(self, text: str, out: list[int]) -> None:
        for word in _wpm_words(text):
            word1 = ("▁" + word).encode()
            n = len(word1)
            start = len(out)
            i = 0
            while i < n:
                match = False
                for j in range(min(n, i + self.max_len + 1), i, -1):
                    tid = self.vocab.get(word1[i:j])
                    if tid is not None:
                        out.append(tid)
                        match = True
                        i = j - 1
                        break
                if not match:
                    del out[start:]
                    break
                i += 1
            if len(out) == start:
                out.append(self.unk)

    def tokenize(self, text: str) -> list[int]:
        out = [self.cls]
        # Special-token text is parsed as the token itself (llama-server
        # tokenizes prompts with parse_special on).
        pieces: list[str | int] = [text]
        for tok, tid in self.special.items():
            nxt: list[str | int] = []
            for p in pieces:
                if isinstance(p, int) or tok not in p:
                    nxt.append(p)
                    continue
                parts = p.split(tok)
                for k, part in enumerate(parts):
                    if part:
                        nxt.append(part)
                    if k < len(parts) - 1:
                        nxt.append(tid)
            pieces = nxt
        for p in pieces:
            if isinstance(p, int):
                out.append(p)
            else:
                self._wpm(p, out)
        out.append(self.sep)
        return out

    # -- model -------------------------------------------------------------

    def _qmm(self, x: mx.array, name: str) -> mx.array:
        w = self.w
        return mx.quantized_matmul(
            x, w[f"{name}.weight"], w[f"{name}.scales"], w[f"{name}.biases"],
            transpose=True, group_size=32, bits=8,
        )

    def _ln(self, x: mx.array, name: str) -> mx.array:
        return mx.fast.layer_norm(x, self.w[f"{name}.weight"], self.w[f"{name}.bias"], self.eps)

    def _forward(self, ids: list[int]) -> mx.array:
        w = self.w
        idx = mx.array(ids)
        emb = mx.dequantize(
            w["token_embd.weight"][idx], w["token_embd.scales"][idx], w["token_embd.biases"][idx],
            group_size=32, bits=8,
        ).astype(mx.float32)
        x = emb + w["token_types.weight"][0]
        x = self._ln(x, "token_embd_norm")[None]
        L, H, D = len(ids), self.n_head, self.head_dim
        for i in range(self.n_layer):
            p = f"blk.{i}"
            qkv = self._qmm(x, f"{p}.attn_qkv")
            q, k, v = mx.split(qkv, 3, axis=-1)
            q = q.reshape(1, L, H, D).transpose(0, 2, 1, 3)
            k = k.reshape(1, L, H, D).transpose(0, 2, 1, 3)
            v = v.reshape(1, L, H, D).transpose(0, 2, 1, 3)
            q = mx.fast.rope(q, D, traditional=False, base=self.rope_base, scale=1.0, offset=0)
            k = mx.fast.rope(k, D, traditional=False, base=self.rope_base, scale=1.0, offset=0)
            a = mx.fast.scaled_dot_product_attention(q, k, v, scale=D ** -0.5)
            a = a.transpose(0, 2, 1, 3).reshape(1, L, self.n_embd)
            x = self._ln(x + self._qmm(a, f"{p}.attn_output"), f"{p}.attn_output_norm")
            up = self._qmm(x, f"{p}.ffn_up")
            gate = self._qmm(x, f"{p}.ffn_gate")
            h = self._qmm(gate * mx.sigmoid(gate) * up, f"{p}.ffn_down")
            x = self._ln(x + h, f"{p}.layer_output_norm")
        x = x[0]
        pooled = x[0] if self.pooling == "cls" else mx.mean(x, axis=0)
        return pooled / mx.maximum(mx.linalg.norm(pooled), 1e-12)

    def embed(self, texts: list[str], truncate: bool = True) -> tuple[list[list[float]], int]:
        vectors = []
        total = 0
        for text in texts:
            ids = self.tokenize(text)
            if len(ids) > self.max_tokens:
                if not truncate:
                    raise ValueError(
                        f"input is {len(ids)} tokens, over the {self.max_tokens}-token context"
                    )
                ids = ids[: self.max_tokens - 1] + [self.sep]
            total += len(ids)
            vectors.append(self._forward(ids))
        mx.eval(vectors)
        return [v.tolist() for v in vectors], total
