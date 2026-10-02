#!/usr/bin/env python3
"""
TikTok friend mass-messenger (Playwright).

Sends one message to every friend listed in friends.json, using the email
login credentials stored in .env. The browser profile is persisted in the
user_data/ folder, so you only have to log in (and solve any captcha) once.

Usage:
    python send_messages.py                 send to everyone in friends.json
    python send_messages.py --test          send only to the first friend
    python send_messages.py --login-only    just log in and save the session
    python send_messages.py --delay 30      ~30s (randomised) between friends
    python send_messages.py --headless      hide the browser (may trip TikTok)
    python send_messages.py --yes           skip the confirmation prompt
    python send_messages.py --unattended    never prompt (used by the daily scheduled run)

Runs headless automatically on a machine with no desktop session (e.g. a
Debian server); on Windows it stays headed by default. A fresh profile is
seeded from storage_state.json so a Windows login can be migrated.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# Windows consoles are often cp1252 — never crash on emoji in messages.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# ----------------------------------------------------------------- config --
BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"
FRIENDS_FILE = BASE_DIR / "friends.json"
MESSAGE_FILE = BASE_DIR / "message.txt"
USER_DATA_DIR = BASE_DIR / "user_data"        # persistent browser profile
STORAGE_STATE_FILE = BASE_DIR / "storage_state.json"
FAILURE_DIR = BASE_DIR / "failures"           # screenshots on failure

HOME_URL = "https://www.tiktok.com/"
INBOX_URL = "https://www.tiktok.com/en/inbox"

MIN_DELAY_BETWEEN_FRIENDS = 5.0    # seconds, randomised +-30%
ACTION_PAUSE = (1.2, 2.8)          # short human-like pauses between actions
CHAT_BOX_TIMEOUT = 15_000          # ms to wait for the chat box to appear
SEND_VERIFY_TIMEOUT = 12_000       # ms to confirm the message appeared
LOGIN_WAIT_SECONDS = 90            # time given to finish a captcha/2FA

# Selectors that may match the "type a message" box. TikTok changes these
# from time to time; add new candidates here if the script stops finding it.
MESSAGE_BOX_SELECTORS = [
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    'textarea[placeholder*="message" i]',
    'textarea',
]

# The login modal defaults to the Phone tab — we must click the Email tab
# before typing the address, or it lands in the phone-number field.
EMAIL_TAB_SELECTORS = [
    '[role="tab"]:has-text("Email")',
    '[data-e2e="email-tab"]',
    'button:has-text("Email")',
    'div[class*="tab"]:has-text("Email")',
    'text=/^\\s*Email\\s*$/i',
]

EMAIL_INPUT_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[placeholder*="email" i]',
]

LOGIN_COOKIE_NAMES = ("sessionid", "sessionid_ss")


# ------------------------------------------------------------- utilities --
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_env() -> dict:
    return dict(dotenv_values(ENV_FILE)) if ENV_FILE.exists() else {}


def default_headless() -> bool:
    """Desktop machines show a window; a server box with no display does not."""
    if sys.platform.startswith(("win", "darwin")):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def chromium_major_version(pw) -> str | None:
    try:
        out = subprocess.check_output([pw.chromium.executable_path, "--version"],
                                      text=True, timeout=15)
        m = re.search(r"(\d+)\.", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def human_pause(lo: float = ACTION_PAUSE[0], hi: float = ACTION_PAUSE[1]) -> None:
    time.sleep(random.uniform(lo, hi))


def first_visible(page, selectors, timeout: int = 5_000, try_each_ms: int = 700):
    """Return the first locator matching any selector that is actually visible."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                for loc in page.locator(sel).all():
                    if loc.is_visible():
                        return loc
            except PlaywrightError:
                continue
        time.sleep(try_each_ms / 1000)
    return None


def safe_click(page, selectors, timeout: int = 5_000) -> bool:
    loc = first_visible(page, selectors, timeout=timeout)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except PlaywrightError:
        return False


def is_logged_in(context) -> bool:
    try:
        cookies = context.cookies("https://www.tiktok.com")
    except PlaywrightError:
        return False
    return any(
        c["name"] in LOGIN_COOKIE_NAMES and c.get("value") for c in cookies
    )


def wait_for_login(context, timeout: int = LOGIN_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_logged_in(context):
            return True
        time.sleep(2)
    return is_logged_in(context)


def fail_screenshot(page, friend: dict) -> str | None:
    try:
        FAILURE_DIR.mkdir(exist_ok=True)
        who = re.sub(r"[^\w.-]", "_", friend.get("username") or friend.get("name") or "unknown")
        path = FAILURE_DIR / f"{who}_{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(path))
        return str(path)
    except PlaywrightError:
        return None


# ---------------------------------------------------------------- config --
def load_credentials() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit("Missing .env file. Copy .env.example to .env and fill it in.")
    values = dotenv_values(ENV_FILE)
    email = (values.get("TIKTOK_EMAIL") or "").strip()
    password = (values.get("TIKTOK_PASSWORD") or "").strip()
    if not email or not password:
        sys.exit("Set TIKTOK_EMAIL and TIKTOK_PASSWORD in .env first.")
    return email, password


def load_friends() -> list[dict]:
    if not FRIENDS_FILE.exists():
        sys.exit(f"Missing {FRIENDS_FILE.name}. Edit friends.json and add your friends.")
    try:
        raw = json.loads(FRIENDS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"friends.json is not valid JSON: {exc}")
    if not isinstance(raw, list) or not raw:
        sys.exit("friends.json must be a non-empty JSON array.")

    friends = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            item = {"username": item}
        if not isinstance(item, dict):
            sys.exit(f"friends.json entry #{i + 1} must be a string or object.")
        username = str(item.get("username", "")).strip().lstrip("@")
        name = str(item.get("name", "")).strip() or username
        if not username and not name:
            sys.exit(f"friends.json entry #{i + 1} needs a 'username' and/or 'name'.")
        friends.append({"name": name, "username": username})
    return friends


def message_lines_for(template: str, friend: dict) -> list[str]:
    """Fill the {name}/{first_name}/{username} placeholders; each non-empty
    line becomes its own message (Enter sends on TikTok web)."""
    values = {
        "name": friend["name"],
        "first_name": (friend["name"].split()[0] if friend["name"] else friend["username"]),
        "username": friend["username"],
    }
    rendered = re.sub(r"\{(name|first_name|username)\}", lambda m: values[m.group(1)], template)
    return [line.strip() for line in rendered.splitlines() if line.strip()]


# ------------------------------------------------------------------ login --
def submit_candidates(page):
    """Yield likely submit buttons in preference order.

    The current TikTok login form submits with a button labelled exactly
    'Continue'; older builds used 'Log in' / 'Submit' / 'Next'. SS0 buttons
    like 'Continue with Google' are deliberately NOT matched."""
    try:
        exact_continue = re.compile(r"^\s*Continue\s*$", re.I)
        yield from page.get_by_role("button", name=exact_continue).all()
    except PlaywrightError:
        pass
    for sel in ('button:has-text("Log in")', 'button:has-text("Submit")',
                'button:has-text("Next")', 'button:has-text("Verify")',
                'input[type="submit"]'):
        try:
            yield from page.locator(sel).all()
        except PlaywrightError:
            continue


def click_submit(page, timeout: int = 6_000) -> bool:
    """Click the login form's submit (Continue) button once it's enabled."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for loc in submit_candidates(page):
            try:
                if loc.is_visible() and loc.is_enabled():
                    loc.click()
                    return True
            except PlaywrightError:
                continue
        time.sleep(0.5)
    return False


def looks_like_phone(loc) -> bool:
    """True if the input is actually the phone-number field."""
    try:
        if (loc.get_attribute("type") or "").lower() == "tel":
            return True
        if "phone" in (loc.get_attribute("placeholder") or "").lower():
            return True
        if (loc.get_attribute("name") or "").lower() in ("phone", "mobile_number", "mobile"):
            return True
    except PlaywrightError:
        pass
    return False


def find_email_box(page, timeout: int = 8_000):
    """Find the email input, explicitly rejecting anything phone-like."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for sel in EMAIL_INPUT_SELECTORS:
            try:
                for loc in page.locator(sel).all():
                    if loc.is_visible() and not looks_like_phone(loc):
                        return loc
            except PlaywrightError:
                continue
        time.sleep(0.5)
    return None


def switch_to_email_tab(page) -> bool:
    """Click the 'Email' tab in the login modal and wait for the email field.

    The modal defaults to the Phone tab, so typing straight into the first
    text input would fill the phone-number box instead."""
    if find_email_box(page, timeout=1_000) is not None:
        return True  # already on the email screen

    clicked = False
    for sel in EMAIL_TAB_SELECTORS:
        loc = first_visible(page, [sel], timeout=1_500, try_each_ms=300)
        if loc is not None:
            try:
                loc.click()
                clicked = True
                break
            except PlaywrightError:
                continue

    box = find_email_box(page, timeout=6_000 if clicked else 2_000)
    if box is not None:
        log("Login form switched to the Email tab.")
        return True
    log("WARNING: could not switch the login form to Email — the email may "
        "land in the phone field. If that happens, click 'Email' manually.")
    return False


def maybe_handle_code_prompt(page, unattended: bool = False) -> None:
    """If TikTok asks for an email/SMS verification code, ask the user for it."""
    code_box = None
    try:
        for loc in page.locator('input[name="code"], input[placeholder*="code" i]').all():
            if loc.is_visible():
                code_box = loc
                break
    except PlaywrightError:
        pass
    if code_box is None:
        return
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=3_000).lower()
    except PlaywrightError:
        return
    if not any(k in body for k in ("verification code", "enter the code", "we sent")):
        return
    if unattended:
        log("A verification code is required, but this run is unattended — "
            "run 'python send_messages.py --login-only' interactively to refresh the session.")
        return
    code = input("TikTok sent a verification code. Type it here and press Enter: ").strip()
    code_box.fill(code)
    human_pause()
    click_submit(page, timeout=5_000)


def ensure_logged_in(page, context, email: str, password: str, unattended: bool = False) -> bool:
    page.goto(HOME_URL, wait_until="domcontentloaded")
    human_pause(1.5, 3.0)

    if is_logged_in(context):
        log("Already logged in (saved session).")
        return True

    log("Not logged in yet — trying the email/password login flow…")
    safe_click(page, ['[data-testid="login-button"]', 'button:has-text("Log in")',
                      'div[data-e2e="login-button"]'], timeout=8_000)
    human_pause()

    # Some TikTok versions show a "Use phone or email" button before the tab form.
    safe_click(page, ['text="Use phone or email"', 'button:has-text("Use phone or email")'],
               timeout=5_000)
    human_pause()

    # The modal opens on the Phone tab — switch it to Email first, otherwise
    # the address gets typed into the phone-number field.
    switch_to_email_tab(page)

    email_box = find_email_box(page, timeout=8_000)
    if email_box is None:
        log("Could not find the email field automatically.")
    else:
        email_box.click()
        email_box.type(email, delay=45)
        human_pause()

        pw_box = first_visible(page, ['input[name="password"]', 'input[type="password"]'],
                               timeout=6_000)
        if pw_box is None:
            # Some flows ask for the email first; submit to reveal the
            # password step (the button is labelled 'Continue' nowadays).
            click_submit(page, timeout=5_000)
            pw_box = first_visible(page, ['input[name="password"]',
                                          'input[type="password"]'], timeout=8_000)
        if pw_box is not None:
            pw_box.click()
            pw_box.type(password, delay=45)
            human_pause()
            if not click_submit(page):
                page.keyboard.press("Enter")  # last resort

    human_pause(2.0, 3.5)
    if not is_logged_in(context):
        # A missed or still-disabled 'Continue' click is the usual culprit here.
        log("No session yet — retrying the Continue button…")
        click_submit(page, timeout=6_000)
    maybe_handle_code_prompt(page, unattended)

    if wait_for_login(context, 30 if unattended else LOGIN_WAIT_SECONDS):
        log("Logged in successfully.")
        return True

    if unattended:
        # Nobody is watching the console — never block on input().
        log("Login needs human help (captcha / verification). Run "
            "'python send_messages.py --login-only' interactively once, then the "
            "daily runs will reuse the saved session.")
        shot = fail_screenshot(page, {"username": "login"})
        if shot:
            log(f"Screenshot saved to {shot}")
        return False

    print("\nTikTok is showing something that needs a human "
          "(captcha, 'choose a verification method', etc.).")
    input("Finish it in the browser window, then come back here and press Enter… ")
    if wait_for_login(context, LOGIN_WAIT_SECONDS):
        log("Logged in successfully.")
        return True

    log("Still not logged in — check the credentials in .env and try again.")
    return False


# ---------------------------------------------------------------- sending --
def find_message_box(page, timeout: int = CHAT_BOX_TIMEOUT):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for sel in MESSAGE_BOX_SELECTORS:
            try:
                for loc in page.locator(sel).all():
                    try:
                        if loc.is_visible() and loc.bounding_box()["width"] > 120:
                            return loc
                    except (PlaywrightError, KeyError, TypeError):
                        continue
            except PlaywrightError:
                continue
        time.sleep(0.5)
    return None


def click_message_button(page, timeout: int = 8_000) -> bool:
    """Click the 'Message' button on a profile page.

    Matches the exact button label only, so page tabs like 'Messages' or
    unrelated text are not clicked by accident."""
    exact = re.compile(r"^\s*Message\s*$", re.I)
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            for loc in page.get_by_role("button", name=exact).all():
                if loc.is_visible():
                    try:
                        loc.click()
                        return True
                    except PlaywrightError:
                        continue
        except PlaywrightError:
            pass
        if safe_click(page, ['[data-e2e="message-button"]', 'button:has-text("Message")'],
                      timeout=1_000):
            return True
        time.sleep(0.5)
    return False


def open_chat(page, friend: dict) -> bool:
    """Open a DM thread with this friend. Returns True if the message box showed up.

    Primary flow (what TikTok's web UI actually supports): go to the
    profile page, click the 'Message' button, wait for the chat box.
    Fallbacks: the direct /message URL, then inbox search."""
    if friend["username"]:
        who = f"@{friend['username']}"
        log(f"  opening {who}'s profile…")
        page.goto(f"https://www.tiktok.com/@{friend['username']}",
                  wait_until="domcontentloaded")
        human_pause(0.6, 1.2)

        if click_message_button(page):
            if find_message_box(page) is not None:
                return True
            log("  clicked 'Message' but the chat box did not appear yet…")
            if find_message_box(page, timeout=8_000) is not None:
                return True
        else:
            log(f"  no 'Message' button on {who}'s profile "
                "(not mutual friends, or the page is still the login wall?)")

        # Fallback 1: direct chat URL — sometimes redirects straight to the thread.
        log("  trying the direct chat URL…")
        page.goto(f"https://www.tiktok.com/@{friend['username']}/message",
                  wait_until="domcontentloaded")
        if find_message_box(page, timeout=8_000) is not None:
            return True

    # Fallback 2: inbox + search by username or display name.
    query = friend["username"] or friend["name"]
    log(f"  falling back to an inbox search for '{query}'…")
    page.goto(INBOX_URL, wait_until="domcontentloaded")
    human_pause(1.0, 1.8)
    safe_click(page, ['button:has-text("Message")', '[data-e2e="inbox-message-button"]'],
               timeout=4_000)
    human_pause(0.5, 1.0)

    search_box = first_visible(page, ['input[placeholder*="search" i]'], timeout=6_000)
    if search_box is None:
        return False
    search_box.click()
    search_box.type(query, delay=55)
    human_pause(0.8, 1.5)

    for sel in (f'text="@{query}"', f'text="{query}"'):
        try:
            for loc in page.locator(sel).all():
                if loc.is_visible():
                    loc.click()
                    if find_message_box(page, timeout=10_000) is not None:
                        return True
                    break
        except PlaywrightError:
            continue
    return False


def send_line(page, box, line: str) -> bool:
    """Type one line into the chat box and send it; returns whether it appeared."""
    box.click()
    human_pause(0.15, 0.35)
    try:
        page.keyboard.insert_text(line)
    except PlaywrightError:
        box.press_sequentially(line, delay=45)
    human_pause(0.2, 0.5)
    page.keyboard.press("Enter")

    if verify_sent(page, line):
        return True
    # Some layouts need the send button instead of Enter.
    if safe_click(page, ['button[aria-label*="send" i]', '[data-e2e="send-message-button"]'],
                  timeout=3_000):
        return verify_sent(page, line)
    return False


def verify_sent(page, line: str) -> bool:
    snippet = line[:30].strip()
    if not snippet:
        return True
    try:
        page.get_by_text(snippet, exact=False).first.wait_for(
            state="visible", timeout=SEND_VERIFY_TIMEOUT)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        return False


def send_to_friend(page, template: str, friend: dict) -> tuple[bool, str]:
    if not open_chat(page, friend):
        shot = fail_screenshot(page, friend)
        return False, f"could not open a chat{f' (see {shot})' if shot else ''}"

    box = find_message_box(page, timeout=5_000)
    if box is None:
        shot = fail_screenshot(page, friend)
        return False, f"chat opened but no message box was found{f' (see {shot})' if shot else ''}"

    lines = message_lines_for(template, friend)
    for i, line in enumerate(lines):
        if not send_line(page, box, line):
            shot = fail_screenshot(page, friend)
            who = friend["username"] or friend["name"]
            return False, f"message line {i + 1} did not confirm as sent{f' (see {shot})' if shot else ''}"
        box = find_message_box(page, timeout=5_000) or box
        if i < len(lines) - 1:
            human_pause(0.4, 0.9)

    n = len(lines)
    return True, f"sent ({n} message{'s' if n != 1 else ''})"


# ------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description="Send a TikTok DM to every friend in friends.json.")
    ap.add_argument("--test", action="store_true", help="send only to the first friend")
    ap.add_argument("--login-only", action="store_true", help="just log in and save the session")
    ap.add_argument("--delay", type=float, default=MIN_DELAY_BETWEEN_FRIENDS,
                    metavar="SEC", help="seconds to wait between friends (default 5, randomised)")
    ap.add_argument("--headless", action="store_true",
                    help="force headless even on a desktop machine")
    ap.add_argument("--headed", action="store_true",
                    help="force a visible window (default on desktop OSes)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--unattended", action="store_true",
                    help="never prompt for input; fail with a clear message if login "
                         "needs interactive help (used by the scheduled daily run)")
    args = ap.parse_args()

    email, password = load_credentials()
    friends = load_friends()
    if args.test:
        friends = friends[:1]
        log("--test: sending only to the first friend.")
    if not MESSAGE_FILE.exists():
        sys.exit("Missing message.txt. Put the message you want to send in message.txt.")
    template = MESSAGE_FILE.read_text(encoding="utf-8").strip()
    if not template:
        sys.exit("message.txt is empty.")

    if not args.login_only and not args.yes and not args.unattended:
        print(f"\nAbout to send the message from {message_lines_for(template, friends[0])[0][:80]!r}")
        input(f"to {len(friends)} friend(s). Press Enter to start, Ctrl+C to abort…\n")
    elif args.unattended:
        log(f"Unattended run: messaging {len(friends)} friend(s).")

    fresh_profile = not USER_DATA_DIR.exists() or not any(USER_DATA_DIR.iterdir())
    USER_DATA_DIR.mkdir(exist_ok=True)
    results: list[tuple[dict, bool, str]] = []

    headless = (args.headless or default_headless()) and not args.headed

    with sync_playwright() as pw:
        launch_kwargs: dict = dict(
            user_data_dir=str(USER_DATA_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if headless:
            launch_kwargs["viewport"] = {"width": 1920, "height": 1080}
            log("Running headless (no desktop session detected).")
        else:
            launch_kwargs["no_viewport"] = True
            launch_kwargs["args"].append("--start-maximized")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            launch_kwargs["args"].append("--no-sandbox")  # root on a server
        if fresh_profile and STORAGE_STATE_FILE.exists():
            # Migrated session (e.g. copied from a Windows box, whose profile
            # cookies are DPAPI-encrypted and useless here, but this file isn't).
            launch_kwargs["storage_state"] = str(STORAGE_STATE_FILE)
            log(f"Fresh profile — seeding it from {STORAGE_STATE_FILE.name}.")
        if headless:
            env = load_env()
            ua = (env.get("BROWSER_USER_AGENT") or "").strip()
            if not ua:
                major = chromium_major_version(pw)
                if major:
                    ua = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
            if ua:
                launch_kwargs["user_agent"] = ua
            launch_kwargs["locale"] = (env.get("BROWSER_LOCALE") or "").strip() or "en-US"
            tz = (env.get("BROWSER_TIMEZONE") or "").strip()
            if tz:
                launch_kwargs["timezone_id"] = tz

        try:
            context = pw.chromium.launch_persistent_context(**launch_kwargs)
        except TypeError:  # older Playwright rejecting a context option
            for k in ("user_agent", "locale", "timezone_id", "storage_state", "viewport"):
                launch_kwargs.pop(k, None)
            context = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30_000)

        try:
            if not ensure_logged_in(page, context, email, password, args.unattended):
                return 1
            try:
                context.storage_state(path=str(STORAGE_STATE_FILE))
            except PlaywrightError:
                pass

            if args.login_only:
                log("Session saved in user_data/ — you can close this window and run for real now.")
                if not args.unattended:
                    input("Press Enter to close the browser… ")
                return 0

            for i, friend in enumerate(friends, 1):
                who = f"@{friend['username']}" if friend["username"] else friend["name"]
                log(f"[{i}/{len(friends)}] {who} …")
                try:
                    ok, detail = send_to_friend(page, template, friend)
                except PlaywrightError as exc:
                    ok, detail = False, f"browser error: {str(exc).splitlines()[0]}"
                results.append((friend, ok, detail))
                log(f"           -> {'OK' if ok else 'FAIL'}: {detail}")
                if i < len(friends):
                    wait = args.delay * random.uniform(0.7, 1.3)
                    log(f"           waiting {wait:.0f}s before the next friend…")
                    time.sleep(wait)

            print("\n=================== SUMMARY ===================")
            for friend, ok, detail in results:
                who = f"@{friend['username']}" if friend["username"] else friend["name"]
                print(f"  [{'OK  ' if ok else 'FAIL'}] {who:<28} {detail}")
            sent = sum(1 for _, ok, _ in results if ok)
            print(f"  {sent}/{len(results)} friends messaged.")

            if sent < len(results) and not args.unattended:
                input("\nPress Enter to close the browser… ")
        finally:
            context.close()

    return 0 if all(ok for _, ok, _ in results) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
