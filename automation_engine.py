"""
automation_engine.py
Playwright-based stealth browser automation engine for Microsoft account
login and alias expansion via account.live.com/names/manage.
"""

import asyncio
import random
import re
import string
import time
import threading
from typing import Callable, Optional
from playwright.async_api import async_playwright, Page, BrowserContext


# ─── Stealth helpers ──────────────────────────────────────────────────────────

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--start-maximized",
    "--disable-web-security",
    "--lang=en-US",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


async def _stealth_init(page: Page) -> None:
    """Inject stealth JS overrides to mask automation signals."""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'permissions', {
            get: () => ({
                query: async (p) => ({ state: p.name === 'notifications' ? 'denied' : 'granted' })
            })
        });
    """)


async def _human_type(page: Page, selector: str, text: str) -> None:
    """Type text with human-like random delays between keystrokes."""
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.6))
    for char in text:
        await page.type(selector, char, delay=random.randint(60, 180))
    await asyncio.sleep(random.uniform(0.2, 0.5))


async def _human_click(page: Page, selector: str) -> None:
    """Click with a small random offset and pre-click delay."""
    await asyncio.sleep(random.uniform(0.4, 0.9))
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.7))


# ─── Alias generators ─────────────────────────────────────────────────────────

FIRST_NAMES = [
    "james", "mary", "robert", "patricia", "john", "jennifer", "michael", "linda",
    "david", "elizabeth", "william", "barbara", "richard", "susan", "joseph", "jessica",
    "thomas", "sarah", "charles", "karen", "christopher", "lisa", "daniel", "nancy",
    "matthew", "betty", "anthony", "sandra", "mark", "ashley", "donald", "kimberly",
    "steven", "emily", "paul", "donna", "andrew", "michelle", "joshua", "carol",
    "kenneth", "amanda", "kevin", "dorothy", "brian", "melissa", "george", "deborah",
    "timothy", "stephanie", "ronald", "rebecca", "jason", "sharon", "edward", "laura",
    "jeffrey", "cynthia", "ryan", "kathleen", "jacob", "amy", "gary", "shirley",
    "nicholas", "angela", "eric", "helen", "jonathan", "anna", "stephen", "brenda",
    "larry", "pamela", "justin", "nicole", "scott", "emma", "brandon", "samantha",
    "benjamin", "katherine", "samuel", "christine", "gregory", "debra", "alexander",
    "rachel", "patrick", "carolyn", "frank", "janet", "raymond", "catherine", "jack",
    "maria", "dennis", "heather", "jerry", "diane", "tyler", "virginia", "aaron",
    "julie", "joyce", "adam", "victoria", "nathan", "olivia", "henry", "kelly",
    "zachary", "christina", "douglas", "lauren", "peter", "kyle", "evelyn", "ethan"
]

LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis",
    "rodriguez", "martinez", "hernandez", "lopez", "gonzalez", "wilson", "anderson",
    "thomas", "taylor", "moore", "jackson", "martin", "lee", "perez", "thompson",
    "white", "harris", "sanchez", "clark", "ramirez", "lewis", "robinson", "walker",
    "young", "allen", "king", "wright", "scott", "torres", "nguyen", "hill", "flores",
    "green", "nelson", "baker", "hall", "rivera", "campbell", "mitchell", "carter",
    "roberts", "gomez", "phillips", "evans", "turner", "diaz", "parker", "cruz",
    "edwards", "collins", "reyes", "stewart", "morris", "morales", "murphy", "cook",
    "rogers", "gutierrez", "ortiz", "morgan", "cooper", "peterson", "bailey", "reed"
]


def _random_alias(length: int = 10) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_name_alias() -> str:
    """Generate alias with full first name + full last name + 4 or 5 digits (e.g. johnsmith48291)."""
    fn = random.choice(FIRST_NAMES)
    ln = random.choice(LAST_NAMES)
    num_digits = random.choice([4, 5])
    if num_digits == 4:
        num = random.randint(1000, 9999)
    else:
        num = random.randint(10000, 99999)

    return f"{fn}{ln}{num}"


WORD_PREFIXES = [
    "swift", "nova", "edge", "bright", "cloud", "prime", "echo",
    "forge", "lunar", "nexus", "orbit", "pulse", "quest", "spark",
]


def _word_alias() -> str:
    word = random.choice(WORD_PREFIXES)
    suffix = ''.join(random.choices(string.digits, k=random.randint(4, 5)))
    return f"{word}{suffix}"


def generate_alias(strategy: str = "random", index: int = 0, prefix: str = "alias") -> str:
    """Generate an alias using the Random Name + 4-5 digits generator."""
    return _random_name_alias()


# ─── Core automation ──────────────────────────────────────────────────────────

class AccountAutomation:
    """Handles one Microsoft account: login + alias expansion."""

    def __init__(
        self,
        email: str,
        password: str,
        target_url: str,
        aliases_count: int,
        alias_strategy: str,
        alias_prefix: str,
        headless: bool,
        min_delay: float,
        max_delay: float,
        emit: Callable[[str, str, str], None],  # (account_id, level, message)
        stop_event: Optional[threading.Event] = None,
    ):
        self.email = email
        self.password = password
        self.target_url = target_url
        self.aliases_count = aliases_count
        self.alias_strategy = alias_strategy
        self.alias_prefix = alias_prefix
        # On Linux servers without an X11 display, force headless=True to avoid headful launch errors
        import sys, os
        if sys.platform.startswith("linux") and "DISPLAY" not in os.environ:
            self.headless = True
        else:
            self.headless = headless
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.emit = emit
        self.stop_event: Optional[threading.Event] = stop_event
        self.created_aliases: list[str] = []
        self.existing_aliases: list[str] = []

    def log(self, level: str, msg: str) -> None:
        self.emit(self.email, level, msg)

    def check_stopped(self) -> None:
        if self.stop_event and self.stop_event.is_set():
            raise Exception("Job stopped by user.")

    async def sleep_check(self, seconds: float) -> None:
        end_time = time.time() + seconds
        while time.time() < end_time:
            self.check_stopped()
            await asyncio.sleep(min(0.3, max(0.05, end_time - time.time())))
        self.check_stopped()

    async def run(self) -> dict:
        """Execute the full login + alias creation flow. Returns result dict."""
        self.log("INFO", f"Starting automation for {self.email}")
        result = {
            "email": self.email,
            "password": self.password,
            "status": "failed",
            "created_aliases": [],
            "error": None,
        }

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    headless=self.headless,
                    args=STEALTH_ARGS,
                )
            except Exception as launch_err:
                if not self.headless:
                    self.log("WARN", f"Headful browser launch failed on server: {launch_err}. Falling back to headless mode.")
                    browser = await pw.chromium.launch(
                        headless=True,
                        args=STEALTH_ARGS,
                    )
                else:
                    raise launch_err
            ua = random.choice(USER_AGENTS)
            context: BrowserContext = await browser.new_context(
                user_agent=ua,
                viewport={"width": 1366, "height": 768},
                locale="en-US",
            )
            page = await context.new_page()
            await _stealth_init(page)

            try:
                # ── Step 1: Navigate to Microsoft login ──────────────────
                self.log("INFO", "Navigating to Microsoft login page…")
                await page.goto(
                    "https://login.live.com/login.srf",
                    wait_until="domcontentloaded",  # networkidle never fires on MS login (persistent bg connections)
                    timeout=45000,
                )
                await asyncio.sleep(random.uniform(1.5, 2.5))

                # ── Step 2: Enter email ──────────────────────────────────
                self.log("INFO", "Waiting for email input field…")
                EMAIL_SELECTORS = [
                    'input[name="loginfmt"]',
                    'input[type="email"]',
                    '#i0116',
                ]
                email_field = None
                for sel in EMAIL_SELECTORS:
                    try:
                        email_field = await page.wait_for_selector(sel, timeout=15000, state="visible")
                        if email_field:
                            break
                    except Exception:
                        continue

                if not email_field:
                    raise Exception("Could not locate email input field on the login page.")

                self.log("INFO", "Entering email address…")
                await email_field.click()
                await asyncio.sleep(random.uniform(0.3, 0.6))
                for char in self.email:
                    await email_field.type(char, delay=random.randint(60, 160))
                await asyncio.sleep(random.uniform(0.4, 0.8))

                # Click Next / Submit after email
                NEXT_SELECTORS = [
                    '#idSIButton9',
                    'input[type="submit"]',
                    'button[type="submit"]',
                    'button:has-text("Next")',
                    'input[value="Next"]',
                ]
                clicked_next = False
                for sel in NEXT_SELECTORS:
                    try:
                        await page.click(sel, timeout=5000)
                        clicked_next = True
                        break
                    except Exception:
                        continue
                if not clicked_next:
                    self.log("WARN", "Next button after email not found — trying Enter key.")
                    await email_field.press("Enter")

                self.log("INFO", "Email submitted — detecting next page state…")
                await asyncio.sleep(random.uniform(1.5, 2.5))

                # ── State detection: what did Microsoft show after email? ──────
                # Microsoft's new passwordless-first flow hides the password behind
                # a "Use your password" link — we detect and click it automatically.
                PASSWORD_SEL = 'input[name="passwd"], #i0118, input[type="password"]'

                # "Use your password" / "Sign in another way" — passwordless bypass
                USE_PASSWORD_SEL = (
                    'span[role="button"]:has-text("Use your password"), '
                    'a:has-text("Use your password"), '
                    'button:has-text("Use your password"), '
                    'span:has-text("Use your password"), '
                    'a:has-text("Sign in another way"), '
                    'button:has-text("Sign in another way"), '
                    '[id*="UseAnotherAccount"], '
                    '[id*="usePassword"]'
                )

                CHALLENGE_SEL = (
                    # Phone/email verification, "Get a code", 2FA prompt
                    '#idDiv_SAOTCS_Proofs, '
                    '[data-testid*="proof"], '
                    'a[id*="iProof"], '
                    '#idTxtBx_OTC_Password1, '
                    'input[name="otc"], '
                    '#idRichContext_DisplaySign, '
                    'div:has-text("Verify your identity"), '
                    'div:has-text("Help us protect your account")'
                )
                ERROR_SEL = (
                    '#usernameError, '
                    "[id*='Error'], "
                    ".alert-error, "
                    "div[role='alert']"
                )

                detected_state = None
                for attempt in range(30):  # poll every 1s up to 30s
                    try:
                        # 1. Check for "Use your password" button (passwordless bypass)
                        up = await page.query_selector(USE_PASSWORD_SEL)
                        if up and await up.is_visible():
                            detected_state = "use_password_btn"
                            break
                    except Exception:
                        pass
                    try:
                        # 2. Check password field
                        pf = await page.query_selector(PASSWORD_SEL)
                        if pf and await pf.is_visible():
                            detected_state = "password"
                            break
                    except Exception:
                        pass
                    try:
                        # 3. Check verification / code challenge
                        cf = await page.query_selector(CHALLENGE_SEL)
                        if cf and await cf.is_visible():
                            detected_state = "challenge"
                            break
                    except Exception:
                        pass
                    try:
                        # 4. Check error message
                        ef = await page.query_selector(ERROR_SEL)
                        if ef and await ef.is_visible():
                            detected_state = "error"
                            break
                    except Exception:
                        pass

                    await asyncio.sleep(1)

                if detected_state == "use_password_btn":
                    self.log("INFO", "Detected Microsoft passwordless screen — clicking 'Use your password'…")
                    try:
                        # Click whichever "Use your password" element was found
                        for sel in USE_PASSWORD_SEL.split(", "):
                            sel = sel.strip().rstrip(",")
                            try:
                                el = await page.query_selector(sel)
                                if el and await el.is_visible():
                                    await el.click()
                                    break
                            except Exception:
                                continue
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                        # Now wait for the password field to appear
                        for _ in range(10):
                            pf = await page.query_selector(PASSWORD_SEL)
                            if pf and await pf.is_visible():
                                detected_state = "password"
                                break
                            await asyncio.sleep(1)
                        if detected_state != "password":
                            raise Exception(
                                "Clicked 'Use your password' but password field did not appear. "
                                "Enable Headful mode to inspect."
                            )
                    except Exception as e:
                        raise Exception(f"Failed to click 'Use your password': {e}")

                if detected_state == "password":
                    self.log("INFO", "Password field detected. Proceeding…")

                elif detected_state == "challenge":
                    self.log("WARN", "Microsoft is showing a verification challenge (phone/email code, 2FA).")
                    if not self.headless:
                        self.log("WARN", "Headful mode active — waiting up to 3 minutes for manual challenge completion…")
                        for _ in range(36):  # 36 × 5s = 3 min
                            await asyncio.sleep(5)
                            pf = await page.query_selector(PASSWORD_SEL)
                            if pf and await pf.is_visible():
                                detected_state = "password"
                                break
                            # Also check if we've passed the login page entirely
                            if "login.live.com" not in page.url and "login.microsoftonline.com" not in page.url:
                                detected_state = "logged_in"
                                break
                        if detected_state not in ("password", "logged_in"):
                            raise Exception(
                                "Verification challenge not completed within 3 minutes. "
                                "Re-run the account in headful mode and complete the challenge manually."
                            )
                    else:
                        raise Exception(
                            "Microsoft requires identity verification (phone/email code) for this account. "
                            "Enable 'Headful / Visible Browser' mode and complete the challenge manually."
                        )

                elif detected_state == "error":
                    page_text = await page.inner_text("body")
                    # Extract the visible error snippet
                    err_snippet = page_text[:300].replace("\n", " ").strip()
                    raise Exception(f"Microsoft showed a login error after email submit: {err_snippet}")

                else:
                    # Nothing recognised after 30s — check if we somehow passed login already
                    if "login.live.com" not in page.url and "login.microsoftonline.com" not in page.url:
                        detected_state = "logged_in"
                        self.log("SUCCESS", f"Outlook already logged in for {self.email}! URL: {page.url}")
                    else:
                        # Dump page content for debugging
                        page_title = await page.title()
                        raise Exception(
                            f"Unrecognised page state after email submit. "
                            f"Page title: '{page_title}'. URL: {page.url}. "
                            "Enable Headful mode to inspect what Microsoft is showing."
                        )

                # ── Step 3: Enter password (if we're at that step) ───────────
                if detected_state in ("password",):
                    self.log("INFO", "Entering password…")
                    PASSWD_SELECTORS = [
                        'input[name="passwd"]',
                        '#i0118',
                        'input[type="password"]',
                    ]
                    passwd_field = None
                    for sel in PASSWD_SELECTORS:
                        try:
                            passwd_field = await page.wait_for_selector(sel, timeout=10000, state="visible")
                            if passwd_field:
                                break
                        except Exception:
                            continue

                    if not passwd_field:
                        raise Exception("Password field vanished before we could type. Enable Headful mode.")

                    await passwd_field.click()
                    await asyncio.sleep(random.uniform(0.3, 0.6))
                    for char in self.password:
                        await passwd_field.type(char, delay=random.randint(60, 160))
                    await asyncio.sleep(random.uniform(0.4, 0.8))

                    # Click Sign In
                    for sel in NEXT_SELECTORS:
                        try:
                            await page.click(sel, timeout=5000)
                            break
                        except Exception:
                            continue

                    self.log("INFO", "Password submitted — waiting for post-login redirect…")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(2.0, 3.5))



                # ── Step 4: Handle "Stay signed in?" prompt ──────────────
                try:
                    stay_btn = await page.wait_for_selector(
                        'input[id="idBtn_Back"], #idBtn_Back, button:has-text("No")',
                        timeout=6000,
                    )
                    if stay_btn:
                        self.log("INFO", 'Dismissing "Stay signed in?" prompt…')
                        await _human_click(page, 'input[id="idBtn_Back"], #idBtn_Back, button:has-text("No")')
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                except Exception:
                    pass  # prompt not shown

                # ── Step 5: Verify login success ─────────────────────────
                current_url = page.url
                if current_url.startswith("chrome-error://"):
                    self.log("WARN", "Transient network glitch detected after login submit — attempting direct navigation to target URL...")
                    await asyncio.sleep(2.0)
                elif "login.live.com" in current_url or "account.live.com/resproof" in current_url:
                    # Check if we're on a security checkpoint
                    self.log("WARN", "Security checkpoint detected — manual intervention may be needed.")
                    # Wait up to 120s for user to handle 2FA/CAPTCHA in headful mode
                    if not self.headless:
                        self.log("WARN", "Waiting up to 120s for manual challenge resolution…")
                        for _ in range(24):
                            await asyncio.sleep(5)
                            if "login.live.com" not in page.url:
                                break
                    if "login.live.com" in page.url:
                        raise Exception("Login failed — still on login page after timeout.")

                # Explicitly log login success to console
                self.log("SUCCESS", f"Login successful for {self.email}!")

                # ── Step 6: Navigate to alias management ─────────────────
                self.log("INFO", f"Navigating to alias management page…")
                # Append mkt=en-US if not already in the URL for consistent English UI
                target = self.target_url
                if "mkt=" not in target:
                    sep = "&" if "?" in target else "?"
                    target = target + sep + "mkt=en-US"
                
                try:
                    await page.goto(target, wait_until="domcontentloaded", timeout=35000)
                except Exception as nav_err:
                    self.log("WARN", f"First navigation attempt encountered: {nav_err}. Retrying...")
                    await asyncio.sleep(3)
                    await page.goto(target, wait_until="domcontentloaded", timeout=35000)

                await asyncio.sleep(random.uniform(2.5, 4.0))

                if "login.live.com" in page.url:
                    raise Exception("Redirected back to login page. Password or account verification required.")

                self.log("SUCCESS", f"Reached alias management page: {page.url}")

                # ── Scan for existing aliases on account ─────────────────
                try:
                    body_text = await page.inner_text("body")
                    found_emails = re.findall(r'[a-zA-Z0-9._%+-]+@(?:outlook|hotmail|live|msn)\.[a-zA-Z]{2,}', body_text, re.IGNORECASE)
                    unique_found = list(dict.fromkeys([e.lower() for e in found_emails if e.lower() != self.email.lower()]))
                    if unique_found:
                        self.existing_aliases = unique_found
                        self.log("INFO", f"Existing alias(es) detected on account ({len(unique_found)}): {', '.join(unique_found)}")
                    else:
                        self.log("INFO", "No pre-existing aliases found on this account.")
                except Exception:
                    pass

                # ── Step 7: Create aliases ───────────────────────────────────
                for i in range(self.aliases_count):
                    self.check_stopped()
                    alias = generate_alias(self.alias_strategy, i, self.alias_prefix)
                    self.log("INFO", f"[{i+1}/{self.aliases_count}] Attempting alias: {alias}")

                    try:
                        # ── 7a. Click the "Add email" / "Add alias" link ──────
                        ADD_BTN = [
                            '#idAddAliasLink',
                            'a[href*="AddAssocId"]',         # real MS page anchor
                            'a:has-text("Add email")',
                            'button:has-text("Add email")',
                            'a:has-text("Add alias")',
                            'button:has-text("Add alias")',
                            '[id*="AddAssocId"]',
                            'a[id*="add"]',
                        ]
                        clicked_add = False
                        for sel in ADD_BTN:
                            try:
                                el = await page.wait_for_selector(sel, timeout=8000, state="visible")
                                if el:
                                    await el.click()
                                    clicked_add = True
                                    break
                            except Exception:
                                continue

                        if not clicked_add:
                            self.log("WARN", f"'Add email' button not found for alias {i+1}. URL: {page.url}")
                            await page.reload(wait_until="domcontentloaded", timeout=20000)
                            await asyncio.sleep(3)
                            continue

                        await asyncio.sleep(random.uniform(1.2, 2.0))

                        # ── 7b. Select "Create a new email address" radio ─────
                        CREATE_RADIO = [
                            '#iOptNewAddr',
                            'input[id*="NewAddr"]',
                            'input[value="1"]',
                            'input[type="radio"]:first-of-type',
                            'label:has-text("Create a new")',
                        ]
                        for sel in CREATE_RADIO:
                            try:
                                el = await page.wait_for_selector(sel, timeout=5000, state="visible")
                                if el:
                                    await el.click()
                                    break
                            except Exception:
                                continue
                        await asyncio.sleep(random.uniform(0.5, 1.0))

                        # ── 7c. Type the alias name ───────────────────────────
                        ALIAS_INPUT = [
                            '#AssociatedIdLive',
                            'input[name="AssociatedIdLive"]',
                            '#iNewAddr',
                            'input[id*="NewAddr"]',
                            'input[name="AddedAlias"]',
                            'input[name*="alias" i]',
                            'input[placeholder*="alias" i]',
                            'input[placeholder*="email" i]',
                        ]
                        alias_el = None
                        for sel in ALIAS_INPUT:
                            try:
                                alias_el = await page.wait_for_selector(sel, timeout=8000, state="visible")
                                if alias_el:
                                    break
                            except Exception:
                                continue

                        if not alias_el:
                            self.log("ERROR", f"Alias input field not found for alias {i+1}.")
                            continue

                        await alias_el.click()
                        await alias_el.fill("")  # clear pre-filled text safely
                        await asyncio.sleep(0.2)
                        for char in alias:
                            await alias_el.type(char, delay=random.randint(70, 160))
                        await asyncio.sleep(random.uniform(0.5, 1.0))

                        # ── 7d. Select domain (outlook.com) ──────────────────
                        try:
                            domain_sel = await page.wait_for_selector(
                                '#iDomainSelect, select[name*="Domain" i], select[id*="domain" i]',
                                timeout=3000, state="visible",
                            )
                            if domain_sel:
                                await domain_sel.select_option(label="outlook.com")
                        except Exception:
                            pass  # no domain dropdown on this account — OK
                        await asyncio.sleep(random.uniform(0.3, 0.7))

                        # ── 7e. Submit ─────────────────────────────────────────
                        SUBMIT_BTN = [
                            '#iSaveButton',
                            'input[type="submit"]',
                            'button[type="submit"]',
                            'button:has-text("Add alias")',
                            'button:has-text("Save")',
                            'input[value*="Save" i]',
                            'input[value*="Add" i]',
                        ]
                        for sel in SUBMIT_BTN:
                            try:
                                el = await page.wait_for_selector(sel, timeout=5000, state="visible")
                                if el:
                                    await el.click()
                                    break
                            except Exception:
                                continue

                        # ── 7f. Wait & handle possible password re-prompt ─────
                        await asyncio.sleep(random.uniform(2.5, 3.5))

                        try:
                            pwd_re = await page.query_selector('input[name="passwd"], #i0118, input[type="password"]')
                            if pwd_re and await pwd_re.is_visible():
                                self.log("INFO", "Microsoft requested password re-confirmation — entering password…")
                                await pwd_re.fill(self.password)
                                for s_sel in SUBMIT_BTN:
                                    try:
                                        s_btn = await page.query_selector(s_sel)
                                        if s_btn and await s_btn.is_visible():
                                            await s_btn.click()
                                            break
                                    except Exception:
                                        continue
                                await asyncio.sleep(3.0)
                        except Exception:
                            pass

                        # ── 7g. Verify alias was created ──────────────────────
                        page_text = await page.inner_text("body")
                        full_alias = f"{alias}@outlook.com"
                        
                        if alias.lower() in page_text.lower():
                            self.created_aliases.append(full_alias)
                            self.log("SUCCESS", f"Alias created successfully: {full_alias}")
                        elif any(kw in page_text.lower() for kw in ["limit", "maximum", "too many"]):
                            self.log("WARN", "Account reached Microsoft's maximum alias limit (10 aliases max).")
                        elif any(kw in page_text.lower() for kw in ["already in use", "already exists", "not available"]):
                            self.log("WARN", f"Alias '{alias}' is already taken by another user.")
                        else:
                            # Re-check page by reloading target URL
                            try:
                                await page.goto(target, wait_until="domcontentloaded", timeout=20000)
                                await asyncio.sleep(2.0)
                                page_text_fresh = await page.inner_text("body")
                                if alias.lower() in page_text_fresh.lower():
                                    self.created_aliases.append(full_alias)
                                    self.log("SUCCESS", f"Alias created successfully: {full_alias}")
                                else:
                                    self.log("WARN", f"Alias '{alias}' submission completed.")
                            except Exception:
                                self.log("WARN", f"Alias '{alias}' processed.")

                    except Exception as alias_err:
                        self.log("ERROR", f"Error on alias {i+1} ({alias}): {alias_err}")

                    # Human-like delay + reload between aliases to reset page state
                    delay = random.uniform(self.min_delay, self.max_delay)
                    self.log("INFO", f"Waiting {delay:.1f}s before next alias…")
                    await self.sleep_check(delay)
                    if i + 1 < self.aliases_count:
                        await page.goto(target, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(random.uniform(2.0, 3.0))


                # ONLY include newly created aliases in created_aliases (do NOT include pre-existing aliases)
                result["status"] = "success" if self.created_aliases else "failed"
                result["created_aliases"] = self.created_aliases
                self.log("SUCCESS", f"Completed. {len(self.created_aliases)} new alias(es) created.")

            except Exception as err:
                result["error"] = str(err)
                self.log("ERROR", f"Fatal error: {err}")

            finally:
                await context.close()
                await browser.close()

        return result


# ─── Batch job manager ────────────────────────────────────────────────────────

class BatchJob:
    """Manages a batch of AccountAutomation runs across multiple accounts."""

    def __init__(self, accounts: list[dict], config: dict, emit_fn: Callable):
        self.accounts = accounts  # list of {email, password}
        self.config = config
        self.emit_fn = emit_fn
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.results: list[dict] = []
        self.total = len(accounts)
        self.completed = 0
        self.successful = 0
        self.failed = 0
        self.running = False

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run_sync, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.running = False

    def _run_sync(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as err:
            self.running = False
            self.emit_fn("SYSTEM", "ERROR", f"Fatal job exception: {err}")

    async def _run_async(self) -> None:
        self.emit_fn("SYSTEM", "INFO", f"Batch started — {self.total} accounts queued.")
        for account in self.accounts:
            if self._stop_event.is_set():
                self.emit_fn("SYSTEM", "WARN", "Batch job stopped by user.")
                break

            automation = AccountAutomation(
                email=account["email"],
                password=account["password"],
                target_url=self.config.get("target_url", "https://account.live.com/names/manage"),
                aliases_count=int(self.config.get("aliases_per_account", 3)),
                alias_strategy=self.config.get("alias_strategy", "random"),
                alias_prefix=self.config.get("alias_prefix", "alias"),
                headless=self.config.get("headless", True),
                min_delay=float(self.config.get("min_delay", 2.0)),
                max_delay=float(self.config.get("max_delay", 5.0)),
                emit=self.emit_fn,
                stop_event=self._stop_event,
            )

            result = await automation.run()
            self.results.append(result)
            self.completed += 1

            if result["status"] == "success":
                self.successful += 1
            else:
                self.failed += 1

            self.emit_fn(
                "SYSTEM", "INFO",
                f"Progress: {self.completed}/{self.total} accounts processed. "
                f"{self.successful} success | {self.failed} failed."
            )

        self.running = False
        self.emit_fn("SYSTEM", "SUCCESS", "Batch job complete.")

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "total": self.total,
            "completed": self.completed,
            "successful": self.successful,
            "failed": self.failed,
            "results": self.results,
        }
