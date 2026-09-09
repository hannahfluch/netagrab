#!/usr/bin/env python3
"""Export an enrolled NetAcad course for offline study using Chromium."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse, urljoin, parse_qs
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from collections import defaultdict, Counter

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from pypdf import PdfReader, PdfWriter

DEFAULT_URL = "https://www.netacad.com/launch?id=8d2217e7-b386-4560-bed2-caaa11e5f8a3&tab=curriculum&view=083b1f4c-8120-5797-88f9-e8525038fb98"
SESSION = Path(".session/auth.json")


def save_session(context):
    SESSION.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    SESSION.parent.chmod(0o700)
    context.storage_state(path=str(SESSION))
    SESSION.chmod(0o600)


def login(page, context, url, headed=False):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if page.locator("#username").is_visible():
            email = os.environ.get("NETACAD_USERNAME") or input("NetAcad email: ")
            page.locator("#username").fill(email)
            if not page.locator("#password").is_visible():
                page.locator("#kc-login").click()
            page.locator("#password").wait_for(state="visible", timeout=30000)
            password = os.environ.get("NETACAD_PASSWORD") or getpass.getpass("NetAcad password: ")
            page.locator("#password").fill(password)
            del password
            page.locator("#kc-login").click()
            break
        if page.locator("iframe").count():
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(4000)
    if "auth.netacad.com" in page.url:
        if not headed:
            raise RuntimeError("Login needs attention. Run with --headed to complete login in the browser.")
        input("Complete login in Chromium, then press Enter here: ")
    if "auth.netacad.com" in page.url:
        raise RuntimeError("Login did not complete.")
    save_session(context)


def merge_pdfs(entries, destination):
    writer = PdfWriter()
    for entry in entries:
        writer.append(entry["pdf"], outline_item=entry["title"])
    temp = destination.with_suffix(".tmp.pdf")
    writer.write(temp)
    writer.close()
    temp.replace(destination)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def clean_url(url):
    return url.split("?")[0].split("#")[0]


def transcript(vtt):
    """Extract cue payloads, preserving spoken numbers and dropping VTT metadata."""
    cues = []
    for block in re.split(r"\n\s*\n", vtt.replace("\r\n", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if "-->" in line:
                cue = " ".join(lines[index + 1:]).strip()
                cue = re.sub(r"<\d{2}:\d{2}[^>]*>", "", cue)
                if cue:
                    cues.append(cue)
                break
    return "".join("<p>" + " ".join(cues[i:i + 12]) + "</p>" for i in range(0, len(cues), 12))


def discover(page, context, url, headed):
    login(page, context, url, headed)
    page.locator("#course-outline").wait_for(timeout=60000)
    reject = page.get_by_role("button", name="Reject", exact=True)
    if reject.is_visible():
        reject.click()
    labels = page.locator('[class*="nodeName--"]').all_text_contents()
    modules = []
    for title in labels:
        match = re.match(r"Module (\d+):", title)
        if match:
            modules.append({"number": int(match[1]), "title": title})
    if not modules:
        raise RuntimeError("No modules found in the enrolled course outline.")
    first = page.get_by_role("button", name=modules[0]["title"]).first
    if first.get_attribute("aria-expanded") != "true":
        first.click()
    section = first.locator('xpath=../..')
    section.locator('button[class*="subModuleBtn--"]').first.click()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        for frame in page.frames:
            if "/content/" not in frame.url:
                continue
            urls = frame.evaluate("performance.getEntriesByType('resource').map(r=>r.name)")
            for resource in urls:
                match = re.match(r"(https://[^?]+/courses/content/)m\d+/([^/]+)/components.json$", resource)
                if match:
                    return {"url": url, "modules": modules, "content_base": match[1],
                            "language": match[2], "outline": labels}
        page.wait_for_timeout(500)
    raise RuntimeError("The course does not expose the supported Adapt module layout.")


class Exporter:
    def __init__(self, output, manifest, browser, page=None, videos=True):
        self.output, self.manifest, self.browser = output, manifest, browser
        self.page, self.videos = page, videos
        self.base = manifest["content_base"]
        self.global_base = self.base.split("courses/content/")[0] + "_assets/"
        self.language = manifest["language"]
        self.issues, self.attachments, self.entries = [], {}, []
        self.types = Counter()
        self.cache = {}
        self.rendered = set()
        self.output.mkdir(parents=True, exist_ok=True)
        self.output.chmod(0o700)
        (output / "assets").mkdir(exist_ok=True)
        self.render_page = browser.new_page(viewport={"width": 1100, "height": 1000})
        # Exported source HTML never runs course scripts or contacts its tracking endpoints.
        self.render_page.route("https://**/*", lambda route: route.abort())
        self.render_page.route("http://**/*", lambda route: route.abort())

    def issue(self, kind, detail, component=None):
        item = {"module": getattr(self, "number", None), "kind": kind, "detail": detail}
        if component:
            item["component"] = component
        if item not in self.issues:
            self.issues.append(item)

    def fetch(self, url, path):
        if path.exists() and path.stat().st_size:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": "NetAcadOfflineStudy/1.0"}), timeout=60) as response:
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type and path.suffix != ".html":
                        raise ValueError("Received HTML instead of the requested asset")
                    temp = path.with_suffix(path.suffix + ".part")
                    with temp.open("wb") as target:
                        while chunk := response.read(1024 * 1024):
                            target.write(chunk)
                    temp.replace(path)
                return path
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                if isinstance(exc, HTTPError) and exc.code in (401, 403, 404):
                    break
                time.sleep(2 ** attempt)
        self.issue("download_failed", clean_url(url))
        return None

    def asset_url(self, source):
        if source.startswith("media/"):
            return urljoin(self.global_base, source)
        if source.startswith("assets/"):
            return urljoin(self.module_base + self.language + "/", source)
        return urljoin(self.module_base, source)

    def asset(self, source, label=""):
        if not source:
            return ""
        if source.startswith("data:"):
            return source
        url = self.asset_url(html.unescape(source))
        if urlparse(url).scheme not in ("http", "https"):
            return ""
        if url in self.cache:
            return self.cache[url]
        suffix = Path(urlparse(url).path).suffix.lower()
        name = hashlib.sha256(clean_url(url).encode()).hexdigest()[:16] + suffix
        path = self.fetch(url, self.output / "assets" / name)
        result = "assets/" + name if path else ""
        self.cache[url] = result
        if path and suffix in (".pdf", ".pka", ".pkt", ".zip", ".docx", ".pcap", ".pcapng", ".txt"):
            self.attachments[result] = label or Path(urlparse(url).path).name
        return result

    def markup(self, value):
        value = str(value or "").replace("{{_moduleNumber}}", str(self.number))
        value = value.replace("{{baseOrigin}}", "https://www.netacad.com")
        value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.S | re.I)
        value = re.sub(r'\s+on\w+\s*=\s*("[^"]*"|\x27[^\x27]*\x27)', "", value, flags=re.I)
        def attribute(match):
            attr, quote, source = match.groups()
            if attr.lower() == "src" or re.search(r"\.(pdf|pka|pkt|zip|pcapng?|docx)(?:[?#]|$)", source, re.I):
                local = self.asset(source)
                return f'{attr}={quote}{html.escape(local, quote=True)}{quote}'
            if source.startswith(("javascript:", "data:text/html")):
                return 'href="#"'
            return match[0]
        return re.sub(r'\b(src|href)\s*=\s*(["\x27])(.*?)\2', attribute, value, flags=re.I)

    def graphic(self, graphic):
        if not isinstance(graphic, dict):
            return ""
        source = graphic.get("src") or graphic.get("large") or graphic.get("_large") or graphic.get("file")
        if not source:
            return ""
        local = self.asset(source)
        alt = html.escape(graphic.get("alt", graphic.get("description", "")))
        return f'<figure><img src="{local}" alt="{alt}"></figure>' if local else f'<p>Image unavailable: {alt}</p>'

    def dynamic_graphic(self, c):
        d = c["details"]
        image = self.asset(d.get("primary_image", ""))
        stylesheet = self.asset(d.get("stylesheet", ""))
        uuid = html.escape(d.get("uuid", c["_id"]))
        width, height = map(float, d.get("aspect_ratio", "750/500").split("/"))
        scale = min(1, 680 / width)
        labels = "".join(f'<div class="dynamic-text-item" id="{html.escape(t.get("class", t.get("id", "")))}">{self.markup(t.get("text", ""))}</div>' for t in d.get("texts", []))
        style = f'<link rel="stylesheet" href="{stylesheet}">' if stylesheet else ""
        return (style + f'<figure class="diagram" style="height:{height*scale}px"><div id="{uuid}">'
                f'<div id="importID{uuid}" class="dynamic-graphic-display" style="width:{width}px;height:{height}px;transform:scale({scale});transform-origin:top left">'
                '<div class="dynamic-graphic-content" style="position:relative;width:100%;height:100%">'
                f'<img style="width:100%;height:100%" src="{image}"><div class="dynamic-text">{labels}</div></div></div></div></figure>'
                + (f'<p class="caption">{self.markup(d["caption"])}</p>' if d.get("caption") else ""))

    def video(self, c):
        result = ""
        for video_id in c.get("videoIds", []):
            metadata_path = self.output / "media" / f"{video_id}.json"
            metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else None
            missing = [key for key in ("poster", "captions", "video") if metadata and metadata.get(key) and not (self.output / metadata[key]).exists()]
            refresh = metadata is None or bool(missing) or (self.videos and not metadata.get("video"))
            if refresh and self.page:
                query = '''query getVideoDetails($videoId: ID!, $lang: String, $publicFlag: Boolean, $serviceId: ID) {
                  getVideoDetails(videoId: $videoId, lang: $lang, publicFlag: $publicFlag, serviceId: $serviceId) {
                    data { attributes { localeVideos { localeName videoUrls } subtitles { localeName closedCaptioning } posterURL } }
                  }
                }'''
                payload = {"operationName": "getVideoDetails", "query": query, "variables": {
                    "videoId": video_id, "lang": None, "publicFlag": None,
                    "serviceId": parse_qs(urlparse(self.manifest["url"]).query)["id"][0]}}
                response = self.page.evaluate('''async payload => {
                  const r = await fetch('https://api.netacad.com/api', {method:'POST', headers: {
                    'Content-Type':'application/json', authorization: 'Bearer ' + localStorage.getItem('AuthToken'), orgname:'skillsforall'
                  }, body:JSON.stringify(payload)}); return r.json();
                }''', payload)
                attrs = response.get("data", {}).get("getVideoDetails", {}).get("data", {}).get("attributes")
                if attrs:
                    metadata = {"video_id": video_id}
                    poster = attrs.get("posterURL")
                    if poster:
                        metadata["poster"] = self.asset(poster)
                    for sub in attrs.get("subtitles", []):
                        if sub.get("localeName") == self.language:
                            metadata["captions"] = self.asset(sub["closedCaptioning"])
                    if self.videos:
                        for locale in attrs.get("localeVideos", []):
                            if locale.get("localeName") == self.language:
                                urls = locale.get("videoUrls", {}).get("mp4", {})
                                if isinstance(urls, dict) and urls:
                                    key = "720p" if "720p" in urls else next(iter(urls))
                                    metadata["video"] = self.asset(urls[key])
                    write_json(metadata_path, metadata)
            if metadata:
                for key in ("poster", "captions", "video"):
                    if metadata.get(key) and not (self.output / metadata[key]).exists():
                        self.issue("missing_cached_asset", metadata.pop(key))
            if not metadata:
                self.issue("video_unavailable", video_id)
                result += '<p class="notice">Video unavailable offline; see this lesson online.</p>'
                continue
            if metadata.get("poster"):
                result += f'<img class="poster" src="{metadata["poster"]}">'
            if metadata.get("video"):
                result += f'<p><a href="{metadata["video"]}">Open downloaded video</a></p>'
            if metadata.get("captions"):
                captions = (self.output / metadata["captions"]).read_text(encoding="utf-8-sig")
                result += '<h4>Video transcript</h4>' + self.markup(transcript(captions))
            else:
                self.issue("transcript_unavailable", video_id)
        return result

    def component(self, c):
        cid = c["_id"]
        if cid in self.rendered:
            return ""
        self.rendered.add(cid)
        typ = c.get("_component", "unknown")
        self.types[typ] += 1
        if typ in {"blank", "quicknav", "assessmentResults", "adaptiveStartScreen"}:
            return ""
        result = self.markup(c.get("body", "")) + self.markup(c.get("instruction", ""))
        result += self.graphic(c.get("_graphic"))
        if typ == "dynamic-graphic":
            result += self.dynamic_graphic(c)
        elif typ == "table":
            result += '<table>'
            for row in c.get("_rows", []):
                result += '<tr>'
                for cell in row.get("_cells", []):
                    tag = "th" if cell.get("_isHeading") else "td"
                    result += f'<{tag} colspan="{int(cell.get("_colSpan", 1))}" rowspan="{int(cell.get("_rowSpan", 1))}">{self.markup(cell.get("text"))}</{tag}>'
                result += '</tr>'
            result += '</table>'
        elif typ == "commandWindow":
            result += '<pre>' + '\n'.join(self.markup(line.get("text", "")) for line in c.get("precode", [])) + '</pre>'
        elif typ == "media":
            result += self.video(c)
        elif typ in {"adobe-animate", "adobe-animate-ia", "webComponentMedia", "dynamic-dropdown"}:
            self.issue("static_interactive", (c.get("title") or getattr(self, "section_title", cid)).replace("{{_moduleNumber}}", str(self.number)), cid)
            result += '<p class="notice">Interactive activity / animation: static study extract.</p>'
            result += '<p>' + self.markup(c.get("a11y_description", "")) + '</p>'
            d = c.get("details", {})
            result += self.graphic(d.get("image")) + self.markup(d.get("instructions", ""))
            result += '<ul>' + ''.join('<li>' + self.markup(t.get("text", t.get("value", ""))) + '</li>' for t in d.get("texts", d.get("text", []))) + '</ul>'
            for dropdown in d.get("dropdowns", []):
                result += '<p>' + html.escape(dropdown["id"]) + ': ' + ' / '.join(self.markup(o["text"]) for o in dropdown["options"]) + '</p>'
        elif typ == "list-picker":
            d = c["details"]
            result += self.markup(d.get("instructions", ""))
            result += '<p>Choices: ' + ' / '.join(self.markup(o["label"]) for o in d.get("listItemTypes", [])) + '</p><ul>'
            result += ''.join('<li>' + self.markup(o["label"]) + ' __________</li>' for o in d.get("listItems", [])) + '</ul>'
        elif typ not in {"text", "graphic", "tabs", "mcq", "packetTracer"}:
            self.issue("unsupported_component", typ + ": " + cid)
        for item in c.get("_items", []):
            title = item.get("tabTitle") or item.get("title")
            if title:
                result += '<h4>' + self.markup(title) + '</h4>'
            result += self.markup(item.get("body", ""))
            if item.get("text"):
                result += '<p>□ ' + self.markup(item["text"]) + '</p>'
            result += self.graphic(item.get("_graphic"))
            for key in ("_downloadUrl", "iframeurl"):
                source = item.get(key)
                if source and (key != "iframeurl" or source != item.get("_downloadUrl")):
                    local = self.asset(source, title or "Activity")
                    if local:
                        result += f'<p><a href="{local}">Download: {self.markup(title or source)}</a></p>'
            # Tabs can reference separately defined components by their ID.
            for child in self.components.values():
                if child["_id"] != cid and child["_id"] in json.dumps(item):
                    result += self.component(child)
        return f'<div class="component" id="{html.escape(cid)}">{result}</div>'

    def document(self, title, body):
        css = Path(__file__).with_name("print.css").read_text()
        return ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' data:; style-src \'self\' \'unsafe-inline\'; media-src \'self\';">'
                f'<title>{html.escape(title)}</title><style>{css}</style><body>{body}</body></html>')

    def pdf(self, html_path, pdf_path):
        self.render_page.goto(html_path.resolve().as_uri(), wait_until="load")
        self.render_page.evaluate("""async () => {
          await document.fonts.ready;
          await Promise.all([...document.images].map(img => img.decode().catch(() => {})));
        }""")
        broken = self.render_page.evaluate("[...document.images].filter(i=>!i.naturalWidth).map(i=>i.getAttribute('src'))")
        for source in broken:
            self.issue("broken_image", source)
        self.render_page.pdf(path=str(pdf_path), format="A4", print_background=True,
                             margin={"top": "16mm", "bottom": "17mm", "left": "14mm", "right": "14mm"},
                             display_header_footer=True, header_template="<span></span>",
                             footer_template='<div style="width:100%;text-align:center;font-size:9px;color:#666">Cisco CyberOps • Offline study • <span class="pageNumber"></span></div>')

    def module(self, module):
        self.number = module["number"]
        self.module_base = self.base + f'm{self.number}/'
        data = {}
        for name in ("course", "contentObjects", "articles", "blocks", "components", "assets"):
            path = self.output / "source" / f'm{self.number}' / f'{name}.json'
            if not self.fetch(self.module_base + self.language + '/' + name + '.json', path):
                raise RuntimeError(f"Cannot download module {self.number} {name}. See report.json.")
            data[name] = json.loads(path.read_text())
        self.components = {c["_id"]: c for c in data["components"]}
        self.rendered = set()
        children = defaultdict(list)
        for name in ("contentObjects", "articles", "blocks", "components"):
            for obj in data[name]:
                children[obj.get("_parentId")].append(obj)
        def render_node(node, depth):
            if node.get("_type") == "component":
                return self.component(node)
            title = node.get("displayTitle") or (node.get("title", "") if depth == 2 else "")
            if title:
                self.section_title = title
            tag = min(depth, 4)
            result = f'<h{tag} id="{node["_id"]}">{self.markup(title)}</h{tag}>' if title else ""
            result += self.markup(node.get("body", "")) + self.markup(node.get("pageBody", ""))
            for child in children[node["_id"]]:
                result += render_node(child, depth + 1)
            return result
        body = f'<h1>{html.escape(module["title"])}</h1><p class="source">Cisco Networking Academy · {self.language}</p>'
        for node in data["contentObjects"]:
            if node.get("_parentId") not in {n["_id"] for n in data["contentObjects"]}:
                body += render_node(node, 2)
        # Preserve nested components even if an unfamiliar parent convention is used.
        for c in data["components"]:
            if c["_id"] not in self.rendered:
                self.issue("unplaced_component", c["_id"])
                body += '<h3>' + self.markup(c.get("title", "Additional activity")) + '</h3>' + self.component(c)
        stem = f'module-{self.number:02d}'
        html_path = self.output / f'{stem}.html'
        html_path.write_text(self.document(module["title"], body))
        pdf_path = self.output / f'{stem}.pdf'
        self.pdf(html_path, pdf_path)
        pages = len(PdfReader(pdf_path).pages)
        self.entries.append({"title": module["title"], "pdf": str(pdf_path), "html": html_path.name,
                             "components": len(self.rendered), "sections": len(data["contentObjects"]), "pages": pages})
        self.report()
        print(f'{module["title"]}: {len(self.rendered)} components, {pages} pages', flush=True)

    def report(self):
        write_json(self.output / "report.json", {"modules_expected": len(self.manifest["modules"]),
            "modules_exported": len(self.entries), "modules": self.entries,
            "component_types": dict(self.types), "attachments": self.attachments, "issues": self.issues,
            "external_assessments": [title for title in self.manifest.get("outline", []) if not re.match(r"Module \d+:", title)],
            "limitations": ["PDFs contain static content; interactive behavior is not preserved.",
                            "External checkpoint/final exams are not launched or submitted."]})

    def finish(self):
        links = ''.join(f'<li><a href="{e["html"]}">{html.escape(e["title"])}</a></li>' for e in self.entries)
        body = '<h1>CyberOps Associate</h1><p>Offline study edition · Cisco Networking Academy</p><ol>' + links + '</ol>'
        body += '<h2>Lab handouts and downloads</h2><ul>' + ''.join(f'<li><a href="{path}">{html.escape(title)}</a></li>' for path, title in self.attachments.items()) + '</ul>'
        body += '<h2>Export notes</h2><p>Videos are separate files; available captions are included as transcripts. Animations and interactive activities are represented by static extracts. External exams require NetAcad.</p>'
        body += f'<p>{len(self.issues)} items are detailed in <a href="report.json">the coverage report</a>.</p>'
        index = self.output / "index.html"
        index.write_text(self.document("CyberOps Associate — Offline study", body))
        self.pdf(index, self.output / "contents.pdf")
        entries = [{"pdf": str(self.output / "contents.pdf"), "title": "Contents and export notes"}] + self.entries
        for path, title in self.attachments.items():
            if path.endswith(".pdf"):
                try:
                    PdfReader(self.output / path)
                    entries.append({"pdf": str(self.output / path), "title": "Lab: " + title})
                except Exception:
                    self.issue("invalid_pdf", path)
        merge_pdfs(entries, self.output / "course.pdf")
        self.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--headed", action="store_true", help="Show Chromium for manual login/MFA")
    parser.add_argument("--offline", action="store_true", help="Rebuild from downloaded sources and assets")
    parser.add_argument("--modules", help="Comma-separated module numbers for a partial export")
    parser.add_argument("--no-videos", action="store_true", help="Download captions and posters, skip video files")
    args = parser.parse_args()
    os.umask(0o077)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"), headless=not args.headed)
        try:
            manifest_path = args.output / "course-manifest.json"
            page = None
            if args.offline:
                manifest = json.loads(manifest_path.read_text())
            else:
                context = browser.new_context(storage_state=str(SESSION) if SESSION.exists() else None)
                page = context.new_page()
                manifest = discover(page, context, args.url, args.headed)
                write_json(manifest_path, manifest)
                save_session(context)
            exporter = Exporter(args.output, manifest, browser, page, not args.no_videos)
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
            print(f'PDF: {args.output / "course.pdf"}\nOffline HTML: {args.output / "index.html"}\nCoverage report: {args.output / "report.json"}')
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError, PlaywrightTimeout) as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        sys.exit(1)
