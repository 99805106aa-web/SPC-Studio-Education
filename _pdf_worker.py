# -*- coding: utf-8 -*-
"""Playwright PDF 렌더링 전용 워커 (Streamlit asyncio 환경 회피)."""
from __future__ import annotations

import sys
import shutil
from pathlib import Path


def render_html_file_to_pdf(html_path: str) -> bytes:
    from playwright.sync_api import sync_playwright

    # A4 가로 96dpi 기준 (297mm x 210mm)
    a4_landscape_w = 1122
    a4_landscape_h = 794
    file_url = Path(html_path).resolve().as_uri()
    with sync_playwright() as playwright:
        # Community Cloud installs Debian Chromium through packages.txt.
        # Desktop installations keep using Playwright's managed browser.
        from spc_access import education_mode
        classroom = education_mode()
        executable = shutil.which("chromium") if sys.platform.startswith("linux") and classroom else None
        browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": a4_landscape_w, "height": a4_landscape_h},
                                java_script_enabled=not classroom)
        if classroom:
            # Classroom uploads must not make server-side network/file requests.
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("data:") else route.abort())
            page.set_content(Path(html_path).read_text(encoding="utf-8"), wait_until="load")
        else:
            page.goto(file_url, wait_until="networkidle")
        page.emulate_media(media="print")
        page.wait_for_timeout(800)
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=False,
            margin={"top": "6mm", "bottom": "6mm", "left": "6mm", "right": "6mm"},
        )
        browser.close()
    return pdf_bytes


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pdf_worker.py <html_path> <pdf_path>", file=sys.stderr)
        return 2
    html_path = sys.argv[1]
    pdf_path = sys.argv[2]
    try:
        Path(pdf_path).write_bytes(render_html_file_to_pdf(html_path))
    except Exception as exc:
        print(str(exc).strip() or repr(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
