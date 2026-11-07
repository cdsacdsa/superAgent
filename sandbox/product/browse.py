#!/usr/bin/env python3
"""Drive a web UI from inside the sandbox with a headless Chromium.

    browse URL [--step ACTION ...] [--screenshot FILE]

Steps run in order: click:<selector>  fill:<selector>=<value>  select:<selector>=<value>
press:<key>  wait:<milliseconds>  goto:<url>
Prints the final URL and HTTP status, the page title, downloads, console errors, failed
requests (HTTP >= 400 included) and the visible text of the page.
"""

import argparse
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def main() -> int:
    p = argparse.ArgumentParser(description="Drive a web UI with a headless browser.")
    p.add_argument("url")
    p.add_argument("--step", action="append", default=[], help="click:SEL | fill:SEL=VALUE | select:SEL=VALUE | "
                   "press:KEY | wait:MS | goto:URL")
    p.add_argument("--screenshot", help="save a full-page PNG here")
    p.add_argument("--max-chars", type=int, default=6000)
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    problems: list[str] = []
    downloads: list[str] = []
    download_urls: list[str] = []
    status: dict[str, int | None] = {"code": None}
    with sync_playwright() as pw:
        # The session itself is the isolation boundary (Hyper-V / locked-down container).
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_context(accept_downloads=True).new_page()
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("requestfailed", lambda r: problems.append(f"request failed: {r.method} {r.url} ({r.failure})"))

        def on_response(r):
            if r.request.is_navigation_request() and r.frame == page.main_frame:
                status["code"] = r.status
            if r.status >= 400:
                problems.append(f"HTTP {r.status}: {r.request.method} {r.url}")

        def on_download(d):
            downloads.append(f"{d.suggested_filename} from {d.url}")
            download_urls.append(d.url)

        def goto(url: str) -> None:
            try:
                page.goto(url, wait_until="networkidle", timeout=30_000)
            except PlaywrightError:
                page.wait_for_timeout(500)  # a file download aborts the navigation: that is not an error
                if url not in download_urls:
                    raise

        page.on("response", on_response)
        page.on("download", on_download)
        try:
            goto(a.url)
            for step in a.step:
                action, _, arg = step.partition(":")
                sel, _, value = arg.partition("=")
                if action == "click":
                    page.click(arg, timeout=10_000)
                elif action == "fill":
                    page.fill(sel, value, timeout=10_000)
                elif action == "select":
                    page.select_option(sel, value, timeout=10_000)
                elif action == "press":
                    page.keyboard.press(arg)
                elif action == "wait":
                    page.wait_for_timeout(int(arg))
                elif action == "goto":
                    goto(arg)
                else:
                    print(f"unknown step {step!r}", file=sys.stderr)
                    return 2
                page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightError as e:
            problems.append(f"browser error: {str(e).splitlines()[0]}")
        if a.screenshot:
            page.screenshot(path=a.screenshot, full_page=True)
        problems = [x for x in problems if not ("ERR_ABORTED" in x and any(u in x for u in download_urls))]
        text = page.inner_text("body") if page.query_selector("body") else ""
        print(f"url: {page.url}\nstatus: {status['code']}\ntitle: {page.title()}")
        print("downloads: " + ("; ".join(downloads) or "none"))
        print("problems:\n" + ("\n".join(f"- {x}" for x in problems) or "- none"))
        print("text:\n" + text[: a.max_chars])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
