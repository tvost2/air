"""Builds the final markdown report from results_conversation.json and
results_full.json -- the required per-turn table plus summary stats, for
each of the two measurements, kept in clearly separate sections."""

from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(r"E:\x\token_bench")


def load(mode: str):
    path = OUT_DIR / f"results_{mode}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def savings_pct(baseline_cum: int, air_cum: int) -> float:
    if baseline_cum == 0:
        return 0.0
    return 1 - (air_cum / baseline_cum)


def build_table(results: list[dict]) -> tuple[str, list[float]]:
    lines = ["| Turn | Baseline tokens | AIR tokens | Baseline cumulative | AIR cumulative | Savings % |", "|---|---|---|---|---|---|"]
    savings_list = []
    prev_baseline_cum = 0
    prev_air_cum = 0
    for r in results:
        b_cum = r["baseline_cumulative_tokens"]
        a_cum = r["air_cumulative_tokens"]
        b_turn = b_cum - prev_baseline_cum
        a_turn = a_cum - prev_air_cum
        sav = savings_pct(b_cum, a_cum)
        savings_list.append(sav)
        lines.append(f"| {r['turn']} | {b_turn} | {a_turn} | {b_cum} | {a_cum} | {sav*100:.1f}% |")
        prev_baseline_cum = b_cum
        prev_air_cum = a_cum
    return "\n".join(lines), savings_list


def summarize(results: list[dict], savings_list: list[float]) -> dict:
    last = results[-1]
    return {
        "total_baseline_tokens": last["baseline_cumulative_tokens"],
        "total_air_tokens": last["air_cumulative_tokens"],
        "tokens_saved": last["baseline_cumulative_tokens"] - last["air_cumulative_tokens"],
        "total_savings_pct": savings_pct(last["baseline_cumulative_tokens"], last["air_cumulative_tokens"]) * 100,
        "max_savings_pct_any_turn": max(savings_list) * 100 if savings_list else 0.0,
        "min_savings_pct_any_turn": min(savings_list) * 100 if savings_list else 0.0,
        "mean_savings_pct": (sum(savings_list) / len(savings_list) * 100) if savings_list else 0.0,
        "n_turns": len(results),
    }


def main():
    report_parts = []
    for mode in ("conversation", "full"):
        results = load(mode)
        table, savings_list = build_table(results)
        summary = summarize(results, savings_list)
        report_parts.append(f"## Mode: {mode}\n\n{table}\n\n### Summary\n\n" + "\n".join(f"- **{k}**: {v}" for k, v in summary.items()))

    full_report = "\n\n".join(report_parts)
    out_path = OUT_DIR / "REPORT_BODY.md"
    out_path.write_text(full_report, encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"length: {len(full_report)} chars")


if __name__ == "__main__":
    main()
