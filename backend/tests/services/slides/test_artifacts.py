"""Tests for the backend-owned package-cleanup helper (Fix 1a/1c).

The author no longer runs ``clean.py`` itself; the backend calls it, deterministically,
after every author attempt through ``clean_candidate_deck``. These tests exercise that
wrapper against small, self-contained fixtures rather than a production artifact.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services.slides.artifacts import (
    DELIVERY_CJK_FONT,
    clean_candidate_deck,
    create_job_fontconfig,
    normalize_delivery_fonts,
)

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _write_deck_with_notes(deck: Path) -> None:
    """A minimal, valid OOXML package with one slide and its notes graph."""

    deck.parent.mkdir(parents=True, exist_ok=True)
    parts = {
        "[Content_Types].xml": f'''<?xml version="1.0"?><Types xmlns="{CT_NS}">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
        </Types>''',
        "ppt/presentation.xml": f'''<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">
          <p:sldIdLst><p:sldId id="1" r:id="rId1"/></p:sldIdLst>
        </p:presentation>''',
        "ppt/_rels/presentation.xml.rels": f'''<Relationships xmlns="{REL_NS}">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
        </Relationships>''',
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{P_NS}"><p:cSld><p:spTree/></p:cSld></p:sld>',
        "ppt/slides/_rels/slide1.xml.rels": f'''<Relationships xmlns="{REL_NS}">
          <Relationship Id="rIdNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
        </Relationships>''',
        "ppt/notesSlides/notesSlide1.xml": f'<p:notes xmlns:p="{P_NS}"><p:cSld><p:spTree/></p:cSld></p:notes>',
    }
    with zipfile.ZipFile(deck, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)


class CleanCandidateDeckTests(unittest.TestCase):
    def test_strips_the_notes_graph_from_a_real_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            deck = workspace / "output" / "presentation.pptx"
            _write_deck_with_notes(deck)

            removed = clean_candidate_deck(workspace)

            self.assertIn("ppt/notesSlides/notesSlide1.xml", removed)
            with zipfile.ZipFile(deck) as archive:
                names = archive.namelist()
            self.assertFalse(any(name.startswith("ppt/notesSlides/") for name in names))
            self.assertIn("ppt/slides/slide1.xml", names)

    def test_skips_cleanup_when_presentation_xml_looks_rewritten(self):
        """``clean.py``'s slide-reference regex hardcodes literal `p:sldId`/`r:id`
        prefixes; on a rewritten ``presentation.xml`` it matches nothing for the real
        slide list *and* for its own empty-package refusal check, so it silently deletes
        every slide instead of refusing. The backend must skip the cleaner rather than
        run it on a package it cannot safely parse, leaving the candidate untouched for
        the deterministic validator's ``package_xml_rewritten`` finding to report instead.
        """

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            deck = workspace / "output" / "presentation.pptx"
            _write_deck_with_notes(deck)
            with zipfile.ZipFile(deck) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            parts["ppt/presentation.xml"] = ET.tostring(
                ET.fromstring(parts["ppt/presentation.xml"]), encoding="utf-8"
            )
            with zipfile.ZipFile(deck, "w") as archive:
                for name, data in parts.items():
                    archive.writestr(name, data)

            removed = clean_candidate_deck(workspace)

            self.assertEqual(removed, [])
            with zipfile.ZipFile(deck) as archive:
                names = archive.namelist()
            self.assertIn("ppt/slides/slide1.xml", names)
            self.assertIn("ppt/notesSlides/notesSlide1.xml", names)

    def test_returns_empty_when_no_candidate_deck_exists_yet(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self.assertEqual(clean_candidate_deck(workspace), [])

    def test_also_normalizes_chart_and_theme_fonts(self):
        """Windows fonts task: cleanup forces East Asian chart/theme text onto the
        delivery font (Microsoft JhengHei) so PowerPoint never substitutes 新細明體.
        """

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            deck = workspace / "output" / "presentation.pptx"
            _write_deck_with_notes(deck)
            with zipfile.ZipFile(deck) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            parts["ppt/theme/theme1.xml"] = (
                f'<a:theme xmlns:a="{A_NS}" name="t"><a:themeElements><a:fontScheme name="fs">'
                f'<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
                f'<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="Kept As-Is"/><a:cs typeface=""/></a:minorFont>'
                f"</a:fontScheme></a:themeElements></a:theme>"
            ).encode("utf-8")
            # clean.py drops any theme part no relationship resolves to; reference it
            # from the slide's own .rels so cleanup leaves it in place to normalize.
            parts["ppt/slides/_rels/slide1.xml.rels"] = (
                parts["ppt/slides/_rels/slide1.xml.rels"]
                .decode("utf-8")
                .replace(
                    "</Relationships>",
                    '<Relationship Id="rIdTheme" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" '
                    'Target="../theme/theme1.xml"/></Relationships>',
                )
                .encode("utf-8")
            )
            with zipfile.ZipFile(deck, "w") as archive:
                for name, data in parts.items():
                    archive.writestr(name, data)

            clean_candidate_deck(workspace)

            with zipfile.ZipFile(deck) as archive:
                theme = archive.read("ppt/theme/theme1.xml")
            root = ET.fromstring(theme)
            a = f"{{{A_NS}}}"
            self.assertEqual(root.find(f".//{a}majorFont/{a}ea").get("typeface"), DELIVERY_CJK_FONT)
            self.assertEqual(root.find(f".//{a}minorFont/{a}ea").get("typeface"), "Kept As-Is")


class CreateJobFontconfigTests(unittest.TestCase):
    def test_aliases_delivery_font_to_the_detected_container_font(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets_root = root / "assets"
            (assets_root / "fonts").mkdir(parents=True)
            (assets_root / "fonts" / "NotoSansTC-Regular.ttf").write_bytes(b"font")
            windows_fonts_dir = root / "no-such-windows-fonts"

            config_path = create_job_fontconfig(
                root / "job",
                assets_root=assets_root,
                windows_fonts_dir=windows_fonts_dir,
                cjk_font="Noto Sans TC",
            )

            self.assertIsNotNone(config_path)
            text = config_path.read_text(encoding="utf-8")
            ET.fromstring(text)  # must remain well-formed XML
            self.assertIn(f"<family>{DELIVERY_CJK_FONT}</family>", text)
            self.assertIn("<family>微軟正黑體</family>", text)
            self.assertIn("<family>Noto Sans TC</family></accept>", text)

    def test_no_alias_when_the_detected_font_already_is_the_delivery_font(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets_root = root / "assets"
            (assets_root / "fonts").mkdir(parents=True)
            (assets_root / "fonts" / "NotoSansTC-Regular.ttf").write_bytes(b"font")
            windows_fonts_dir = root / "no-such-windows-fonts"

            config_path = create_job_fontconfig(
                root / "job",
                assets_root=assets_root,
                windows_fonts_dir=windows_fonts_dir,
                cjk_font=DELIVERY_CJK_FONT,
            )

            text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("<alias>", text)

    def test_no_config_without_cjk_font(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(
                create_job_fontconfig(
                    root / "job",
                    assets_root=root / "no-such-assets",
                    windows_fonts_dir=root / "no-such-windows-fonts",
                    cjk_font="Noto Sans TC",
                )
            )


class NormalizeDeliveryFontsTests(unittest.TestCase):
    def _write_theme_and_chart(self, deck: Path) -> None:
        deck.parent.mkdir(parents=True, exist_ok=True)
        theme_xml = (
            f'<a:theme xmlns:a="{A_NS}" name="t"><a:themeElements><a:fontScheme name="fs">'
            f'<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            f'<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="Already Set"/><a:cs typeface=""/></a:minorFont>'
            f"</a:fontScheme></a:themeElements></a:theme>"
        )
        chart_xml = (
            f'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="{A_NS}">'
            "<c:chart><c:plotArea><c:barChart><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr>"
            '<a:defRPr sz="1000"><a:latin typeface="Arial"/></a:defRPr>'
            "</a:pPr></a:p></c:txPr></c:barChart></c:plotArea></c:chart>"
            "<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:r>"
            '<a:rPr sz="1200"><a:latin typeface="Arial"/><a:ea typeface="Already Set"/></a:rPr>'
            "<a:t>x</a:t></a:r></a:p></c:txPr></c:chartSpace>"
        )
        with zipfile.ZipFile(deck, "w") as archive:
            archive.writestr("ppt/theme/theme1.xml", theme_xml)
            archive.writestr("ppt/charts/chart1.xml", chart_xml)

    def test_fills_empty_theme_ea_and_adds_missing_chart_ea(self):
        with tempfile.TemporaryDirectory() as temporary:
            deck = Path(temporary) / "presentation.pptx"
            self._write_theme_and_chart(deck)

            changed = normalize_delivery_fonts(deck, delivery_font=DELIVERY_CJK_FONT)

            self.assertEqual(sorted(changed), ["ppt/charts/chart1.xml", "ppt/theme/theme1.xml"])
            with zipfile.ZipFile(deck) as archive:
                theme_root = ET.fromstring(archive.read("ppt/theme/theme1.xml"))
                chart_root = ET.fromstring(archive.read("ppt/charts/chart1.xml"))
            a = f"{{{A_NS}}}"
            self.assertEqual(theme_root.find(f".//{a}majorFont/{a}ea").get("typeface"), DELIVERY_CJK_FONT)
            self.assertEqual(theme_root.find(f".//{a}minorFont/{a}ea").get("typeface"), "Already Set")
            self.assertEqual(chart_root.find(f".//{a}defRPr/{a}ea").get("typeface"), DELIVERY_CJK_FONT)
            rpr = chart_root.find(f".//{a}rPr")
            self.assertEqual(rpr.find(f"{a}ea").get("typeface"), "Already Set")
            # Schema order: latin, ea, cs, ... -- ea must immediately follow latin.
            defrpr = chart_root.find(f".//{a}defRPr")
            self.assertEqual([child.tag for child in defrpr], [f"{a}latin", f"{a}ea"])

    def test_no_changes_when_everything_already_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            deck = Path(temporary) / "presentation.pptx"
            a = f"{{{A_NS}}}"
            theme_xml = (
                f'<a:theme xmlns:a="{A_NS}" name="t"><a:themeElements><a:fontScheme name="fs">'
                f'<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface="Filled"/><a:cs typeface=""/></a:majorFont>'
                f"</a:fontScheme></a:themeElements></a:theme>"
            )
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("ppt/theme/theme1.xml", theme_xml)
            before = deck.read_bytes()

            changed = normalize_delivery_fonts(deck, delivery_font=DELIVERY_CJK_FONT)

            self.assertEqual(changed, [])
            self.assertEqual(deck.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
