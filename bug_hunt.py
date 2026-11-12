import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

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
        out = BUG_DIR / f"{self.started:%Y-%m-%d_%H%M%S}_findings.md"
        lines = [f"# Bug-finding run {self.started:%d-%b-%Y %H:%M:%S}", "",
                 f"- Persona: {persona}", f"- Test user: {S.DATA['email']} / {S.DATA['mobile']}",
                 f"- Start: {'log in to existing account' if S.DATA['mode'] == 'login' else 'new sign-up'}",
                 f"- Result: {result}", f"- Screenshots and Word report: {BUG_DIR}", "",
                 "## Automatic findings (review each one before adding it to the bug report)", ""]
        lines += [f"- {x}" for x in self.items] or ["- None recorded"]
        lines += ["", "## Server calls (POST/PUT/PATCH)", ""] + ([f"- {x}" for x in self.calls] or ["- None"])
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the BHASKAR registration journey as a tester and record findings. "
                                                 "Uses its own Word report and screenshot folder and does not change "
                                                 "the daily persona rotation.")
    parser.add_argument("--persona", choices=list(SF.PERSONAS), help="persona to test (default: the next one in the daily rotation)")
    parser.add_argument("--login", action="store_true", help="log in to the existing account and continue where it stopped")
    args = parser.parse_args()

    S.REPORT_FILE = BUG_DIR / "BHASKAR_PROD_Bug_Run_Report.docx"
    S.SCREENSHOT_DIR = BUG_DIR / "screenshots"
    S.REPORT_TITLE = "BHASKAR Production - Bug-finding Runs"
    if args.login:
        S.DATA["mode"] = "login"
    persona = args.persona or S.pick_persona()
    findings = Findings()
    result = "FAIL"
    try:
        S.main(persona=persona, rotate=False, on_page=findings.attach)
        result = "PASS"
    finally:
        out = findings.save(persona, result)
        print(f"[FINDINGS] {len(findings.items)} automatic finding(s) -> {out}")


if __name__ == "__main__":
    main()
