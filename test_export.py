"""Local integration tests: no NetAcad login or network requests needed."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from netacad_pdf import Exporter, merge_pdfs, transcript


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=os.environ["CHROMIUM_PATH"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_nested_tabs_tables_graphics_pdf_and_offline_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"content_base": "https://example.invalid/courses/content/", "language": "en-US", "modules": [{"number": 1}]}
            exporter = Exporter(root, manifest, self.browser)
            exporter.number = 1
            exporter.module_base = manifest["content_base"] + "m1/"
            image = root / "assets" / "sample.png"
            Image.new("RGB", (80, 50), "blue").save(image)
            exporter.cache[exporter.asset_url("en-US/assets/sample.png")] = "assets/sample.png"
            graphic = {"_id": "nested", "_component": "graphic", "_graphic": {"src": "en-US/assets/sample.png"}, "body": "Nested diagram explanation"}
            exporter.components = {"nested": graphic}
            tabs = {"_id": "tabs", "_component": "tabs", "body": "<p>Intro</p>", "_items": [
                {"tabTitle": "First panel", "body": "First hidden panel"},
                {"tabTitle": "Second panel", "body": "Second hidden panel", "component": {"modelid": "nested"}}]}
            table = {"_id": "table", "_component": "table", "_rows": [{"_cells": [{"text": "Destination", "_isHeading": True}, {"text": "192.0.2.1"}]}]}
            markup = exporter.component(tabs) + exporter.component(table)
            markup += exporter.markup('<a href="{{baseOrigin}}/resources">Resources</a>')
            self.assertIn("https://www.netacad.com/resources", markup)
            self.assertEqual(markup.count("Nested diagram explanation"), 1)
            self.assertEqual(exporter.component(graphic), "")
            self.assertNotIn("example.invalid", markup)
            path = root / "test.html"
            path.write_text(exporter.document("Fixture", markup))
            exporter.pdf(path, root / "test.pdf")
            reader = PdfReader(root / "test.pdf")
            text = "\n".join(p.extract_text() for p in reader.pages)
            for expected in ("First hidden panel", "Second hidden panel", "Nested diagram", "192.0.2.1"):
                self.assertIn(expected, text)
            self.assertGreater(sum(len(p.images) for p in reader.pages), 0)
            self.assertEqual(exporter.issues, [])
            merge_pdfs([{"pdf": str(root / "test.pdf"), "title": "Module one"},
                        {"pdf": str(root / "test.pdf"), "title": "Lab handout"}], root / "merged.pdf")
            merged = PdfReader(root / "merged.pdf")
            self.assertEqual(len(merged.pages), 2 * len(reader.pages))
            self.assertEqual(len(merged.outline), 2)
            exporter.render_page.close()

    def test_caption_metadata_and_identifiers_are_not_spoken_text(self):
        text = transcript("WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\ncue-123\n00:00.000 --> 00:01.000\n1024\n\n2\n00:01.000 --> 00:02.000\nports\n")
        self.assertEqual(text, "<p>1024 ports</p>")


if __name__ == "__main__":
    unittest.main()
