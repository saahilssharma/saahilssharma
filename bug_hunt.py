import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.sync_api import ConsoleMessage, Error, Page, Request, Response

import personas as SF
import signup as S

BUG_DIR = S.REPORT_DIR / "bug_runs"
WATCH = ("/sih/api", "/bhaskar/")


class Findings:
    def __init__(self) -> None:
        self.started = datetime.now()
        self.items: list[str] = []
        self.calls: list[str] = []

    def add(self, kind: str, text: str) -> None:
        line = f"{datetime.now():%H:%M:%S} [{kind}] {text}"
        if line.split("] ", 1)[1] not in (x.split("] ", 1)[1] for x in self.items):
            self.items.append(line)
            print(f"[FINDING] {kind}: {text}")

    def on_response(self, r: Response) -> None:
        if not any(w in r.url for w in WATCH):
            return
        url = r.url.split("?")[0]
        if r.status >= 400:
            self.add("HTTP error", f"{r.request.method} {url} -> HTTP {r.status}")
        if "/sih/api" not in r.url or r.request.method not in ("POST", "PUT", "PATCH"):
            return
        try:
            body: Any = r.json()
        except Exception:
            return
        if not isinstance(body, dict):
            return
        reply = cast(dict[str, Any], body)
        self.calls.append(f"{r.request.method} {url} -> HTTP {r.status} status={reply.get('status')} "
                          f"message={reply.get('message')!r}")
        if reply.get("status") is False:
            self.add("Server refused", f"{url} -> {reply.get('message')!r} {json.dumps(reply.get('data'))[:300]}"
                     " - check the step screenshot: was this reason shown on the page?")

    def on_console(self, m: ConsoleMessage) -> None:
        if m.type == "error":
            self.add("Console error", m.text[:300])

    def on_page_error(self, e: Error) -> None:
        self.add("Page error", f"{e.name}: {e.message}"[:300])

    def on_request_failed(self, r: Request) -> None:
        if any(w in r.url for w in WATCH):
            self.add("Request failed", f"{r.method} {r.url.split('?')[0]} ({r.failure})")

    def attach(self, page: Page) -> None:
        page.on("response", self.on_response)
        page.on("console", self.on_console)
        page.on("pageerror", self.on_page_error)
        page.on("requestfailed", self.on_request_failed)

    def save(self, persona: str, result: str) -> Path:
        BUG_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", persona).strip("_")
        out = BUG_DIR / f"{self.started:%Y-%m-%d_%H%M%S}_{slug}_findings.md"
        lines = [f"# Bug-finding run {self.started:%d-%b-%Y %H:%M:%S}", "",
                 f"- Persona: {persona}", f"- Test user: {S.DATA['email']} / {S.DATA['mobile']}",
                 f"- Start: {'log in to existing account' if S.DATA['mode'] == 'login' else 'new sign-up'}",
                 f"- Result: {result}", f"- Screenshots and Word report: {BUG_DIR}", "",
                 "## Automatic findings (review each one before adding it to the bug report)", ""]
        lines += [f"- {x}" for x in self.items] or ["- None recorded"]
        lines += ["", "## Server calls (POST/PUT/PATCH)", ""] + ([f"- {x}" for x in self.calls] or ["- None"])
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out


ACCOUNTS_FILE = BUG_DIR / "test_accounts.json"
TEST_DATA_XLSX = BUG_DIR / "BHASKAR_Bug_Run_Test_Data.xlsx"
EMAIL_BASE = "whonobodyme00"
USED_EMAILS = {"whonobody.me00", "who.nobody.me00", "w.honobody.me00"}
MOBILE_BASE = 5550200100
XLSX_COLUMNS = ["#", "Entity", "Persona", "Email", "Mobile", "Password", "First name", "Last name", "Year of birth",
                "Gender", "PIN", "Individual PAN", "Entity PAN", "CIN", "GSTIN", "Organisation name", "BHASKAR ID",
                "Result", "Stopped at / error", "Last run"]


def email_variants() -> list[str]:
    out: list[str] = []
    n = len(EMAIL_BASE)
    for i in range(1, n):
        for j in range(i + 1, n):
            local = f"{EMAIL_BASE[:i]}.{EMAIL_BASE[i:j]}.{EMAIL_BASE[j:]}"
            if local.replace(".", "") == EMAIL_BASE and local not in USED_EMAILS:
                out.append(local)
    return out


def load_accounts() -> dict[str, dict[str, str]]:
    accounts: dict[str, dict[str, str]] = {}
    if ACCOUNTS_FILE.exists():
        accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    taken = {a["email"] for a in accounts.values()}
    free = [e for e in email_variants() if f"{e}@gmail.com" not in taken]
    for i, persona in enumerate(SF.PERSONAS):
        if persona not in accounts:
            accounts[persona] = {"email": f"{free.pop(0)}@gmail.com", "mobile": str(MOBILE_BASE + i + 1),
                                 "registered": "", "result": "", "bhaskar_id": "", "error": "", "last_run": ""}
    return accounts


def save_accounts(accounts: dict[str, dict[str, str]]) -> None:
    BUG_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=1), encoding="utf-8")
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Test data"
    ws.append(XLSX_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
    for i, (persona, a) in enumerate(accounts.items(), 1):
        ws.append([i, SF.PERSONAS[persona]["entity"], persona, a["email"], a["mobile"], S.DATA["password"],
                   S.DATA["first_name"], S.DATA["last_name"], a.get("year_of_birth", ""), a.get("gender", ""),
                   a.get("pin", ""), a.get("pan", ""), a.get("org_pan", ""), a.get("cin", ""), a.get("gstin", ""),
                   a.get("org_name", ""), a.get("bhaskar_id", ""), a.get("result", "") or "not run",
                   a.get("error", ""), a.get("last_run", "")])
        colour = {"PASS": "C6EFCE", "FAIL": "FFC7CE"}.get(a.get("result", ""))
        if colour:
            ws.cell(row=i + 1, column=18).fill = PatternFill("solid", fgColor=colour)
    for idx, col in enumerate(ws.iter_cols(values_only=True), 1):
        width = max(len(str(v or "")) for v in col)
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 6), 60)
    ws.freeze_panes = "D2"
    while True:
        try:
            wb.save(str(TEST_DATA_XLSX))
            return
        except PermissionError:
            print(f"[WARN] {TEST_DATA_XLSX.name} is open in Excel; close it so the test data can be saved. Retrying in 10 s.")
            time.sleep(10)


def run_one(persona: str, login: bool) -> tuple[str, str]:
    S.DATA["mode"] = "login" if login else "signup"
    findings = Findings()
    result, error = "FAIL", ""
    try:
        S.main(persona=persona, rotate=False, on_page=findings.attach)
        result = "PASS"
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        if isinstance(e, KeyboardInterrupt):
            raise
    finally:
        out = findings.save(persona, result)
        print(f"[FINDINGS] {len(findings.items)} automatic finding(s) -> {out}")
    return result, error


def run_all(selected: list[str], force: bool) -> None:
    accounts = load_accounts()
    save_accounts(accounts)
    print(f"[TEST DATA] {TEST_DATA_XLSX}")
    for n, persona in enumerate(selected, 1):
        a = accounts[persona]
        if a.get("result") == "PASS" and not force:
            print(f"[SKIP] {persona}: already passed ({a.get('bhaskar_id')})")
            continue
        login = bool(a.get("registered"))
        S.DATA["email"], S.DATA["mobile"] = a["email"], a["mobile"]
        print("\n" + "=" * 90)
        print(f"[{n}/{len(selected)}] {SF.PERSONAS[persona]['entity']} - {persona}")
        print(f"        Email {a['email']}   Mobile {a['mobile']}   "
              f"{'log in and continue' if login else 'NEW SIGN-UP: OTPs for this email and mobile are needed'}")
        print("=" * 90)
        result, error = run_one(persona, login)
        report = S.last_report
        if report is not None:
            done = {s["name"] for s in report.steps if s["status"] == "PASS"}
            if done & {"Get your BHASKAR ID", "Log in"}:
                a["registered"] = "1"
            a.update({k: v for k, v in report.test_data.items() if k not in ("email", "mobile")})
            a["bhaskar_id"] = report.bhaskar_id or a.get("bhaskar_id", "")
            fail = next((s for s in report.steps if s["status"] == "FAIL"), None)
            if fail is not None:
                error = f"Stage {fail['stage']} {S.STAGES[fail['stage'] - 1]} / {fail['name']}: {fail['note']}"[:500]
        a["result"], a["error"], a["last_run"] = result, error if result != "PASS" else "", f"{datetime.now():%d-%b-%Y %H:%M}"
        save_accounts(accounts)
        print(f"[RESULT] {persona}: {result}" + (f" - {error[:200]}" if error and result != "PASS" else ""))
    print("\n[SUMMARY]")
    for persona in selected:
        a = accounts[persona]
        print(f"  {a.get('result') or 'not run':8} {SF.PERSONAS[persona]['entity']:12} {persona:32} {a.get('bhaskar_id', '')}")
    print(f"[TEST DATA] {TEST_DATA_XLSX}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the BHASKAR registration journey as a tester and record findings. "
                                                 "Uses its own Word report and screenshot folder and does not change "
                                                 "the daily persona rotation.")
    parser.add_argument("--persona", choices=list(SF.PERSONAS), action="append",
                        help="persona to test; repeat for several (default: the next one in the daily rotation)")
    parser.add_argument("--all", action="store_true", help="test every persona, each with its own test account")
    parser.add_argument("--entity", choices=[SF.IND, SF.ORG], help="with --all: only this entity type")
    parser.add_argument("--force", action="store_true", help="with --all: also rerun personas that already passed")
    parser.add_argument("--login", action="store_true", help="log in to the existing account and continue where it stopped")
    args = parser.parse_args()

    S.REPORT_FILE = BUG_DIR / "BHASKAR_PROD_Bug_Run_Report.docx"
    S.SCREENSHOT_DIR = BUG_DIR / "screenshots"
    S.REPORT_TITLE = "BHASKAR Production - Bug-finding Runs"
    if args.all or (args.persona and len(args.persona) > 1):
        selected = args.persona or [p for p in SF.PERSONAS if not args.entity or SF.PERSONAS[p]["entity"] == args.entity]
        run_all(selected, args.force)
        return
    persona = args.persona[0] if args.persona else S.pick_persona()
    result, error = run_one(persona, args.login)
    if result != "PASS":
        raise SystemExit(error)


if __name__ == "__main__":
    main()
