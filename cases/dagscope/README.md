# DAGScope benchmark data

Source: DAGScope by Sarvesh, branch `origin/sarvesh`, commit `5310275` (`benchmark/` directory, excluding
`benchmark/rcaeval`, which is handled separately). Copied verbatim; nothing here was regenerated.

| Folder | What it is |
|---|---|
| `runs/` | One captured execution per scenario (`scenario_1..4.json`): events, explicit dependency records, coverage metadata, integrity report, `manifest` (logical name -> event id), `sql_closure` (all reachable pairs in the complete private graph), persisted DB state, interventions. |
| `tasks/` | The 12 public tasks (what the AI sees): question, event-id pairs to judge with allowed relationships, coverage, AI-visible log export (with and without the `parents=` column). |
| `ground_truth/` | Private answers per task: expected relationship per slot (computed by hand-written BFS + DB state, not an LLM), the mask that hid events/edges from the AI. |
| `model_attempts/` | First attempts per task. `openai_gpt-5-mini__explicit_parents` and `openai_gpt-5-mini__chronological_only` are real model output; `HANDCRAFTED_*` are fixtures (correct / plausible-but-wrong). New gpt-6-luna attempts from the RootProof page are saved in `gpt-6-luna/`. |
| `results/` | DAGScope's graded output (`per_task.json`, `metrics.json`) per attempt set, plus `REPORT.md`. |

The teammate’s saved `openai/gpt-5-mini` results were obtained via OpenRouter (2026-11-06). The separate `gpt-6-luna` attempt set contains RootProof’s own first attempts; handcrafted fixtures are not model output.

`rootproof/dagscope/verify.py` is a stdlib port of DAGScope's verifier and grader; it reproduces every saved
`results/*/per_task.json` record exactly (48 tasks, 128 claims, including dependency paths).
