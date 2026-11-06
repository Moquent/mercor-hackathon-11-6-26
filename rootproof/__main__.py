"""Command line.

    python3 -m rootproof evaluate            re-verify every saved answer and rebuild results/ and docs/
    python3 -m rootproof ask                 freeze prompts and ask the subject AI for simulated incidents
    python3 -m rootproof ask-rcaeval         ask the subject AI about the prepared RCAEval cases
    python3 -m rootproof prepare-rcaeval DIR build RCAEval packets from downloaded cases (needs pandas, pyarrow)
    python3 -m rootproof prompt ID           print the exact prompt for one incident
"""
import sys

USAGE = __doc__


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "evaluate":
        from .pipeline import run_evaluation
        return run_evaluation()
    if cmd == "ask":
        from .harness import ask_all
        return ask_all()
    if cmd == "ask-rcaeval":
        from .rcaeval.run import ask_all
        return ask_all()
    if cmd == "prepare-rcaeval":
        from .rcaeval.prepare import prepare_all
        return prepare_all(rest[0])
    if cmd == "prompt":
        from .packets import prompt
        from .scenario.incidents import INCIDENTS
        matches = [i for i in INCIDENTS if i.id.startswith(rest[0])]
        sys.stdout.write(prompt(matches[0]))
        return 0
    print(f"unknown command: {cmd}\n\n{USAGE}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
