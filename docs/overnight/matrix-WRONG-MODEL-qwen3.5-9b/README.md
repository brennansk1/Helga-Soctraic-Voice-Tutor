# Discard these results — wrong model

This run started 2026-08-20 09:24 and was killed at ~10:50, incomplete.

`tools/golden_courses.py matrix` was invoked by `/tmp/task0/final_long_runs.sh`,
which exports `LLM_API_URL`, `OLLAMA_URL` and `RESEARCH_URL` but **not**
`OLLAMA_MODEL`. `services/common/model_roles.py` defaulted to `qwen3.5:9b`,
so the whole run measured the previous model rather than `nail-35b-a3b-ctx`.

`docs/MODEL.md` claimed the Nail tag was already "the default in
docker-compose.yml and in the code defaults" — only the compose half had been
done. Fixed by giving the default one home, `model_roles.DEFAULT_MODEL`.

Kept only as evidence of the drift. Do not compare anything against these
numbers: the golden matrix exists to fix the n=1 problem for the configuration
that ships, and this is not that configuration.
