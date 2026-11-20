"""Parse Riftbound deck lists (the "Section:\\n<qty> <name>" export format)."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

LINE_RE = re.compile(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", re.I)


@dataclass
class Entry:
    name: str
    qty: int
    section: str

    def to_dict(self):
        return asdict(self)


def parse_deck(text: str) -> list[Entry]:
    """Return one Entry per (section, card). Unknown lines are ignored."""
    section = "MainDeck"
    out: dict[tuple[str, str], Entry] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if line.endswith(":") and not LINE_RE.match(line):
            section = line[:-1].strip()
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        qty, name = int(m.group(1)), m.group(2)
        key = (section, name.lower())
        if key in out:
            out[key].qty += qty
        else:
            out[key] = Entry(name=name, qty=qty, section=section)
    return list(out.values())


def is_rune_section(section: str) -> bool:
    return "rune" in section.lower()


def is_sideboard(section: str) -> bool:
    return "side" in section.lower()
