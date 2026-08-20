# Golden matrix — stopped part-way, deliberately

Started 10:47 on `nail-35b-a3b-ctx` (the correct model; the earlier run in
`../matrix-WRONG-MODEL-qwen3.5-9b/` measured the wrong one).

Stopped to free the machine for the A4.1a/b measurement — the domain
benchmark needs the model, and the two cannot share it: a plain
`/v1/chat/completions` probe timed out at 90s while the matrix was building.

## What it produced before stopping

| slot | request | outcome |
|---|---|---|
| 1/6 | scope=2 mastery=2 | **course_440a8494 — GATE: PASS** (2884s) |
| 2/6 | scope=5 mastery=5 | **FAILED** — the model returned 5, 7, 7 modules against a required 11, three attempts |
| 3/6 | scope=3 mastery=4 | building when stopped |

Course 1's gate result, re-run independently:

    structure  4M / 8U / 12L / 24C   degenerate lessons: 0
    content    835/1109/1426 words   stubs=0  missing=0  total=26,403
    grounding  citations 100.0%   42 unique URLs   confidence mean 0.896
    bloom      [1,2,2,3] span=2 monotonic
    depth      mastery=2 met=95.8%  level_verified
    GATE: PASS

## The two findings worth carrying forward

1. **scope=5 cannot hit its module count on a sourceless topic.** Required 11,
   the model produced 7 twice and 5 once. Either the requirement is wrong for
   that scope or the prompt needs the same named-violation treatment the
   dialogue contract just got.
2. **Research was rate-limited.** Six HTTP 429s from wikibooks/wikiversity
   during course 1 alone, so no source cleared the 6.0 grounding bar and the
   skeleton was model-proposed (`CHECK:SYLLABUS:INADEQUATE:0%`). The CONTENT
   was still well cited — those are different measurements — but the SHAPE of
   the course had no external warrant.

Re-run with `/tmp/task0/final_long_runs.sh`, which now exports OLLAMA_MODEL
explicitly so the run states what it measured.
