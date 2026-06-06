"""
XSLT engine: one long-lived Saxon processor per process that renders a dapytains
passage (an lxml subtree) to HTML or plain text via the stylesheets in xslt/.

The stylesheets are robust to receiving only *part* of a node (an excerpt), so
whatever dapytains hands us renders without assuming a whole <TEI>/<teiHeader>.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree
from saxonche import PySaxonProcessor

# Repository root is the parent of this package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent
XSLT_DIR = ROOT_DIR / "xslt"
HTML_XSLT = XSLT_DIR / "tei-to-html.xsl"
TEXT_XSLT = XSLT_DIR / "tei-to-text.xsl"
CSS_FILE = XSLT_DIR / "tei.css"


class Renderer:
    """Compiles the two stylesheets once and transforms passages on demand."""

    def __init__(self) -> None:
        self._proc = PySaxonProcessor(license=False)
        xslt = self._proc.new_xslt30_processor()
        self._html = xslt.compile_stylesheet(stylesheet_file=str(HTML_XSLT))
        self._text = xslt.compile_stylesheet(stylesheet_file=str(TEXT_XSLT))

    def render(self, passage: etree._Element, mode: str = "html") -> str:
        """Transform an lxml passage element to HTML (mode='html') or text."""
        xml = etree.tostring(passage, encoding="unicode")
        node = self._proc.parse_xml(xml_text=xml)
        executable = self._html if mode == "html" else self._text
        out = executable.transform_to_string(xdm_node=node)
        return out if out is not None else ""
