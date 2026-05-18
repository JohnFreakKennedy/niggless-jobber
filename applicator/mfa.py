"""MFA resolution: TOTP and email OTP."""
from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

_TOTP_KEYWORDS = ("authenticator", "6-digit", "verification code", "one-time", "totp", "2fa")
_EMAIL_OTP_KEYWORDS = ("sent you a code", "check your email", "email verification", "verify your email", "email code")
_OTP_PATTERN = re.compile(r"\b([0-9]{4,8})\b")
_IMAP_POLL_INTERVAL = 5
_IMAP_TIMEOUT = 60


class MFAType(str):
    TOTP = "totp"
    EMAIL_OTP = "email_otp"
    UNKNOWN = "unknown"


async def detect_mfa(page: "Page") -> str | None:
    content = (await page.content()).lower()
    if any(k in content for k in _TOTP_KEYWORDS):
        return MFAType.TOTP
    if any(k in content for k in _EMAIL_OTP_KEYWORDS):
        return MFAType.EMAIL_OTP
    return None


async def resolve_mfa(page: "Page", hostname: str) -> bool:
    """
    Detect and resolve MFA on the current page.
    Returns True if resolved, False if not.
    """
    mfa_type = await detect_mfa(page)
    if mfa_type is None:
        return True

    log.info("MFA detected on %s: type=%s", hostname, mfa_type)

    if mfa_type == MFAType.TOTP:
        return await _resolve_totp(page, hostname)
    if mfa_type == MFAType.EMAIL_OTP:
        return await _resolve_email_otp(page)
    return await _notify_and_wait(page)


async def _resolve_totp(page: "Page", hostname: str) -> bool:
    from storage.vault import get_totp_seed
    seed = get_totp_seed(hostname)
    if not seed:
        log.warning("No TOTP seed for %s; trying manual wait", hostname)
        return await _notify_and_wait(page)

    import pyotp
    token = pyotp.TOTP(seed).now()
    log.info("Generated TOTP for %s: %s", hostname, token)

    selectors = [
        "input[type='text'][maxlength='6']",
        "input[placeholder*='code']",
        "input[name*='otp']",
        "input[name*='token']",
        "input[autocomplete='one-time-code']",
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            await el.fill(token)
            await asyncio.sleep(0.5)
            await _submit(page)
            await page.wait_for_load_state("networkidle", timeout=10_000)
            return True

    log.warning("Could not find TOTP input field")
    return False


async def _resolve_email_otp(page: "Page") -> bool:
    from storage.vault import get_secret
    server = get_secret("IMAP_SERVER")
    port = int(get_secret("IMAP_PORT") or "993")
    email_addr = get_secret("IMAP_EMAIL")
    password = get_secret("IMAP_PASSWORD")

    if not all([server, email_addr, password]):
        log.warning("IMAP credentials not configured; trying manual wait")
        return await _notify_and_wait(page)

    code = await _poll_imap(server, port, email_addr, password)
    if not code:
        log.warning("Email OTP not received within timeout")
        return False

    selectors = [
        "input[type='text'][maxlength]",
        "input[name*='code']",
        "input[name*='otp']",
        "input[autocomplete='one-time-code']",
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            await el.fill(code)
            await asyncio.sleep(0.5)
            await _submit(page)
            return True
    return False


async def _poll_imap(
    server: str,
    port: int,
    email_addr: str,
    password: str,
) -> str | None:
    deadline = asyncio.get_event_loop().time() + _IMAP_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        try:
            code = await asyncio.to_thread(_imap_fetch_code, server, port, email_addr, password)
            if code:
                return code
        except Exception as exc:
            log.debug("IMAP poll error: %s", exc)
        await asyncio.sleep(_IMAP_POLL_INTERVAL)
    return None


def _imap_fetch_code(server: str, port: int, email_addr: str, password: str) -> str | None:
    import datetime
    since = (datetime.date.today()).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(server, port) as imap:
        imap.login(email_addr, password)
        imap.select("INBOX")
        _, data = imap.search(None, f'(UNSEEN SINCE "{since}")')
        for num in (data[0] or b"").split()[-5:]:
            _, msg_data = imap.fetch(num, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")
            subject = msg.get("Subject", "")
            m = _OTP_PATTERN.search(subject + " " + body)
            if m:
                return m.group(1)
    return None


async def _submit(page: "Page") -> None:
    for sel in ("button[type='submit']", "input[type='submit']", "button:has-text('Verify')", "button:has-text('Continue')"):
        btn = await page.query_selector(sel)
        if btn:
            await btn.click()
            return


async def _notify_and_wait(page: "Page", timeout: int = 300) -> bool:
    """Send desktop notification and wait for manual resolution."""
    try:
        import subprocess
        subprocess.run([
            "osascript", "-e",
            'display notification "MFA required - please resolve in the browser" with title "niggless-jobber"'
        ], check=False)
    except Exception:
        pass

    log.warning("Waiting %ds for manual MFA resolution...", timeout)
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        content = (await page.content()).lower()
        if not any(k in content for k in _TOTP_KEYWORDS + _EMAIL_OTP_KEYWORDS):
            log.info("MFA appears to have been resolved manually")
            return True
    return False
