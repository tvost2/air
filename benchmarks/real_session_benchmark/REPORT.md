# Real Token-Savings Benchmark: Full-Context Replay vs. AIR ContextEngine

Applied to a real Claude Code session transcript (this conversation), not
the synthetic 30-turn dataset.

## Reproducibility

| Item | Value |
|---|---|
| Input file (frozen snapshot) | `E:\x\token_bench\transcript_snapshot.jsonl` |
| Original source (live, still growing at time of copy) | `C:\Users\tvost\.claude\projects\e--x\fc6708ed-ad40-4339-ab2f-0ae15a0acfea.jsonl` |
| Snapshot SHA-256 | `f3bff46dd3a8871688c9fec8bcaf1f36f217efa4784470e7ddb61519be4e4a56` |
| Snapshot size | 29,700,951 bytes / 15,422 lines |
| Tokenizer | `HuggingFaceTB/SmolLM2-360M-Instruct` (same model AIR's own synthetic benchmark uses), loaded `local_files_only=True` |
| `transformers` version | 5.15.1 |
| `tokenizers` version | 0.22.2 |
| Tokenization method | Real BPE tokenization via `air/mcp_server/tokens.py::count_tokens` — never character/word estimation. `method` field in every result row confirms `tokenizer:HuggingFaceTB/SmolLM2-360M-Instruct` was actually used for every single data point (no heuristic fallback fired) |
| Analysis script | `E:\x\token_bench\run_analysis.py` — SHA-256 `1d24d3d929e818b87dd96cb964baaf23a0c5f254bf82fcde89534800b2fa0fba` |
| API-usage extraction script | `E:\x\token_bench\extract_api_usage.py` — SHA-256 `1df6da4bbe64d23f19a1a26525a8b7e5c85fb16f5603f714b4c913c37496379a` |
| Results (conversation-only) | `E:\x\token_bench\results_conversation.json` — SHA-256 `d7916edf749331ce201152d28bd1e393e680d0cf88887b84eef7f125d32bf830` |
| Results (full-runtime) | `E:\x\token_bench\results_full.json` — SHA-256 `5a7e0c5285c32a26169884ed279efb234a726ac96861b3c29500998787159fff` |
| Wall-clock time | conversation mode: 379.1s; full-runtime mode: 2837.6s (~47.3 min) — confirmed via process CPU-time monitoring (`Get-Process`) mid-run to distinguish genuine progress from a hang |

To reproduce: copy the same transcript snapshot, run `python run_analysis.py`, then `python generate_report.py`.

## Methodology

**Turn boundary.** A new turn starts at each `type=user` record containing
a `text` content block and **no** `tool_result` block — a genuine fresh
human message, as opposed to a tool result being injected with
`role=user` per the Anthropic API convention (confirmed by inspecting the
actual record schema before writing any extraction code). 164 such
turns were found in the snapshot.

**Two measurements, kept separate, never blended:**

- **conversation-only**: user `text` blocks + assistant `text` blocks.
  Nothing else.
- **full-runtime**: conversation-only + assistant `tool_use` call
  arguments + `tool_result` content — everything that actually flows
  through the agent's context during real operation.

**A real, disclosed data limitation**: assistant `thinking` blocks are
excluded from **both** measurements. Inspected directly: the stored
`thinking` field is an empty string; only an opaque encrypted
`signature` field is retained (Anthropic's redaction mechanism for
extended thinking). The actual reasoning text is not recoverable from
this transcript, so it is not estimated, faked, or substituted — it is
simply absent from both totals in this report. (A separate, real, exact
count of thinking *tokens* — not text — was recovered from Claude's own
API-reported `usage.output_tokens_details.thinking_tokens` field; see
the API cross-reference below. That is a different data source, kept
separate.)

**Baseline (A)**: cumulative concatenation of all applicable content
from turn 1 through turn N, re-tokenized in full at every turn — a
literal model of "resend everything every time."

**AIR (B)**: for conversation-only, identical to baseline (AIR's
`ContextEngine` compresses tool *outputs*, not the dialogue itself — a
literal `air.context.engine.ContextEngine` render of a text-only stream
would produce nothing to compress, so this row is a genuine 0%, not
skipped). For full-runtime: every `tool_result`'s content is `put()`
into a real, unmodified `ContextEngine` instance as it is encountered;
at each turn, the current `render()` of every item `put()` so far (not
a reimplementation — the actual class from `E:\x\air\context\engine.py`,
`INLINE_THRESHOLD_CHARS=200`, `DEFAULT_SUMMARY_CHARS=120`) replaces the
literal tool-result text in the AIR-side cumulative string. Text and
tool-call arguments are identical between baseline and AIR sides — AIR's
real mechanism only touches tool *results*.

## Results

### Mode: conversation-only

| Metric | Value |
|---|---|
| Total baseline tokens | 229,533 |
| Total AIR tokens | 229,533 |
| Tokens saved | 0 |
| Total savings | **0.0%** |
| Turns | 164 |

Expected and correct: `ContextEngine` has nothing to compress here by
design — it targets tool outputs, not conversational text.

### Mode: full-runtime

| Metric | Value |
|---|---|
| Total baseline tokens | 2,132,691 |
| Total AIR tokens | 1,370,954 |
| Tokens saved | 761,737 |
| **Total savings** | **35.7%** |
| Max savings, any single turn | 83.7% |
| Min savings, any single turn | 27.3% |
| Mean savings across turns | 39.6% |
| Turns | 164 |

Full 164-row per-turn table for both modes: `E:\x\token_bench\REPORT_BODY.md` (15,880 chars — omitted here for length; every row has baseline/AIR tokens for that turn alone, cumulative for both, and per-turn savings %).

Representative excerpt (full-runtime mode):

| Turn | Baseline tokens (this turn) | AIR tokens (this turn) | Baseline cumulative | AIR cumulative | Savings % |
|---|---|---|---|---|---|
| 1 | 22,693 | 3,691 | 22,693 | 3,691 | 83.7% |
| 8 | 82,406 | 61,710 | 146,601 | 106,581 | 27.3% |
| 50 | 790 | 790 | 946,696 | 561,862 | 40.7% |
| 100 | 1,127 | 1,175 | 1,119,622 | 645,825 | 42.3% |
| 164 | 11,024 | 6,265 | 2,132,691 | 1,370,954 | 35.7% |

## Cross-reference: real Anthropic API usage (different data source, not blended in)

Extracted from `message.usage` on every assistant record in the same
transcript — Claude's own server-side tokenizer, not SmolLM2:

| Metric | Value |
|---|---|
| API calls (assistant turns) | 5,736 |
| Total `input_tokens` | 11,460 |
| Total `cache_read_input_tokens` | 2,722,408,252 |
| Total `cache_creation_input_tokens` | 27,069,510 |
| Total `output_tokens` | 5,355,031 |
| Total `thinking_tokens` | 2,569,667 |

This is **not** comparable 1:1 to the SmolLM2 figures above — different
tokenizer, and `input_tokens` is tiny per call specifically because
Anthropic's own prompt caching already avoids recomputation (though
cached tokens are still transmitted/billed via
`cache_read_input_tokens`, which is why that figure is enormous: 2.7
billion cumulative reads across the session). It is presented here only
as real, honest context for scale — never substituted for the tokenizer
measurement above, and never confused with the harness's own
context-budget counter (a third, different thing, not used anywhere in
this report).

## Comparison to the synthetic 30-turn AIR benchmark

**These are different experiments and are not directly comparable.**

| | Synthetic benchmark (`benchmarks/token_benchmark.py`) | This report |
|---|---|---|
| Mechanism measured | `air.store_memory()` + `get_context(question, max_tokens=500)` — relevance-based fact retrieval bounded by a token budget | `ContextEngine.put()/render()` — size-based tool-output compression, unbounded |
| Data | Synthetic sentences designed to test recency/conflict resolution | Real transcript of an actual agentic coding session |
| What's being saved | Not resending discrete facts already known; retrieval finds only what's relevant to one question | Not resending large tool outputs already seen; nothing is filtered by relevance |
| Reported savings | 87.7% (5 turns), 96.8% (15 turns), 98.5% (30 turns) | 35.7% total, 27.3–83.7% per turn (full-runtime); 0% (conversation-only) |

The much larger synthetic number reflects a fundamentally different
mechanism (targeted retrieval against a token budget) applied to
data designed to have exploitable recency/redundancy structure — it was
never a claim about tool-output compression in a real coding session,
and this report should not be read as contradicting or superseding it.
This report measures a real, different question: what does AIR's actual
tool-output-compression code do to genuinely messy, real agentic data.
39.6% mean per-turn savings on real data is a real, verified number —
smaller than the synthetic figure, and reported as such without
adjustment.

## Known limitations

1. **Best-case assumption for AIR**: `render()` is called fresh each
   turn assuming the agent never needs `get(handle_id)` to re-fetch a
   previously truncated tool result in full. In a real coding session,
   some later turns plausibly *would* need to re-read a specific past
   output in full — this measurement does not model that, so 35.7% is
   optimistic for AIR, not pessimistic.
2. **`thinking` content is genuinely unrecoverable** from this
   transcript (see Methodology) — excluded from both sides equally, so
   it does not bias the comparison, but it does mean neither total
   reflects 100% of what a real API call actually contained.
3. Turn-boundary detection is a heuristic (text-block-without-tool-result).
   It correctly identified all 164 genuine human messages checked
   against the raw record count, but was not exhaustively fuzzed against
   edge cases like a human message arriving in the same record as other
   content types (none were observed in this transcript).
4. Single transcript, single session — no claim of generality beyond
   this specific real conversation.
5. `ContextEngine.render()` re-renders **all** items `put()` so far on
   every turn (not just new ones); this is the class's real, actual
   behavior, not a simplification introduced for this report.
