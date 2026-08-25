"""Separate, clearly-labeled cross-reference: Anthropic's own real
API-reported usage per assistant turn (input_tokens, cache_read/creation,
output_tokens, thinking_tokens). This is NOT the SmolLM2 tokenizer count
and is never mixed into the baseline/AIR comparison -- it's a different,
real data source (Claude's own tokenizer, measured server-side), reported
on its own so it isn't confused with either the harness's remaining-budget
counter or the SmolLM2-based analysis."""

from __future__ import annotations

import json
from pathlib import Path

TRANSCRIPT_PATH = Path(r"E:\x\token_bench\transcript_snapshot.jsonl")
OUT_PATH = Path(r"E:\x\token_bench\api_usage_summary.json")


def main():
    total_input = 0
    total_cache_read = 0
    total_cache_creation = 0
    total_output = 0
    total_thinking = 0
    n_api_calls = 0

    with open(TRANSCRIPT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            usage = rec.get("message", {}).get("usage")
            if not usage:
                continue
            n_api_calls += 1
            total_input += usage.get("input_tokens", 0)
            total_cache_read += usage.get("cache_read_input_tokens", 0)
            total_cache_creation += usage.get("cache_creation_input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            total_thinking += (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)

    summary = {
        "source": "Anthropic API 'usage' field, as logged per assistant record in this transcript",
        "tokenizer": "Claude's own server-side tokenizer (not SmolLM2, not comparable 1:1 to the rest of this report)",
        "n_api_calls": n_api_calls,
        "total_input_tokens": total_input,
        "total_cache_read_input_tokens": total_cache_read,
        "total_cache_creation_input_tokens": total_cache_creation,
        "total_output_tokens": total_output,
        "total_thinking_tokens": total_thinking,
        "note": (
            "input_tokens here is tiny per call because of prompt caching "
            "(cache_read_input_tokens dominates) -- this number reflects "
            "what Claude Code's own harness actually sent over the wire "
            "per API call, which already benefits from Anthropic's server-side "
            "prompt caching. It is NOT a baseline 'resend everything raw' "
            "measurement and is not directly comparable to the SmolLM2 "
            "cumulative-tokenization figures in this report."
        ),
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
