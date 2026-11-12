import random
import re
import string
import time
from typing import Callable

from playwright.sync_api import Locator, Page, Response

IND, ORG = "Individual", "Organization"


def _persona(entity: str, card: str, org: str, headline: str, org_box: str = "", daily: bool = False) -> dict[str, str]:
    return {"entity": entity, "card": card, "org": org, "headline": headline, "org_box": org_box, "daily": "1" if daily else ""}


PERSONAS: dict[str, dict[str, str]] = {
    "Startup Founder / Entrepreneur": _persona(IND, r"^\s*Startup\s*Founder", "QA Test Startup", "QA Test Founder",
                                               "Please input your Startup's", True),
    "Traditional Business Owner": _persona(IND, r"^\s*Traditional\s*Business\s*(Owner|\.\.\.)?\s*$", "QA Test Traders",
                                           "QA Test Business Owner", "Mention your current organisation", True),
    "Mentor": _persona(IND, r"^\s*Mentor\s*$", "QA Test Mentoring", "QA Test Mentor", "Mention your current organisation", True),
    "Individual Investor": _persona(IND, r"^\s*Individual\s*Investor\s*$", "QA Test Investments", "QA Test Investor"),
    "Enabler": _persona(IND, r"^\s*Enabler\s*$", "QA Test Enablers", "QA Test Enabler"),
    "Explorer": _persona(IND, r"^\s*Explorers?\s*$", "QA Test Explorers", "QA Test Explorer"),
    "Student": _persona(IND, r"^\s*Students?\s*$", "QA Test College", "QA Test Student"),
    "Startup": _persona(ORG, r"^\s*Startups?\s*$", "QA Test Startup Labs", "QA Test Startup Organisation"),
    "Traditional Businesses": _persona(ORG, r"^\s*Traditional\s*Business(es)?\s*$", "QA Test Enterprises", "QA Test Business"),
    "Incubator": _persona(ORG, r"^\s*Incubators?\s*$", "QA Test Incubation Centre", "QA Test Incubator"),
    "Accelerator": _persona(ORG, r"^\s*Accelerators?\s*$", "QA Test Accelerator Hub", "QA Test Accelerator"),
    "Corporates": _persona(ORG, r"^\s*Corporates?\s*$", "QA Test Corporation", "QA Test Corporate"),
    "Institutional Investor": _persona(ORG, r"^\s*Institutional\s*Investors?", "QA Test Capital", "QA Test Fund"),
    "Industry Association": _persona(ORG, r"^\s*Industry\s*Associations?\s*$", "QA Test Industry Council", "QA Test Association"),
    "Government Bodies": _persona(ORG, r"^\s*(Government\s*Bod(y|ies)|Govt\.?\s*Bod(y|ies))", "QA Test Department", "QA Test Government Body"),
    "Service Enabler": _persona(ORG, r"^\s*Service\s*Enablers?\s*$", "QA Test Services", "QA Test Service Enabler"),
    "Academia": _persona(ORG, r"^\s*(Academia|Academic\s*Institutions?)\s*$", "QA Test Institute", "QA Test Academia"),
}
ANY_CARD_RX = re.compile("|".join(f"(?:{v['card']})" for v in PERSONAS.values()), re.I)
DAILY = [k for k, v in PERSONAS.items() if v["daily"]]
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
    names = DAILY
    return names[(names.index(last) + 1) % len(names)] if last in names else names[0]


def make_test_data(last_name: str, persona: str, rng: random.Random = RNG) -> dict[str, str]:
    tag = f"{rng.randint(1000, 9999)}"
    word = "".join(rng.choice(string.ascii_uppercase) for _ in range(4))
    p = PERSONAS[persona]
    pin = rng.choice(PINS)
    state_letters, gst_code = PIN_STATE[pin[:2]]
    org_pan = make_org_pan("C", rng)
    return {
        "year_of_birth": str(rng.randint(1975, 2000)),
        "gender": rng.choice(["Male", "Female"]),
        "pin": pin,
        "pan": make_pan(last_name, rng),
        "org_pan": org_pan,
        "cin": f"U{rng.choice(['72900', '62099', '63999', '74999'])}{state_letters}{rng.randint(2012, 2022)}PTC{rng.randint(1, 999999):06d}",
        "gstin": make_gstin(org_pan, gst_code),
        "udyam": f"UDYAM-{state_letters}-{rng.randint(1, 30):02d}-{rng.randint(1, 9999999):07d}",
        "sebi": f"INA{rng.randint(100000000, 999999999)}",
        "founded": str(rng.randint(2012, 2022)),
        "org_name": f"{p['org']} {word}",
        "headline": f"{p['headline']} {tag}",
        "introduction": "QA automation test profile created by the production bug finding check.",
        "address": "QA Test Address",
    }


PIN_STATE = {"11": ("DL", "07"), "40": ("MH", "27"), "56": ("KA", "29"), "60": ("TN", "33"), "38": ("GJ", "24"),
             "50": ("TG", "36"), "22": ("UP", "09"), "70": ("WB", "19"), "30": ("RJ", "08"), "12": ("HR", "06")}
CHARS36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def make_org_pan(holder: str, rng: random.Random = RNG) -> str:
    return ("".join(rng.choice(string.ascii_uppercase) for _ in range(3)) + holder + "Q"
            + f"{rng.randint(1, 9999):04d}" + rng.choice(string.ascii_uppercase))


def make_gstin(pan: str, state_code: str) -> str:
    base = f"{state_code}{pan}1Z"
    total = 0
    for i, ch in enumerate(base):
        prod = CHARS36.index(ch) * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return base + CHARS36[(36 - total % 36) % 36]


def value_for(label: str, placeholder: str, data: dict[str, str]) -> str:
    t = f"{label} {placeholder}".upper()
    if "PAN" in re.findall(r"[A-Z]+", t):
        return data["org_pan"] if re.search(r"ENTITY|ORGANI|COMPANY|FIRM", t) else data["pan"]
    rules: list[tuple[str, str]] = [
        (r"\bCIN\b", data["cin"]), (r"GSTIN|\bGST\b", data["gstin"]), (r"UDYAM", data["udyam"]),
        (r"SEBI", data["sebi"]), (r"PIN|ZIP|POSTAL", data["pin"]),
        (r"SECONDARY.*E-?MAIL|E-?MAIL.*(KEY PERSON|E-CELL|PROGRAM|DEPARTMENT)", "qa.tester@example.com"),
        (r"E-?MAIL", data.get("email", "")), (r"PHONE|MOBILE|CONTACT NUMBER|HELPLINE|TELEPHONE", data.get("mobile", "")),
        (r"HTTP|LINK|URL|WEBSITE|VLOG|BLOG", "https://example.com"),
        (r"^\s*(IN)?DIRECT\b|EMPLOYEE|JOBS|HEADCOUNT|TEAM SIZE|NUMBER OF|AMOUNT|HOW MANY|COUNT|TOTAL", "10"),
        (r"DESIGNATION|\bROLE\b", "Director"),
        (r"NAME OF (A )?KEY PERSON|LEAD|FOUNDING TEAM MEMBER|CONTACT PERSON|FIRST NAME|LAST NAME", "QA Tester"),
        (r"ADDRESS", data["address"]),
        (r"HEADLINE", data["headline"]),
        (r"INTRODUCTION|DESCRIPTION|DETAILS|PROCESS|ABOUT|AWARD|RECOGNITION|SUPPORT", "QA test details for the bug finding run"),
        (r"NAME|ORGANI|ENTITY|INSTITUT|BRAND|PROGRAM|FUND|COMPANY|UNIVERSITY|COLLEGE|BOARD|E-CELL|DEPARTMENT", data["org_name"]),
    ]
    return next((v for rx, v in rules if re.search(rx, t)), "QA Test")


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
    return react_pick_id(page, rid, want, count)


def react_pick_id(page: Page, rid: str, want: str | None = None, count: int = 1) -> list[str]:
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
        names = [n.strip() for n in opts.all_inner_texts()]
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


def pick_year(page: Page, box_name: str, year: str, max_pages: int = 12, attempts: int = 3) -> None:
    for _ in range(attempts):
        box = textbox(page, box_name)
        if box is None:
            raise FlowError(f"year picker '{box_name}' not found")
        box.scroll_into_view_if_needed()
        box.click()
        page.wait_for_timeout(500)
        for _ in range(max_pages):
            cell = visible(page.locator(".react-datepicker__year-text, [class*='year-text']").get_by_text(str(year), exact=True)) \
                or visible(page.get_by_text(str(year), exact=True))
            if cell is not None:
                cell.click()
                page.wait_for_timeout(500)
                break
            prev = visible(page.get_by_role("button", name=re.compile(r"previous", re.I)))
            if prev is None:
                break
            prev.click()
            page.wait_for_timeout(250)
        if box.input_value().strip() == str(year):
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    raise FlowError(f"year {year} was not kept by the year picker '{box_name}'")


def persona_card(page: Page) -> Locator | None:
    return visible(page.get_by_text(ANY_CARD_RX), 10)


def choose_entity(page: Page, persona: str, timeout_s: float) -> None:
    entity = PERSONAS[persona]["entity"]
    rx = re.compile(r"^\s*Organi[sz]ation\s*$" if entity == ORG else r"^\s*Individual\s*$", re.I)
    head = wait_visible(page, lambda: page.get_by_role("heading", name=rx), timeout_s)
    if head is None:
        raise FlowError(f"'{entity}' entity type not shown")
    card_rx = re.compile(PERSONAS[persona]["card"], re.I)
    for _ in range(3):
        if visible(page.get_by_text(card_rx), 10) is not None:
            return
        head.click()
        page.wait_for_timeout(1500)
    if visible(page.get_by_text(card_rx), 10) is None:
        shown = [t.strip() for t in page.locator("h1, h2, h3, h4, h5, h6, p, span").all_inner_texts() if t.strip()][:40]
        raise FlowError(f"'{persona}' card not shown after choosing '{entity}'. Texts on page: {shown}")


def choose_persona(page: Page, persona: str, timeout_s: float) -> None:
    card = wait_visible(page, lambda: page.get_by_text(re.compile(PERSONAS[persona]["card"], re.I)), timeout_s)
    if card is None:
        shown = [t.strip() for t in page.locator("h1, h2, h3, h4, h5, h6, p, span").all_inner_texts() if t.strip()][:40]
        raise FlowError(f"'{persona}' card not found. Texts on page: {shown}")
    card.scroll_into_view_if_needed()
    card.click(force=True)
    page.wait_for_timeout(800)


def fill_basic_form(page: Page, data: dict[str, str], email: str, timeout_s: float,
                    log: Callable[[str], None] = print) -> None:
    submit = re.compile(r"^\s*PREVIEW AND SUBMIT", re.I)
    if wait_visible(page, lambda: page.get_by_role("button", name=submit), timeout_s) is None:
        raise FlowError("basic form (PREVIEW AND SUBMIT) did not open")
    page.wait_for_timeout(1500)
    if textbox(page, "Select year of birth") is not None:
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
    if fill_textbox(page, "Please write the 6 digit pin", data["pin"], required=False):
        page.wait_for_timeout(1500)
    fill_textbox(page, "Please enter your PAN.", data["pan"], required=False)
    sliders = page.locator(".switch input[type=checkbox], label.switch input")
    if sliders.count() and not sliders.first.is_checked():
        sliders.first.locator("xpath=following-sibling::*[contains(@class,'slider')][1]").click(force=True)
    box = textbox(page, "Enter your Email ID for all")
    if box is not None and not box.input_value():
        box.fill(email)
    fill_remaining(page, data, log)


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
        if box is not None:
            box.fill(data["org_name"])
        else:
            log(f"Organisation box '{org_box}' not found; left to the remaining-fields pass")
    for label_rx, want in ((r"^Country Of Headquarter", "India"), (r"^Current Role", "CXO level role")):
        if page.evaluate(REACT_INPUT_JS, label_rx):
            pick(label_rx, want=want, name=label_rx.strip("^"))
    fill_remaining(page, data, log)
    return empty


EMPTY_FIELDS_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = e => {
    for (let p = e.parentElement, i = 0; p && i < 7; p = p.parentElement, i++) {
      const l = p.querySelector('label, h5, h6');
      if (l && l.innerText.trim()) return l.innerText.trim().split('\n')[0];
    }
    return '';
  };
  const out = [];
  const main = document.querySelector('footer');
  const inMain = e => !(main && main.contains(e));
  for (const e of document.querySelectorAll('input[id^="react-select-"][id$="-input"]')) {
    const c = e.closest('[class*="container"]');
    if (!c || !vis(c) || !inMain(e)) continue;
    if (c.querySelector('[class*="singleValue"], [class*="multiValue"], [class*="single-value"], [class*="multi-value"]')) continue;
    if (c.querySelector('input[disabled]') || e.disabled) continue;
    out.push({kind: 'select', id: e.id, label: label(c)});
  }
  for (const e of document.querySelectorAll('input[type=text], input[type=number], input[type=tel], input[type=email], input[type=date], input:not([type]), textarea')) {
    if (e.id.startsWith('react-select') || !vis(e) || !inMain(e) || e.value || e.disabled) continue;
    if (e.placeholder === 'Enter your email') continue;
    out.push({kind: 'text', name: e.name, placeholder: e.placeholder, label: label(e), type: e.type, ro: e.readOnly ? '1' : ''});
  }
  for (const e of document.querySelectorAll('select')) {
    if (!vis(e) || !inMain(e) || e.disabled || e.value) continue;
    out.push({kind: 'native', name: e.name, id: e.id, label: label(e)});
  }
  const groups = {};
  for (const e of document.querySelectorAll('input[type=radio]')) {
    if (!inMain(e) || e.disabled) continue;
    (groups[e.name] = groups[e.name] || []).push(e);
  }
  for (const [name, list] of Object.entries(groups)) {
    if (!list.some(x => x.checked) && list.some(x => vis(x) || vis(x.parentElement))) out.push({kind: 'radio', name, label: label(list[0])});
  }
  return out;
}"""


def fill_remaining(page: Page, data: dict[str, str], log: Callable[[str], None], passes: int = 3) -> None:
    done: set[str] = set()
    for _ in range(passes):
        fields: list[dict[str, str]] = page.evaluate(EMPTY_FIELDS_JS)
        fields = [f for f in fields if (f.get("id") or f.get("name") or f.get("placeholder", "")) not in done]
        if not fields:
            return
        for f in fields:
            key = f.get("id") or f.get("name") or f.get("placeholder", "")
            done.add(key)
            label = f.get("label", "")
            try:
                if f["kind"] == "radio":
                    radios = page.locator(f'input[type=radio][name="{f["name"]}"]')
                    choice = radios.last if re.search(r"agnostic", label, re.I) else radios.first
                    choice.check(force=True)
                    log(f"[extra] {label or f['name']}: radio option {choice.get_attribute('value')}")
                elif f["kind"] == "select":
                    multi = bool(re.search(r"area|expertise|sector|stage|mode|type", label, re.I))
                    year = None
                    if re.search(r"\byear\b|since", label, re.I):
                        year = "2015" if re.search(r"start|since", label, re.I) else "2024"
                    got = react_pick_id(page, f["id"], want=year, count=1 if year or not multi else 2)
                    log(f"[extra] {label or f['id']}: {', '.join(got) or 'no options'}")
                elif f["kind"] == "native":
                    sel = page.locator(f'select[name="{f["name"]}"]').first if f.get("name") else page.locator(f'#{f["id"]}')
                    options = [o for o in sel.locator("option").all_inner_texts() if o.strip()]
                    usable = [o for o in options if not re.search(r"^\s*(select|please|--|choose)", o, re.I)]
                    choice = next((o for o in usable if o.strip() in ("India", "Male")), usable[0] if usable else "")
                    if choice:
                        sel.select_option(label=choice)
                    log(f"[extra] {label or f.get('name')}: {choice or 'no options'}")
                else:
                    if f.get("name"):
                        box = page.locator(f'[name="{f["name"]}"]').first
                    elif f.get("placeholder"):
                        box = page.get_by_placeholder(f["placeholder"], exact=True).first
                    else:
                        continue
                    text = f"{label} {f.get('placeholder', '')}".upper()
                    if f.get("type") == "date" or re.search(r"\bDATE\b", text):
                        value = f"{data['founded']}-04-01" if f.get("type") == "date" else f"01/04/{data['founded']}"
                        box.fill(value)
                        page.keyboard.press("Escape")
                    elif re.search(r"\bYEAR\b|SINCE", text):
                        value = ("2015" if re.search(r"START|SINCE", text) else data["year_of_birth"] if "BIRTH" in text
                                 else data["founded"] if re.search(r"INCORPORAT|ESTABLISH|REGISTRATION|FOUND", text) else "2024")
                        box.click()
                        page.wait_for_timeout(400)
                        for _ in range(12):
                            cell = visible(page.get_by_text(value, exact=True))
                            if cell is not None:
                                cell.click()
                                break
                            prev = visible(page.get_by_role("button", name=re.compile(r"previous", re.I)))
                            if prev is None:
                                break
                            prev.click()
                            page.wait_for_timeout(250)
                        if not box.input_value() and not f.get("ro"):
                            box.fill(value)
                    elif f.get("ro"):
                        continue
                    else:
                        value = value_for(label, f.get("placeholder", ""), data)
                        box.fill(value)
                        box.press("Tab")
                    log(f"[extra] {label or f.get('name') or f.get('placeholder')}: {value}")
                page.wait_for_timeout(300)
            except Exception as e:
                log(f"[extra] {label or key}: could not fill ({type(e).__name__})")


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
