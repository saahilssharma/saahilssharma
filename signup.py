import json
import re
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
}

GREEN, RED, AMBER = RGBColor(0x1B, 0x7F, 0x3B), RGBColor(0xC6, 0x28, 0x28), RGBColor(0xB4, 0x53, 0x09)


class RunReport:
    def __init__(self):
        self.started = datetime.now()
        self.steps = []
        self.result = "IN PROGRESS"
        self.error = ""
        self._t = time.monotonic()
        self.shot_dir = SCREENSHOT_DIR / f"{self.started:%Y-%m-%d_%H%M%S}"
        self._base = None
        self._base_loaded = False
        self._lock_warned = False

    def step(self, page: Page, name: str, status: str = "PASS", note: str = "", message: bool = False) -> None:
        png = msg_text = msg_png = None
        if not page.is_closed():
            if message:
                msg_text, msg_png = capture_message(page)
            try:
                png = page.screenshot(full_page=True)
            except Exception:
                pass
        self._store_pngs(len(self.steps) + 1, name, status, png, msg_png)
        now = time.monotonic()
        self.steps.append({"name": name, "status": status, "note": note, "png": png, "secs": now - self._t,
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
        doc.save(str(REPORT_FILE))
        return REPORT_FILE

    def _store_pngs(self, n: int, name: str, status: str, png, msg_png) -> None:
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


def wait_for_otps_in_browser(page: Page, report: RunReport) -> None:
    print("[ACTION] In the browser, type the Email OTP and Mobile OTP and click both Verify buttons.")
    done = {"email": False, "mobile": False}
    seen: set[str] = set()
    get_id = page.locator(LOC["get_id_btn"])
    deadline = time.monotonic() + OTP_WAIT_MS / 1000
    while not get_id.is_visible():
        if time.monotonic() > deadline:
            raise TimeoutError(f"OTPs were not verified in the browser within {OTP_WAIT_MS // 60000} minutes")
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
        page.wait_for_timeout(500)
    for key, name in (("email", "Verify Email OTP"), ("mobile", "Verify Mobile OTP")):
        if not done[key]:
            report.step(page, name, note="Verified in the browser (success message was not captured)")


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

    page.get_by_role("textbox", name=LOC["email_otp"]).wait_for(state="visible")
    report.step(page, "Generate OTP for Email & Mobile", note="Site accepted registration, OTPs sent", message=True)

    wait_for_otps_in_browser(page, report)

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
            input("Done. Press Enter to close the browser...")
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
