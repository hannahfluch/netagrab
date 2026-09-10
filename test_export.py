"""Local integration tests: no NetAcad login or network requests needed."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import io
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from netacad_pdf import Exporter, merge_pdfs, transcript, to_markdown, parse_args, output_directory, login, load_session


class InputTest(unittest.TestCase):
    URL = 'https://www.netacad.com/launch?id=example-course&tab=curriculum'

    def test_url_argument_flag_and_stdin(self):
        with patch('builtins.input') as prompt:
            self.assertEqual(parse_args([self.URL, '--format', 'markdown']).url, self.URL)
            self.assertEqual(parse_args(['--url', self.URL]).url, self.URL)
            self.assertIsNone(parse_args(['--offline']).url)
            prompt.assert_not_called()
        with patch('sys.stdin', io.StringIO('  ' + self.URL + '  \n')), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(parse_args([]).url, self.URL)
            self.assertIn('NetAcad course URL:', out.getvalue())

    def test_bad_or_missing_input_is_rejected(self):
        for argv in [['--url', 'https://example.org/launch?id=x'], [self.URL, '--url', self.URL], ['--url', '']]:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(argv)
        with patch('sys.stdin', io.StringIO('')), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args([])

    def test_other_courses_do_not_reuse_cached_content(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'course-manifest.json').write_text(json.dumps({'url': self.URL}))
            args = parse_args([self.URL, '--output', str(out)])
            self.assertEqual(output_directory(args), out)
            args.url = 'https://www.netacad.com/launch?id=different-course'
            with self.assertRaisesRegex(ValueError, 'another course'):
                output_directory(args)
            args.output = None
            with patch('netacad_pdf.Path', side_effect=lambda value: out if value == 'output' else Path(value)), redirect_stdout(io.StringIO()):
                separate = output_directory(args)
            self.assertEqual(separate.parent, out)
            self.assertTrue(separate.name.startswith('course-'))

    def test_missing_or_corrupt_session_starts_fresh(self):
        with tempfile.TemporaryDirectory() as temp, patch('netacad_pdf.SESSION', Path(temp) / 'auth.json'):
            self.assertIsNone(load_session())
            Path(temp, 'auth.json').write_text('not json')
            with redirect_stdout(io.StringIO()):
                self.assertIsNone(load_session())


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=os.environ["CHROMIUM_PATH"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_login_prompts_after_redirect_and_reuses_authenticated_page(self):
        course = 'https://www.netacad.com/launch?id=test-course'
        with tempfile.TemporaryDirectory() as temp, patch('netacad_pdf.SESSION', Path(temp) / 'auth.json'):
            context = self.browser.new_context()
            def respond(route):
                if 'auth.netacad.com' in route.request.url:
                    route.fulfill(content_type='text/html', body='<iframe src="about:blank"></iframe>'
                        '<input id="username"><input id="password" type="password">'
                        '<button id="kc-login" onclick="location.href=\'' + course + '&signedin=1\'">Login</button>')
                elif 'signedin=1' in route.request.url:
                    route.fulfill(content_type='text/html', body='<div id="course-outline">My course</div>')
                else:
                    route.fulfill(content_type='text/html', body='<script>location.replace("https://auth.netacad.com/login")</script>')
            context.route('https://**/*', respond)
            page = context.new_page()
            with patch.dict(os.environ, {}, clear=True), patch('builtins.input', return_value='student@example.invalid') as email, patch('getpass.getpass', return_value='test-password') as password:
                login(page, context, course)
                email.assert_called_once()
                password.assert_called_once()
            saved = Path(temp, 'auth.json')
            self.assertTrue(saved.exists())
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('test-password', saved.read_text())
            with patch('builtins.input') as email, patch('getpass.getpass') as password:
                login(page, context, course + '&signedin=1')
                email.assert_not_called()
                password.assert_not_called()
            context.close()

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

    def test_markdown_preserves_tables_code_images_and_external_links(self):
        md = to_markdown('<style>unwanted css</style><h2>Routing</h2>'
                         '<pre>Router&gt; enable\n  show ip route</pre>'
                         '<table><tr><th>Network</th><th>Port</th></tr><tr><td>192.0.2.0/24</td><td>Gi0/1</td></tr></table>'
                         '<img src="assets/router.svg" alt="Router connected to two hosts">'
                         '<div class="dynamic-text-item">LAN one</div>'
                         '<a href="https://example.com/reference.html">Reference</a>')
        self.assertIn('## Routing', md)
        self.assertIn('```', md)
        self.assertIn('Router> enable\n  show ip route', md)
        self.assertIn('| Network | Port |', md)
        self.assertIn('![Router connected to two hosts](assets/router.svg)', md)
        self.assertIn('LAN one', md)
        self.assertIn('https://example.com/reference.html', md)
        self.assertNotIn('unwanted css', md)

    def test_format_selection_structure_and_lab_text(self):
        for formats in [('markdown',), ('json',), ('pdf', 'markdown', 'json')]:
            with self.subTest(formats=formats), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                module = {'number': 1, 'title': 'Module 1: Routing'}
                manifest = {'url': 'https://example.invalid/course', 'content_base': 'https://example.invalid/courses/content/',
                            'language': 'en-US', 'modules': [module]}
                exporter = Exporter(root, manifest, self.browser if 'pdf' in formats else None, formats=formats)
                source = root / 'source' / 'm1'
                source.mkdir(parents=True)
                data = {'course': {}, 'assets': [],
                        'contentObjects': [{'_id': 'section-1', '_parentId': 'course', '_type': 'page', 'title': '1.1 Routing'}],
                        'articles': [{'_id': 'article-1', '_parentId': 'section-1', '_type': 'article'}],
                        'blocks': [{'_id': 'block-1', '_parentId': 'article-1', '_type': 'block'}],
                        'components': [{'_id': 'component-1', '_parentId': 'block-1', '_type': 'component', '_component': 'text',
                                        'body': '<p>Packets follow the routing table.</p>'}]}
                for name, value in data.items():
                    (source / f'{name}.json').write_text(json.dumps(value))
                def local_only(url, path):
                    self.assertTrue(path.exists(), 'Unexpected fetch: ' + url)
                    return path
                exporter.fetch = local_only
                lab = self.browser.new_page()
                lab.set_content('<h1>Routing lab</h1><p>Configure a static route to 192.0.2.0/24.</p>')
                lab.pdf(path=str(root / 'assets/lab.pdf'))
                lab.close()
                exporter.attachments['assets/lab.pdf'] = 'Routing lab'
                exporter.module(module)
                exporter.finish()
                for fmt, suffix in [('pdf', 'pdf'), ('markdown', 'md'), ('json', 'json')]:
                    self.assertEqual((root / f'course.{suffix}').exists(), fmt in formats)
                    self.assertEqual((root / f'module-01.{suffix}').exists(), fmt in formats)
                if 'markdown' in formats:
                    md = (root / 'course.md').read_text()
                    self.assertIn('Packets follow the routing table.', md)
                    self.assertIn('Configure a static route', md)
                    self.assertIn('(assets/lab.pdf)', md)
                if 'json' in formats:
                    course = json.loads((root / 'course.json').read_text())
                    section = course['modules'][0]['sections'][0]
                    self.assertEqual(section['id'], 'section-1')
                    self.assertEqual(section['component_ids'], ['component-1'])
                    self.assertIn('Packets follow', section['markdown'])
                    self.assertIn('Configure a static route', course['attachments'][0]['pages'][0]['text'])
                    self.assertEqual(course['schema_version'], 1)
                self.assertEqual(exporter.issues, [])
                if exporter.render_page:
                    exporter.render_page.close()


if __name__ == "__main__":
    unittest.main()
