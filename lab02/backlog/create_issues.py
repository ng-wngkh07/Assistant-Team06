#!/usr/bin/env python3
"""Create the Lab 2 backlog in your team repository with the GitHub CLI.

Usage (inside the team repo, after `gh auth login`):
    python create_issues.py issues.csv --milestone "Lab 2" --dry-run
    python create_issues.py issues.csv --milestone "Lab 2"
Then assign issues on GitHub: every member takes at least two.
"""
import argparse
import csv
import subprocess

p = argparse.ArgumentParser()
p.add_argument("csv_file")
p.add_argument("--milestone", default=None)
p.add_argument("--dry-run", action="store_true")
args = p.parse_args()

with open(args.csv_file, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

labels = sorted({lab for r in rows for lab in r["labels"].split(",")} | {"size:S", "size:M"})
for lab in labels:
    cmd = ["gh", "label", "create", lab, "--force"]
    print(" ".join(cmd)) if args.dry_run else subprocess.run(cmd, check=False)

for r in rows:
    body = f"{r['body']}\n\n**Component:** {r['component']}\n**Size:** {r['size']}\n\n## Definition of Done\nSee DEFINITION_OF_DONE.md"
    cmd = ["gh", "issue", "create", "--title", r["title"], "--body", body,
           "--label", f"{r['labels']},size:{r['size']}"]
    if args.milestone:
        cmd += ["--milestone", args.milestone]
    print(" ".join(cmd[:4]) + " ...") if args.dry_run else subprocess.run(cmd, check=True)
print(f"{len(rows)} issues {'would be ' if args.dry_run else ''}created.")
