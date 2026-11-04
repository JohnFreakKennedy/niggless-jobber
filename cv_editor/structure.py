"""Parse a LaTeX CV into the text units that tailoring may edit, and write them back.

Only these parts of a CV are ever edited; everything else (preamble, header,
headings, dates, education) is copied byte for byte:

- the summary, between ``% @summary-begin`` and ``% @summary-end``;
- every ``\\resumeItem{...}`` bullet, grouped by the ``\\resumeSubheading``
  (or ``\\resumeSubheadingSimple``) entry above it;
- prose paragraphs under a ``\\resumeSubheadingSimple`` entry that has no
  bullets (older templates);
- every ``\\skillline{Category}{a, b, c}`` row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SUMMARY_RE = re.compile(
    r"^[ \t]*%[ \t]*@summary-begin[ \t]*\n(.*?)\n?[ \t]*%[ \t]*@summary-end",
    re.MULTILINE | re.DOTALL,
)
_HEADING_RE = re.compile(r"\\resumeSubheading(?:Simple)?(?![A-Za-z])")
_ITEM_RE = re.compile(r"\\resumeItem(?![A-Za-z])")
_SKILL_RE = re.compile(r"\\skillline(?![A-Za-z])")
_BLOCK_END_RE = re.compile(
    r"\\resumeSubheading|\\section|\\end\{document\}|\\resumeSubHeadingListEnd"
)


@dataclass
class Unit:
    id: str
    kind: str  # "summary" | "bullet" | "skills"
    start: int  # span in the source that render() replaces
    end: int
    text: str
    role_id: str | None = None
    category: str = ""
    items: list[str] = field(default_factory=list)


@dataclass
class Role:
    id: str
    company: str
    title: str
    bullet_ids: list[str] = field(default_factory=list)
    # False when bullets carry a separate title (\resumeItem{Title}{Text}):
    # moving only the text would put it under the wrong title.
    reorderable: bool = True


@dataclass
class CVStructure:
    source: str
    units: dict[str, Unit] = field(default_factory=dict)
    roles: list[Role] = field(default_factory=list)
    summary_id: str | None = None
    skill_ids: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.units


def _read_group(text: str, i: int) -> int:
    """*text[i]* is ``{``; return the index just past its matching ``}``, or -1."""
    depth = 0
    j = i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _read_groups(text: str, pos: int, n: int) -> list[tuple[int, int]] | None:
    """Read *n* consecutive ``{...}`` groups starting at *pos* (whitespace allowed between)."""
    spans = []
    for _ in range(n):
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            return None
        end = _read_group(text, pos)
        if end == -1:
            return None
        spans.append((pos + 1, end - 1))
        pos = end
    return spans


def _is_commented(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    return bool(re.search(r"(?<!\\)%", text[line_start:pos]))


def _body_start(text: str) -> int:
    idx = text.find("\\begin{document}")
    return idx + len("\\begin{document}") if idx != -1 else 0


def _split_items(items: str) -> list[str]:
    """Split on commas outside (), [] and {}: "AWS (EC2, S3), GCP" is two items."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(items):
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth = max(depth - 1, 0)
        elif ch == "," and depth == 0:
            out.append(items[start:i])
            start = i + 1
    out.append(items[start:])
    return [s.strip() for s in out if s.strip()]


def parse_cv(tex: str) -> CVStructure:
    cv = CVStructure(source=tex)
    body = _body_start(tex)

    m = _SUMMARY_RE.search(tex, body)
    if m and m.group(1).strip():
        cv.summary_id = "summary"
        cv.units["summary"] = Unit("summary", "summary", m.start(1), m.end(1), m.group(1).strip())

    headings: list[tuple[int, int, Role, bool]] = []  # (start, end, role, is_simple)
    for hm in _HEADING_RE.finditer(tex, body):
        if _is_commented(tex, hm.start()):
            continue
        groups = _read_groups(tex, hm.end(), 4)
        if not groups:
            continue
        role = Role(
            id=f"r{len(headings) + 1}",
            company=tex[groups[0][0]:groups[0][1]].strip(),
            title=tex[groups[2][0]:groups[2][1]].strip(),
        )
        is_simple = hm.group(0).endswith("Simple")
        headings.append((hm.start(), groups[3][1] + 1, role, is_simple))

    def role_at(pos: int) -> Role | None:
        owner = None
        for start, _end, role, _simple in headings:
            if start < pos:
                owner = role
        return owner

    used_roles: dict[str, Role] = {}
    general = Role(id="r0", company="", title="")

    def add_bullet(start: int, end: int, role: Role | None, reorderable: bool = True) -> None:
        role = role or general
        role.reorderable = role.reorderable and reorderable
        uid = f"b{sum(1 for u in cv.units.values() if u.kind == 'bullet') + 1}"
        cv.units[uid] = Unit(uid, "bullet", start, end, tex[start:end].strip(), role_id=role.id)
        role.bullet_ids.append(uid)
        used_roles[role.id] = role

    for im in _ITEM_RE.finditer(tex, body):
        if _is_commented(tex, im.start()):
            continue
        groups = _read_groups(tex, im.end(), 1)
        if not groups:
            continue
        # Older two-argument form \resumeItem{Title}{Text}: edit the text.
        second = _read_groups(tex, groups[0][1] + 1, 1)
        span = second[0] if second else groups[0]
        if tex[span[0]:span[1]].strip():
            add_bullet(span[0], span[1], role_at(im.start()), reorderable=not second)

    # Prose paragraphs under \resumeSubheadingSimple entries without bullets.
    for _start, h_end, role, is_simple in headings:
        if not is_simple or role.bullet_ids:
            continue
        nxt = _BLOCK_END_RE.search(tex, h_end)
        block_end = nxt.start() if nxt else len(tex)
        pos = h_end
        para_start = para_end = None
        for line in tex[h_end:block_end].splitlines(keepends=True):
            stripped = line.strip()
            is_prose = stripped and not stripped.startswith(("\\", "%", "{", "}"))
            if is_prose:
                if para_start is None:
                    para_start = pos + (len(line) - len(line.lstrip()))
                para_end = pos + len(line.rstrip())
            elif para_start is not None:
                add_bullet(para_start, para_end, role)
                para_start = None
            pos += len(line)
        if para_start is not None:
            add_bullet(para_start, para_end, role)

    cv.roles = [r for r in [general] + [h[2] for h in headings] if r.id in used_roles]

    for sm in _SKILL_RE.finditer(tex, body):
        if _is_commented(tex, sm.start()):
            continue
        groups = _read_groups(tex, sm.end(), 2)
        if not groups:
            continue
        uid = f"k{len(cv.skill_ids) + 1}"
        category = tex[groups[0][0]:groups[0][1]].strip()
        items_raw = tex[groups[1][0]:groups[1][1]]
        cv.units[uid] = Unit(
            uid, "skills", sm.start(), groups[1][1] + 1, items_raw.strip(),
            category=category, items=_split_items(items_raw),
        )
        cv.skill_ids.append(uid)

    return cv


def to_prompt_payload(cv: CVStructure) -> dict:
    """The CV as the JSON object the tailoring prompt shows the model."""
    payload: dict = {}
    if cv.summary_id:
        payload["summary"] = cv.units[cv.summary_id].text
    payload["roles"] = [
        {
            "role_id": r.id,
            "company": r.company,
            "title": r.title,
            "bullets": [{"id": b, "text": cv.units[b].text} for b in r.bullet_ids],
        }
        for r in cv.roles
    ]
    payload["skills"] = [
        {"id": k, "category": cv.units[k].category, "items": cv.units[k].items}
        for k in cv.skill_ids
    ]
    return payload


def plain_text(cv: CVStructure) -> str:
    """All editable text plus role titles and companies, for keyword matching."""
    parts = [r.company + " " + r.title for r in cv.roles]
    for u in cv.units.values():
        parts.append(u.category + ": " + ", ".join(u.items) if u.kind == "skills" else u.text)
    return "\n".join(parts)


def headline_text(cv: CVStructure, skill_lines: int = 2) -> str:
    """The parts a recruiter reads first: summary, first bullet per role, top skill lines."""
    parts = [cv.units[cv.summary_id].text] if cv.summary_id else []
    parts += [cv.units[r.bullet_ids[0]].text for r in cv.roles if r.bullet_ids]
    for uid in cv.skill_ids[:skill_lines]:
        parts.append(cv.units[uid].category + ": " + ", ".join(cv.units[uid].items))
    return "\n".join(parts)


def render(
    cv: CVStructure,
    texts: dict[str, str] | None = None,
    bullet_order: dict[str, list[str]] | None = None,
    skill_items: dict[str, list[str]] | None = None,
    skill_order: list[str] | None = None,
) -> str:
    """
    Write edits back into the source.

    *texts* maps summary/bullet ids to new text. *bullet_order* maps a role id
    to its bullet ids in the new order; *skill_order* lists skill ids in the new
    order; *skill_items* maps a skill id to its items in the new order. Orders
    are applied by putting unit X's content into unit Y's slot, so the LaTeX
    around each slot never moves. Callers validate orders as permutations.
    """
    texts = texts or {}
    skill_items = skill_items or {}
    slot_content: dict[str, str] = {}

    for uid, unit in cv.units.items():
        if unit.kind != "skills":
            slot_content[uid] = texts.get(uid, unit.text)

    for role in cv.roles:
        order = (bullet_order or {}).get(role.id)
        if order and role.reorderable:
            moved = {slot: slot_content[src] for slot, src in zip(role.bullet_ids, order)}
            slot_content.update(moved)

    order = skill_order or cv.skill_ids
    for slot, src in zip(cv.skill_ids, order):
        unit = cv.units[src]
        if src not in skill_items:
            slot_content[slot] = cv.source[unit.start:unit.end]
            continue
        slot_content[slot] = f"\\skillline{{{unit.category}}}{{{', '.join(skill_items[src])}}}"

    out = cv.source
    for uid in sorted(cv.units, key=lambda u: cv.units[u].start, reverse=True):
        unit = cv.units[uid]
        new = slot_content[uid]
        if unit.kind != "skills" and new == unit.text:
            continue
        if unit.kind == "skills" and new == cv.source[unit.start:unit.end]:
            continue
        out = out[: unit.start] + new + out[unit.end:]
    return out
