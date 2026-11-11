import json
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, TypedDict, TypeVar, cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.styles.style import ParagraphStyle
from playwright.sync_api import sync_playwright, Page, expect

import personas as SF

URL = "https://www.startupindia.gov.in/bhaskar/register"

DATA: dict[str, str] = {
    "first_name": "",
    "last_name": "",
    "nationality": "Indian",
    "email": "",
    "mobile": "",
    "password": "",
    "mode": "signup",
    "persona": "rotate",
}
DATA_FILE = Path(__file__).with_name("test_data.json")
PERSONA_STATE = Path(__file__).with_name("last_persona.txt")
if DATA_FILE.exists():
    DATA.update(json.loads(DATA_FILE.read_text(encoding="utf-8")))

HEADLESS = False
SLOW_MO_MS = 200
TIMEOUT_MS = 30_000
OTP_WAIT_MS = 600_000
API_TIMEOUT_MS = 60_000
REGISTER_API = "/sih/api/noauth/signup/bhaskar/register"
REPORT_DIR = Path(__file__).with_name("reports")
REPORT_FILE = REPORT_DIR / "BHASKAR_PROD_Daily_Signup_Report.docx"
SCREENSHOT_DIR = REPORT_DIR / "screenshots"

LOC = {
    "first_name":       "#firstName",
    "last_name":        "#lastName",
    "nationality":      "#nationality",
    "email":            "#emailId",
    "mobile":           "#mobileNumber",
    "password":         "#password",
    "confirm_password": "#confirmPassword",
    "generate_otp_btn": "button:has-text('Generate OTP for Email & Mobile')",
    "email_otp":        "Email OTP",
    "get_id_btn":       "button:has-text('Get your BHASKAR ID')",
    "email_otp_input":  "input[name='emailOtp']",
    "mobile_otp_input": "input[name='mobileOtp']",
    "verify_email_btn": "button:has-text('Verfiy Email OTP')",
    "verify_mobile_btn": "button:has-text('Verify Mobile OTP')",
    "login_username":   "input[name='username']",
    "login_password":   "input[name='password']",
}

STAGES = ["Register", "Select entity type", "Select persona", "Basic details", "T&C",
          "BHASKAR ID generated", "Complete your profile"]

GREEN, RED, AMBER = RGBColor(0x1B, 0x7F, 0x3B), RGBColor(0xC6, 0x28, 0x28), RGBColor(0xB4, 0x53, 0x09)


class Step(TypedDict):
    stage: int
    name: str
    status: str
    note: str
    png: bytes | None
    secs: float
    msg_text: str | None
    msg_png: bytes | None
    msg_expected: bool


T = TypeVar("T")


class RunReport:
    def __init__(self) -> None:
        self.started = datetime.now()
        self.steps: list[Step] = []
        self.result = "IN PROGRESS"
        self.error = ""
        self._t = time.monotonic()
        self.shot_dir = SCREENSHOT_DIR / f"{self.started:%Y-%m-%d_%H%M%S}"
        self._base: bytes | None = None
        self._base_loaded = False
        self._lock_warned = False
        self.persona = "Startup Founder / Entrepreneur (Individual)"
        self.test_data: dict[str, str] = {}
        self.bhaskar_id = ""
        self.stage = 1

    def set_stage(self, name: str) -> None:
        self.stage = STAGES.index(name) + 1
        print(f"=== Stage {self.stage}: {name}")

    def step(self, page: Page, name: str, status: str = "PASS", note: str = "", message: bool = False) -> None:
        png: bytes | None = None
        msg_text: str | None = None
        msg_png: bytes | None = None
        if not page.is_closed():
            if message:
                msg_text, msg_png = capture_message(page)
            try:
                png = page.screenshot(full_page=True)
            except Exception:
                pass
        self._store_pngs(len(self.steps) + 1, f"S{self.stage}_{name}", status, png, msg_png)
        now = time.monotonic()
        self.steps.append({"stage": self.stage, "name": name, "status": status, "note": note, "png": png, "secs": now - self._t,
                           "msg_text": msg_text, "msg_png": msg_png, "msg_expected": message})
        self._t = now
        print(f"[{status}] {name}" + (f" - {note}" if note else ""))
        if msg_text:
            print(f"        Site message: {msg_text}")
        try:
            self.save()
        except PermissionError:
            if not self._lock_warned:
                print(f"[WARN] {REPORT_FILE.name} is open in Word. Steps are kept and will be saved into it once it is closed.")
                self._lock_warned = True
        except Exception as e:
            print(f"[WARN] Could not save report after this step: {type(e).__name__}: {e}")

    def save_final(self) -> Path:
        while True:
            try:
                return self.save()
            except PermissionError:
                print(f"[WAIT] {REPORT_FILE.name} is open in Word. Close it, then press Enter to save this run into it.")
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    raise RuntimeError(f"Run not saved into {REPORT_FILE.name}; screenshots are in {self.shot_dir}")

    def save(self) -> Path:
        REPORT_DIR.mkdir(exist_ok=True)
        if not self._base_loaded:
            if REPORT_FILE.exists():
                self._base = REPORT_FILE.read_bytes()
            self._base_loaded = True
        if self._base is not None:
            doc = Document(BytesIO(self._base))
            self._separator(doc)
        else:
            doc = Document()
            normal = doc.styles["Normal"]
            assert isinstance(normal, ParagraphStyle)
            normal.font.name = "Calibri"
            normal.font.size = Pt(10)
            doc.add_heading("BHASKAR Production - Daily Signup", 0)
        doc.add_heading(f"Run {self.started:%d-%b-%Y %H:%M:%S} - {self.result}", 1)
        self._kv(doc, "Result", self.result, {"PASS": GREEN, "FAIL": RED}.get(self.result, AMBER))
        self._kv(doc, "Run", f"{self.started:%d-%b-%Y %H:%M:%S}  ->  {datetime.now():%H:%M:%S}")
        self._kv(doc, "URL", URL)
        self._kv(doc, "Test user", f"{DATA['first_name']} {DATA['last_name']}  /  {DATA['email']}  /  {DATA['mobile']}")
        self._kv(doc, "Persona", f"{self.persona}  |  start: {'log in to existing account' if DATA['mode'] == 'login' else 'new sign-up'}")
        if self.test_data:
            d = self.test_data
            self._kv(doc, "Generated data", f"Year of birth {d['year_of_birth']}, {d['gender']}, PIN {d['pin']}, "
                                            f"PAN {d['pan']}, {d['org_name']}")
        self._kv(doc, "BHASKAR ID", self.bhaskar_id or "-", GREEN if self.bhaskar_id else None)
        if self.error:
            self._kv(doc, "Error", self.error, RED)

        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, head in zip(table.rows[0].cells, ("Stage", "Step", "Status", "Duration")):
            cell.text = head
        for s in self.steps:
            row = table.add_row().cells
            row[0].text = f"{s['stage']}. {STAGES[s['stage'] - 1]}"
            row[1].text, row[2].text, row[3].text = s["name"], s["status"], f"{s['secs']:.1f} s"
            row[2].paragraphs[0].runs[0].font.color.rgb = GREEN if s["status"] == "PASS" else RED

        current = 0
        for i, s in enumerate(self.steps, 1):
            if s["stage"] != current:
                current = s["stage"]
                stage_steps = [x for x in self.steps if x["stage"] == current]
                stage_status = "FAIL" if any(x["status"] != "PASS" for x in stage_steps) else "PASS"
                doc.add_heading(f"Stage {current}: {STAGES[current - 1]} - {stage_status}", 2)
            doc.add_heading(f"Step {i}: {s['name']} - {s['status']}", 3)
            if s["note"]:
                doc.add_paragraph(s["note"])
            colour = GREEN if s["status"] == "PASS" else RED
            label = "Success message" if s["status"] == "PASS" else "Error message"
            if s["msg_text"] or s["msg_png"]:
                p = doc.add_paragraph()
                p.add_run(f"{label}: ").bold = True
                r = p.add_run(s["msg_text"] or "-")
                r.bold, r.font.color.rgb = True, colour
            if s["msg_png"]:
                doc.add_picture(BytesIO(s["msg_png"]), width=Inches(4.0))
            elif s["msg_expected"] and not s["msg_text"]:
                doc.add_paragraph("No on-screen message was shown.")
            if s["png"]:
                doc.add_paragraph("Full page:").runs[0].italic = True
                doc.add_picture(BytesIO(s["png"]), width=Inches(6.0))
        doc.save(str(REPORT_FILE))
        return REPORT_FILE

    def _store_pngs(self, n: int, name: str, status: str, png: bytes | None, msg_png: bytes | None) -> None:
        stem = f"{n:02d}_{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')}_{status}"
        try:
            self.shot_dir.mkdir(parents=True, exist_ok=True)
            if png:
                (self.shot_dir / f"{stem}.png").write_bytes(png)
            if msg_png:
                (self.shot_dir / f"{stem}_message.png").write_bytes(msg_png)
        except OSError as e:
            print(f"[WARN] Could not save screenshot files: {e}")

    @staticmethod
    def _separator(doc: DocxDocument) -> None:
        p: Paragraph = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(18)
        border = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="double" w:sz="12" w:space="1" w:color="404040"/></w:pBdr>')
        element: Any = p.paragraph_format.element
        element.get_or_add_pPr().append(border)

    @staticmethod
    def _kv(doc: DocxDocument, key: str, value: str, colour: RGBColor | None = None) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run(f"{key}: ").bold = True
        r = p.add_run(str(value))
        if colour is not None:
            r.bold, r.font.color.rgb = True, colour


def capture_message(page: Page, timeout_ms: int = 5000) -> tuple[str | None, bytes | None]:
    toast = page.locator("[role='status']").first
    try:
        toast.wait_for(state="visible", timeout=timeout_ms)
        text = " ".join(toast.inner_text().split())
        box = toast.locator("xpath=..")
        return text, box.screenshot()
    except Exception:
        return None, None


def fill_registration_form(page: Page, report: RunReport) -> None:
    report.set_stage("Register")
    page.goto(URL, wait_until="domcontentloaded")
    page.locator(LOC["first_name"]).wait_for(state="visible")
    report.step(page, "Open registration page")

    page.locator(LOC["first_name"]).fill(DATA["first_name"])
    page.locator(LOC["last_name"]).fill(DATA["last_name"])
    page.locator(LOC["nationality"]).select_option(DATA["nationality"])
    page.locator(LOC["email"]).fill(DATA["email"])
    page.locator(LOC["mobile"]).fill(DATA["mobile"])
    page.locator(LOC["password"]).fill(DATA["password"])
    page.locator(LOC["confirm_password"]).fill(DATA["password"])
    report.step(page, "Fill registration form")


def auto_verify(page: Page, key: str, tried: dict[str, str]) -> None:
    box = page.locator(LOC[f"{key}_otp_input"])
    button = page.locator(LOC[f"verify_{key}_btn"])
    try:
        if not (box.is_visible() and button.is_visible()):
            return
        value = box.input_value().strip()
        if len(value) == 6 and value != tried[key] and button.is_enabled():
            tried[key] = value
            print(f"[AUTO] Clicking Verify for the {key} OTP")
            button.click(timeout=5000)
    except Exception as e:
        print(f"[WARN] Could not click Verify for the {key} OTP: {type(e).__name__}")


def wait_for_otps_in_browser(page: Page, report: RunReport) -> None:
    print("[ACTION] In the browser, type the 6-digit Email OTP and Mobile OTP. Verify is clicked automatically.")
    done = {"email": False, "mobile": False}
    tried = {"email": "", "mobile": ""}
    seen: set[str] = set()
    get_id = page.locator(LOC["get_id_btn"])
    deadline = time.monotonic() + OTP_WAIT_MS / 1000
    while True:
        finished = get_id.is_visible()
        if not finished and time.monotonic() > deadline:
            raise TimeoutError(f"OTPs were not verified in the browser within {OTP_WAIT_MS // 60000} minutes")
        for key in ("email", "mobile"):
            if not done[key]:
                auto_verify(page, key, tried)
        texts = {" ".join(t.split()) for t in page.locator("[role='status']").all_inner_texts()}
        for text in texts - seen:
            low = text.lower()
            if "email otp successfully verified" in low:
                done["email"] = True
                report.step(page, "Verify Email OTP", message=True)
            elif "mobile otp successfully verified" in low:
                done["mobile"] = True
                report.step(page, "Verify Mobile OTP", message=True)
            elif "invalid" in low or "please enter the otp" in low:
                report.step(page, "OTP verification attempt", "FAIL", "Wrong or empty OTP entered, waiting for a retry", message=True)
        seen = texts
        if finished:
            break
        page.wait_for_timeout(500)
    for key, name in (("email", "Verify Email OTP"), ("mobile", "Verify Mobile OTP")):
        if not done[key]:
            report.step(page, name, note="Verified in the browser", message=True)


def generate_and_verify_otp(page: Page, report: RunReport) -> None:
    with page.expect_response(lambda r: REGISTER_API in r.url, timeout=API_TIMEOUT_MS) as resp_info:
        page.locator(LOC["generate_otp_btn"]).click()

    result: dict[str, Any] = resp_info.value.json()
    if not result.get("status"):
        details: Any = result.get("data")
        if isinstance(details, dict):
            details = list(cast(dict[str, Any], details).values())
        items = cast(list[Any], details) if isinstance(details, list) else []
        rows = cast(list[dict[str, Any]], [d for d in items if isinstance(d, dict)])
        fields = [f"{d.get('field')}: {d.get('message')}" for d in rows]
        message = f"Registration rejected by site: {result.get('message')}"
        if fields:
            message += " (" + "; ".join(fields) + ")"
        report.step(page, "Generate OTP for Email & Mobile", "FAIL", message, message=True)
        raise RuntimeError(message)

    page.get_by_role("textbox", name=LOC["email_otp"]).wait_for(state="visible")
    report.step(page, "Generate OTP for Email & Mobile", note="Site accepted registration, OTPs sent", message=True)

    wait_for_otps_in_browser(page, report)

    get_id = page.locator(LOC["get_id_btn"])
    expect(get_id).to_be_enabled()
    get_id.click()
    try:
        page.wait_for_url("**/register/stepone**", timeout=API_TIMEOUT_MS)
        page.wait_for_load_state("networkidle")
    except Exception:
        message = f"Did not reach the next registration page; still on {page.url}"
        report.step(page, "Get your BHASKAR ID", "FAIL", message, message=True)
        raise RuntimeError(message)
    report.step(page, "Get your BHASKAR ID", note=f"Landed on {page.url}")


def log_in(page: Page, report: RunReport) -> str:
    report.set_stage("Register")
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Login", exact=True).first.click()
    page.locator(LOC["login_username"]).wait_for(state="visible")
    report.step(page, "Open login form")
    page.locator(LOC["login_username"]).fill(DATA["email"])
    page.locator(LOC["login_password"]).fill(DATA["password"])
    page.get_by_role("button", name="Login", exact=True).last.click()
    individual = page.get_by_role("heading", name=re.compile(r"^\s*Individual\s*$", re.I))
    publish_btn = page.get_by_role("button", name=re.compile(r"^\s*Publish Your Profile", re.I))
    stage, end, reopened = "", time.monotonic() + API_TIMEOUT_MS / 1000, False
    while not stage and time.monotonic() < end:
        if individual.first.is_visible():
            stage = "persona"
        elif "stepfour" in page.url and publish_btn.first.is_visible():
            stage = "profile"
        elif page.url.rstrip("/").endswith("/bhaskar/profile"):
            user = json.loads(page.evaluate("() => localStorage.getItem('user') || '{}'"))
            if not reopened and not user.get("published") and user.get("uidBhaskar") and user.get("bhaskarId"):
                print(f"[INFO] Profile of {user['bhaskarId']} is not published yet; reopening the profile form")
                page.goto(f"{URL}/stepfour?role={user.get('role')}&userid={user['uidBhaskar']}&bhaskarid={user['bhaskarId']}")
                reopened = True
                continue
            bid = SF.bhaskar_id_from_page(page)
            message = (f"This account has already finished registration (BHASKAR ID {bid or 'not read'}) and opens its "
                       f"dashboard. Use a new email and mobile with mode \"signup\" in test_data.json.")
            report.step(page, "Log in", "FAIL", message, message=True)
            raise RuntimeError(message)
        else:
            page.wait_for_timeout(500)
    if not stage:
        message = f"Neither persona selection nor the profile form opened after login; now on {page.url}"
        report.step(page, "Log in", "FAIL", message, message=True)
        raise RuntimeError(message)
    where = "persona selection" if stage == "persona" else "Complete Your Public Profile"
    report.step(page, "Log in", note=f"Logged in as {DATA['email']}, landed on {where}", message=True)
    return stage


def flow_step(page: Page, report: RunReport, name: str, action: Callable[[], T], note: str | Callable[[], str] = "",
              message: bool = False) -> T:
    try:
        result = action()
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        report.step(page, name, "FAIL", reason, message=True)
        raise RuntimeError(f"{name} failed - {reason}")
    report.step(page, name, note=note() if callable(note) else note, message=message)
    return result


def pick_persona() -> str:
    if DATA["persona"] != "rotate":
        return DATA["persona"]
    last = PERSONA_STATE.read_text(encoding="utf-8").strip() if PERSONA_STATE.exists() else ""
    return SF.next_persona(last)


def register_persona(page: Page, report: RunReport, persona: str, stage: str = "persona") -> None:
    t = TIMEOUT_MS / 1000
    data = SF.make_test_data(DATA["last_name"], persona)
    report.test_data = data
    print(f"[DATA] {persona}: year of birth {data['year_of_birth']}, {data['gender']}, PIN {data['pin']}, "
          f"PAN {data['pan']}, {data['org_name']}")
    if stage == "profile":
        report.set_stage("Complete your profile")
        report.bhaskar_id = SF.bhaskar_id_from_page(page)
        print(f"[INFO] Account already has BHASKAR ID {report.bhaskar_id or '(not read)'}; continuing with the profile")
        complete_profile(page, report, data, persona, t)
        return
    report.set_stage("Select entity type")
    flow_step(page, report, "Choose Individual", lambda: SF.choose_entity(page, t))
    report.set_stage("Select persona")
    flow_step(page, report, f"Choose {persona}", lambda: SF.choose_persona(page, persona, t))
    report.set_stage("Basic details")
    flow_step(page, report, "Fill basic form", lambda: SF.fill_basic_form(page, data, DATA["email"], t),
              note=f"Year of birth {data['year_of_birth']}, {data['gender']}, India, PIN {data['pin']}, PAN {data['pan']}")
    flow_step(page, report, "Preview and submit", lambda: SF.preview_and_submit(page, t))
    report.set_stage("T&C")
    flow_step(page, report, "Accept Terms & Conditions", lambda: SF.accept_terms(page, t))
    report.set_stage("BHASKAR ID generated")
    try:
        bid = SF.generate_bhaskar_id(page, t)
    except Exception as e:
        report.step(page, "Generate BHASKAR ID", "FAIL", f"{type(e).__name__}: {e}", message=True)
        raise RuntimeError(f"Generate BHASKAR ID failed - {e}")
    report.bhaskar_id = bid
    report.step(page, "Generate BHASKAR ID", note=f"BHASKAR ID: {bid or 'not read from the page'}", message=True)
    report.set_stage("Complete your profile")
    flow_step(page, report, "Open Complete your profile", lambda: SF.open_profile(page, t))
    complete_profile(page, report, data, persona, t)


def complete_profile(page: Page, report: RunReport, data: dict[str, str], persona: str, t: float) -> None:
    empty: list[str] = []
    flow_step(page, report, "Fill public profile",
              lambda: empty.extend(SF.fill_profile(page, data, DATA["mobile"], persona, print)),
              note=lambda: f"Dropdowns without options: {', '.join(empty)}" if empty else "All fields filled")
    try:
        published, replies = SF.publish(page, t)
    except Exception as e:
        report.step(page, "Publish Your Profile", "FAIL", f"{type(e).__name__}: {e}", message=True)
        raise RuntimeError(f"Publish Your Profile failed - {e}")
    details = " | ".join(replies) if replies else "no server reply recorded"
    if not published:
        message = f"Page did not move on after Publish (still on {page.url.split('?')[0]}). Server: {details}"
        report.step(page, "Publish Your Profile", "FAIL", message, message=True)
        raise RuntimeError("Profile was not published")
    report.step(page, "Publish Your Profile", note=f"Published. Server: {details}", message=True)


def main() -> None:
    missing = [k for k, v in DATA.items() if not v]
    if DATA["mode"] not in ("signup", "login"):
        raise SystemExit('mode in test_data.json must be "signup" or "login"')
    if missing:
        raise SystemExit(f"Fill these values in test_data.json first: {', '.join(missing)}")
    if DATA["persona"] != "rotate" and DATA["persona"] not in SF.PERSONAS:
        raise SystemExit(f'persona in test_data.json must be "rotate" or one of: {", ".join(SF.PERSONAS)}')

    persona = pick_persona()
    report = RunReport()
    report.persona = f"{persona} (Individual)"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        page = browser.new_context().new_page()
        page.set_default_timeout(TIMEOUT_MS)
        try:
            stage = "persona"
            if DATA["mode"] == "login":
                stage = log_in(page, report)
            else:
                fill_registration_form(page, report)
                generate_and_verify_otp(page, report)
            register_persona(page, report, persona, stage)
            report.result = "PASS"
            PERSONA_STATE.write_text(persona, encoding="utf-8")
            print("[DONE] Profile published")
        except BaseException as e:
            report.result = "FAIL"
            report.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            if not report.steps or report.steps[-1]["status"] != "FAIL":
                report.step(page, "Failure point", "FAIL", report.error, message=True)
            raise
        finally:
            print(f"[SCREENSHOTS] {report.shot_dir}")
            try:
                print(f"[REPORT] {report.save_final()}")
            except Exception as e:
                print(f"[REPORT FAILED] {type(e).__name__}: {e}")
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
