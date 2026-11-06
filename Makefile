PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: rcaeval-fetch rcaeval-validate rcaeval-tasks rcaeval-evaluate venv up down smoke live benchmark tasks fixtures test test-offline mutation evaluate evaluate-ablation report dashboard-local package

venv:            ## Local Python env (outside any synced folder if you prefer)
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
up:              ## Build and start everything
	docker compose up --build -d
down:
	docker compose down
smoke:           ## One real order through every service, then verified
	$(PY) scripts/smoke_test.py
live:            ## Run all scenarios on the live stack without touching the canonical benchmark
	$(PY) scripts/run_scenarios.py --out benchmark/live
benchmark:       ## Regenerate canonical runs, tasks, fixtures (refuses once real attempts exist)
	$(PY) scripts/run_scenarios.py && $(PY) scripts/generate_tasks.py && $(PY) scripts/generate_fixtures.py
tasks:
	$(PY) scripts/generate_tasks.py
fixtures:
	$(PY) scripts/generate_fixtures.py
test:            ## All tests; writes benchmark/results/verifier_tests.json
	$(PY) scripts/run_verifier_tests.py
test-offline:    ## No Docker needed: runs against the captured real traces
	$(PY) -m pytest --ignore=tests/test_end_to_end.py
mutation:        ## Plant bugs in the verifier and check the tests catch them
	$(PY) scripts/mutation_check.py
evaluate:        ## First attempts from MODEL_NAME on all 12 tasks
	$(PY) scripts/evaluate_model.py
evaluate-ablation: ## Same tasks with the parents column removed
	$(PY) scripts/evaluate_model.py --condition chronological_only
rcaeval-fetch:   ## Download the fixed 16-case RCAEval sample (~200 MB) to ~/.cache/dagscope
	$(PY) -I scripts/rcaeval_fetch.py
rcaeval-validate: ## Verifier integrity, oracle and masking checks on real traces (needs the fetch)
	$(PY) -I scripts/rcaeval_validate.py
rcaeval-tasks:   ## Regenerate external tasks (refused once attempts exist)
	$(PY) -I scripts/rcaeval_tasks.py
rcaeval-evaluate: ## First attempts on the external tasks
	$(PY) scripts/rcaeval_evaluate.py
report:
	$(PY) -m app.evaluation.report
dashboard-local: ## Dashboard without Docker; replays captured evidence
	$(PY) -m streamlit run app/dashboard/app.py --client.toolbarMode minimal
package:         ## dist/dagscope-submission.zip, without secrets or caches
	$(PY) scripts/package_submission.py
