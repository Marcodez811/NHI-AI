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

from app.services.slides.artifacts import clean_candidate_deck

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


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


if __name__ == "__main__":
    unittest.main()
