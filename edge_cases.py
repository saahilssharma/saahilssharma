import argparse
import json
import re
import struct
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page, Response, sync_playwright

import bug_hunt as BH
import personas as SF
import signup as S

URL = S.URL
API = "https://api.startupindia.gov.in/sih/api"
OUT_DIR = BH.BUG_DIR / "edge_cases"
NOISE = re.compile(r"^\(IN |^\(IN\s|UPTO 2 MB|HTTPS FORMAT", re.I)
ERRORS_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const sel = '[class*="error" i], [class*="invalid" i], .text-danger, [role="alert"]';
  const out = [...document.querySelectorAll(sel)].filter(vis).map(e => (e.innerText || '').trim()).filter(Boolean);
  return [...new Set(out)];
}"""


class Suite:
    def __init__(self, page: Page, section: str) -> None:
        self.page = page
        self.section = section
        self.rows: list[dict[str, Any]] = []
        self.replies: list[str] = []
        page.on("response", self._on_response)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.shots = OUT_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{section}"
        self.shots.mkdir(parents=True, exist_ok=True)

    def _on_response(self, r: Response) -> None:
        if "/sih/api" not in r.url or r.request.method not in ("POST", "PUT", "PATCH"):
            return
        try:
            body = " ".join(r.text().split())[:300]
        except Exception:
            body = ""
        self.replies.append(f"{r.request.method} {r.url.split('?')[0].replace(API, '')} -> {r.status} {body}")

    def observe(self) -> dict[str, Any]:
        page = self.page
        errors = [e for e in page.evaluate(ERRORS_JS) if not NOISE.search(e)]
        toasts = [" ".join(t.split()) for t in page.locator("[role='status']").all_inner_texts() if t.strip()]
        return {"errors": errors[:12], "toasts": toasts[:5], "url": page.url}

    def case(self, cid: str, area: str, applies: str, what: str, expected: str,
             action: Callable[[], Any], judge: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
        self.replies = []
        note = ""
        try:
            extra = action()
            if isinstance(extra, str):
                note = extra
        except Exception as e:
            note = f"action error: {type(e).__name__}: {str(e)[:200]}"
        self.page.wait_for_timeout(1500)
        obs = self.observe()
        obs["server"] = list(self.replies)
        obs["note"] = note
        verdict = "ERROR" if note.startswith("action error") else judge(obs)
        shot = self.shots / f"{cid}_{verdict}.png"
        try:
            self.page.screenshot(path=str(shot))
        except Exception:
            pass
        row = {"id": cid, "area": area, "applies_to": applies, "case": what, "expected": expected, "verdict": verdict,
               "observed": obs, "screenshot": str(shot)}
        self.rows.append(row)
        print(f"[{verdict}] {cid} {what} | errors={obs['errors'][:4]} toasts={obs['toasts'][:2]} "
              f"server={[s[:120] for s in obs['server']][:2]} {note[:150]}", flush=True)
        return row

    def save(self) -> Path:
        out = OUT_DIR / f"{self.shots.name}_results.json"
        out.write_text(json.dumps(self.rows, indent=1, ensure_ascii=False), encoding="utf-8")
        md = [f"# Edge / negative cases - {self.section} - {datetime.now():%d-%b-%Y %H:%M}", "",
              "| ID | Verdict | Applies to | Case | Expected | Observed |", "|---|---|---|---|---|---|"]
        for r in self.rows:
            o = r["observed"]
            seen = "; ".join((o["errors"] + o["toasts"])[:4]) or "no message"
            if o["server"]:
                seen += " / server: " + o["server"][0][:160]
            if o["note"]:
                seen += " / " + o["note"][:160]
            md.append(f"| {r['id']} | {r['verdict']} | {r['applies_to']} | {r['case']} | {r['expected']} | "
                      f"{seen.replace('|', '/')} |")
        (OUT_DIR / f"{self.shots.name}_results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"[RESULTS] {out}")
        return out


def has_error(o: dict[str, Any]) -> bool:
    return bool(o["errors"]) or any(re.search(r"invalid|error|fail|incorrect|wrong|not|required|please", t, re.I)
                                    for t in o["toasts"])


def server_ok(o: dict[str, Any], path: str) -> bool:
    return any(path in s and '"status":true' in s for s in o["server"])


def blocked(o: dict[str, Any]) -> str:
    return "PASS" if has_error(o) else "FAIL"


def login(page: Page, email: str, password: str) -> None:
    page.goto(URL, wait_until="domcontentloaded")
    page.get_by_role("button", name="Login", exact=True).first.click()
    page.locator(S.LOC["login_username"]).wait_for(state="visible")
    page.locator(S.LOC["login_username"]).fill(email)
    page.locator(S.LOC["login_password"]).fill(password)
    page.get_by_role("button", name="Login", exact=True).last.click()
    page.wait_for_timeout(4000)


def open_profile_form(page: Page, email: str, password: str) -> dict[str, Any]:
    login(page, email, password)
    end = time.time() + 60
    while time.time() < end and "/bhaskar/profile" not in page.url and "stepfour" not in page.url:
        page.wait_for_timeout(500)
    page.wait_for_timeout(2500)
    u: dict[str, Any] = json.loads(page.evaluate("() => localStorage.getItem('user') || '{}'"))
    step = "stepfour/org" if str(u.get("bhaskarId", "")).startswith("OI") else "stepfour"
    page.goto(f"{URL}/{step}?role={u.get('role')}&userid={u.get('uidBhaskar')}&bhaskarid={u.get('bhaskarId')}")
    page.wait_for_timeout(9000)
    return u


def fill_signup(page: Page, email: str, mobile: str, password: str, confirm: str | None = None,
                first: str = "QA", last: str = "Test") -> None:
    page.goto(URL, wait_until="domcontentloaded")
    page.locator(S.LOC["first_name"]).wait_for(state="visible")
    page.locator(S.LOC["first_name"]).fill(first)
    page.locator(S.LOC["last_name"]).fill(last)
    page.locator(S.LOC["nationality"]).select_option("Indian")
    page.locator(S.LOC["email"]).fill(email)
    page.locator(S.LOC["mobile"]).fill(mobile)
    page.locator(S.LOC["password"]).fill(password)
    page.locator(S.LOC["confirm_password"]).fill(password if confirm is None else confirm)


def click_generate(page: Page) -> None:
    page.locator(S.LOC["generate_otp_btn"]).click()
    page.wait_for_timeout(4000)


def png_bytes(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def big_jpeg_like(size_mb: float) -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * int(size_mb * 1024 * 1024) + b"\xff\xd9"


# ---------------------------------------------------------------- sections

def section_signup(page: Page, accounts: list[tuple[str, str]], password: str) -> Suite:
    s = Suite(page, "signup")
    reg = "/noauth/signup/bhaskar/register"

    def judge_reg(o: dict[str, Any]) -> str:
        return "FAIL" if server_ok(o, reg) else ("PASS" if has_error(o) else "CHECK")

    cases = [
        ("S1", "Password of 4 characters ('Ab1!')", "Rejected with a password-rule message", "Ab1!", None),
        ("S2", "Password of only lower-case letters ('abcdefgh')", "Rejected: needs upper case, digit, special character", "abcdefgh", None),
        ("S3", "Password and Confirm Password differ", "'Passwords do not match' and nothing sent", password, password + "x"),
        ("S4", "Password of 120 characters", "Rejected or limited to a maximum length", "Aa1!" * 30, None),
        ("S5", "Password with spaces only ('        ')", "Rejected", " " * 8, None),
    ]
    for i, (cid, what, exp, pwd, conf) in enumerate(cases):
        email, mobile = accounts[i]

        def act(e: str = email, m: str = mobile, p: str = pwd, c: str | None = conf) -> str:
            fill_signup(page, e, m, p, c)
            click_generate(page)
            return f"email {e}, mobile {m}"
        s.case(cid, "Sign-up / password", "Common (before entity choice)", what, exp, act, judge_reg)

    def act_ws() -> str:
        fill_signup(page, "  " + accounts[5][0].upper() + "  ", accounts[5][1], password)
        click_generate(page)
        return f"email typed with spaces and upper case: '  {accounts[5][0].upper()}  '"
    s.case("S6", "Sign-up / email", "Common (before entity choice)", "Email with leading/trailing spaces and upper case",
           "Spaces trimmed and the address treated case-insensitively, or a clear error",
           act_ws, lambda o: "CHECK")
    return s


def section_login(page: Page, email: str, password: str) -> Suite:
    s = Suite(page, "login")

    def wrong_pwd() -> str:
        login(page, email, password + "x")
        return ""
    s.case("L1", "Login", "Common", "Correct email, wrong password", "Generic 'invalid email or password' message",
           wrong_pwd, lambda o: "PASS" if has_error(o) else "FAIL")

    def unknown() -> str:
        login(page, "qa.unknown.user.zz9@gmail.com", password)
        return ""
    s.case("L2", "Login", "Common", "Email that is not registered",
           "Same generic message as L1 (no hint whether the email exists)", unknown,
           lambda o: "PASS" if has_error(o) else "FAIL")

    def empty() -> str:
        page.goto(URL, wait_until="domcontentloaded")
        page.get_by_role("button", name="Login", exact=True).first.click()
        page.locator(S.LOC["login_username"]).wait_for(state="visible")
        page.get_by_role("button", name="Login", exact=True).last.click()
        page.wait_for_timeout(2000)
        return ""
    s.case("L3", "Login", "Common", "Both fields empty", "Required-field messages, nothing sent", empty,
           lambda o: "PASS" if has_error(o) and not o["server"] else ("FAIL" if o["server"] else "CHECK"))

    def inject() -> str:
        login(page, "' OR '1'='1", "' OR '1'='1")
        return ""
    s.case("L4", "Login", "Common", "SQL-style text in both fields", "Rejected like any wrong login", inject,
           lambda o: "FAIL" if "/bhaskar/profile" in o["url"] else "PASS")

    def brute() -> str:
        for _ in range(6):
            login(page, email, password + "x")
        login(page, email, password)
        end = time.time() + 30
        while time.time() < end and "/bhaskar/profile" not in page.url and "stepfour" not in page.url:
            page.wait_for_timeout(500)
        return f"after 6 wrong passwords the correct password {'still logged in' if 'bhaskar/profile' in page.url or 'stepfour' in page.url else 'did not log in'}"
    s.case("L5", "Login", "Common", "6 wrong passwords in a row, then the correct one",
           "Account temporarily locked / CAPTCHA after repeated failures", brute,
           lambda o: "FAIL" if "still logged in" in o["note"] else "PASS")
    return s


def section_profile_individual(page: Page, email: str, password: str) -> Suite:
    s = Suite(page, "profile_individual")
    open_profile_form(page, email, password)
    publish = re.compile(r"^\s*Publish Your Profile", re.I)

    def empty_publish() -> str:
        page.get_by_role("button", name=publish).first.click()
        page.wait_for_timeout(2500)
        return ""
    s.case("P1", "Profile / required", "Entity: Individual (Mentor form)", "Publish with every field empty",
           "Each required field marked, nothing sent", empty_publish,
           lambda o: "PASS" if has_error(o) and not o["server"] else ("FAIL" if o["server"] else "CHECK"))

    def long_text(name: str, n: int) -> Callable[[], str]:
        def act() -> str:
            box = page.locator(f"textarea[name='{name}']").first
            box.fill("A" * n)
            return f"{name}: typed {n} characters, field kept {len(box.input_value())}"
        return act
    s.case("P2", "Profile / length", "Common", "Professional headline of 150 characters (limit 100)",
           "Field stops at 100 characters or shows an error", long_text("professionalHeadline", 150),
           lambda o: "PASS" if "kept 100" in o["note"] or has_error(o) else "FAIL")
    s.case("P3", "Profile / length", "Common", "Brief introduction of 300 characters (limit 200)",
           "Field stops at 200 characters or shows an error", long_text("briefIntroduction", 300),
           lambda o: "PASS" if "kept 200" in o["note"] or has_error(o) else "FAIL")

    for cid, value in (("P4", "http://example.com"), ("P5", "javascript:alert(1)"), ("P6", "not a link")):
        def act_link(v: str = value) -> str:
            box = page.locator("input[name='vlogBlogWebsite']").first
            box.fill(v)
            box.press("Tab")
            page.get_by_role("button", name=publish).first.click()
            page.wait_for_timeout(2000)
            return f"website = {v!r}"
        s.case(cid, "Profile / links", "Common", f"Website field = '{value}' (rule: https format)",
               "Rejected with 'enter a valid https link'", act_link, blocked)

    for cid, value in (("P7", "12345"), ("P8", "abcdefghij"), ("P9", "0000000000")):
        def act_phone(v: str = value) -> str:
            box = page.locator("input[name='phoneNumber']").first
            box.fill(v)
            box.press("Tab")
            page.get_by_role("button", name=publish).first.click()
            page.wait_for_timeout(2000)
            return f"secondary phone = {v!r}, field kept {box.input_value()!r}"
        s.case(cid, "Profile / phone", "Entity: Individual", f"Secondary phone number = '{value}'",
               "Rejected unless it is a valid 10-digit mobile", act_phone, blocked)

    def multi_limit() -> str:
        rid = page.evaluate(SF.REACT_INPUT_JS, r"^PRIMARY AREA OF DOMAIN EXPERTISE")
        if not rid:
            return "field not found"
        got = SF.react_pick_id(page, rid, count=8)
        return f"selected {len(got)} values (rule: up to 5)"
    s.case("P10", "Profile / multi-select", "Persona: Mentor (also Enabler, Service Enabler)",
           "Pick 8 values in 'Primary area of domain expertise' (spec: up to 5)", "No more than 5 can be selected",
           multi_limit, lambda o: "FAIL" if re.search(r"selected ([6-9]|\d\d)", o["note"]) else "PASS")

    def years() -> str:
        start = page.evaluate(SF.REACT_INPUT_JS, r"^START YEAR OF CURRENT")
        end_ = page.evaluate(SF.REACT_INPUT_JS, r"^END YEAR OF CURRENT")
        if not (start and end_):
            return "fields not found"
        SF.react_pick_id(page, start, want="2024")
        SF.react_pick_id(page, end_, want="2015")
        page.get_by_role("button", name=publish).first.click()
        page.wait_for_timeout(2000)
        return "start 2024, end 2015"
    s.case("P11", "Profile / years", "Persona: Mentor", "Start year 2024 and End year 2015",
           "'End year cannot be before start year'", years,
           lambda o: "PASS" if any(re.search(r"year", e, re.I) for e in o["errors"] + o["toasts"]) else "FAIL")

    tmp = OUT_DIR / "upload_samples"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "not_an_image.jpg").write_bytes(b"this is plain text, not a picture")
    (tmp / "picture.png").write_bytes(png_bytes(40, 40))
    (tmp / "too_big_3mb.jpg").write_bytes(big_jpeg_like(3.0))
    for cid, name, exp in (("P12", "not_an_image.jpg", "Rejected: file is not a JPG image"),
                           ("P13", "picture.png", "Rejected: only JPG allowed"),
                           ("P14", "too_big_3mb.jpg", "Rejected: larger than 2 MB")):
        def act(n: str = name) -> str:
            page.locator("input[type=file]").first.set_input_files(str(tmp / n))
            page.wait_for_timeout(2500)
            return f"uploaded {n}"
        s.case(cid, "Profile / upload", "Common (profile picture / logo)", f"Upload '{name}'", exp, act, blocked)
    return s


def section_profile_org(page: Page, email: str, password: str, persona: str) -> Suite:
    s = Suite(page, f"profile_{re.sub(r'[^A-Za-z]+', '_', persona)}")
    open_profile_form(page, email, password)
    publish = re.compile(r"^\s*Publish Your Profile", re.I)

    def empty_publish() -> str:
        page.get_by_role("button", name=publish).first.click()
        page.wait_for_timeout(2500)
        return ""
    s.case("O1", "Profile / required", f"Persona: {persona}", "Publish with every field empty",
           "Each required field marked, nothing sent", empty_publish,
           lambda o: "PASS" if has_error(o) and not o["server"] else ("FAIL" if o["server"] else "CHECK"))

    numeric = page.evaluate(r"""() => [...document.querySelectorAll('input')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width > 0 && /number|count|graduated|incubation|direct/i.test((e.placeholder || '') + ' ' + (e.name || ''));
      }).map(e => e.name || e.placeholder)""")
    for cid, value in (("O2", "-5"), ("O3", "2.5"), ("O4", "99999999999999")):
        def act(v: str = value) -> str:
            if not numeric:
                return "no numeric field on this form"
            key = numeric[0]
            box = page.locator(f"input[name='{key}']").first if page.locator(f"input[name='{key}']").count() \
                else page.get_by_placeholder(key).first
            box.fill(v)
            box.press("Tab")
            page.get_by_role("button", name=publish).first.click()
            page.wait_for_timeout(2000)
            return f"{key} = {v!r}, field kept {box.input_value()!r}"
        s.case(cid, "Profile / numbers", f"Persona: {persona}", f"Count field = '{value}'",
               "Only whole numbers >= 0 within a sensible range", act, blocked)

    def bad_email() -> str:
        boxes = page.locator("input").filter(has=page.locator("xpath=self::*[contains(translate(@placeholder,'EMAIL','email'),'email')]"))
        box = page.get_by_placeholder(re.compile("email", re.I)).first
        if not box.count() or not box.is_visible():
            return f"no email field visible ({boxes.count()})"
        box.fill("abc@")
        box.press("Tab")
        page.get_by_role("button", name=publish).first.click()
        page.wait_for_timeout(2000)
        return "key-personnel email = 'abc@'"
    s.case("O5", "Profile / email", f"Persona: {persona}", "Key-personnel email = 'abc@'",
           "Rejected as an invalid email", bad_email,
           lambda o: "CHECK" if o["note"].startswith("no email") else blocked(o))
    return s


def section_nav(page: Page) -> Suite:
    s = Suite(page, "navigation")
    for cid, path, what in (("N1", "/bhaskar/profile", "Open the dashboard URL without logging in"),
                            ("N2", "/bhaskar/register/stepone", "Open 'Select entity type' URL without logging in"),
                            ("N3", "/bhaskar/register/stepthree", "Open the T&C step URL without logging in")):
        def act(pth: str = path) -> str:
            page.context.clear_cookies()
            page.goto("https://www.startupindia.gov.in/bhaskar/register", wait_until="domcontentloaded")
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            page.goto("https://www.startupindia.gov.in" + pth, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            return f"landed on {page.url}"
        s.case(cid, "Navigation", "Common", what, "Redirected to login / registration, no private page shown", act,
               lambda o, pth=path: "FAIL" if o["url"].rstrip("/").endswith(pth) else "PASS")
    return s


INPUT_BY_LABEL_JS = r"""(rx) => {
  const re = new RegExp(rx, 'i');
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const labels = [...document.querySelectorAll('label, h5, h6, span, p')].filter(l => vis(l) && re.test((l.innerText || '').trim()));
  for (const l of labels) {
    for (let p = l.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
      const inp = [...p.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([id^="react-select"])')].find(vis);
      if (inp) { inp.setAttribute('data-qa', rx); return true; }
    }
  }
  return false;
}"""


def field(page: Page, label_rx: str):
    if not page.evaluate(INPUT_BY_LABEL_JS, label_rx):
        return None
    return page.locator(f"[data-qa='{label_rx}']").first


def near_messages(page: Page, label_rx: str) -> list[str]:
    box = field(page, label_rx)
    if box is None:
        return []
    text = box.locator("xpath=ancestor::div[3]").inner_text()
    return [t.strip() for t in text.split("\n") if re.search(r"valid|required|must|invalid|format|digit|character|enter", t, re.I)]


def section_otp_basic(page: Page, email: str, mobile: str, password: str) -> list[Suite]:
    s = Suite(page, "otp")
    fill_signup(page, email, mobile, password)
    click_generate(page)
    page.locator(S.LOC["email_otp_input"]).wait_for(state="visible", timeout=60_000)
    print(f"[INFO] New test account for OTP/Basic checks: {email} / {mobile}", flush=True)

    def wrong(kind: str, value: str) -> Callable[[], str]:
        def act() -> str:
            box = page.locator(S.LOC[f"{kind}_otp_input"])
            box.fill(value)
            kept = box.input_value()
            btn = page.locator(S.LOC[f"verify_{kind}_btn"])
            if btn.is_enabled():
                btn.click()
                page.wait_for_timeout(2500)
                return f"typed {value!r}, field kept {kept!r}, Verify clicked"
            return f"typed {value!r}, field kept {kept!r}, Verify button disabled"
        return act
    s.case("O1", "OTP", "Common", "Email OTP '000000' (wrong)", "'Invalid OTP' and the email stays unverified",
           wrong("email", "000000"), lambda o: "PASS" if has_error(o) else "FAIL")
    s.case("O2", "OTP", "Common", "Mobile OTP '000000' (wrong)", "'Invalid OTP' and the mobile stays unverified",
           wrong("mobile", "000000"), lambda o: "PASS" if has_error(o) else "FAIL")
    s.case("O3", "OTP", "Common", "Email OTP with letters 'abcdef'", "Letters refused or 'Invalid OTP'",
           wrong("email", "abcdef"), lambda o: "PASS" if "disabled" in o["note"] or "kept ''" in o["note"] or has_error(o) else "FAIL")
    s.case("O4", "OTP", "Common", "Email OTP with 5 digits '12345'", "Verify stays disabled or 'Invalid OTP'",
           wrong("email", "12345"), lambda o: "PASS" if "disabled" in o["note"] or has_error(o) else "FAIL")

    def many_wrong() -> str:
        replies_before = len(s.replies)
        for _ in range(6):
            wrong("mobile", "111111")()
        msgs = [r for r in s.replies[replies_before:]]
        return f"6 wrong mobile OTPs sent; last reply: {msgs[-1][:160] if msgs else 'none'}"
    s.case("O5", "OTP", "Common", "6 wrong mobile OTPs in a row", "Further attempts blocked / OTP expired after a few tries",
           many_wrong, lambda o: "PASS" if re.search(r"attempt|limit|block|exceed|expired", " ".join(o["server"] + o["toasts"]), re.I) else "FAIL")

    def resend() -> str:
        btn = page.get_by_role("button", name=re.compile(r"resend", re.I))
        txt = page.locator("body").inner_text()
        timer = re.findall(r"(?i)resend[^\n]{0,40}", txt)[:3]
        if not btn.count():
            return f"no Resend button; text near: {timer}"
        first = btn.first
        state = "enabled" if first.is_enabled() else "disabled"
        if first.is_enabled():
            first.click()
            page.wait_for_timeout(2500)
        return f"Resend button {state} right after Generate OTP; text: {timer}"
    s.case("O6", "OTP", "Common", "Resend OTP straight away", "Resend available only after a countdown, with a message",
           resend, lambda o: "CHECK")

    def get_id_early() -> str:
        btn = page.locator(S.LOC["get_id_btn"])
        return f"'Get your BHASKAR ID' visible={btn.is_visible()}"
    s.case("O7", "OTP", "Common", "'Get your BHASKAR ID' before verifying", "Not available until both OTPs are verified",
           get_id_early, lambda o: "FAIL" if "visible=True" in o["note"] else "PASS")
    s.save()

    print("[ACTION] Now type the REAL Email OTP and Mobile OTP for this account in the browser and click Verify for each.",
          f"Email {email} / Mobile {mobile}. Waiting up to 15 minutes ...", flush=True)
    page.locator(S.LOC["get_id_btn"]).wait_for(state="visible", timeout=900_000)
    page.locator(S.LOC["get_id_btn"]).click()
    page.wait_for_url(re.compile("stepone"), timeout=60_000)

    b = Suite(page, "basic")
    SF.choose_entity(page, "Startup Founder / Entrepreneur", 30)
    SF.choose_persona(page, "Startup Founder / Entrepreneur", 30)
    page.get_by_role("button", name=re.compile(r"^\s*PREVIEW AND SUBMIT", re.I)).wait_for(timeout=30_000)
    preview = re.compile(r"^\s*PREVIEW AND SUBMIT", re.I)

    def submit_empty() -> str:
        page.get_by_role("button", name=preview).first.click()
        page.wait_for_timeout(2000)
        return ""
    b.case("B1", "Basic details", "Entity: Individual", "PREVIEW AND SUBMIT with nothing filled",
           "Required fields marked, nothing sent", submit_empty,
           lambda o: "PASS" if has_error(o) and not o["server"] else "FAIL")

    def put(label_rx: str, value: str) -> Callable[[], str]:
        def act() -> str:
            box = field(page, label_rx)
            if box is None:
                return f"field {label_rx!r} not found"
            box.fill(value)
            box.press("Tab")
            page.wait_for_timeout(1500)
            return f"{label_rx} = {value!r}, field kept {box.input_value()!r}; messages: {near_messages(page, label_rx)}"
        return act

    def judge_msg(o: dict[str, Any]) -> str:
        if "not found" in o["note"]:
            return "CHECK"
        return "PASS" if re.search(r"messages: \[.+\]", o["note"]) else "FAIL"
    for cid, rx, value, what, exp in (
            ("B2", r"^PAN", "abcpt1234d", "PAN in lower case", "Converted to upper case or a format error"),
            ("B3", r"^PAN", "ABCCT1234D", "Company PAN (4th letter C) on an Individual form", "Rejected: 4th letter must be P"),
            ("B4", r"^PAN", "ABCPT1234", "PAN with 9 characters", "Format error"),
            ("B5", r"^PIN", "000000", "PIN code 000000", "Rejected as invalid PIN"),
            ("B6", r"^PIN", "12345", "PIN code with 5 digits", "Format error"),
            ("B7", r"^PIN", "abcdef", "PIN code with letters", "Letters refused or error")):
        b.case(cid, "Basic details", "Entity: Individual (PAN-based personas)", what, exp, put(rx, value),
               (lambda o: "PASS" if "kept 'ABCPT1234D'" in o["note"] or re.search(r"messages: \[.+\]", o["note"]) else "FAIL")
               if cid == "B2" else judge_msg)

    def years_offered() -> str:
        box = page.get_by_role("textbox", name="Select year of birth").first
        box.click()
        page.wait_for_timeout(800)
        cells = [t.strip() for t in page.locator("[class*='year-text'], [class*='year']").all_inner_texts() if t.strip().isdigit()]
        page.keyboard.press("Escape")
        latest = max((int(c) for c in cells), default=0)
        return f"years on the first picker page: {cells[:15]}; latest selectable {latest}"
    b.case("B8", "Basic details", "Entity: Individual", "Year of birth picker - latest year offered",
           "Nobody younger than 18 (born after 2008) can be selected", years_offered,
           lambda o: "FAIL" if re.search(r"latest selectable 20(09|1\d|2\d)", o["note"]) else "PASS")
    b.save()

    org = Suite(page, "basic_org")

    def to_org() -> str:
        back = page.get_by_role("button", name=re.compile(r"^\s*BACK\s*$", re.I)).first
        back.click()
        page.wait_for_timeout(3000)
        SF.choose_entity(page, "Startup", 30)
        SF.choose_persona(page, "Startup", 30)
        page.get_by_role("button", name=preview).wait_for(timeout=30_000)
        page.wait_for_timeout(1500)
        return f"after BACK + Organization > Startup, now on {page.url}"
    org.case("B9", "Navigation", "Common", "BACK from Basic details and switch to Organization > Startup",
             "Allowed before submitting; the new form opens", to_org,
             lambda o: "PASS" if "now on" in o["note"] else "FAIL")
    for cid, rx, value, what, exp in (
            ("B10", r"^PAN OF THE", "ABCPQ1234D", "Entity PAN with 4th letter P (a person's PAN)", "Rejected: not an entity PAN"),
            ("B11", r"^PAN OF THE", "ABCCQ123", "Entity PAN with 8 characters", "Format error"),
            ("B12", r"^GENERAL CONTACT EMAIL", "abc@", "General contact email 'abc@'", "Invalid email error"),
            ("B13", r"^GENERAL CONTACT PHONE", "12345", "General contact phone '12345'", "10-digit error"),
            ("B14", r"^NAME OF THE ENTITY", "<b>QA</b> 123 !!", "Entity name with HTML, digits and symbols", "Rejected or cleaned"),
            ("B15", r"^PIN", "999999", "Entity PIN 999999 (not a real PIN)", "Rejected as invalid PIN")):
        org.case(cid, "Basic details", "Entity: Organisation (PAN-based personas)" if "PAN" in rx else "Entity: Organisation",
                 what, exp, put(rx, value), judge_msg)
    org.save()
    return [s, b, org]


def main() -> None:
    ap = argparse.ArgumentParser(description="Edge and negative checks on the BHASKAR registration journey (production).")
    ap.add_argument("sections", nargs="+", choices=["signup", "login", "profile", "org", "nav", "otp"])
    args = ap.parse_args()
    accounts = BH.load_accounts()
    password = S.DATA["password"]
    used = {a["email"] for a in accounts.values()}
    spare = [f"{e}@gmail.com" for e in BH.email_variants() if f"{e}@gmail.com" not in used][-6:]
    signup_accounts = [(spare[i], str(5550200300 + i + 1)) for i in range(6)]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        for section in args.sections:
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            page.set_default_timeout(20_000)
            if section == "signup":
                section_signup(page, signup_accounts, password).save()
            elif section == "login":
                section_login(page, accounts["Startup Founder / Entrepreneur"]["email"], password).save()
            elif section == "profile":
                section_profile_individual(page, accounts["Mentor"]["email"], password).save()
            elif section == "org":
                for persona in ("Incubator", "Corporates"):
                    pg = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
                    pg.set_default_timeout(20_000)
                    section_profile_org(pg, accounts[persona]["email"], password, persona).save()
            elif section == "nav":
                section_nav(page).save()
            elif section == "otp":
                section_otp_basic(page, spare[0], "5550200401", password)
        browser.close()


if __name__ == "__main__":
    main()
