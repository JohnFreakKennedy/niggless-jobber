"""End-to-end tests for cover letter generation with a mocked AI client.

All personal data here is fictional.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cover_letter import generator
from cover_letter.generator import (
    _ACCEPT_MAX,
    _ACCEPT_MIN,
    _WORD_MAX,
    _WORD_MIN,
    _assemble,
    _clean_body,
    _cv_facts,
    _problems,
    _word_count,
    _years,
    generate_cover_letter,
)
from cv_editor.client import AIConfig
from cv_editor.editor import _display_years, calculate_experience_years
from parser.schema import JobListing

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

CV_TEX = r"""% Fictional CV for tests
\documentclass[a4paper,11pt]{article}
\usepackage[a4paper,left=0.6in,right=0.6in,top=0.5in,bottom=0.5in]{geometry}
\usepackage{enumitem}
\usepackage{tabularx}
\usepackage[hidelinks]{hyperref}
\newcommand{\resumeSubheading}[4]{\item \textbf{#1} #2 \textit{#3} #4}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
\newcommand{\skillline}[2]{\textbf{#1} & #2 \\}
\newcommand{\jobtitle}{Platform Engineer}

\begin{document}
\begin{center}
  {\LARGE\bfseries Jane Sample}\\
  \href{mailto:jane@example.com}{jane@example.com} $\cdot$ +1 555 000 0000
\end{center}

\section{Summary}
% @summary-begin
Platform Engineer with 6 years of experience building backend services and
cloud infrastructure.
% @summary-end

\section{Experience}
\resumeSubHeadingListStart
  \resumeSubheading{Samplecorp}{Remote}{Senior Platform Engineer}{Jan 2022 -- Present}
  \resumeItemListStart
    \resumeItem{Built REST services in \textbf{Python} and PostgreSQL serving 40k daily users.}
    \resumeItem{Cut p95 API latency by 35\% by adding query caching.}
    \resumeItem{Moved deployments to Kubernetes with Terraform, cutting release time from 2 hours to 15 minutes.}
  \resumeItemListEnd
  \resumeSubheading{Example Labs}{Remote}{DevOps Engineer}{Jun 2019 -- Dec 2021}
  \resumeItemListStart
    \resumeItem{Maintained Docker images and GitLab CI pipelines for 12 services.}
    \resumeItem{Set up Prometheus and Grafana alerts for the billing services.}
  \resumeItemListEnd
\resumeSubHeadingListEnd

\section{Skills}
\begin{tabularx}{\textwidth}{lX}
  \skillline{Languages}{Python, Bash, SQL}
  \skillline{Infrastructure}{Terraform, Docker, Kubernetes}
  \skillline{Observability}{Prometheus, Grafana}
\end{tabularx}
\end{document}
"""

WORK_HISTORY = [
    {"title": "Senior Platform Engineer", "company": "Samplecorp", "start": "2022-01", "end": "present"},
    {"title": "DevOps Engineer", "company": "Example Labs", "start": "2019-06", "end": "2021-12"},
]

INFO = {
    "name": {"full": "Jane Sample"},
    "email": "jane@example.com",
    "phone": "+1 555 000 0000",
    "address": {"city": "Springfield", "country": "Exampleland"},
    "summary": "Platform engineer focused on reliable backend systems.",
    "skills": {"languages": ["Python", "Bash"], "infra": ["Terraform", "Kubernetes"]},
    "work_history": WORK_HISTORY,
}

JD = (
    "Acme Widgets is hiring a Platform Engineer to run our Kubernetes platform. "
    "You will write Python services, manage Terraform modules and own alerting. "
    "Our team of 8 engineers ships to production daily. Experience with Kafka is a plus."
)

PARA_1 = (
    "This role needs someone who can run Kubernetes in production, write Python "
    "services and keep infrastructure in Terraform. At Samplecorp I built REST "
    "services in Python and PostgreSQL that serve 40k daily users, and I moved our "
    "deployments to Kubernetes with Terraform. That change cut release time from 2 "
    "hours to 15 minutes, which let the team ship small changes several times a day "
    "instead of batching them into risky weekly releases."
)
PARA_2 = (
    "The work closest to your main responsibility was latency. Our main API had "
    "become slow under load and customers noticed it during peak hours. I profiled "
    "the slowest endpoints, found repeated queries against PostgreSQL and added a "
    "query caching layer in front of them. The p95 latency dropped by 35% and stayed "
    "there through the next traffic peak. Before that, at Example Labs, I maintained "
    "Docker images and GitLab CI pipelines for 12 services and set up Prometheus and "
    "Grafana alerts for the billing services, so I am used to owning both the build "
    "path and the alerts that tell you when something breaks."
)
PARA_3 = (
    "Your ad describes a small team that owns its platform end to end and ships to "
    "production every day, which is the way I like to work and the kind of ownership "
    "I have had in my last two roles. I would be glad to talk about how I could help "
    "run and improve your Kubernetes platform, and I am available for a call at any "
    "time that suits you."
)
GOOD_BODY = "\n\n".join([PARA_1, PARA_2, PARA_3])

KAFKA_SENTENCE = "I also ran Kafka clusters for event streaming."
NUMBER_SENTENCE = "I reduced cloud costs by 73% in one quarter."


def _listing(**kw) -> JobListing:
    defaults = dict(
        source="test", title="Platform Engineer", company="Acme Widgets",
        url="https://example.com/job", apply_url="https://example.com/apply",
        description=JD, skills=["Python", "Kubernetes", "Terraform", "Kafka"],
    )
    defaults.update(kw)
    return JobListing(**defaults)


def _client(*responses: str) -> SimpleNamespace:
    complete = AsyncMock(side_effect=list(responses))
    return SimpleNamespace(config=AIConfig(), complete=complete)


@pytest.fixture()
def cv_path(tmp_path):
    path = tmp_path / "cv.tex"
    path.write_text(CV_TEX, encoding="utf-8")
    return path


@pytest.fixture()
def env(monkeypatch, cv_path):
    """Patch personal info and the base CV; return a holder to tweak them."""
    state = SimpleNamespace(info=dict(INFO), cv_path=cv_path)
    monkeypatch.setattr(generator, "_load_personal_info", lambda: state.info)

    def resolve():
        if state.cv_path is None:
            raise FileNotFoundError("No CV template found.")
        return state.cv_path

    monkeypatch.setattr("cv_editor.editor._resolve_base_cv", resolve)
    return state


def _run(listing, out_dir, client):
    return asyncio.run(generate_cover_letter(listing, out_dir, client))


def _prompt(client, call: int = 0) -> tuple[str, str]:
    args = client.complete.await_args_list[call].args
    return args[0], args[1]


def _expected_years() -> str:
    return _display_years(calculate_experience_years(WORK_HISTORY))


# ---------------------------------------------------------------------------
# Sanity: the fixture body really is acceptable
# ---------------------------------------------------------------------------

def test_good_body_fixture_is_clean(env):
    facts, _ = _cv_facts(env.info)
    assert _ACCEPT_MIN <= _word_count(GOOD_BODY) <= _ACCEPT_MAX
    assert _problems(GOOD_BODY, facts, _listing(), _years(env.info)) == []


# ---------------------------------------------------------------------------
# Prompt contents
# ---------------------------------------------------------------------------

class TestPrompt:
    def test_prompt_has_cv_bullets_as_plain_text(self, env, tmp_path):
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        _, user = _prompt(client)
        assert "Cut p95 API latency by 35% by adding query caching." in user
        assert "35\\%" not in user
        assert "Built REST services in Python and PostgreSQL serving 40k daily users." in user
        assert "\\textbf" not in user
        assert "Maintained Docker images and GitLab CI pipelines for 12 services." in user
        assert "Skills (Infrastructure): Terraform, Docker, Kubernetes" in user
        assert "Summary: Platform Engineer with 7 years of experience" in user  # years patched like the CV

    def test_prompt_has_title_years_and_jd(self, env, tmp_path):
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        _, user = _prompt(client)
        assert "Current title: Senior Platform Engineer" in user
        assert f"Years of experience: {_expected_years()}" in user
        assert JD in user
        assert "Company: Acme Widgets" in user
        assert "Role: Platform Engineer" in user
        assert "Name: Jane Sample" in user

    def test_prompt_has_no_latex_preamble(self, env, tmp_path):
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        system, user = _prompt(client)
        for marker in ("\\documentclass", "\\usepackage", "\\newcommand",
                       "\\begin{document}", "\\resumeItem", "\\skillline", "@summary-begin"):
            assert marker not in user
            assert marker not in system

    def test_current_title_falls_back_to_cv_role(self, env, tmp_path):
        env.info = {**INFO, "work_history": []}
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        _, user = _prompt(client)
        assert "Current title: Senior Platform Engineer" in user

    def test_system_prompt_has_word_limits_filled(self, env, tmp_path):
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        system, _ = _prompt(client)
        assert "{word_min}" not in system and "{word_max}" not in system
        assert f"{_WORD_MIN}-{_WORD_MAX} words" in system

    def test_max_tokens_from_config(self, env, tmp_path):
        client = _client(GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_args.kwargs["max_tokens"] == AIConfig().cover_letter_max_tokens


# ---------------------------------------------------------------------------
# Years
# ---------------------------------------------------------------------------

class TestYears:
    def test_years_match_calculate_experience_years(self):
        assert _years(INFO) == str(round(calculate_experience_years(WORK_HISTORY)))

    def test_years_fallback_to_static_field(self):
        assert _years({"years_of_experience": 7}) == "7"
        assert _years({}) == ""

    def test_years_number_is_allowed_in_body(self, env):
        facts, _ = _cv_facts(env.info)
        years = _years(env.info)
        body = GOOD_BODY + f" I have {years} years of hands-on work behind me."
        assert not any("numbers" in p for p in _problems(body, facts, _listing(), years))


# ---------------------------------------------------------------------------
# Body checks and retry flow
# ---------------------------------------------------------------------------

def _body_in_tex(tex: str) -> str:
    start = tex.index("position at") if "position at" in tex else tex.index("position.")
    end = tex.index("Best regards")
    return tex[start:end]


class TestWriteBody:
    def test_good_body_accepted_with_one_call(self, env, tmp_path):
        client = _client(GOOD_BODY)
        out = _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 1
        assert out.exists() and out.suffix == ".tex"
        tex = out.read_text(encoding="utf-8")
        assert "query caching layer" in tex
        assert "35\\%" in tex

    def test_invented_skill_triggers_retry_naming_it(self, env, tmp_path):
        client = _client(GOOD_BODY + " " + KAFKA_SENTENCE, GOOD_BODY)
        out = _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 2
        _, retry = _prompt(client, 1)
        assert "rejected" in retry
        assert re.search(r"not in the CV facts: .*kafka", retry, re.IGNORECASE)
        assert KAFKA_SENTENCE in retry  # previous letter is shown
        assert "Kafka" not in _body_in_tex(out.read_text(encoding="utf-8"))

    def test_invented_number_triggers_retry(self, env, tmp_path):
        client = _client(GOOD_BODY + " " + NUMBER_SENTENCE, GOOD_BODY)
        out = _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 2
        _, retry = _prompt(client, 1)
        assert re.search(r"numbers that are not in the CV facts: 73", retry)
        assert "73" not in _body_in_tex(out.read_text(encoding="utf-8"))

    def test_number_from_jd_is_allowed(self, env, tmp_path):
        body = GOOD_BODY + " I have worked in teams of 8 engineers before."
        client = _client(body)
        _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 1

    def test_too_short_triggers_retry(self, env, tmp_path):
        client = _client("I would like this job. Python is my main language.", GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 2
        _, retry = _prompt(client, 1)
        assert f"write {_WORD_MIN}-{_WORD_MAX}" in retry

    def test_too_long_triggers_retry(self, env, tmp_path):
        long_body = "\n\n".join([PARA_1, PARA_2, PARA_3, PARA_2, PARA_1])
        assert _word_count(long_body) > _ACCEPT_MAX
        client = _client(long_body, GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 2
        _, retry = _prompt(client, 1)
        assert f"it has {_word_count(long_body)} words" in retry

    def test_still_bad_after_retry_drops_offending_sentences(self, env, tmp_path):
        bad = PARA_1 + " " + KAFKA_SENTENCE + "\n\n" + PARA_2 + " " + NUMBER_SENTENCE + "\n\n" + PARA_3
        client = _client(bad, bad)
        out = _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 2
        body = _body_in_tex(out.read_text(encoding="utf-8"))
        assert "Kafka" not in body
        assert "73" not in body
        assert "query caching layer" in body
        assert "40k daily users" in body
        assert "available for a call" in body


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

class TestCleanBody:
    def test_greeting_and_signoff_removed(self):
        raw = "Dear Hiring Team,\n\n" + GOOD_BODY + "\n\nSincerely,\nJane Sample"
        cleaned = _clean_body(raw)
        assert not cleaned.startswith("Dear")
        assert "Sincerely" not in cleaned
        assert cleaned.endswith("suits you.")

    def test_best_regards_removed(self):
        cleaned = _clean_body(GOOD_BODY + "\n\nBest regards,\nJane")
        assert "regards" not in cleaned.lower()

    def test_markdown_bold_and_heading_removed(self):
        cleaned = _clean_body("# Cover letter\n\nI built **Python** services and __Terraform__ modules.")
        assert "**" not in cleaned and "__" not in cleaned and "#" not in cleaned
        assert "I built Python services and Terraform modules." in cleaned

    def test_code_fences_removed(self):
        cleaned = _clean_body("```text\n" + GOOD_BODY + "\n```")
        assert "```" not in cleaned
        assert cleaned.startswith("This role needs")

    def test_bullets_as_separate_paragraphs_removed(self):
        cleaned = _clean_body("- I built Python services.\n\n* I ran Kubernetes.")
        assert cleaned == "I built Python services.\n\nI ran Kubernetes."

    def test_bullet_list_in_one_paragraph_removed(self):
        cleaned = _clean_body("- I built Python services.\n- I ran Kubernetes.\n* I wrote Terraform.")
        assert " - " not in cleaned and " * " not in cleaned

    def test_latex_formatting_stripped(self):
        cleaned = _clean_body(r"I built \textbf{Python} services and \emph{Terraform} modules.")
        assert cleaned == "I built Python services and Terraform modules."

    def test_em_dash_and_cliches_cleaned(self):
        cleaned = _clean_body("I leveraged Terraform — and utilized Kubernetes.")
        assert "—" not in cleaned
        assert "leveraged" not in cleaned and "utilized" not in cleaned
        assert "used Terraform" in cleaned

    def test_cleaning_applies_in_full_flow(self, env, tmp_path):
        raw = "```\nDear Hiring Team,\n\n" + GOOD_BODY.replace("Python", "**Python**", 1) + "\n\nBest regards,\nJane Sample\n```"
        client = _client(raw)
        out = _run(_listing(), tmp_path / "out", client)
        assert client.complete.await_count == 1
        body = _body_in_tex(out.read_text(encoding="utf-8"))
        assert "**" not in body and "```" not in body
        assert body.count("Dear Hiring Team") == 0
        assert body.count("Best regards") == 0


# ---------------------------------------------------------------------------
# Assembly and escaping
# ---------------------------------------------------------------------------

class TestAssemble:
    def test_body_specials_escaped(self):
        tex = _assemble("Cut costs 35% & sped up the build_step job.", _listing(), INFO)
        assert "35\\% \\& sped up the build\\_step job." in tex

    def test_body_dollar_escaped(self):
        tex = _assemble("It saved $5 per run.", _listing(), INFO)
        assert "saved \\$5 per run." in tex

    def test_already_escaped_body_not_double_escaped(self):
        tex = _assemble("Cut latency by 35\\% \\& more.", _listing(), INFO)
        assert "35\\% \\& more." in tex
        assert "\\\\%" not in tex

    def test_company_with_ampersand_escaped(self):
        tex = _assemble("Body.", _listing(company="R&D Labs"), INFO)
        assert "R\\&D Labs" in tex
        assert re.search(r"(?<!\\)&D", tex) is None

    def test_title_name_address_escaped(self):
        info = {**INFO, "name": {"full": "Jane_Sample & Co"},
                "address": {"city": "Spring_field", "country": "Example & Land"}}
        tex = _assemble("Body.", _listing(title="C# & Go_Dev"), info)
        assert "C\\# \\& Go\\_Dev" in tex
        assert "Jane\\_Sample \\& Co" in tex
        assert "Spring\\_field, Example \\& Land" in tex

    def test_contact_and_placeholders_filled(self):
        tex = _assemble("Body text.", _listing(), INFO)
        assert "jane@example.com" in tex
        assert "+1 555 000 0000" in tex
        assert "Springfield, Exampleland" in tex
        assert "%%" not in tex.split("\\begin{document}", 1)[1]

    def test_empty_company_has_no_dangling_parts(self):
        tex = _assemble("Body.", _listing(company=""), INFO)
        doc = tex.split("\\begin{document}", 1)[1]
        assert " at ." not in doc
        assert "\\textbf{}" not in doc
        assert "%%COMPANY%%" not in doc
        assert "Hiring Team \\\\" not in doc
        assert re.search(r"Hiring Team\s*\\\\", doc) is None
        assert "position." in doc

    def test_location_like_company_omitted(self):
        tex = _assemble("Body.", _listing(company="Poland (Remote)"), INFO)
        doc = tex.split("\\begin{document}", 1)[1]
        assert "Poland" not in doc
        assert " at ." not in doc
        assert re.search(r"Hiring Team\s*\\\\", doc) is None

    def test_real_company_kept(self):
        tex = _assemble("Body.", _listing(company="Acme Widgets"), INFO)
        assert "position at \\textbf{Acme Widgets}." in tex
        assert re.search(r"Hiring Team \\\\\s*Acme Widgets", tex)

    def test_escaping_in_full_flow(self, env, tmp_path):
        body = GOOD_BODY + " That work cut costs by 35% & kept the build_step fast."
        client = _client(body)
        out = _run(_listing(company="R&D Labs"), tmp_path / "out", client)
        tex = out.read_text(encoding="utf-8")
        assert "35\\% \\& kept the build\\_step fast." in tex
        assert "R\\&D Labs" in tex


# ---------------------------------------------------------------------------
# Fallback facts
# ---------------------------------------------------------------------------

class TestFallbackFacts:
    def test_personal_info_facts_when_no_cv(self, env, tmp_path):
        env.cv_path = None
        client = _client(GOOD_BODY, GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        _, user = _prompt(client)
        assert "Summary: Platform engineer focused on reliable backend systems." in user
        assert "Skills: Python, Bash, Terraform, Kubernetes" in user
        assert "Senior Platform Engineer at Samplecorp" in user
        assert "DevOps Engineer at Example Labs" in user
        assert "Current title: Senior Platform Engineer" in user
        assert "not available" not in user

    def test_cv_facts_function_fallback(self, env):
        env.cv_path = None
        facts, title = _cv_facts(env.info)
        assert "Skills: Python, Bash, Terraform, Kubernetes" in facts
        assert title == "Senior Platform Engineer"

    def test_no_cv_and_no_info(self, env, tmp_path):
        env.cv_path = None
        env.info = {}
        facts, title = _cv_facts({})
        assert facts == "" and title == "not given"
        client = _client(GOOD_BODY, GOOD_BODY)
        _run(_listing(), tmp_path / "out", client)
        _, user = _prompt(client)
        assert "not available" in user
        assert "Years of experience: not given" in user


# ---------------------------------------------------------------------------
# Real compile
# ---------------------------------------------------------------------------

def _xelatex() -> str | None:
    from cover_letter import compiler
    return shutil.which("xelatex") or shutil.which("xelatex", path=compiler._ENV["PATH"])


@pytest.mark.skipif(_xelatex() is None, reason="xelatex not installed")
def test_assembled_letter_compiles_with_xelatex(env, tmp_path):
    from cover_letter.compiler import compile_pdf

    body = GOOD_BODY + " That work cut costs by 35% & kept the build_step fast."
    client = _client(body)
    out = _run(_listing(company="R&D Labs", title="Platform & SRE Engineer"), tmp_path / "out", client)
    pdf = compile_pdf(out)
    assert pdf.exists() and pdf.stat().st_size > 0


@pytest.mark.skipif(_xelatex() is None, reason="xelatex not installed")
def test_letter_without_company_compiles_with_xelatex(env, tmp_path):
    from cover_letter.compiler import compile_pdf

    client = _client(GOOD_BODY)
    out = _run(_listing(company=""), tmp_path / "out", client)
    pdf = compile_pdf(out)
    assert pdf.exists() and pdf.stat().st_size > 0


class TestYearsClaims:
    @pytest.mark.parametrize("text,bad", [
        ("With over four years of experience as an engineer.", True),
        ("With 6+ years of experience in DevOps.", True),
        ("With nearly 4 years of experience in DevOps.", True),
        ("With 4 years of experience in DevOps.", False),
        ("I cut the release cycle by 5 years worth of debt.", False),
    ])
    def test_years_claim_must_match(self, text, bad):
        from types import SimpleNamespace
        from cover_letter.generator import _problems
        filler = "word " * 160
        listing = SimpleNamespace(skills=[], description="")
        problems = [p for p in _problems(filler + text, filler, listing, "4") if "years" in p and "words" not in p]
        assert bool(problems) == bad, problems


def test_banned_phrases_are_problems():
    from types import SimpleNamespace
    from cover_letter.generator import _problems
    filler = "word " * 160
    listing = SimpleNamespace(skills=[], description="")
    problems = _problems(filler + "This aligns well with the role.", filler, listing, "4")
    assert any("aligns well" in p for p in problems)
    assert not [p for p in _problems(filler + "I built it.", filler, listing, "4") if "banned" in p]


class TestRoundTwoReviewRegressions:
    def test_banned_phrase_needs_word_boundary(self):
        from types import SimpleNamespace
        from cover_letter.generator import _problems
        filler = "word " * 160
        listing = SimpleNamespace(skills=[], description="")
        text = filler + "I phoned the on-call team and stayed compassionate."
        assert not [p for p in _problems(text, filler, listing, "4") if "banned" in p]

    def test_project_duration_is_not_a_years_claim(self):
        from types import SimpleNamespace
        from cover_letter.generator import _problems
        filler = "word " * 160
        listing = SimpleNamespace(skills=[], description="")
        text = filler + "I spent two years building the billing platform."
        assert not [p for p in _problems(text, filler, listing, "4") if "write exactly" in p]

    def test_years_fall_back_to_stated_value(self):
        from cover_letter.generator import _years
        assert _years({"work_history": [{"start": "bad"}], "years_of_experience": "4+"}) == "4"
        assert _years({}) == ""

    async def test_empty_retry_keeps_first_attempt(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from cover_letter.generator import _write_body
        from cv_editor.client import AIConfig
        good = " ".join(["I ran Kubernetes clusters for product teams."] * 25)
        first = good + " I also built Snowflake pipelines."
        client = SimpleNamespace(config=AIConfig(), complete=AsyncMock(side_effect=[first, ""]))
        listing = SimpleNamespace(skills=["Snowflake"], description="")
        body = await _write_body(client, "msg", "Ran Kubernetes clusters.", listing, "4")
        assert "Snowflake" not in body and body.count("Kubernetes") == 25

    async def test_letter_with_nothing_left_raises(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from cover_letter.generator import _write_body
        from cv_editor.client import AIConfig
        bad = "I built Snowflake pipelines. " * 40
        client = SimpleNamespace(config=AIConfig(), complete=AsyncMock(side_effect=[bad, bad]))
        listing = SimpleNamespace(skills=["Snowflake"], description="")
        with pytest.raises(RuntimeError, match="too short"):
            await _write_body(client, "msg", "Ran Kubernetes clusters.", listing, "4")

    def test_email_with_underscore_escaped_in_text_not_in_link(self):
        from types import SimpleNamespace
        from cover_letter.generator import _assemble
        listing = SimpleNamespace(id="x" * 12, title="SRE", company="Acme")
        info = {"name": {"full": "Jane Sample"}, "email": "jane_sample@example.com",
                "phone": "+1 555 000 0000", "address": {"city": "Anytown"}}
        tex = _assemble("Body.", listing, info)
        assert "{mailto:jane_sample@example.com}" in tex
        assert "{jane\\_sample@example.com}" in tex
