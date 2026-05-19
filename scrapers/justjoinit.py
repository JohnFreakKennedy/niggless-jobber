"""
JustJoinIT scraper -- Playwright-based.

JustJoinIT removed their public REST API.  The site is a Next.js SPA that
renders job cards as  <li data-index="N">  elements via client-side hydration.
We load the search page, scroll to the bottom to trigger virtual-list renders,
then harvest all  li[data-index]  nodes.

URL params that work:
  keyword=<term>              -- keyword search
  city=<City>                 -- city filter  (exact, Polish-ASCII or English)
  workingType=remote          -- remote-only
  tab=list                    -- list view (not map)
  sortBy=publishedAt          -- newest first
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse

from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

_BASE_URL = "https://justjoin.it/job-offers"
_SCROLL_PAUSE = 2.0          # seconds between scroll steps
_MAX_SCROLL_STEPS = 8        # cap to avoid endless scrolling
_PAGE_LOAD_TIMEOUT = 30_000  # ms


def _build_url(keyword: str, location: str) -> str:
    params: dict[str, str] = {
        "keyword": keyword,
        "tab": "list",
        "sortBy": "publishedAt",
    }
    loc_lower = location.lower().strip()
    if loc_lower in ("remote", ""):
        params["workingType"] = "remote"
    else:
        params["city"] = location
    return _BASE_URL + "?" + urllib.parse.urlencode(params)


def _parse_card_text(text: str) -> dict:
    """
    Parse the inner-text of a JustJoinIT li[data-index] card.

    JustJoinIT separates card sections with blank lines:

      [badges]
      Title
      [Salary]

      Company name          <- absent for anonymous postings

      City[, +N more]
      [Remote]
      Xd left
      skill1
      skill2 ...

    The blank-line boundaries are the reliable way to distinguish the
    company section from the location section; a line-by-line approach
    misidentifies city names as the company for anonymous jobs.
    """
    _BADGES = {"super offer", "1-click apply", "new"}
    _SALARY_PAT = re.compile(r"\d[\d\s]*[-\u2013]\s*\d|undisclosed salary", re.I)
    _TIME_PAT = re.compile(r"\d+[dh]\s+left|new", re.I)

    # Split on blank lines to get sections; preserve section order.
    sections = [s.strip() for s in re.split(r"\n[ \t]*\n", text) if s.strip()]

    # ---- Section 0: badges + title + optional salary --------------------
    sec0_lines = [l.strip() for l in sections[0].splitlines() if l.strip()] if sections else []
    while sec0_lines and sec0_lines[0].lower() in _BADGES:
        sec0_lines.pop(0)

    title = sec0_lines[0] if sec0_lines else ""
    salary = next((l for l in sec0_lines[1:] if _SALARY_PAT.search(l)), "")

    # ---- Company: present only when there are 3 or more sections --------
    # With 2 sections: [title+salary | location+skills]  (anonymous company)
    # With 3 sections: [title+salary | company | location+skills]
    # With 4+ sections: extra badge/promo sections may appear before company.
    company = ""
    loc_section_idx = 1  # default: location block is section 1 (anonymous)

    if len(sections) >= 3:
        # Middle section(s) before the location block contain the company.
        # The location block is the one that contains a time marker.
        for i, sec in enumerate(sections[1:], start=1):
            if _TIME_PAT.search(sec):
                loc_section_idx = i
                # Everything between sec0 and loc_section is company info.
                company_lines = []
                for j in range(1, i):
                    company_lines += [l.strip() for l in sections[j].splitlines() if l.strip()]
                company = company_lines[0] if company_lines else ""
                break

    # ---- Location section: city, remote flag, time marker, skills -------
    loc_sec = sections[loc_section_idx] if loc_section_idx < len(sections) else ""
    loc_lines = [l.strip() for l in loc_sec.splitlines() if l.strip()]

    location_txt = ""
    is_remote = False
    skills: list[str] = []
    in_skills = False

    for line in loc_lines:
        if in_skills:
            skills.append(line)
        elif _TIME_PAT.match(line):
            in_skills = True
        elif line.lower() == "remote":
            is_remote = True
        elif ", +" in line or line.lower().startswith("+"):
            continue
        elif not location_txt:
            location_txt = line

    return {
        "title": title,
        "salary_raw": salary,
        "company": company,
        "location": "Remote" if is_remote else location_txt,
        "is_remote": is_remote,
        "skills": skills,
    }


async def _login_justjoinit(page, ctx) -> bool:
    """
    Attempt to log in to JustJoinIT.  Priority:
      1. Google browser profile (reuses saved Google session).
      2. JUSTJOINIT_EMAIL + JUSTJOINIT_PASSWORD from vault.
    Returns True if login succeeded, False otherwise.
    """
    from scrapers.google_auth import click_google_signin, profile_exists

    await page.goto("https://justjoin.it/", wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)

    # Check if already signed in (avatar / profile link present)
    already = await page.query_selector("[data-testid='user-avatar'], a[href*='/profile'], button[aria-label*='account']")
    if already:
        log.info("JustJoinIT: already signed in")
        return True

    # Click the Sign In button
    try:
        sign_in_btn = await page.wait_for_selector("button:has-text('Sign in'), a:has-text('Sign in')", timeout=5_000)
        if sign_in_btn:
            await sign_in_btn.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    # Try Google sign-in first
    if profile_exists():
        signed_in = await click_google_signin(page)
        if signed_in:
            await page.wait_for_load_state("networkidle", timeout=15_000)
            log.info("JustJoinIT: signed in via Google account")
            return True

    # Fall back to email/password
    from storage.vault import get_secret
    email = get_secret("JUSTJOINIT_EMAIL")
    password = get_secret("JUSTJOINIT_PASSWORD")
    if email and password:
        try:
            await page.fill("input[type='email'], input[name='email']", email)
            await asyncio.sleep(0.5)
            await page.fill("input[type='password'], input[name='password']", password)
            await asyncio.sleep(0.5)
            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)
            log.info("JustJoinIT: signed in with email/password")
            return True
        except Exception as exc:
            log.warning("JustJoinIT email/password login failed: %s", exc)

    log.info("JustJoinIT: proceeding without login (set JUSTJOINIT_EMAIL/PASSWORD in vault or run --auth-google-account)")
    return False


class JustJoinITScraper(BaseScraper):
    source = "justjoinit"

    async def search(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth as _Stealth
        except ImportError:
            log.error("playwright not installed; run: pip install playwright && playwright install chromium")
            return []

        seen_urls: set[str] = set()
        results: list[dict] = []

        from scrapers.google_auth import make_context, profile_exists

        async with async_playwright() as pw:
            ctx, browser = await make_context(
                pw,
                headless=True,
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-US",
            )
            await _Stealth().apply_stealth_async(ctx)
            page = await ctx.new_page()

            await _login_justjoinit(page, ctx)

            for keyword in self.keywords:
                for location in self.locations:
                    if len(results) >= self.max_results:
                        break

                    url = _build_url(keyword, location)
                    log.info("JustJoinIT: scraping %s", url)
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
                    except Exception as exc:
                        log.warning("JustJoinIT: page load failed (%s), skipping", exc)
                        continue

                    await asyncio.sleep(2)

                    # Scroll to load more virtual-list items
                    prev_count = 0
                    for _ in range(_MAX_SCROLL_STEPS):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(_SCROLL_PAUSE)
                        count = await page.evaluate(
                            "() => document.querySelectorAll('li[data-index]').length"
                        )
                        if count == prev_count:
                            break
                        prev_count = count
                        if count >= self.max_results * 2:
                            break

                    # Extract all cards
                    cards = await page.evaluate("""() => {
                        return [...document.querySelectorAll('li[data-index]')].map(el => {
                            const link = el.querySelector('a[href]');
                            return {
                                href: link ? link.href : null,
                                text: el.innerText
                            };
                        });
                    }""")

                    for card in cards:
                        if len(results) >= self.max_results:
                            break
                        href = card.get("href")
                        if not href or href in seen_urls:
                            continue
                        seen_urls.add(href)

                        parsed = _parse_card_text(card.get("text", ""))
                        if not parsed["title"] or not parsed["company"]:
                            continue

                        # Slug-based ID (last path segment)
                        slug = href.rstrip("/").split("/")[-1]

                        results.append({
                            "id": slug,
                            "title": parsed["title"],
                            "company": parsed["company"],
                            "url": href,
                            "apply_url": href,
                            "description": "",  # fetched below if needed
                            "location": parsed["location"],
                            "salary_raw": parsed["salary_raw"],
                            "posted_at": "",
                            "skills": parsed["skills"],
                        })

                    log.info(
                        "JustJoinIT: %d listings from keyword=%r location=%r",
                        len(cards), keyword, location,
                    )

            # Optionally fetch descriptions for the top N results
            _DETAIL_LIMIT = min(len(results), 20)
            for i in range(_DETAIL_LIMIT):
                try:
                    await page.goto(results[i]["url"], wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
                    await asyncio.sleep(1)
                    desc = await page.evaluate("""() => {
                        const el = document.querySelector(
                            '[class*=\"jobDescription\"], [class*=\"JobDescription\"], [data-testid=\"job-description\"], .MuiBox-root section'
                        );
                        return el ? el.innerText : '';
                    }""")
                    results[i]["description"] = desc or ""
                except Exception as exc:
                    log.debug("JustJoinIT detail fetch failed for %s: %s", results[i]["url"], exc)

            await ctx.close()
            if browser:
                await browser.close()

        return results[:self.max_results]
