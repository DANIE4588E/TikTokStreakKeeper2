# TikTok Streak Keeper

A [Playwright](https://playwright.dev/python/) script that opens TikTok in a real
Chromium window, logs in with the **email + password from `.env`**, then sends a
customisable DM to every friend in a list.

## Files

| File | Purpose |
|---|---|
| [`send_messages.py`](send_messages.py) | The script |
| [`.env`](.env) | Your TikTok email & password (**git-ignored**) |
| [`friends.json`](friends.json) | The list of friends to message |
| [`message.txt`](message.txt) | The message to send |
| `user_data/` | Persisted browser profile — created on first run, so you only log in once |
| `failures/` | Screenshots saved when a friend fails |

## Setup (once)

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

Then:

1. Put your credentials in **`.env`**:
   ```ini
   TIKTOK_EMAIL=you@example.com
   TIKTOK_PASSWORD=your-password
   ```
2. List your friends in **`friends.json`** (username = the part after the `@`):
   ```json
   [
     { "name": "Alice Example", "username": "alice.example" },
     "charlie_123"
   ]
   ```
   Plain strings are fine too — they're treated as usernames. `name` is optional
   and only used for the `{name}` placeholder and inbox-search fallback.
3. Write the message in **`message.txt`**. Supported placeholders:
   `{name}`, `{first_name}`, `{username}`.
   Each non-empty **line** is sent as its own message (Enter sends on TikTok web).

## Run

```powershell
python send_messages.py            # send to everyone (asks for confirmation first)
python send_messages.py --test     # send only to the first friend — try this first!
python send_messages.py --login-only   # just log in and cache the session
python send_messages.py --delay 30     # wait ~30s between friends
python send_messages.py --yes          # skip the confirmation prompt
python send_messages.py --unattended   # never prompt (used by the daily task)
python send_messages.py --headed       # force a visible window on a server
```
It runs **headless automatically** whenever there's no desktop session (a Debian
server), and headed by default on Windows/macOS.

## Running on a Debian server

1. Copy the project over and run the one-time setup on the server:
   ```bash
   scp .env friends.json message.txt storage_state.json send_messages.py \
       requirements.txt setup_server.sh run_daily.sh install_cron.sh \
       user@server:~/TikTokStreakKeeper2/
   ssh user@server
   cd ~/TikTokStreakKeeper2 && sed -i 's/\r$//' *.sh && bash setup_server.sh
   ```
   (`sed` undos any Windows line endings; harmless if there are none.)
2. **Migrating your login**: `storage_state.json` holds your session cookies in
   plain JSON. Don't copy `user_data/` — Windows encrypts its cookies with
   DPAPI, so they're unreadable on Linux. On first run the script creates a
   fresh Linux profile **seeded from `storage_state.json`**. When the session
   eventually lapses: log in once on Windows (`--login-only`), then copy the
   refreshed `storage_state.json` back to the server.
3. Test it: `./.venv/bin/python send_messages.py --test --unattended`
4. Schedule it: `bash install_cron.sh` → runs `run_daily.sh` at **00:01 in the
   server's timezone** (check with `timedatectl`; set yours with
   `sudo timedatectl set-timezone Asia/Singapore`). Logs: `logs/cron.log`.

Headless browsers get challenged a bit more by TikTok than desktop ones. If
daily runs start failing with login/captcha messages in `logs/cron.log`,
set `BROWSER_TIMEZONE` / `BROWSER_LOCALE` / `BROWSER_USER_AGENT` in `.env` to
match your usual environment (they're only applied when headless).

> If you keep the server task, remove the Windows one so you don't send twice:
> `Unregister-ScheduledTask -TaskName 'TikTokStreakKeeper Daily' -Confirm:$false`

## Daily automation at 00:01

The scheduled task **"TikTokStreakKeeper Daily"** is registered with Windows
Task Scheduler (see [`register_task.ps1`](register_task.ps1)); it runs
[`run_daily.cmd`](run_daily.cmd) every day at **00:01**, which executes
`send_messages.py --unattended` and appends everything to `logs\cron.log`.

- It runs in **your desktop session** — the PC must be on and you must be
  logged on. If it was asleep/off at 00:01, `StartWhenAvailable` fires the run
  as soon as you're back.
- Nothing ever waits for Enter. If the saved TikTok login has lapsed and a
  captcha/verification is needed, the run logs the problem, drops a screenshot
  in `failures/`, and exits — you then just do a one-time
  `python send_messages.py --login-only` to refresh the session.
- Check on it any time:
  ```powershell
  Get-ScheduledTaskInfo "TikTokStreakKeeper Daily"   # next/last run + result
  Get-Content logs\cron.log -Tail 40                 # recent output
  ```
- To remove: `Unregister-ScheduledTask -TaskName 'TikTokStreakKeeper Daily' -Confirm:$false`
  (re-add with `powershell -ExecutionPolicy Bypass -File register_task.ps1`).

### First run

A visible Chromium window opens. If TikTok shows a captcha, an email/SMS code, or
a "choose verification method" screen, **finish it in the browser window** —
the script waits for you (and can ask you to type a code in the console). Your
session is saved in `user_data/`, so later runs skip login. If TikTok ever logs
you out, delete the `user_data/` folder and run `--login-only` again.

To reset the saved session: `--login-only`, or delete `user_data/`.

## How it works

1. Opens tiktok.com with a persistent profile; logs in via
   *Log in → Use phone or email* using the `.env` credentials if needed.
2. For each friend, opens their profile page `https://www.tiktok.com/@username`
   and clicks the **Message** button to open the chat
   (fallbacks: the direct `/message` URL, then inbox → search).
3. Types the message into the chat box, presses Enter, and **verifies the
   message bubble appeared** before moving on.
4. Waits a randomised ~10–20 s (`--delay`) between friends and prints a summary;
   failures get a screenshot in `failures/`.

## Fair warning

Automating TikTok is against their Terms of Service and mass-messaging can get
an account rate-limited or banned, especially to people who don't message you
back. Keep the delays generous, message only real friends, keep volumes low,
and use at your own risk.

## Troubleshooting

- **"chat opened but no message box was found"** — TikTok changed its markup.
  Open a chat manually, inspect the input, and add a selector to
  `MESSAGE_BOX_SELECTORS` at the top of the script.
- **"no 'Message' button on @…'s profile"** — TikTok only shows that button for
  mutual friends you can DM; the script then tries the direct URL and the inbox
  search fallback automatically.
- **"could not open a chat"** — the user may not be a mutual friend you can DM,
  or the username is wrong; check the screenshot in `failures/`.
- **Login loops / challenges** — run with a visible window (default), avoid
  `--headless`, and slow down with `--delay 30`.
