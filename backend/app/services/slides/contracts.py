"""Small shared contracts for the slides service implementation."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any


class JobError(RuntimeError):
    """A generation failure tagged with a stable pipeline stage."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

# ``xml.etree.ElementTree``'s own serializer invents ``ns0:``, ``ns1:``, ... prefixes for
# every namespace it has no name for, instead of the human-readable prefixes (``p:``,
# ``a:``, ``r:``, ``mc:``, ...) every real OOXML producer writes. Matching this exact
# generated-prefix pattern, rather than flagging any unusual prefix, is what lets both
# consumers below catch ad hoc package surgery without ever tripping on a normal deck.
GENERATED_NAMESPACE_PREFIX_RE = re.compile(rb"xmlns:ns\d+=")

# The only two package parts where an ElementTree-style rewrite is known to cause harm,
# each proven independently by rendering a known-good deck with just that one part
# rewritten:
# - ``[Content_Types].xml``: LibreOffice refuses to load the package at all (it produces
#   zero PDF pages on conversion), even though the rewritten XML is namespace-equivalent
#   and parses fine.
# - ``ppt/presentation.xml``: ``clean.py``'s slide-reference scan is a regex that
#   hardcodes the literal ``p:sldId``/``r:id`` OOXML prefixes; a rewritten
#   ``presentation.xml`` (``ns0:sldId``/``ns1:id``) makes that regex match nothing --
#   for the real slide list *and* for its own empty-package refusal check -- so it
#   silently deletes every slide instead of refusing.
# Every other part (individual slides, notes, `.rels` files) tolerates an ElementTree
# rewrite in both LibreOffice and ``clean.py``, so flagging them as blocking would reject
# working decks. ``artifacts.py``'s pre-cleanup guard and ``validation.py``'s
# deterministic-validator finding both key off this exact set so they cannot drift apart.
PACKAGE_XML_REWRITE_SENSITIVE_PARTS = ("[Content_Types].xml", "ppt/presentation.xml")
