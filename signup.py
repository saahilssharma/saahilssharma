import json
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.styles.style import ParagraphStyle
from playwright.sync_api import sync_playwright, Page, expect

URL = "https://www.startupindia.gov.in/bhaskar/register"

DATA = {
    "first_name": "",
    "last_name": "",
    "nationality": "Indian",
    "email": "",
    "mobile": "",
    "password": "",
}
DATA_FILE = Path(__file__).with_name("test_data.json")
if DATA_FILE.exists():
    DATA.update(json.loads(DATA_FILE.read_text(encoding="utf-8")))

HEADLESS = False
SLOW_MO_MS = 200
TIMEOUT_MS = 30_000
API_TIMEOUT_MS = 60_000
REGISTER_API = "/sih/api/noauth/signup/bhaskar/register"
REPORT_DIR = Path(__file__).with_name("reports")
REPORT_FILE = REPORT_DIR / "BHASKAR_PROD_Daily_Signup_Report.docx"

LOC = {
    "first_name":       "#firstName",
    "last_name":        "#lastName",
    "nationality":      "#nationality",
    "email":            "#emailId",
    "mobile":           "#mobileNumber",
    "password":         "#password",
    "confirm_password": "#confirmPassword",
    "generate_otp_btn": "button:has-text('Generate OTP for Email & Mobile')",
    "mobile_otp":       "Mobile OTP",
    "email_otp":        "Email OTP",
    "verify_email_btn": "button:has-text('Verfiy Email OTP')",
    "verify_mobile_btn": "button:has-text('Verify Mobile OTP')",
    "get_id_btn":       "button:has-text('Get your BHASKAR ID')",
}

GREEN, RED, AMBER = RGBColor(0x1B, 0x7F, 0x3B), RGBColor(0xC6, 0x28, 0x28), RGBColor(0xB4, 0x53, 0x09)


class RunReport:
    def __init__(self):
        self.started = datetime.now()
        self.steps = []
        self.result = "IN PROGRESS"
        self.error = ""
        self._t = time.monotonic()
        self.target = REPORT_FILE
        self._base = None
        if REPORT_FILE.exists():
            try:
                self._base = REPORT_FILE.read_bytes()
            except PermissionError:
                self.target = self._fallback_path()

    def step(self, page: Page, name: str, status: str = "PASS", note: str = "", message: bool = False) -> None:
        png = msg_text = msg_png = None
        if not page.is_closed():
            if message:
                msg_text, msg_png = capture_message(page)
            try:
                png = page.screenshot(full_page=True)
            except Exception:
                pass
        now = time.monotonic()
        self.steps.append({"name": name, "status": status, "note": note, "png": png, "secs": now - self._t,
                           "msg_text": msg_text, "msg_png": msg_png, "msg_expected": message})
        self._t = now
        print(f"[{status}] {name}" + (f" - {note}" if note else ""))
        if msg_text:
            print(f"        Site message: {msg_text}")
        try:
            self.save()
        except Exception as e:
            print(f"[WARN] Could not save report after this step: {type(e).__name__}: {e}")

    def save(self) -> Path:
        REPORT_DIR.mkdir(exist_ok=True)
        if self._base is not None and self.target == REPORT_FILE:
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
        if self.error:
            self._kv(doc, "Error", self.error, RED)

        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, head in zip(table.rows[0].cells, ("Step", "Status", "Duration")):
            cell.text = head
        for s in self.steps:
            row = table.add_row().cells
            row[0].text, row[1].text, row[2].text = s["name"], s["status"], f"{s['secs']:.1f} s"
            row[1].paragraphs[0].runs[0].font.color.rgb = GREEN if s["status"] == "PASS" else RED

        for i, s in enumerate(self.steps, 1):
            doc.add_heading(f"Step {i}: {s['name']} - {s['status']}", 2)
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
        try:
            doc.save(str(self.target))
        except PermissionError:
            if self.target != REPORT_FILE:
                raise
            self.target = self._fallback_path()
            print(f"[WARN] {REPORT_FILE.name} is open in Word, so this run is saved separately")
            return self.save()
        return self.target

    def _fallback_path(self) -> Path:
        return REPORT_DIR / f"BHASKAR_PROD_Daily_Signup_{self.started:%Y-%m-%d_%H%M%S}.docx"

    @staticmethod
    def _separator(doc):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(18)
        border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "double"), ("w:sz", "12"), ("w:space", "1"), ("w:color", "404040")):
            bottom.set(qn(k), v)
        border.append(bottom)
        p._p.get_or_add_pPr().append(border)

    @staticmethod
    def _kv(doc, key, value, colour=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run(f"{key}: ").bold = True
        r = p.add_run(str(value))
        if colour is not None:
            r.bold, r.font.color.rgb = True, colour


def capture_message(page: Page, timeout_ms: int = 5000):
    toast = page.locator("[role='status']").first
    try:
        toast.wait_for(state="visible", timeout=timeout_ms)
        text = " ".join(toast.inner_text().split())
        box = toast.locator("xpath=..")
        return text, box.screenshot()
    except Exception:
        return None, None


def fill_registration_form(page: Page, report: RunReport) -> None:
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


def generate_and_verify_otp(page: Page, report: RunReport) -> None:
    with page.expect_response(lambda r: REGISTER_API in r.url, timeout=API_TIMEOUT_MS) as resp_info:
        page.locator(LOC["generate_otp_btn"]).click()

    result = resp_info.value.json()
    if not result.get("status"):
        details = result.get("data")
        items = details.values() if isinstance(details, dict) else details if isinstance(details, list) else []
        fields = [f"{d.get('field')}: {d.get('message')}" for d in items if isinstance(d, dict)]
        message = f"Registration rejected by site: {result.get('message')}"
        if fields:
            message += " (" + "; ".join(fields) + ")"
        report.step(page, "Generate OTP for Email & Mobile", "FAIL", message, message=True)
        raise RuntimeError(message)

    email_otp = page.get_by_role("textbox", name=LOC["email_otp"])
    mobile_otp = page.get_by_role("textbox", name=LOC["mobile_otp"])
    email_otp.wait_for(state="visible")
    report.step(page, "Generate OTP for Email & Mobile", note="Site accepted registration, OTPs sent", message=True)

    email_otp.fill(input("Enter Email OTP: ").strip())
    page.locator(LOC["verify_email_btn"]).click()
    report.step(page, "Verify Email OTP", message=True)

    mobile_otp.fill(input("Enter Mobile OTP: ").strip())
    page.locator(LOC["verify_mobile_btn"]).click()
    report.step(page, "Verify Mobile OTP", message=True)

    get_id = page.locator(LOC["get_id_btn"])
    expect(get_id).to_be_enabled()
    get_id.click()
    page.wait_for_load_state("networkidle")
    report.step(page, "Get your BHASKAR ID", note=f"Landed on {page.url}")


def main() -> None:
    missing = [k for k, v in DATA.items() if not v]
    if missing:
        raise SystemExit(f"Fill these values in test_data.json first: {', '.join(missing)}")

    report = RunReport()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        page = browser.new_context().new_page()
        page.set_default_timeout(TIMEOUT_MS)
        try:
            fill_registration_form(page, report)
            generate_and_verify_otp(page, report)
            report.result = "PASS"
            report.save()
            input("Done. Press Enter to close the browser...")
        except BaseException as e:
            report.result = "FAIL"
            report.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            if not report.steps or report.steps[-1]["status"] != "FAIL":
                report.step(page, "Failure point", "FAIL", report.error, message=True)
            raise
        finally:
            try:
                print(f"[REPORT] {report.save()}")
            except Exception as e:
                print(f"[REPORT FAILED] {type(e).__name__}: {e}")
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
