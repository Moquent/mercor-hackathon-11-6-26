"""Paths, model ids and the replay constants shared by every module."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(ROOT, "tasks")
ATTEMPTS = os.path.join(ROOT, "attempts")
RESULTS = os.path.join(ROOT, "results")
DOCS = os.path.join(ROOT, "docs")
RCAEVAL_DATA = os.path.join(ROOT, "data", "rcaeval")
RCAEVAL_TASKS = os.path.join(TASKS, "rcaeval")
RCAEVAL_ATTEMPTS = os.path.join(ATTEMPTS, "rcaeval")
JEV_CACHE = os.path.join(RESULTS, "jev_cache.json")

SUBJECT_MODEL = "gpt-6-luna"
JEV_MODEL = "jev-latest"
RUNS = (1, 2, 3)

SIM_T = 600                  # simulated seconds per replay
PAGE_T = 300                 # the on-call is paged here; actions apply from here
JUDGE_WINDOW = (450, 600)    # the SLO is judged over this window of the replay
SLO_ERROR_RATE = 0.05        # resolved if the entry error rate stays below this
JEV_THRESHOLD = 0.5          # Jev probability at which a catalog action or causal edge counts as claimed
