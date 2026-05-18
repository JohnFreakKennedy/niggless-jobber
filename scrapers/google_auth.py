"""
Google account session manager.

Logs into Google once via Playwright and persists the browser profile to
~/.niggless-jobber/google_browser_profile/.  Any subsequent Playwright
context launched with that profile directory will have an active Google
session, making "Sign in with Google" / "Continue with Google" buttons
work automatically on supported sites (Indeed, Djinni, JustJoinIT, etc.).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NIGGLESS_DATA_DIR", "") or Path.home() / ".niggless-jobber")
GOOGLE_PROFILE_DIR = _DATA_DIR / "google_browser_profile"

# Selectors Google uses across its login flow
_EMAIL_SEL = "input[type='email']"
_NEXT_SEL = "button:has-text('Next'), #identifierNext"
_PASSWORD_SEL = "input[type='password']"
_PW_NEXT_SEL = "button:has-text('Next'), #passwordNext"
_VERIFY_KEYWORDS = ("verify", "confirm", "2-step", "two-step", "authenticator", "phone")


def profile_exists() -> bool:
    return (GOOGLE_PROFILE_DIR / "Default").exists()


async def login(headless: bool = False) -> None:
    """
    Interactive Google login.  Launches a visible browser (headless=False by default)
    so the user can complete any 2FA challenge manually, then saves the profile.
    Call via:  python run.py --auth-google-account
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright not installed")

    GOOGLE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    from storage.vault import get_secret
    email = get_secret("GOOGLE_EMAIL")
    password = get_secret("GOOGLE_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "GOOGLE_EMAIL and GOOGLE_PASSWORD not in vault.\n"
            "Run:\n"
            "  python run.py --vault-set GOOGLE_EMAIL you@gmail.com\n"
            "  python run.py --vault-set GOOGLE_PASSWORD yourpassword"
        )

    print(f"Opening browser to log in to Google as {email} ...")
    print("If a verification screen appears, complete it manually in the browser window.")
    print("The window will close automatically once login is confirmed.")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(GOOGLE_PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Check if already logged in
        await page.goto("https://myaccount.google.com/", wait_until="domcontentloaded")
        if "myaccount.google.com" in page.url and await page.query_selector("img[data-gb-avatar]"):
            print("Already logged in to Google. Profile saved.")
            await browser.close()
            return

        # Start login
        await page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded")
        await asyncio.sleep(1)

        await page.fill(_EMAIL_SEL, email)
        await asyncio.sleep(random.uniform(0.4, 0.9))
        await page.click(_NEXT_SEL)
        await page.wait_for_load_state("networkidle", timeout=15_000)
        await asyncio.sleep(1)

        await page.fill(_PASSWORD_SEL, password)
        await asyncio.sleep(random.uniform(0.4, 0.9))
        await page.click(_PW_NEXT_SEL)
        await page.wait_for_load_state("networkidle", timeout=15_000)
        await asyncio.sleep(2)

        # Check for 2FA / verification prompt
        content = (await page.content()).lower()
        if any(k in content for k in _VERIFY_KEYWORDS):
            print("\n2-Step Verification required.")
            print("Complete the verification in the browser window, then press ENTER here.")
            try:
                input("Press ENTER after completing verification... ")
            except EOFError:
                pass
            await page.wait_for_url("**/myaccount.google.com/**", timeout=120_000)

        # Confirm we ended up logged in
        await page.goto("https://myaccount.google.com/", wait_until="domcontentloaded")
        if "myaccount.google.com" not in page.url:
            raise RuntimeError("Google login did not succeed. Please try again.")

        await browser.close()

    print(f"Google session saved to {GOOGLE_PROFILE_DIR}")
    print("Job sites that support 'Sign in with Google' will now log in automatically.")


async def make_context(pw, headless: bool = True, **kwargs):
    """
    Return a Playwright BrowserContext that uses the saved Google profile if
    available, otherwise falls back to a fresh context.
    """
    if profile_exists():
        log.debug("Using saved Google browser profile from %s", GOOGLE_PROFILE_DIR)
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(GOOGLE_PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            **kwargs,
        )
        return ctx, None   # persistent context has no separate browser object

    # No saved profile - launch a regular browser
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(**kwargs)
    return ctx, browser


async def click_google_signin(page, timeout: int = 5_000) -> bool:
    """
    If the current page has a 'Sign in with Google' / 'Continue with Google' button,
    click it and handle the OAuth popup.  Returns True if the button was found.
    """
    selectors = [
        "a:has-text('Continue with Google')",
        "button:has-text('Continue with Google')",
        "a:has-text('Sign in with Google')",
        "button:has-text('Sign in with Google')",
        "[data-provider='google']",
        "a[href*='accounts.google.com']",
        ".google-login-btn",
        "#google-login-btn",
    ]
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            if el:
                log.info("Clicking 'Sign in with Google' button")
                async with page.expect_popup() as popup_info:
                    await el.click()
                popup = await popup_info.value
                await popup.wait_for_load_state("networkidle", timeout=30_000)
                # If Google asks to confirm the account, click the email address
                account_btn = await popup.query_selector(f"[data-email], [data-identifier]")
                if account_btn:
                    await account_btn.click()
                    await popup.wait_for_load_state("networkidle", timeout=15_000)
                await page.wait_for_load_state("networkidle", timeout=20_000)
                return True
        except Exception:
            continue
    return False
