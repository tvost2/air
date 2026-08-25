"""
Real token-savings benchmark: baseline (full-context replay) vs AIR
(ContextEngine.put/render), applied to a real Claude Code session
transcript -- not the synthetic dataset.

Input: a frozen snapshot of the session's .jsonl transcript (Claude Code's
own on-disk log format). Every record is either type="user" (a genuine
human text message, OR a tool_result injected with role="user" per the
Anthropic API convention) or type="assistant" (text / thinking / tool_use
content blocks).

Two separate measurements, never mixed:
  - conversation-only: user text blocks + assistant text blocks only.
  - full-runtime: conversation-only + tool_use call args + tool_result
    content. Assistant "thinking" blocks are EXCLUDED from both -- the
    actual reasoning text is not recoverable from this transcript (the
    stored blocks have an empty "thinking" field and only an opaque
    encrypted "signature", Anthropic's redaction mechanism). This is a
    real, disclosed data limitation, not a design choice.

Turn boundary: a new turn starts at each user record that has at least
one text block and no tool_result block (i.e. a genuine fresh human
message, not a tool result arriving mid-turn). Everything between one
turn boundary and the next (assistant blocks, tool_result-carrying user
records) belongs to that turn.

AIR side (full-runtime only -- conversation-only has no tool output to
compress): every tool_result's content is put() into a real
air.context.engine.ContextEngine instance as it's encountered, then at
every turn boundary the CURRENT render() of all items so far replaces
the literal tool_result text in the AIR-side cumulative string. This is
air.context.engine.ContextEngine's actual, unmodified code
(INLINE_THRESHOLD_CHARS=200, DEFAULT_SUMMARY_CHARS=120) -- not a
reimplementation.

Tokenizer: HuggingFaceTB/SmolLM2-360M-Instruct via
air/mcp_server/tokens.py::count_tokens (the same real tokenizer AIR's
own synthetic benchmark uses), loaded local_files_only.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

AIR_ROOT = Path(r"E:\x\air")
sys.path.insert(0, str(AIR_ROOT))

from context.engine import ContextEngine  # noqa: E402
from mcp_server.tokens import count_tokens, MODEL_NAME  # noqa: E402

TRANSCRIPT_PATH = Path(r"E:\x\token_bench\transcript_snapshot.jsonl")
OUT_DIR = Path(r"E:\x\token_bench")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_blocks(rec: dict) -> dict:
    """Pull out the four content categories from one record, as raw text
    pieces (in order). Returns a dict with keys: text, tool_use, tool_result
    (each a list of strings), and a bool is_turn_boundary (only meaningful
    for type=user records)."""
    out = {"text": [], "tool_use": [], "tool_result": [], "is_turn_boundary": False}
    t = rec.get("type")
    msg = rec.get("message", {})
    content = msg.get("content")

    if t == "user":
        has_text = False
        has_tool_result = False
        if isinstance(content, str):
            out["text"].append(content)
            has_text = True
        elif isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                ct = c.get("type")
                if ct == "text":
                    out["text"].append(c.get("text", ""))
                    has_text = True
                elif ct == "tool_result":
                    has_tool_result = True
                    cc = c.get("content")
                    if isinstance(cc, str):
                        out["tool_result"].append(cc)
                    elif isinstance(cc, list):
                        for cc2 in cc:
                            if isinstance(cc2, dict) and cc2.get("type") == "text":
                                out["tool_result"].append(cc2.get("text", ""))
        out["is_turn_boundary"] = has_text and not has_tool_result

    elif t == "assistant":
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                ct = c.get("type")
                if ct == "text":
                    out["text"].append(c.get("text", ""))
                elif ct == "tool_use":
                    # the call itself (tool name + args) -- small, not
                    # put() through ContextEngine; only the RESULT is.
                    call_repr = json.dumps({"tool": c.get("name"), "input": c.get("input", {})}, ensure_ascii=False)
                    out["tool_use"].append(call_repr)
                # "thinking" deliberately excluded -- see module docstring.

    return out


def load_turns(path: Path) -> list[dict]:
    """Group the flat record stream into turns. Each turn is a dict with
    lists of (text|tool_use|tool_result) strings, in original order,
    tagged with which record type produced them (for potential future
    breakdown, not required by the current report)."""
    turns: list[dict] = []
    current = None

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue

            blocks = extract_blocks(rec)

            if rec.get("type") == "user" and blocks["is_turn_boundary"]:
                current = {"text": [], "tool_use": [], "tool_result": []}
                turns.append(current)

            if current is None:
                # content appearing before the first genuine human turn
                # (e.g. a leading system/tool record) -- put it in an
                # implicit "turn 0" bucket so no data is silently dropped.
                current = {"text": [], "tool_use": [], "tool_result": []}
                turns.insert(0, current)

            current["text"].extend(blocks["text"])
            current["tool_use"].extend(blocks["tool_use"])
            current["tool_result"].extend(blocks["tool_result"])

    return turns


def build_series(turns: list[dict], mode: str):
    """mode = 'conversation' or 'full'. Returns per-turn (baseline_text,
    air_text) where air_text == baseline_text for 'conversation' mode
    (no tool output to compress there).

    Tracks two cumulative lists incrementally (no re-derivation from
    `turns` per iteration, which would be O(n^2) for no reason):
    - baseline_parts: every piece of text, in order, mode-dependent.
    - non_tool_result_parts: text + tool_use only (never tool_result) --
      the part of AIR's context that is identical to baseline, since AIR
      only compresses tool RESULTS, not conversation text or the call
      itself.
    """
    engine = ContextEngine()
    baseline_parts: list[str] = []
    non_tool_result_parts: list[str] = []
    tool_result_handle_order: list[str] = []

    rows = []
    for turn in turns:
        baseline_parts.extend(turn["text"])
        non_tool_result_parts.extend(turn["text"])

        if mode == "full":
            baseline_parts.extend(turn["tool_use"])
            non_tool_result_parts.extend(turn["tool_use"])
            for tr in turn["tool_result"]:
                baseline_parts.append(tr)
                handle = engine.put(tr, kind="tool_result", label=tr[:60].replace("\n", " "))
                tool_result_handle_order.append(handle)

        baseline_text = "\n".join(baseline_parts)

        if mode == "conversation":
            air_text = baseline_text
        else:
            rendered_tool_results = engine.render(tool_result_handle_order)
            air_text = "\n".join(non_tool_result_parts) + "\n" + rendered_tool_results

        rows.append((baseline_text, air_text))

    return rows


def tokenize_series(rows) -> list[dict]:
    out = []
    for i, (baseline_cum, air_cum) in enumerate(rows, start=1):
        t0 = time.perf_counter()
        b = count_tokens(baseline_cum)
        a = count_tokens(air_cum)
        elapsed = time.perf_counter() - t0
        out.append(
            {
                "turn": i,
                "baseline_cumulative_tokens": b["tokens"],
                "baseline_method": b["method"],
                "air_cumulative_tokens": a["tokens"],
                "air_method": a["method"],
                "tokenize_time_s": round(elapsed, 3),
            }
        )
    return out


def main():
    limit = None
    if len(sys.argv) > 1 and sys.argv[1] == "--limit":
        limit = int(sys.argv[2])

    print(f"tokenizer model: {MODEL_NAME}", flush=True)
    print(f"transcript: {TRANSCRIPT_PATH}", flush=True)
    print(f"transcript sha256: {file_sha256(TRANSCRIPT_PATH)}", flush=True)

    turns = load_turns(TRANSCRIPT_PATH)
    print(f"total turns detected: {len(turns)}", flush=True)
    if limit is not None:
        turns = turns[:limit]
        print(f"LIMIT active: using first {len(turns)} turns only (smoke test)", flush=True)

    suffix = f"_limit{limit}" if limit is not None else ""
    for mode in ("conversation", "full"):
        print(f"\n=== mode={mode} ===", flush=True)
        t0 = time.perf_counter()
        rows = build_series(turns, mode)
        results = tokenize_series(rows)
        elapsed = time.perf_counter() - t0
        print(f"mode={mode} done in {elapsed:.1f}s", flush=True)
        out_path = OUT_DIR / f"results_{mode}{suffix}.json"
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
