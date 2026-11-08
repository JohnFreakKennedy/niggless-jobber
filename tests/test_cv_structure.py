"""Tests for cv_editor/structure.py: parsing the editable units of a CV and rendering edits back."""
from __future__ import annotations

from pathlib import Path

import pytest

from cv_editor.structure import parse_cv, plain_text, render, to_prompt_payload

_SAMPLE = Path(__file__).parent.parent / "templates" / "cv_sample.tex"


@pytest.fixture(scope="module")
def sample_tex() -> str:
    return _SAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sample_cv(sample_tex):
    return parse_cv(sample_tex)


def _doc(body: str, preamble: str = "") -> str:
    return "\\documentclass{article}\n" + preamble + "\\begin{document}\n" + body + "\n\\end{document}\n"


# ---------------------------------------------------------------------------
# Parsing the real sample template
# ---------------------------------------------------------------------------

class TestSampleTemplate:
    def test_unit_counts(self, sample_cv):
        kinds = [u.kind for u in sample_cv.units.values()]
        assert kinds.count("summary") == 1
        assert kinds.count("bullet") == 5
        assert kinds.count("skills") == 6
        assert not sample_cv.empty

    def test_summary_text(self, sample_cv):
        assert sample_cv.summary_id == "summary"
        text = sample_cv.units["summary"].text
        assert text.startswith("Software Engineer with 0 years of experience")
        assert text.endswith("clear documentation.")
        assert "@summary" not in text

    def test_roles_grouping(self, sample_cv):
        roles = sample_cv.roles
        assert [(r.id, r.company, r.title) for r in roles] == [
            ("r1", "Company Name", "Software Engineer"),
            ("r2", "Another Company", "Junior DevOps Engineer"),
        ]
        assert roles[0].bullet_ids == ["b1", "b2", "b3"]
        assert roles[1].bullet_ids == ["b4", "b5"]
        for r in roles:
            for b in r.bullet_ids:
                assert sample_cv.units[b].role_id == r.id

    def test_education_heading_without_bullets_is_not_a_role(self, sample_cv):
        assert all(r.company != "Your University" for r in sample_cv.roles)

    def test_preamble_definitions_are_not_units(self, sample_cv):
        # \newcommand{\resumeItem}[1]{\item\small{#1}} is in the preamble.
        assert all("#1" not in u.text for u in sample_cv.units.values())

    def test_bullet_text(self, sample_cv):
        assert sample_cv.units["b1"].text.startswith("Built and ran REST services in Python")
        assert sample_cv.units["b5"].text == "Maintained Docker images and GitLab CI pipelines for 12 services."

    def test_skills(self, sample_cv):
        cats = [(sample_cv.units[k].category, sample_cv.units[k].items) for k in sample_cv.skill_ids]
        assert sample_cv.skill_ids == ["k1", "k2", "k3", "k4", "k5", "k6"]
        assert cats[0] == ("Languages", ["Python", "Go", "Bash", "SQL"])
        assert cats[3] == ("CI/CD", ["GitHub Actions", "GitLab CI"])
        assert cats[5] == ("Spoken", ["English (C1)"])

    def test_spans_point_at_source(self, sample_cv, sample_tex):
        for u in sample_cv.units.values():
            if u.kind == "bullet":
                assert sample_tex[u.start:u.end].strip() == u.text
            elif u.kind == "skills":
                assert sample_tex[u.start:u.end].startswith("\\skillline{")
                assert sample_tex[u.end - 1] == "}"

    def test_prompt_payload_has_no_latex_preamble(self, sample_cv):
        payload = to_prompt_payload(sample_cv)
        assert set(payload) == {"summary", "roles", "skills"}
        assert [r["role_id"] for r in payload["roles"]] == ["r1", "r2"]
        assert payload["roles"][0]["bullets"][0]["id"] == "b1"
        assert payload["skills"][0] == {"id": "k1", "category": "Languages",
                                        "items": ["Python", "Go", "Bash", "SQL"]}
        assert "documentclass" not in repr(payload)

    def test_plain_text_includes_titles_and_skills(self, sample_cv):
        text = plain_text(sample_cv)
        assert "Junior DevOps Engineer" in text
        assert "Languages: Python, Go, Bash, SQL" in text
        assert "\\skillline" not in text


# ---------------------------------------------------------------------------
# Parsing edge cases
# ---------------------------------------------------------------------------

class TestParseEdgeCases:
    def test_commented_out_items_ignored(self):
        tex = _doc(
            "\\resumeSubheading{Co}{Loc}{Engineer}{2020}\n"
            "\\resumeItem{Kept bullet.}\n"
            "% \\resumeItem{Old commented bullet.}\n"
            "  %\\resumeItem{Another old one.}\n"
            "% \\skillline{Old}{A, B}\n"
            "\\skillline{Lang}{Python}\n"
            "% \\resumeSubheading{Ghost}{X}{Ghost Role}{2010}\n"
        )
        cv = parse_cv(tex)
        bullets = [u.text for u in cv.units.values() if u.kind == "bullet"]
        assert bullets == ["Kept bullet."]
        assert [cv.units[k].category for k in cv.skill_ids] == ["Lang"]
        assert [r.company for r in cv.roles] == ["Co"]

    def test_escaped_percent_is_not_a_comment(self):
        tex = _doc("\\resumeSubheading{Co}{L}{T}{D}\n"
                   "Cut cost 30\\% \\resumeItem{Inline after escaped percent.}\n")
        cv = parse_cv(tex)
        assert [u.text for u in cv.units.values() if u.kind == "bullet"] == ["Inline after escaped percent."]

    def test_nested_braces_inside_bullet(self):
        tex = _doc("\\resumeSubheading{Co}{L}{T}{D}\n"
                   "\\resumeItem{Built \\textbf{fast} \\emph{and {nested}} services.}\n"
                   "\\resumeItem{Second.}\n")
        cv = parse_cv(tex)
        assert cv.units["b1"].text == "Built \\textbf{fast} \\emph{and {nested}} services."
        assert cv.units["b2"].text == "Second."

    def test_escaped_braces_inside_bullet(self):
        tex = _doc("\\resumeSubheading{Co}{L}{T}{D}\n"
                   "\\resumeItem{Wrote \\{json\\} and \\} parsers.}\n"
                   "\\resumeItem{After.}\n")
        cv = parse_cv(tex)
        assert cv.units["b1"].text == "Wrote \\{json\\} and \\} parsers."
        assert cv.units["b2"].text == "After."

    def test_legacy_two_argument_resume_item(self):
        tex = _doc("\\resumeSubheading{Co}{L}{T}{D}\n"
                   "\\resumeItem{Automation}{Wrote Python tooling for deploys.}\n")
        cv = parse_cv(tex)
        bullets = [u for u in cv.units.values() if u.kind == "bullet"]
        assert len(bullets) == 1
        assert bullets[0].text == "Wrote Python tooling for deploys."
        out = render(cv, {bullets[0].id: "Wrote Python tools for deploys."})
        assert "\\resumeItem{Automation}{Wrote Python tools for deploys.}" in out

    def test_legacy_subheading_simple_prose(self):
        tex = _doc(
            "\\section{Experience}\n"
            "\\resumeSubheadingSimple{Old Co}{Remote}{Backend Developer}{2018 -- 2020}\n"
            "Built billing services in Java\n"
            "for the finance team.\n"
            "\n"
            "Ran the on-call rotation.\n"
            "\\vspace{2pt}\n"
            "\\resumeSubheadingSimple{Other Co}{Remote}{Intern}{2017}\n"
            "Wrote test scripts.\n"
            "\\section{Skills}\n"
            "\\skillline{Lang}{Java}\n"
        )
        cv = parse_cv(tex)
        assert [(r.company, r.title) for r in cv.roles] == [("Old Co", "Backend Developer"), ("Other Co", "Intern")]
        texts = [cv.units[b].text for b in cv.roles[0].bullet_ids]
        assert texts == ["Built billing services in Java\nfor the finance team.", "Ran the on-call rotation."]
        assert [cv.units[b].text for b in cv.roles[1].bullet_ids] == ["Wrote test scripts."]
        # A prose edit replaces just the paragraph.
        out = render(cv, {cv.roles[0].bullet_ids[1]: "Ran the weekly on-call rotation."})
        assert "\nRan the weekly on-call rotation.\n\\vspace{2pt}" in out
        assert "Built billing services in Java\nfor the finance team." in out

    def test_bullets_before_any_heading_go_to_general_role(self):
        tex = _doc("\\resumeItem{Orphan bullet.}\n")
        cv = parse_cv(tex)
        assert [r.id for r in cv.roles] == ["r0"]
        assert cv.roles[0].bullet_ids == ["b1"]

    def test_no_markers_gives_empty_structure(self):
        tex = _doc("\\section{Experience}\nSome text \\textbf{bold}.\n\\begin{itemize}\\item x\\end{itemize}\n")
        cv = parse_cv(tex)
        assert cv.empty
        assert cv.units == {} and cv.roles == [] and cv.skill_ids == [] and cv.summary_id is None
        assert render(cv) == tex

    def test_empty_summary_markers_are_ignored(self):
        tex = _doc("% @summary-begin\n\n% @summary-end\n")
        assert parse_cv(tex).summary_id is None

    def test_preamble_summary_markers_ignored(self):
        tex = _doc("Body only.", preamble="% @summary-begin\nPreamble prose\n% @summary-end\n")
        assert parse_cv(tex).summary_id is None

    def test_empty_bullet_skipped(self):
        tex = _doc("\\resumeSubheading{Co}{L}{T}{D}\n\\resumeItem{ }\n\\resumeItem{Real.}\n")
        cv = parse_cv(tex)
        assert [u.text for u in cv.units.values() if u.kind == "bullet"] == ["Real."]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _outside_spans(tex: str, spans: list[tuple[int, int]]) -> str:
    """Concatenate every part of *tex* that lies outside *spans*."""
    out, pos = [], 0
    for s, e in sorted(spans):
        out.append(tex[pos:s])
        pos = e
    out.append(tex[pos:])
    return "\x00".join(out)


class TestRender:
    def test_identity_without_edits(self, sample_cv, sample_tex):
        assert render(sample_cv) == sample_tex
        assert render(sample_cv, {}, {}, {}, None) == sample_tex
        assert render(sample_cv, skill_order=list(sample_cv.skill_ids)) == sample_tex

    def test_identity_when_texts_equal_originals(self, sample_cv, sample_tex):
        texts = {uid: u.text for uid, u in sample_cv.units.items() if u.kind != "skills"}
        assert render(sample_cv, texts) == sample_tex

    def test_text_edits_and_orders(self, sample_cv, sample_tex):
        out = render(
            sample_cv,
            texts={"summary": "New summary line.", "b2": "Moved deploys to Kubernetes."},
            bullet_order={"r1": ["b3", "b1", "b2"], "r2": ["b5", "b4"]},
            skill_items={"k1": ["Go", "Python", "SQL", "Bash"]},
            skill_order=["k3", "k1", "k2", "k4", "k5", "k6"],
        )
        cv2 = parse_cv(out)
        assert cv2.units["summary"].text == "New summary line."
        r1 = [cv2.units[b].text for b in cv2.roles[0].bullet_ids]
        assert r1[0].startswith("Set up Prometheus")
        assert r1[1].startswith("Built and ran REST")
        assert r1[2] == "Moved deploys to Kubernetes."
        r2 = [cv2.units[b].text for b in cv2.roles[1].bullet_ids]
        assert r2[0].startswith("Maintained Docker") and r2[1].startswith("Wrote Bash")
        lines = [(cv2.units[k].category, cv2.units[k].items) for k in cv2.skill_ids]
        assert lines[0] == ("Infrastructure", ["Terraform", "Docker", "Kubernetes"])
        assert lines[1] == ("Languages", ["Go", "Python", "SQL", "Bash"])
        assert [c for c, _ in lines[2:]] == ["Cloud", "CI/CD", "Observability", "Spoken"]
        # Bullets stay in their role.
        assert "Kubernetes" not in " ".join(r2)

    def test_only_targeted_spans_change(self, sample_cv, sample_tex):
        out = render(
            sample_cv,
            texts={"summary": "Short summary.", "b4": "Wrote Bash and Python tools for AWS."},
            bullet_order={"r1": ["b2", "b1", "b3"]},
            skill_items={"k2": ["GCP", "AWS"]},
            skill_order=["k2", "k1", "k3", "k4", "k5", "k6"],
        )
        cv2 = parse_cv(out)
        before = _outside_spans(sample_tex, [(u.start, u.end) for u in sample_cv.units.values()])
        after = _outside_spans(out, [(u.start, u.end) for u in cv2.units.values()])
        assert before == after
        # Preamble and header are byte-identical.
        head = sample_tex.index("% @summary-begin")
        assert out[:head] == sample_tex[:head]
        assert out.endswith(sample_tex[sample_tex.index("% --- Education"):])

    def test_unchanged_units_keep_exact_bytes(self, sample_cv, sample_tex):
        out = render(sample_cv, texts={"b1": "Built REST services in Python and PostgreSQL for 40k daily users."})
        assert out.count("\\resumeItem{") == sample_tex.count("\\resumeItem{")
        for uid in ("b2", "b3", "b4", "b5"):
            assert sample_cv.units[uid].text in out
        assert sample_cv.units["summary"].text in out

    def test_render_rebuilds_skill_line_with_category(self):
        tex = _doc("\\skillline{A}{x, y}\n\\skillline{B}{z}\n")
        cv = parse_cv(tex)
        out = render(cv, skill_order=["k2", "k1"])
        assert out.index("\\skillline{B}{z}") < out.index("\\skillline{A}{x, y}")

    def test_identity_for_skill_line_without_spaces(self):
        tex = _doc("\\skillline{Lang}{Python,Go}\n")
        cv = parse_cv(tex)
        assert render(cv) == tex
