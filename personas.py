import random
import re
import string
import time
from typing import Callable

from playwright.sync_api import Locator, Page, Response

PERSONAS: dict[str, dict[str, str]] = {
    "Startup Founder / Entrepreneur": {"card": r"^\s*Startup\s*Founder", "org_box": "Please input your Startup's",
                                       "org": "QA Test Startup", "headline": "QA Test Founder"},
    "Traditional Business Owner": {"card": r"^\s*Traditional\s*Business", "org_box": "Mention your current organisation",
                                   "org": "QA Test Traders", "headline": "QA Test Business Owner"},
}
ANY_CARD_RX = re.compile(r"^\s*(Startup\s*Founder|Traditional\s*Business|Individual\s*Investor|Mentor|Enabler)", re.I)
SITE_PAN_RX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
INDIVIDUAL_PAN_RX = re.compile(r"^[A-Z]{3}P[A-Z][0-9]{4}[A-Z]$")
BHASKAR_ID_RX = re.compile(r"\b((?:IN|OI)-\d{4}-[A-Z0-9]{4,})\b")
RNG = random.Random()
PINS = ["110001", "400001", "560001", "600001", "380001", "500001", "226001", "700001", "302001", "122001"]


class FlowError(Exception):
    pass


def make_pan(last_name: str, rng: random.Random = RNG) -> str:
    pan = ("".join(rng.choice(string.ascii_uppercase) for _ in range(3)) + "P" + (last_name or "T")[0].upper()
           + f"{rng.randint(1, 9999):04d}" + rng.choice(string.ascii_uppercase))
    if not (SITE_PAN_RX.match(pan) and INDIVIDUAL_PAN_RX.match(pan)):
        raise FlowError(f"generated PAN {pan} fails validation")
    return pan


def next_persona(last: str) -> str:
    names = list(PERSONAS)
    return names[(names.index(last) + 1) % len(names)] if last in names else names[0]


def make_test_data(last_name: str, persona: str, rng: random.Random = RNG) -> dict[str, str]:
    tag = f"{rng.randint(1000, 9999)}"
    p = PERSONAS[persona]
    return {
        "year_of_birth": str(rng.randint(1975, 2000)),
        "gender": rng.choice(["Male", "Female"]),
        "pin": rng.choice(PINS),
        "pan": make_pan(last_name, rng),
        "org_name": f"{p['org']} {tag}",
        "headline": f"{p['headline']} {tag}",
        "introduction": "QA automation test profile created by the daily production signup check.",
        "address": "QA Test Address",
    }


def visible(loc: Locator, n: int = 5) -> Locator | None:
    try:
        return next((loc.nth(i) for i in range(min(loc.count(), n)) if loc.nth(i).is_visible()), None)
    except Exception:
        return None


def wait_visible(page: Page, make: Callable[[], Locator], timeout_s: float) -> Locator | None:
    end = time.time() + timeout_s
    while time.time() < end:
        el = visible(make())
        if el is not None:
            return el
        page.wait_for_timeout(300)
    return None


def textbox(page: Page, name: str, exact: bool = False) -> Locator | None:
    return visible(page.get_by_role("textbox", name=name, exact=exact))


def fill_textbox(page: Page, name: str, value: str, exact: bool = False, required: bool = True) -> bool:
    el = textbox(page, name, exact)
    if el is None:
        if required:
            raise FlowError(f"text box '{name}' not found")
        return False
    el.scroll_into_view_if_needed()
    el.fill(str(value))
    el.press("Tab")
    return True


def click_button(page: Page, rx: str | re.Pattern[str], timeout_s: float = 10) -> Locator:
    el = wait_visible(page, lambda: page.get_by_role("button", name=rx), timeout_s)
    if el is None:
        raise FlowError(f"button '{rx.pattern if isinstance(rx, re.Pattern) else rx}' not found")
    end = time.time() + 8
    while not el.is_enabled() and time.time() < end:
        page.wait_for_timeout(400)
    if not el.is_enabled():
        raise FlowError(f"button '{el.inner_text().strip()[:40]}' is disabled")
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(800)
    return el


REACT_INPUT_JS = r"""(rx) => {
  const re = new RegExp(rx, 'i');
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const inputs = [...document.querySelectorAll('input[id^="react-select-"][id$="-input"]')];
  const labels = [...document.querySelectorAll('label, span, p, div, h5, h6, strong')]
     .filter(e => vis(e) && e.children.length <= 3 && re.test((e.innerText || '').trim().split('\n')[0]));
  const inner = labels.filter(l => !labels.some(o => o !== l && l.contains(o)));
  for (const l of inner) {
    let p = l;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const inside = inputs.filter(x => p.contains(x));
      if (inside.length === 1) return inside[0].id;
      if (inside.length > 1) {
        const after = inside.find(x => l.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING);
        return (after || inside[0]).id;
      }
    }
  }
  return null;
}"""


def react_pick(page: Page, label_rx: str, want: str | None = None, count: int = 1) -> list[str]:
    rid: str | None = page.evaluate(REACT_INPUT_JS, label_rx)
    if not rid:
        raise FlowError(f"dropdown '{label_rx}' not found")
    picked: list[str] = []
    for _ in range(max(count, 1)):
        inp = page.locator(f"#{rid}")
        inp.scroll_into_view_if_needed()
        inp.click()
        page.wait_for_timeout(500)
        if want:
            page.keyboard.type(str(want))
            page.wait_for_timeout(500)
        opts = page.get_by_role("option")
        names = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 60))]
        names = [n for n in names if n and n not in picked]
        if not names:
            page.keyboard.press("Escape")
            break
        choice = next((n for n in names if want and n.lower() == str(want).lower()), None) or names[0]
        page.get_by_role("option", name=choice, exact=True).first.click()
        picked.append(choice)
        page.wait_for_timeout(300)
        if want:
            break
    return picked


def switch(page: Page, name: str, on: bool = True) -> bool:
    inp = page.locator(f'input[name="{name}"]').first
    if not inp.count():
        return False
    if inp.is_checked() == on:
        return True
    slider = inp.locator("xpath=following-sibling::*[contains(@class,'slider')][1]")
    try:
        (slider if slider.count() else inp).click(force=True)
    except Exception:
        pass
    page.wait_for_timeout(300)
    if inp.is_checked() != on:
        try:
            inp.set_checked(on, force=True)
            page.wait_for_timeout(200)
        except Exception:
            pass
    return inp.is_checked() == on


def pick_year(page: Page, box_name: str, year: str, max_pages: int = 12) -> None:
    box = textbox(page, box_name)
    if box is None:
        raise FlowError(f"year picker '{box_name}' not found")
    box.click()
    page.wait_for_timeout(300)
    for _ in range(max_pages):
        cell = visible(page.get_by_text(str(year), exact=True))
        if cell is not None:
            cell.click()
            page.wait_for_timeout(300)
            return
        prev = visible(page.get_by_role("button", name=re.compile(r"previous", re.I)))
        if prev is None:
            break
        prev.click()
        page.wait_for_timeout(250)
    raise FlowError(f"year {year} not reachable in the year picker")


def persona_card(page: Page) -> Locator | None:
    return visible(page.get_by_text(ANY_CARD_RX), 10)


def choose_entity(page: Page, timeout_s: float) -> None:
    head = wait_visible(page, lambda: page.get_by_role("heading", name=re.compile(r"^\s*Individual\s*$", re.I)), timeout_s)
    if head is None:
        raise FlowError("'Individual' entity type not shown")
    for _ in range(3):
        if persona_card(page) is not None:
            return
        head.click()
        page.wait_for_timeout(1500)
    if persona_card(page) is None:
        raise FlowError("persona cards did not open after choosing 'Individual'")


def choose_persona(page: Page, persona: str, timeout_s: float) -> None:
    card = wait_visible(page, lambda: page.get_by_text(re.compile(PERSONAS[persona]["card"], re.I)), timeout_s)
    if card is None:
        shown = [t.strip() for t in page.locator("h1, h2, h3, h4, h5, h6, p, span").all_inner_texts() if t.strip()][:40]
        raise FlowError(f"'{persona}' card not found. Texts on page: {shown}")
    card.scroll_into_view_if_needed()
    card.click(force=True)
    page.wait_for_timeout(800)


def fill_basic_form(page: Page, data: dict[str, str], email: str, timeout_s: float) -> None:
    if wait_visible(page, lambda: page.get_by_role("textbox", name="Select year of birth"), timeout_s) is None:
        raise FlowError("basic form (year of birth) did not open")
    pick_year(page, "Select year of birth", data["year_of_birth"])
    gender = page.locator('select[name="gender"]')
    if gender.count():
        try:
            gender.select_option(label=data["gender"])
        except Exception:
            gender.select_option(data["gender"])
    country = page.locator('select[name="country"]')
    if country.count():
        country.select_option(label="India")
    fill_textbox(page, "Please write the 6 digit pin", data["pin"])
    page.wait_for_timeout(1500)
    fill_textbox(page, "Please enter your PAN.", data["pan"])
    sliders = page.locator(".switch input[type=checkbox], label.switch input")
    if sliders.count() and not sliders.first.is_checked():
        sliders.first.locator("xpath=following-sibling::*[contains(@class,'slider')][1]").click(force=True)
    box = textbox(page, "Enter your Email ID for all")
    if box is not None and not box.input_value():
        box.fill(email)


def preview_and_submit(page: Page, timeout_s: float) -> None:
    click_button(page, re.compile(r"^\s*PREVIEW AND SUBMIT", re.I), timeout_s)
    end = time.time() + timeout_s
    while time.time() < end:
        if "stepthree" in page.url or visible(page.get_by_text("I Agree to Terms & Conditions")):
            return
        sub = visible(page.get_by_role("button", name=re.compile(r"^\s*(Submit|Confirm)\s*$", re.I)))
        if sub is not None:
            sub.click()
        page.wait_for_timeout(500)
    raise FlowError("Terms & Conditions did not open after PREVIEW AND SUBMIT")


def accept_terms(page: Page, timeout_s: float) -> None:
    tc = wait_visible(page, lambda: page.get_by_text("I Agree to Terms & Conditions"), timeout_s)
    if tc is None:
        raise FlowError("'I Agree to Terms & Conditions' not shown")
    box = visible(page.get_by_role("checkbox", name=re.compile(r"I Agree", re.I)))
    box.check() if box is not None else tc.click()
    page.wait_for_timeout(400)


def generate_bhaskar_id(page: Page, timeout_s: float) -> str:
    click_button(page, re.compile(r"^\s*Generate BHASKAR ID", re.I), timeout_s)
    btn = wait_visible(page, lambda: page.get_by_role("button", name=re.compile(r"^\s*Complete your profile", re.I)), timeout_s)
    if btn is None:
        raise FlowError("BHASKAR ID popup ('Complete your profile') not shown after Generate BHASKAR ID")
    end = time.time() + 10
    while time.time() < end:
        m = BHASKAR_ID_RX.search(" ".join(page.locator("body").inner_text().split()))
        if m:
            return m.group(1)
        page.wait_for_timeout(400)
    return ""


def open_profile(page: Page, timeout_s: float) -> None:
    click_button(page, re.compile(r"^\s*Complete your profile", re.I), timeout_s)
    page.wait_for_timeout(1200)
    if wait_visible(page, lambda: page.locator("input:not([type=hidden]), select, textarea"), timeout_s) is None:
        raise FlowError("profile form did not open after 'Complete your profile'")


def fill_profile(page: Page, data: dict[str, str], mobile: str, persona: str, log: Callable[[str], None]) -> list[str]:
    empty: list[str] = []

    def pick(label_rx: str, want: str | None = None, count: int = 1, name: str | None = None) -> None:
        try:
            got = react_pick(page, label_rx, want, count)
            log(f"{name or label_rx}: {', '.join(got) or 'no options'}")
            if not got:
                empty.append(name or label_rx)
        except FlowError as e:
            log(f"{name or label_rx}: {e}")
            empty.append(name or label_rx)

    fill_textbox(page, "Please add a brief", data["headline"])
    fill_textbox(page, "Please provide a brief", data["introduction"])
    pick(r"^Primary Sector", count=2, name="Primary Sector of Expertise/Investment")
    if switch(page, "isProfessional", True):
        pick(r"^Total years of Experience", want="2", name="Total years of Experience")
    tab = visible(page.get_by_role("button", name="School", exact=True))
    if tab is not None:
        tab.click()
        page.wait_for_timeout(400)
        fill_textbox(page, "Name of school", "QA Test School", required=False)
        pick(r"^Year of Graduation", want=str(int(data["year_of_birth"]) + 18), name="School - Year of Graduation")
    pick(r"^What type of stakeholder", count=2, name="Stakeholders to engage with")
    pick(r"^What stage of startup", count=1, name="Stage of startup")
    pick(r"^Sector of Interest", count=2, name="Sector of Interest")
    fill_textbox(page, "Please enter number", mobile, required=False)
    addr = visible(page.get_by_placeholder("Please enter your address", exact=True))
    if addr is not None:
        addr.fill(data["address"])
    for n, v in (("address2", "Test Street"), ("address3", "Gurugram"), ("vlogBlogWebsite", "https://example.com"),
                 ("socialMediaLinks", "https://www.linkedin.com/in/qa-test")):
        el = page.locator(f'input[name="{n}"]').first
        if el.count() and el.is_visible():
            el.fill(v)
    for n in ("emailNotifications", "smsWhatsappNotifications", "inAppNotifications"):
        switch(page, n, True)
    org_box = PERSONAS[persona]["org_box"]
    if not fill_textbox(page, org_box, data["org_name"], required=False):
        box = visible(page.locator("input[name='nameOfBusiness'], input[name='nameOfStartup']"))
        if box is None:
            raise FlowError(f"organisation name box '{org_box}...' not found")
        box.fill(data["org_name"])
    pick(r"^Country Of Headquarter", want="India", name="Country of Headquarter")
    pick(r"^Current Role", want="CXO level role", name="Current Role")
    return empty


def publish(page: Page, timeout_s: float) -> tuple[bool, list[str]]:
    before = page.url
    replies: list[str] = []

    def on_response(r: Response) -> None:
        if "/sih/api" in r.url and r.request.method in ("POST", "PUT", "PATCH"):
            try:
                body = " ".join(r.text().split())[:300]
            except Exception:
                body = ""
            replies.append(f"{r.request.method} {r.url.split('?')[0]} -> HTTP {r.status} {body}")

    page.on("response", on_response)
    try:
        click_button(page, re.compile(r"^\s*Publish Your Profile", re.I), timeout_s)
        end = time.time() + timeout_s
        while time.time() < end:
            if page.url != before or visible(page.get_by_text(re.compile(r"profile updated successfully", re.I))):
                return True, replies
            page.wait_for_timeout(400)
        errors = [t.strip() for t in page.locator("p[class*=error], .error, .text-danger").all_inner_texts() if t.strip()]
        if errors:
            replies.append("Errors on page: " + "; ".join(errors[:10]))
        return False, replies
    finally:
        page.remove_listener("response", on_response)


def bhaskar_id_from_page(page: Page) -> str:
    m = re.search(r"bhaskarid=((?:IN|OI)-\d{4}-[A-Z0-9]{4,})", page.url, re.I)
    if m:
        return m.group(1)
    m = BHASKAR_ID_RX.search(" ".join(page.locator("body").inner_text().split()))
    return m.group(1) if m else ""
