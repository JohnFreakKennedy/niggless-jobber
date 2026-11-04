"""tailor_cv always shows listing.title and the computed years, whatever the AI returns."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from cv_editor.client import AIClient, AIConfig
from cv_editor.editor import tailor_cv
from parser.schema import JobListing

# Fictional template in the cv_sample.tex format.
_TEMPLATE = r"""\documentclass[a4paper,11pt]{article}
\newcommand{\resumeSubheading}[4]{\item \textbf{#1} #2 \textit{#3} #4}
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newcommand{\skillline}[2]{\textbf{#1} & #2 \\}
% Old title kept for reference: \newcommand{\jobtitle}{Commented Title}
% \newcommand{\jobtitle}{Commented Title}
\newcommand{\jobtitle}{Placeholder Title}

\begin{document}
{\LARGE Sample Person}\\ {\large \jobtitle}

\section{Summary}
% @summary-begin
Backend Developer with 3 years of experience running web services on Kubernetes
and Terraform for small product teams.
% @summary-end

\section{Experience}
\begin{itemize}
  \resumeSubheading{Example Labs}{Remote}{Backend Developer}{2021 -- 2026}
  \begin{itemize}
    \resumeItem{Ran Kubernetes clusters for 10 services with Terraform modules.}
    \resumeItem{Wrote Python jobs that clean billing data every night.}
  \end{itemize}
\end{itemize}

\section{Skills}
\begin{tabular}{ll}
  \skillline{Infrastructure}{Terraform, Kubernetes}
  \skillline{Languages}{Python, Bash}
\end{tabular}
\end{document}
"""

# An older template: subtitle on the second row of a header tabular, nothing tailorable.
_LEGACY_TEMPLATE = r"""\documentclass{article}
\begin{document}
\begin{tabular*}{\textwidth}{l@{\extracolsep{\fill}}r}
  \textbf{\huge Sample Person} & Email: person@example.com\\
    Software Engineer & { +1 555 000 0000} Linkedin
\end{tabular*}

Sample is a Software Engineer with 9 years of experience in cloud infrastructure.
\end{document}
"""

_WORK_HISTORY = {"work_history": [{"start": "2021-01", "end": "2026-01", "title": "Backend Developer"}]}

_JD = json.dumps({
    "required_skills": ["Kubernetes", "Terraform"],
    "preferred_skills": [],
    "seniority_level": "mid",
    "responsibilities": ["run clusters"],
    "keywords": [],
})


def _listing(title: str = "Platform Engineer", company: str = "Sample Co") -> JobListing:
    return JobListing(
        source="test", title=title, company=company,
        url="https://example.com/jobs/1", apply_url="https://example.com/jobs/1/apply",
        description="We run Kubernetes and Terraform. 5 years of experience required.",
        skills=["Kubernetes", "Terraform"],
    )


def _client(*responses) -> AsyncMock:
    client = AsyncMock(spec=AIClient)
    client.complete = AsyncMock(side_effect=list(responses))
    client.config = AIConfig()
    return client


async def _run(tmp_path, listing, client, template=_TEMPLATE):
    base = tmp_path / "base_cv.tex"
    base.write_text(template, encoding="utf-8")
    out_dir = tmp_path / "out"
    with patch("cv_editor.editor._resolve_base_cv", return_value=base), \
         patch("cv_editor.editor._load_personal_info", return_value=_WORK_HISTORY):
        path = await tailor_cv(listing, out_dir, client)
    return path, path.read_text(encoding="utf-8")


class TestCallContract:
    async def test_complete_called_with_json_mode(self, tmp_path):
        client = _client(_JD, json.dumps({}))
        await _run(tmp_path, _listing(), client)
        assert client.complete.await_count == 2
        for call in client.complete.await_args_list:
            system, user = call.args
            assert isinstance(system, str) and isinstance(user, str)
            assert call.kwargs["json_mode"] is True
            assert isinstance(call.kwargs["max_tokens"], int)
        assert client.complete.await_args_list[1].kwargs["max_tokens"] == AIConfig().cv_max_tokens


class TestPostPatchJobTitle:
    async def test_title_set_from_listing(self, tmp_path):
        path, content = await _run(tmp_path, _listing("Platform Engineer"), _client(_JD, "{}"))
        assert path.name == "Sample_Co_cv.tex"
        assert "\\newcommand{\\jobtitle}{Platform Engineer}" in content
        assert "Placeholder Title" not in content

    async def test_comment_lines_untouched(self, tmp_path):
        _, content = await _run(tmp_path, _listing("SRE"), _client(_JD, "{}"))
        assert "% \\newcommand{\\jobtitle}{Commented Title}" in content
        assert "% Old title kept for reference: \\newcommand{\\jobtitle}{Commented Title}" in content
        assert content.count("\\newcommand{\\jobtitle}{SRE}") == 1

    async def test_ai_cannot_change_title(self, tmp_path):
        edits = {
            "summary": "Staff Platform Engineer with 5 years of experience running web services on "
                       "Kubernetes and Terraform for small product teams.",
            "bullets": {
                "b1": "\\renewcommand{\\jobtitle}{Senior Anything} Ran Kubernetes clusters for 10 services.",
                "b2": "Wrote Python jobs that clean billing data every night for Jit Team.",
            },
        }
        _, content = await _run(tmp_path, _listing("SRE"), _client(_JD, json.dumps(edits)))
        assert "\\newcommand{\\jobtitle}{SRE}" in content
        assert "Senior Anything" not in content
        assert "\\renewcommand" not in content
        assert "Staff" not in content
        # The rest of the edit set still applies.
        assert "Platform Engineer with 5 years of experience" in content

    async def test_title_latex_escaped(self, tmp_path):
        _, content = await _run(tmp_path, _listing("C# & .NET Developer_2 {x}"), _client(_JD, "{}"))
        assert "\\newcommand{\\jobtitle}{C\\# \\& .NET Developer\\_2 x}" in content

    async def test_legacy_tabular_title_and_years_without_ai(self, tmp_path):
        client = _client()
        _, content = await _run(tmp_path, _listing("Cloud Engineer"), client, template=_LEGACY_TEMPLATE)
        assert "\n    Cloud Engineer" in content
        assert "    Software Engineer &" not in content
        assert "5 years of experience" in content
        assert "9 years" not in content
        client.complete.assert_not_awaited()


class TestPostPatchYears:
    async def test_template_years_corrected(self, tmp_path):
        _, content = await _run(tmp_path, _listing(), _client(_JD, "{}"))
        assert "5 years of experience" in content
        assert "3 years of experience" not in content

    async def test_ai_wrong_years_corrected(self, tmp_path):
        # "10" exists elsewhere in the CV, so the summary passes the number check and
        # only the deterministic post-patch can correct it.
        edits = {"summary": "Backend Developer with 10 years of experience running 10 web services on "
                            "Kubernetes and Terraform for small product teams."}
        path, content = await _run(tmp_path, _listing(), _client(_JD, json.dumps(edits)))
        assert "10 years of experience" not in content
        # A summary claiming the wrong years is discarded; the original stays,
        # with the computed years.
        assert "5 years of experience" in content
        report = json.loads(path.with_name("Sample_Co_cv_tailoring.json").read_text())
        assert report["edited_units"] == []
        assert any("claims 10 years" in r["reason"] for r in report["rejected"])

    @pytest.mark.parametrize("phrase", ["over 12 years of experience", "more than 7 years of experience",
                                        "8+ years of experience"])
    async def test_ai_years_variants_corrected(self, tmp_path, phrase):
        edits = {"bullets": {"b1": f"Ran Kubernetes clusters for 10 services with Terraform modules, {phrase}."}}
        _, content = await _run(tmp_path, _listing(), _client(_JD, json.dumps(edits)))
        assert phrase not in content
