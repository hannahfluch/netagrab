"""Download NetAcad courses as PDF, Markdown, or structured JSON."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from contextlib import ExitStack
from importlib.metadata import version
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from .exporter import FORMATS, Exporter, load_session, discover, write_json, save_session


def course_id(url):
    parsed = urlparse(url)
    ids = parse_qs(parsed.query).get("id", [])
    if (parsed.scheme != "https" or parsed.hostname not in {"netacad.com", "www.netacad.com"}
            or parsed.path.rstrip("/") != "/launch" or len(ids) != 1 or not ids[0].strip()
            or parsed.username or parsed.password):
        raise ValueError("Use a NetAcad course launch link: https://www.netacad.com/launch?id=...")
    return ids[0]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="netagrab", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('netagrab')}")
    parser.add_argument("course_url", nargs="?", help="Course launch URL; prompts on stdin when omitted")
    parser.add_argument("--url", help="Alternative to the positional course URL")
    parser.add_argument("--output", type=Path, help="Output folder (default: output, or a course-specific subfolder)")
    parser.add_argument("--headed", action="store_true", help="Show Chromium for manual login/MFA")
    parser.add_argument("--offline", action="store_true", help="Rebuild from downloaded sources and assets")
    parser.add_argument("--modules", help="Comma-separated module numbers for a partial export")
    parser.add_argument("--no-videos", action="store_true", help="Download captions and posters, skip video files")
    parser.add_argument("--format", "--formats", nargs="+", choices=(*FORMATS, "all"), default=["pdf"],
                        help="Output format(s): pdf (default), markdown, json (structured AI input), or all")
    args = parser.parse_args(argv)
    if args.course_url is not None and args.url is not None:
        parser.error("Pass the course URL either as an argument or with --url, not both")
    args.url = args.course_url if args.course_url is not None else args.url
    if args.url is None and not args.offline:
        try:
            args.url = input("NetAcad course URL: ")
        except EOFError:
            parser.error("No course URL received on stdin. Pass it as an argument or with --url")
    if args.url is not None:
        args.url = args.url.strip()
        try:
            course_id(args.url)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def output_directory(args):
    output = args.output if args.output is not None else Path("output")
    cached = output / "course-manifest.json"
    if cached.exists() and args.url:
        old = json.loads(cached.read_text())
        if course_id(old["url"]) != course_id(args.url):
            if args.output is not None or args.offline:
                raise ValueError("That output folder belongs to another course. Choose a different --output folder.")
            key = hashlib.sha256(course_id(args.url).encode()).hexdigest()[:12]
            output = output / f"course-{key}"
            print(f"Saving this course in {output}")
    return output


def run():
    args = parse_args()
    args.output = output_directory(args)
    formats = set(FORMATS) if "all" in args.format else set(args.format)
    os.umask(0o077)
    with ExitStack() as stack:
        browser = None
        if not args.offline or "pdf" in formats:
            p = stack.enter_context(sync_playwright())
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"), headless=not args.headed)
        try:
            manifest_path = args.output / "course-manifest.json"
            page = None
            if args.offline:
                manifest = json.loads(manifest_path.read_text())
            else:
                context = browser.new_context(storage_state=load_session())
                page = context.new_page()
                manifest = discover(page, context, args.url, args.headed)
                if manifest_path.exists():
                    previous = json.loads(manifest_path.read_text())
                    if any(previous.get(key) != manifest.get(key) for key in ("content_base", "language")):
                        raise ValueError("This folder contains another course edition or language. Choose a different --output folder.")
                write_json(manifest_path, manifest)
                save_session(context)
            exporter = Exporter(args.output, manifest, browser, page, not args.no_videos, formats=formats)
            if args.offline:
                def cached_only(url, path):
                    if path.exists() and path.stat().st_size:
                        return path
                    exporter.issue("missing_cached_asset", clean_url(url))
                    return None
                exporter.fetch = cached_only
            modules = manifest["modules"]
            if args.modules:
                selected = {int(n) for n in args.modules.split(",")}
                modules = [m for m in modules if m["number"] in selected]
                if not modules or selected - {m["number"] for m in modules}:
                    raise ValueError("Requested module number is not in the course outline")
            try:
                for module in modules:
                    exporter.module(module)
                exporter.finish()
            finally:
                exporter.report()
            for fmt in FORMATS:
                if fmt in formats:
                    extension = "md" if fmt == "markdown" else fmt
                    print(f'{fmt}: {args.output / ("course." + extension)}')
            print(f'Offline HTML: {args.output / "index.html"}\nCoverage report: {args.output / "report.json"}')
        finally:
            if browser:
                browser.close()


def main():
    """Console entry point shared by netagrab and python -m netagrab."""
    try:
        run()
        return 0
    except EOFError:
        print("Input ended before login details were entered. Run interactively to enter your credentials.", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, FileNotFoundError, PlaywrightTimeout) as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        return 1
