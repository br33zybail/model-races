import csv
import json
import os
import sys


def main():
    ws = sys.argv[1]
    gold = json.load(open(os.path.join(os.path.dirname(__file__), "gold.json")))
    path = os.path.join(ws, "out.csv")
    if not os.path.exists(path):
        print("SCORE 0.0")
        return
    got = {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip().isdigit():
                got[row[0].strip()] = row[1].strip().lower()
    hits = sum(1 for k, v in gold.items() if got.get(k) == v)
    print(f"SCORE {hits/len(gold):.3f}")


if __name__ == "__main__":
    main()
