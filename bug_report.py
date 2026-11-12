import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_BREAK
from docx.shared import Inches, Pt, RGBColor
from docx.styles.style import ParagraphStyle

REPORT_DIR = Path(__file__).with_name("reports")
DEFAULT_NAME = "BHASKAR_PROD_Bug_Report.docx"
SEV = {"High": RGBColor(0xC6, 0x28, 0x28), "Medium": RGBColor(0xB4, 0x53, 0x09), "Low": RGBColor(0x1F, 0x4E, 0x79)}
SUMMARY_RX = re.compile(r"(\d+) issues - High (\d+), Medium (\d+), Low (\d+)")
HEADERS = ("ID", "Severity", "Type", "Stage", "Title")


def default_report() -> Path:
    found = sorted(REPORT_DIR.glob("BHASKAR_PROD_Bug_Report*.docx"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else REPORT_DIR / DEFAULT_NAME


def kv(doc: DocxDocument, key: str, value: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run(f"{key}: ").bold = True
    p.add_run(value)


def new_report(path: Path) -> DocxDocument:
    doc = Document()
    normal = doc.styles["Normal"]
    assert isinstance(normal, ParagraphStyle)
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    doc.add_heading("BHASKAR Production - Bug Report (Registration Journey)", 0)
    kv(doc, "Created", f"{datetime.now():%d-%b-%Y}")
    kv(doc, "Environment", "Production - https://www.startupindia.gov.in/bhaskar/register")
    kv(doc, "Journey", "Register -> Select entity type -> Select persona -> Basic details -> T&C -> BHASKAR ID generated "
                       "-> Complete your profile")
    kv(doc, "Summary", "0 issues - High 0, Medium 0, Low 0")
    table = doc.add_table(rows=1, cols=len(HEADERS))
    table.style = "Light Grid Accent 1"
    for cell, head in zip(table.rows[0].cells, HEADERS):
        cell.text = head
    path.parent.mkdir(parents=True, exist_ok=True)
    return doc


def load_bugs(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    bugs: list[dict[str, Any]] = data.get("bugs", [])
    for i, b in enumerate(bugs, 1):
        missing = [k for k in ("severity", "type", "stage", "title", "steps", "expected", "actual") if not b.get(k)]
        if missing:
            raise SystemExit(f"Bug {i} in {path.name} is missing: {', '.join(missing)}")
        if b["severity"] not in SEV:
            raise SystemExit(f"Bug {i}: severity must be one of {', '.join(SEV)}")
    run: dict[str, Any] = data.get("run", {})
    return run, bugs


def append(report: Path, bugs_file: Path) -> None:
    run, bugs = load_bugs(bugs_file)
    if not bugs:
        raise SystemExit(f"No bugs in {bugs_file}")
    doc = Document(str(report)) if report.exists() else new_report(report)
    summary = next((p for p in doc.paragraphs if p.text.startswith("Summary:")), None)
    match = SUMMARY_RX.search(summary.text) if summary is not None else None
    if summary is None or match is None:
        raise SystemExit(f"'Summary: N issues - High .., Medium .., Low ..' line not found in {report.name}")
    n, high, medium, low = map(int, match.groups())
    counts = {"High": high, "Medium": medium, "Low": low}
    for b in bugs:
        counts[b["severity"]] += 1
    first, last = n + 1, n + len(bugs)
    summary.runs[-1].text = (f"{last} issues - High {counts['High']}, Medium {counts['Medium']}, Low {counts['Low']} "
                             f"(BUG-{first:02d} to BUG-{last:02d} added {datetime.now():%d-%b-%Y})")

    table = doc.tables[0]
    for i, b in enumerate(bugs, first):
        cells = table.add_row().cells
        for cell, value in zip(cells, (f"BUG-{i:02d}", b["severity"], b["type"], b["stage"], b["title"])):
            cell.text = value
        run_ = cells[1].paragraphs[0].runs[0]
        run_.bold, run_.font.color.rgb = True, SEV[b["severity"]]

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading(run.get("heading") or f"Findings added {datetime.now():%d-%b-%Y %H:%M}", 1)
    info = cast(dict[str, Any], run.get("info") or {})
    for key, value in info.items():
        kv(doc, str(key), str(value))
    rows = cast(list[dict[str, Any]], run.get("classification") or [])
    if rows:
        doc.add_heading(str(run.get("classification_heading") or "Where each issue applies"), 2)
        for line in cast(list[str], run.get("classification_notes") or []):
            doc.add_paragraph(str(line), style="List Bullet")
        ctable = doc.add_table(rows=1, cols=3)
        ctable.style = "Light Grid Accent 1"
        for cell, head in zip(ctable.rows[0].cells, ("ID", "Issue", "Applies to")):
            cell.text = head
        for row in rows:
            cells = ctable.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = str(row.get("id", "")), str(row.get("title", "")), str(row.get("applies_to", ""))
    updates = cast(list[dict[str, Any]], run.get("updates") or [])
    if updates:
        doc.add_heading("Updates to earlier bugs", 2)
        for u in updates:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{u.get('id', '')}: ").bold = True
            p.add_run(str(u.get("text", "")))

    missing: list[str] = []
    for i, b in enumerate(bugs, first):
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading(f"BUG-{i:02d}: {b['title']}", 1)
        p = doc.add_paragraph()
        p.add_run("Severity: ").bold = True
        r = p.add_run(b["severity"])
        r.bold, r.font.color.rgb = True, SEV[b["severity"]]
        p.add_run(f"    Type: {b['type']}    Stage: {b['stage']}")
        if b.get("applies_to"):
            p = doc.add_paragraph()
            p.add_run("Applies to: ").bold = True
            p.add_run(str(b["applies_to"]))
        doc.add_paragraph().add_run("Steps to reproduce").bold = True
        for s in b["steps"]:
            doc.add_paragraph(str(s), style="List Number")
        for label, key in (("Expected: ", "expected"), ("Actual: ", "actual")):
            p = doc.add_paragraph()
            p.add_run(label).bold = True
            p.add_run(str(b[key]))
        shots: list[dict[str, Any]] = b.get("evidence") or []
        if shots:
            doc.add_paragraph().add_run("Evidence").bold = True
        for shot in shots:
            img = Path(shot["file"])
            img = img if img.is_absolute() else bugs_file.parent / img
            if not img.exists():
                missing.append(str(img))
                continue
            doc.add_paragraph(str(shot.get("caption", img.name))).runs[0].italic = True
            doc.add_picture(str(img), width=Inches(float(shot.get("width", 6.0))))

    while True:
        try:
            doc.save(str(report))
            break
        except PermissionError:
            input(f"{report.name} is open in Word. Close it, then press Enter to save.")
    print(f"[REPORT] {report} - added BUG-{first:02d} to BUG-{last:02d}")
    if missing:
        print("[WARN] Evidence images not found: " + "; ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser(description="Append new bugs from a JSON file to the BHASKAR bug report (Word).")
    parser.add_argument("bugs_file", type=Path, help="JSON file with the new bugs (see bugs_template.json)")
    parser.add_argument("--report", type=Path, default=None,
                        help=f"Word file to update (default: newest reports/BHASKAR_PROD_Bug_Report*.docx, "
                             f"or a new {DEFAULT_NAME})")
    args = parser.parse_args()
    append(args.report or default_report(), args.bugs_file)


if __name__ == "__main__":
    main()
