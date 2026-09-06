import re, sys, pathlib, json
base = pathlib.Path(sys.argv[1])
rows = []
for d in sorted(base.glob("r*"), key=lambda p: int(p.name[1:])):
    log = (d / "aider.log").read_text(errors="replace")
    sent = re.findall(r"Tokens: ([\d.]+)k? sent, ([\d.]+)k? received", log)
    rows.append(dict(
        run=d.name,
        llm_calls=len(re.findall(r"Tokens: ", log)),
        applied=len(re.findall(r"Applied edit to", log)),
        # aider's search/replace repair signals
        no_match=len(re.findall(r"SearchReplaceNoExactMatch|did not (?:exactly )?match|Failed to (?:apply|match)", log)),
        malformed=len(re.findall(r"badly formed|is not a valid|ELIDED|missing the (?:opening|closing)", log)),
        retries=len(re.findall(r"retr", log, re.I)),
        tokens=sent,
    ))
print(json.dumps(rows, indent=1))
