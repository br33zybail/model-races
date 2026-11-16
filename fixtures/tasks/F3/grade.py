import json
import os
import sys


def main():
    ws = sys.argv[1]
    planted = json.load(open(os.path.join(os.path.dirname(__file__), "planted.json")))
    path = os.path.join(ws, "findings.md")
    text = open(path).read().lower() if os.path.exists(path) else ""
    caught = 0
    for issue, kws in planted.items():
        if sum(1 for k in kws if k.lower() in text) >= 2:
            caught += 1
    print(f"SCORE {caught/len(planted):.3f}")


if __name__ == "__main__":
    main()
