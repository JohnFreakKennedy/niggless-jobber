"""End-to-end tests of tailor_cv with a mocked AI client and the sample template."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cv_editor.client import AIClient, AIConfig
from cv_editor.editor import tailor_cv
from cv_editor.structure import parse_cv
from parser.schema import JobListing

_SAMPLE = Path(__file__).parent.parent / "templates" / "cv_sample.tex"
_EXTRA_BULLET = "    \\resumeItem{Cut CI build time by 40\\% by caching Docker layers.}\n"

B1 = "Built and ran REST services in Python and PostgreSQL serving 40k daily users."
B2 = ("Moved deployments to Kubernetes with Terraform and GitHub Actions, cutting release time "
      "from 2 hours to 15 minutes.")
B3 = "Set up Prometheus and Grafana dashboards and alerts for the payments services."
B5 = "Cut CI build time by 40\\% by caching Docker layers."

_INFO = {"work_history": [{"start": "2021-01", "end": "2026-01", "title": "Site Reliability Engineer"}]}

JD = {
    "required_skills": ["Kubernetes", "Terraform", "AWS", "Kafka"],
    "preferred_skills": ["Grafana"],
    "seniority_level": "Senior",
    "responsibilities": ["run Kubernetes clusters", "build CI/CD pipelines"],
    "keywords": ["payments"],
}

GOOD_EDITS = {
    "summary": "Software Engineer with 5 years of experience running Kubernetes, Terraform and AWS "
               "infrastructure. Comfortable owning a service from design to production on-call.",
    "bullets": {"b3": "Set up Prometheus and Grafana dashboards and alerts for the payments services on Kubernetes."},
    "bullet_order": {"r1": ["b2", "b3", "b1"]},
    "skills": {"k3": ["Kubernetes", "Terraform", "Docker"]},
    "skill_order": ["k3", "k2", "k1", "k4", "k5", "k6"],
}


def _template() -> str:
    """The sample template plus one bullet with an escaped percent sign in the second role."""
    tex = _SAMPLE.read_text(encoding="utf-8")
    anchor = "    \\resumeItem{Maintained Docker images"
    assert anchor in tex
    return tex.replace(anchor, _EXTRA_BULLET + anchor)


def _listing() -> JobListing:
    return JobListing(
        source="test", title="Platform Engineer", company="Fictional Widgets",
        url="https://example.com/jobs/7", apply_url="https://example.com/jobs/7/apply",
        description="We run Kubernetes, Terraform and AWS with Kafka. 3+ years of experience.",
        skills=["Terraform", "Kafka"],
    )


def _client(*responses) -> AsyncMock:
    client = AsyncMock(spec=AIClient)
    client.complete = AsyncMock(side_effect=[json.dumps(r) if isinstance(r, dict) else r for r in responses])
    client.config = AIConfig()
    return client


async def _run(tmp_path, client, info=_INFO):
    base = tmp_path / "base_cv.tex"
    base.write_text(_template(), encoding="utf-8")
    with patch("cv_editor.editor._resolve_base_cv", return_value=base), \
         patch("cv_editor.editor._load_personal_info", return_value=info):
        path = await tailor_cv(_listing(), tmp_path / "out", client)
    report = json.loads(path.with_name(path.stem + "_tailoring.json").read_text(encoding="utf-8"))
    return path, path.read_text(encoding="utf-8"), report


def _bullets(content: str, role_index: int) -> list[str]:
    cv = parse_cv(content)
    return [cv.units[b].text for b in cv.roles[role_index].bullet_ids]


def _skill_lines(content: str) -> list[tuple[str, list[str]]]:
    cv = parse_cv(content)
    return [(cv.units[k].category, cv.units[k].items) for k in cv.skill_ids]


def _user_messages(client) -> list[str]:
    return [c.args[1] for c in client.complete.await_args_list]


def _rejected_ids(report) -> list[str]:
    return [r["id"] for r in report["rejected"]]


# ---------------------------------------------------------------------------
# A good edit set
# ---------------------------------------------------------------------------

class TestGoodEdits:
    async def test_edits_applied(self, tmp_path):
        path, content, report = await _run(tmp_path, _client(JD, GOOD_EDITS))
        assert path.name == "Fictional_Widgets_cv.tex"
        assert GOOD_EDITS["summary"] in content
        assert _bullets(content, 0) == [
            B2, "Set up Prometheus and Grafana dashboards and alerts for the payments services on Kubernetes.", B1,
        ]
        lines = _skill_lines(content)
        assert lines[0] == ("Infrastructure", ["Kubernetes", "Terraform", "Docker"])
        assert [c for c, _ in lines] == ["Infrastructure", "Cloud", "Languages", "CI/CD", "Observability", "Spoken"]
        assert "\\newcommand{\\jobtitle}{Platform Engineer}" in content
        assert report["rejected"] == []

    async def test_report(self, tmp_path):
        _, _, report = await _run(tmp_path, _client(JD, GOOD_EDITS))
        for key in ("status", "jd", "gaps", "coverage_before", "coverage_after", "rejected", "edited_units",
                    "reordered_roles", "reordered_skill_lines", "skill_order_changed", "ai_edits"):
            assert key in report, key
        assert report["status"] == "tailored"
        assert report["job"] == {"title": "Platform Engineer", "company": "Fictional Widgets"}
        assert report["jd"]["required_skills"] == JD["required_skills"]
        assert report["jd"]["seniority_level"] == "senior"
        assert report["gaps"] == ["Kafka"]
        assert report["coverage_before"]["missing"] == ["Kafka"]
        assert report["coverage_before"]["score"] == 0.8
        assert report["coverage_after"]["found"] == ["Kubernetes", "Terraform", "AWS", "Grafana"]
        assert report["edited_units"] == ["b3", "summary"]
        assert report["reordered_roles"] == ["r1"]
        assert "k3" in report["reordered_skill_lines"]
        assert report["skill_order_changed"] is True
        assert report["ai_edits"] == GOOD_EDITS

    async def test_untouched_parts_byte_identical(self, tmp_path):
        _, content, _ = await _run(tmp_path, _client(JD, GOOD_EDITS))
        tpl = _template()
        cut = tpl.index("\\section{Summary}")
        expected_head = tpl[:cut].replace("{YOUR JOB TITLE}", "{Platform Engineer}")
        assert content[:cut + len("Platform Engineer") - len("YOUR JOB TITLE")] == expected_head
        assert content.endswith(tpl[tpl.index("% --- Education"):])

    async def test_empty_edit_set_still_reorders_skills_only(self, tmp_path):
        _, content, report = await _run(tmp_path, _client(JD, {}))
        assert report["status"] == "tailored"
        assert report["edited_units"] == []
        assert _bullets(content, 0) == [B1, B2, B3]
        assert _skill_lines(content)[0][0] == "Infrastructure"


# ---------------------------------------------------------------------------
# Rejected edits
# ---------------------------------------------------------------------------

class TestRejections:
    async def test_hallucinated_skill_rejected(self, tmp_path):
        edits = {"bullets": {
            "b1": "Built and ran REST services in Python, PostgreSQL and Kafka serving 40k daily users.",
            "b3": GOOD_EDITS["bullets"]["b3"],
        }}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert B1 in content
        assert "Kafka" not in content
        [rej] = [r for r in report["rejected"] if r["id"] == "b1"]
        assert "kafka" in rej["reason"]
        # The other edit in the same set is kept.
        assert GOOD_EDITS["bullets"]["b3"] in content
        assert report["edited_units"] == ["b3"]

    async def test_hallucinated_skill_in_summary_sentence_dropped(self, tmp_path):
        edits = {"summary": "Software Engineer with 5 years of experience building backend services on "
                            "Kubernetes. Built Kafka streaming pipelines for payments. Comfortable owning a "
                            "service from design to production on-call."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Kafka" not in content
        assert parse_cv(content).units["summary"].text == (
            "Software Engineer with 5 years of experience building backend services on Kubernetes. "
            "Comfortable owning a service from design to production on-call.")
        [rej] = report["rejected"]
        assert rej["id"] == "summary" and "kafka" in rej["reason"] and "dropped 1 sentence" in rej["reason"]

    async def test_salvaged_summary_too_short_is_rejected(self, tmp_path):
        edits = {"summary": "Software Engineer with 5 years of experience. Built Kafka pipelines."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Kafka" not in content
        assert "Software Engineer with 5 years of experience building backend services and" in content
        assert _rejected_ids(report) == ["summary"]

    async def test_facts_swapped_within_role_rejected(self, tmp_path):
        edits = {"bullets": {"b1": B2, "b2": B1}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert _bullets(content, 0) == [B1, B2, B3]
        assert set(_rejected_ids(report)) == {"b1", "b2"}
        assert all("keeps only" in r["reason"] or "numbers" in r["reason"] for r in report["rejected"])

    async def test_facts_moved_across_roles_rejected(self, tmp_path):
        edits = {"bullets": {"b4": "Wrote Bash and Python tooling to automate AWS account setup on Kubernetes."}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Wrote Bash and Python tooling to automate AWS account setup." in content
        [rej] = report["rejected"]
        assert rej["id"] == "b4" and "this role" in rej["reason"] and "kubernetes" in rej["reason"]

    async def test_invented_number_rejected(self, tmp_path):
        edits = {"bullets": {"b1": "Built and ran REST services in Python and PostgreSQL serving 90k daily users."}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert B1 in content and "90k" not in content
        assert "adds numbers" in report["rejected"][0]["reason"]

    async def test_unknown_ids_and_bad_shapes_ignored(self, tmp_path):
        edits = {"bullets": {"b99": "x", "summary": "y", "k1": "z"}, "bullet_order": ["b1"],
                 "skills": {"k1": ["Python", "Rust"]}, "skill_order": ["k1", "k1"]}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Rust" not in content
        assert set(_rejected_ids(report)) == {"k1", "skill_order"}
        # Rejected skill parts fall back to the deterministic order.
        assert _skill_lines(content)[0][0] == "Infrastructure"


# ---------------------------------------------------------------------------
# Seniority salvage
# ---------------------------------------------------------------------------

class TestSenioritySalvage:
    @pytest.mark.parametrize("word", ["Senior", "Lead", "staff", "Principal"])
    async def test_leading_seniority_word_stripped(self, tmp_path, word):
        edits = {"summary": f"{word} Software Engineer with 5 years of experience running Kubernetes and "
                            "Terraform. Comfortable owning a service from design to production on-call."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Software Engineer with 5 years of experience running Kubernetes and Terraform." in content
        assert word not in parse_cv(content).units["summary"].text
        assert "summary" not in _rejected_ids(report)

    async def test_seniority_sentence_dropped_rest_kept(self, tmp_path):
        edits = {"summary": "Senior Software Engineer with 5 years of experience running Kubernetes and "
                            "Terraform. Expert in AWS and incident response. Comfortable owning a service "
                            "from design to production on-call."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        summary = parse_cv(content).units["summary"].text
        assert summary == ("Software Engineer with 5 years of experience running Kubernetes and Terraform. "
                           "Comfortable owning a service from design to production on-call.")
        [rej] = report["rejected"]
        assert rej["id"] == "summary" and "dropped 1 sentence" in rej["reason"] and "expert" in rej["reason"]
        assert report["edited_units"] == ["summary"]

    async def test_bad_opening_sentence_rejects_whole_summary(self, tmp_path):
        edits = {"summary": "Engineering manager with 5 years of experience. Comfortable owning a service."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "Software Engineer with 5 years of experience building backend services" in content
        assert "manager" not in content
        assert "summary" in _rejected_ids(report)

    async def test_summary_cannot_take_numbers_from_preamble(self, tmp_path):
        edits = {"summary": "Software Engineer with 5 years of experience running 11 production services "
                            "on Kubernetes and Terraform."}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "11 production services" not in content
        assert "summary" in _rejected_ids(report)


# ---------------------------------------------------------------------------
# AI failures
# ---------------------------------------------------------------------------

class TestAIFailures:
    async def test_invalid_json_then_valid_retry(self, tmp_path):
        client = _client(JD, "Sure! Here are my edits: summary should be...", GOOD_EDITS)
        _, content, report = await _run(tmp_path, client)
        assert client.complete.await_count == 3
        first, retry = _user_messages(client)[1:]
        assert retry.startswith(first)
        assert "not a valid JSON object" in retry
        assert report["status"] == "tailored"
        assert GOOD_EDITS["summary"] in content

    async def test_invalid_json_twice(self, tmp_path):
        client = _client(JD, "not json", "[still not an object]")
        path, content, report = await _run(tmp_path, client)
        assert client.complete.await_count == 3
        assert path.exists()
        assert report["status"].startswith("ai failed (ValueError)")
        assert report["ai_edits"] == {}
        assert report["edited_units"] == [] and report["rejected"] == []
        # Skills are still put in job order, deterministically.
        lines = _skill_lines(content)
        assert [c for c, _ in lines] == ["Infrastructure", "Cloud", "Observability", "Languages", "CI/CD", "Spoken"]
        assert lines[0] == ("Infrastructure", ["Terraform", "Kubernetes", "Docker"])
        assert report["skill_order_changed"] is True
        assert report["reordered_skill_lines"] == ["k3", "k5"]
        assert lines[2] == ("Observability", ["Grafana", "Prometheus"])
        assert _bullets(content, 0) == [B1, B2, B3]
        assert "\\newcommand{\\jobtitle}{Platform Engineer}" in content
        assert "5 years of experience" in content

    async def test_invalid_json_twice_is_deterministic(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        _, first, _ = await _run(tmp_path / "a", _client(JD, "x", "y"))
        _, second, _ = await _run(tmp_path / "b", _client(JD, "x", "y"))
        assert first == second

    @pytest.mark.parametrize("jd_response", ["no json here", "[1, 2]", RuntimeError("extract down")])
    async def test_jd_extraction_failure_falls_back_to_listing_skills(self, tmp_path, jd_response):
        client = _client(jd_response, {})
        _, content, report = await _run(tmp_path, client)
        assert report["jd"]["required_skills"] == []
        assert report["jd"]["seniority_level"] == "unknown"
        assert report["gaps"] == ["Kafka"]
        assert report["coverage_before"]["found"] == ["Terraform"]
        assert "Required skills: Terraform, Kafka" in _user_messages(client)[1]
        assert report["status"] == "tailored"
        assert _skill_lines(content)[0][0] == "Infrastructure"

    async def test_ai_exception(self, tmp_path):
        client = _client(JD, RuntimeError("provider down"))
        path, content, report = await _run(tmp_path, client)
        assert path.exists()
        assert report["status"].startswith("ai failed (RuntimeError)")
        assert _skill_lines(content)[0][0] == "Infrastructure"
        assert "\\newcommand{\\jobtitle}{Platform Engineer}" in content


# ---------------------------------------------------------------------------
# Orders, escaping
# ---------------------------------------------------------------------------

class TestOrdersAndEscaping:
    async def test_partial_bullet_order_completed(self, tmp_path):
        edits = {"bullet_order": {"r1": ["b3"], "r2": ["b6", "b4"]}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert _bullets(content, 0) == [B3, B1, B2]
        assert _bullets(content, 1)[0] == "Maintained Docker images and GitLab CI pipelines for 12 services."
        assert report["reordered_roles"] == ["r1", "r2"]

    async def test_bullet_order_with_foreign_id_rejected(self, tmp_path):
        edits = {"bullet_order": {"r1": ["b4", "b1"], "r2": "b5"}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert _bullets(content, 0) == [B1, B2, B3]
        assert _bullets(content, 1)[0] == B5
        assert _rejected_ids(report) == ["r1"]
        assert report["reordered_roles"] == ["r2"]

    async def test_latex_specials_escaped(self, tmp_path):
        edits = {"bullets": {"b5": "Cut CI pipeline build time by 40% by caching Docker layers for R&D."}}
        _, content, report = await _run(tmp_path, _client(JD, edits))
        assert "\\resumeItem{Cut CI pipeline build time by 40\\% by caching Docker layers for R\\&D.}" in content
        assert "40%" not in content.replace("40\\%", "")
        assert report["edited_units"] == ["b5"]

    async def test_escaped_text_from_ai_not_double_escaped(self, tmp_path):
        edits = {"bullets": {"b5": "Cut CI pipeline build time by 40\\% by caching Docker layers."}}
        _, content, _ = await _run(tmp_path, _client(JD, edits))
        assert "by 40\\% by caching" in content
        assert "40\\\\%" not in content


# ---------------------------------------------------------------------------
# What the AI is sent
# ---------------------------------------------------------------------------

class TestPrompt:
    async def test_no_latex_preamble_sent(self, tmp_path):
        client = _client(JD, GOOD_EDITS)
        await _run(tmp_path, client)
        for msg in _user_messages(client):
            assert "\\documentclass" not in msg
            assert "\\usepackage" not in msg
            assert "\\newcommand" not in msg
            assert "\\resumeItem" not in msg
            assert "\\skillline" not in msg
            assert "your.email@example.com" not in msg

    async def test_prompt_contains_gaps_title_and_cv_json(self, tmp_path):
        client = _client(JD, GOOD_EDITS)
        await _run(tmp_path, client)
        msg = _user_messages(client)[1]
        assert "Gaps (required by the job, NOT in the CV; never mention them): Kafka" in msg
        assert "Current title: Site Reliability Engineer" in msg
        assert "Years of experience: 5" in msg
        assert "Title: Platform Engineer" in msg
        assert "Seniority: senior" in msg
        assert '"id": "b1"' in msg and '"role_id": "r1"' in msg
        assert B1 in msg

    async def test_candidate_title_falls_back_to_first_role(self, tmp_path):
        client = _client(JD, {})
        await _run(tmp_path, client, info={"work_history": [{"start": "2021-01", "end": "2026-01"}]})
        assert "Current title: Software Engineer" in _user_messages(client)[1]

    async def test_jd_prompt_contains_description(self, tmp_path):
        client = _client(JD, {})
        await _run(tmp_path, client)
        assert "We run Kubernetes, Terraform and AWS with Kafka." in _user_messages(client)[0]


# ---------------------------------------------------------------------------
# Real compile
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("xelatex") is None, reason="xelatex not installed")
async def test_tailored_cv_compiles_to_one_page(tmp_path):
    from cover_letter.compiler import compile_pdf, page_count

    edits = dict(GOOD_EDITS)
    edits["bullets"] = dict(GOOD_EDITS["bullets"],
                            b5="Cut CI pipeline build time by 40% by caching Docker layers for R&D.")
    path, _, report = await _run(tmp_path, _client(JD, edits))
    assert set(report["edited_units"]) == {"summary", "b3", "b5"}
    pdf = compile_pdf(path, fit_one_page=True)
    assert pdf.exists()
    assert page_count(path) == 1


class TestSummaryYearsClaim:
    async def test_inflated_years_in_summary_rejected(self):
        from cv_editor.editor import _check_summary
        from cv_editor.structure import parse_cv, plain_text
        from cv_editor.tailoring import build_vocabulary
        cv = parse_cv(Path("templates/cv_sample.tex").read_text())
        rejected = []
        out = _check_summary(
            cv.units["summary"].text,
            "Software Engineer with 12+ years building cloud platforms on AWS and GCP for teams.",
            cv, plain_text(cv), build_vocabulary([]), "3",
            lambda uid, reason: rejected.append(reason),
        )
        assert out is None
        assert "12" in rejected[0]
