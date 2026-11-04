"""Tests for cv_editor/tailoring.py: the validator for AI-proposed CV edits and its helpers."""
from __future__ import annotations

import pytest

from cv_editor.tailoring import (
    GENERIC_TERMS,
    MIN_BULLET_OVERLAP,
    TECH_TERMS,
    build_vocabulary,
    check_permutation,
    check_rewrite,
    check_skill_items,
    coverage,
    latex_escape,
    mentions,
    parse_json_object,
    rank_by_relevance,
    split_sentences,
    str_list,
    word_overlap,
)

ORIGINAL = "Built and ran REST services in Python and PostgreSQL serving 40k daily users."
CV_TEXT = (
    "Software Engineer\n"
    + ORIGINAL + "\n"
    "Moved deployments to Kubernetes with Terraform and GitHub Actions, cutting release time "
    "from 2 hours to 15 minutes.\n"
    "Languages: Python, Go, Bash, SQL"
)
VOCAB = build_vocabulary(["Kafka", "Pulsar", "CI/CD", "Python"])


def _check(proposed, original=ORIGINAL, **kw):
    kw.setdefault("cv_text", CV_TEXT)
    kw.setdefault("vocabulary", VOCAB)
    return check_rewrite(original, proposed, **kw)


def _accepted(proposed, **kw):
    text, reason = _check(proposed, **kw)
    assert text is not None, reason
    assert reason == ""
    return text


def _rejected(proposed, **kw):
    text, reason = _check(proposed, **kw)
    assert text is None, f"accepted: {text!r}"
    assert reason
    return reason


# ---------------------------------------------------------------------------
# check_rewrite: basic input handling
# ---------------------------------------------------------------------------

class TestBasics:
    @pytest.mark.parametrize("proposed", [None, 42, ["a"], {"t": "x"}])
    def test_not_a_string(self, proposed):
        assert _rejected(proposed) == "not a string"

    @pytest.mark.parametrize("proposed", ["", "   ", "\n\t"])
    def test_empty(self, proposed):
        assert _rejected(proposed) == "empty"

    def test_unchanged_returns_original(self):
        assert _check(ORIGINAL) == (ORIGINAL, "")

    def test_whitespace_only_change_returns_original_text(self):
        assert _check("  Built and ran REST services\n in Python and PostgreSQL serving 40k daily users. ") == (ORIGINAL, "")

    def test_good_rewrite_accepted(self):
        new = "Built and ran Python REST services on PostgreSQL serving 40k daily users."
        assert _accepted(new) == new

    def test_whitespace_collapsed(self):
        assert _accepted("Built and ran   Python REST\nservices on PostgreSQL serving 40k daily users.") == \
            "Built and ran Python REST services on PostgreSQL serving 40k daily users."


# ---------------------------------------------------------------------------
# Guard: invented technologies and per-role term scope
# ---------------------------------------------------------------------------

class TestInventedTechnologies:
    @pytest.mark.parametrize("proposed, term", [
        ("Built and ran REST services in Python and Kafka serving 40k daily users.", "kafka"),
        ("Built and ran REST services in Python and MongoDB serving 40k daily users.", "mongodb"),
        ("Built and ran REST services in Rust and PostgreSQL serving 40k daily users.", "rust"),
        ("Built and ran REST services in C# and PostgreSQL serving 40k daily users.", "c#"),
        # A JD-only term (not in TECH_TERMS) is enforced once it is in the vocabulary.
        ("Built and ran REST services in Python and Pulsar serving 40k daily users.", "pulsar"),
    ])
    def test_rejects_new_technology(self, proposed, term):
        reason = _rejected(proposed)
        assert "not in the CV" in reason and term in reason

    @pytest.mark.parametrize("proposed", [
        # Technologies the CV already has elsewhere.
        "Built and ran REST services in Python, PostgreSQL and Go serving 40k daily users.",
        "Built and ran REST services in Python and PostgreSQL on Kubernetes for 40k daily users.",
        # Generic practice words from the job are fine.
        "Built and ran REST services and CI/CD in Python and PostgreSQL for 40k daily users.",
        "Built and ran REST microservices in Python and PostgreSQL for 40k daily users.",
        # "go live" is not the Go language.
        "Built and ran REST services in Python and PostgreSQL; go live for 40k daily users.",
    ])
    def test_accepts_known_or_generic_terms(self, proposed):
        _accepted(proposed)

    def test_go_live_not_counted_as_go_when_cv_lacks_go(self):
        cv = "Software Engineer\n" + ORIGINAL
        _accepted("Built and ran REST services in Python and PostgreSQL; go live for 40k daily users.", cv_text=cv)
        reason = _rejected("Built and ran REST services in Go and PostgreSQL serving 40k daily users.", cv_text=cv)
        assert "go" in reason

    def test_generic_terms_never_enter_vocabulary(self):
        vocab = build_vocabulary(["CI/CD", "DevOps", "microservices", "Pulsar", "x" * 41, "  "])
        assert "pulsar" in vocab
        assert not ({"ci/cd", "devops", "microservices"} & vocab)
        assert "x" * 41 not in vocab and "" not in vocab
        assert not (build_vocabulary(list(GENERIC_TERMS)) & set(GENERIC_TERMS) - set(TECH_TERMS))

    def test_term_scope_rejects_tech_from_another_role(self):
        role_text = "Software Engineer " + ORIGINAL
        reason = _rejected(
            "Built and ran REST services in Python and PostgreSQL on Kubernetes for 40k daily users.",
            term_source=role_text,
        )
        assert "not in this role" in reason and "kubernetes" in reason

    def test_term_scope_accepts_tech_from_own_role(self):
        role_text = "Software Engineer " + ORIGINAL
        _accepted("Built and ran Python REST services on PostgreSQL serving 40k daily users.", term_source=role_text)


# ---------------------------------------------------------------------------
# Guard: seniority words
# ---------------------------------------------------------------------------

class TestSeniority:
    @pytest.mark.parametrize("word", ["Senior", "lead", "Staff", "principal", "head", "architect", "expert"])
    def test_rejects_seniority_claim(self, word):
        reason = _rejected(f"As {word} built and ran REST services in Python and PostgreSQL for 40k daily users.")
        assert "seniority" in reason and word.lower() in reason

    def test_accepts_seniority_word_already_in_cv(self):
        cv = CV_TEXT + "\nSenior Software Engineer"
        _accepted("As senior engineer built and ran REST services in Python and PostgreSQL for 40k daily users.", cv_text=cv)

    @pytest.mark.parametrize("proposed", [
        "Led and ran REST services in Python and PostgreSQL serving 40k daily users.",
        "Built and ran REST services with leadership in Python and PostgreSQL for 40k daily users.",
        "Built and ran REST services in Python and PostgreSQL serving 40k daily users ahead of plan.",
    ])
    def test_similar_words_are_not_seniority(self, proposed):
        _accepted(proposed)


# ---------------------------------------------------------------------------
# Guard: numbers
# ---------------------------------------------------------------------------

class TestNumbers:
    @pytest.mark.parametrize("proposed, number", [
        ("Built and ran REST services in Python and PostgreSQL serving 50k daily users.", "50"),
        ("Built and ran 12 REST services in Python and PostgreSQL serving 40k daily users.", "12"),
        ("Built and ran REST services in Python and PostgreSQL serving 40k daily users at 99.9%.", "99.9"),
    ])
    def test_rejects_added_number(self, proposed, number):
        reason = _rejected(proposed)
        assert "adds numbers" in reason and number in reason

    def test_accepts_same_numbers(self):
        _accepted("Served 40k daily users with REST services built and ran in Python and PostgreSQL.")

    def test_accepts_dropping_a_number(self):
        _accepted("Built and ran REST services in Python and PostgreSQL serving daily users at scale.")

    def test_allowed_numbers(self):
        _accepted("Built and ran REST services in Python and PostgreSQL for 40k daily users over 5 years.",
                  allowed_numbers={"5"})

    def test_number_source_replaces_original(self):
        proposed = "Built and ran REST services in Python and PostgreSQL in 15 minutes for 40k users."
        _rejected(proposed)
        _accepted(proposed, number_source=CV_TEXT)


# ---------------------------------------------------------------------------
# Guard: length limits
# ---------------------------------------------------------------------------

class TestLength:
    def test_rejects_too_long(self):
        proposed = ORIGINAL[:-1] + ", with clear documentation, careful reviews, steady on-call support and tests."
        assert len(proposed) > max(len(ORIGINAL) * 1.25, len(ORIGINAL) + 40)
        assert _rejected(proposed).startswith("too long")

    def test_accepts_growth_within_limit(self):
        _accepted(ORIGINAL[:-1] + " with careful on-call.")

    def test_rejects_too_short(self):
        assert _rejected("Built Python services.").startswith("too short")

    def test_max_chars_override(self):
        proposed = "Built and ran REST services in Python and PostgreSQL for 40k users."
        assert _rejected(proposed, max_chars=30).startswith("too long")
        long_one = ORIGINAL[:-1] + ", with clear documentation, careful reviews, steady on-call support and tests."
        _accepted(long_one, max_chars=1000)

    def test_min_chars_override(self):
        _accepted("Built Python services.", min_chars=0)
        assert _rejected("Built and ran Python services for 40k users.", min_chars=60).startswith("too short")


# ---------------------------------------------------------------------------
# Guard: word overlap (facts swapped between bullets)
# ---------------------------------------------------------------------------

class TestOverlap:
    def test_rejects_swapped_facts(self):
        other = "Moved deployments to Kubernetes with Terraform, cutting release time from 2 hours to 15 minutes."
        reason = _rejected(other, min_overlap=MIN_BULLET_OVERLAP, number_source=CV_TEXT)
        assert "keeps only" in reason

    def test_accepts_reworded_same_facts(self):
        _accepted("Ran and built Python REST services with PostgreSQL, serving 40k daily users.",
                  min_overlap=MIN_BULLET_OVERLAP)

    def test_overlap_off_by_default(self):
        _accepted("Moved REST services in Python onto PostgreSQL for 40k users daily.")

    def test_word_overlap_values(self):
        assert word_overlap("", "anything") == 1.0
        assert word_overlap("the and of", "x") == 1.0
        assert word_overlap("Built services quickly", "Built services quickly") == 1.0
        assert word_overlap("Built services", "Unrelated text here") == 0.0
        # Stems (first five letters) count: "deployments" ~ "deploying".
        assert word_overlap("deployments", "deploying") == 1.0


# ---------------------------------------------------------------------------
# Guard: LaTeX escaping, commands, braces
# ---------------------------------------------------------------------------

class TestLatex:
    @pytest.mark.parametrize("raw, escaped", [
        ("cut 40% of cost", "cut 40\\% of cost"),
        ("R&D team", "R\\&D team"),
        ("C# code", "C\\# code"),
        ("snake_case", "snake\\_case"),
        ("already 40\\% done", "already 40\\% done"),
        ("R\\&D", "R\\&D"),
    ])
    def test_latex_escape(self, raw, escaped):
        assert latex_escape(raw) == escaped

    def test_escapes_specials_in_rewrite(self):
        original = "Cut cost by 40\\% for the research team using Python."
        text = _accepted("Cut cost by 40% for the R&D research team using Python.", original=original)
        assert text == "Cut cost by 40\\% for the R\\&D research team using Python."

    def test_dollar_escaped_when_original_has_none(self):
        original = "Saved 5k per month on cloud bills with Python scripts."
        text = _accepted("Saved $5k per month on cloud bills with Python scripts.", original=original)
        assert text == "Saved \\$5k per month on cloud bills with Python scripts."
        text = _accepted("Saved \\$5k per month on cloud bills with Python scripts.", original=original)
        assert text == "Saved \\$5k per month on cloud bills with Python scripts."

    @pytest.mark.parametrize("proposed", [
        "Built and ran REST services {in Python and PostgreSQL serving 40k daily users.",
        "Built and ran REST services} in Python and {PostgreSQL serving 40k daily users.",
        "Built and ran REST services in \\textbf{Python and PostgreSQL serving 40k daily users.",
    ])
    def test_rejects_unbalanced_braces(self, proposed):
        assert _rejected(proposed) == "unbalanced braces"

    def test_escaped_braces_are_balanced(self):
        _accepted("Built and ran REST services in Python and PostgreSQL \\{json\\} for 40k daily users.")

    @pytest.mark.parametrize("proposed, cmd", [
        ("Built and ran REST services in \\textbf{Python} and PostgreSQL for 40k daily users.", "\\textbf"),
        ("Built and ran REST services in Python \\newline and PostgreSQL for 40k daily users.", "\\newline"),
        ("\\newcommand{\\jobtitle}{Lead} REST services in Python and PostgreSQL 40k.", "\\newcommand"),
    ])
    def test_rejects_new_commands(self, proposed, cmd):
        reason = _rejected(proposed)
        assert "adds LaTeX commands" in reason and cmd.replace("\\", "\\\\") in reason

    def test_accepts_commands_already_in_original(self):
        original = "Built \\textbf{REST} services in Python and PostgreSQL serving 40k daily users."
        _accepted("Built and ran \\textbf{REST} services in Python and PostgreSQL for 40k daily users.",
                  original=original)


# ---------------------------------------------------------------------------
# Guard: buzzword cleaner
# ---------------------------------------------------------------------------

class TestCleaner:
    @pytest.mark.parametrize("proposed, expected_fragment, banned", [
        ("Spearheaded REST services in Python and PostgreSQL serving 40k daily users.", "led REST", "Spearheaded"),
        ("Leveraged Python and PostgreSQL to run REST services for 40k daily users.", "used Python", "Leveraged"),
        ("Built robust REST services in Python and PostgreSQL serving 40k daily users.", "strong REST", "robust"),
        ("Built REST services in Python and PostgreSQL — serving 40k daily users.", "PostgreSQL, serving", "—"),
        ("Built REST services in Python and PostgreSQL in order to serve 40k daily users.", "to serve", "in order to"),
    ])
    def test_buzzwords_replaced(self, proposed, expected_fragment, banned):
        text = _accepted(proposed)
        assert expected_fragment in text
        assert banned not in text


# ---------------------------------------------------------------------------
# mentions
# ---------------------------------------------------------------------------

class TestMentions:
    @pytest.mark.parametrize("text, term, expected", [
        ("Wrote C++ services", "c++", True),
        ("Wrote C services", "c++", False),
        ("Wrote C# services", "c#", True),
        ("Wrote C\\# services", "c#", True),
        ("Wrote C services", "c#", False),
        ("Built .NET apps", ".net", True),
        ("Built ASP.NET apps", ".net", False),
        ("Built ASP.NET apps", "asp.net", True),
        ("Built Node.js APIs", "node.js", True),
        ("Built nodejs APIs", "node.js", False),
        ("Planned the go live date", "go", False),
        ("Wrote Go services", "go", True),
        ("Wrote Go, Python", "go", True),
        ("Golang services", "go", False),
        ("A gopher", "go", False),
        ("Ran PostgreSQL", "sql", False),
        ("Ran MySQL", "sql", False),
        ("Wrote SQL", "sql", True),
        ("Wrote sql", "SQL", True),
        ("Ran PostgreSQL", "postgresql", True),
        ("Wrote Java", "javascript", False),
        ("Wrote JavaScript", "java", False),
        ("Rust services", "rust", True),
        ("rust on the car", "rust", False),
        ("Used GitHub Actions", "github actions", True),
        ("Used GitHub-Actions", "GitHub Actions", True),
        ("Used github  actions", "github actions", True),
        ("Used GitHub", "github actions", False),
        ("Apache Spark jobs", "spark", True),
        ("spark joy", "spark", False),
        ("R\\&D team", "r&d", True),
        ("anything", "", False),
        ("anything", "   ", False),
        ("", "go", False),
    ])
    def test_cases(self, text, term, expected):
        assert mentions(text, term) is expected


# ---------------------------------------------------------------------------
# check_permutation
# ---------------------------------------------------------------------------

class TestCheckPermutation:
    EXPECTED = ["b1", "b2", "b3"]

    @pytest.mark.parametrize("proposed, result", [
        (["b3", "b1", "b2"], ["b3", "b1", "b2"]),
        (["b1", "b2", "b3"], ["b1", "b2", "b3"]),
        (["b3"], ["b3", "b1", "b2"]),
        (["b2", "b1"], ["b2", "b1", "b3"]),
        ("b2", ["b2", "b1", "b3"]),
    ])
    def test_accepted(self, proposed, result):
        assert check_permutation(proposed, self.EXPECTED) == result

    @pytest.mark.parametrize("proposed", [
        ["b1", "b9"],
        ["b1", "b1", "b2", "b3"],
        ["b1", "b1"],
        [],
        "",
        None,
        42,
        {"b1": 1},
        ["b1", 2],
        ("b1", "b2"),
    ])
    def test_rejected(self, proposed):
        assert check_permutation(proposed, self.EXPECTED) is None

    def test_does_not_mutate_input(self):
        proposed = ["b2"]
        check_permutation(proposed, self.EXPECTED)
        assert proposed == ["b2"]


# ---------------------------------------------------------------------------
# check_skill_items
# ---------------------------------------------------------------------------

class TestCheckSkillItems:
    ORIGINAL = ["Python", "Go", "Bash", "SQL"]

    @pytest.mark.parametrize("proposed, result", [
        (["SQL", "Go", "Python", "Bash"], ["SQL", "Go", "Python", "Bash"]),
        (["Go"], ["Go", "Python", "Bash", "SQL"]),
        (["sql", "PYTHON"], ["SQL", "Python", "Go", "Bash"]),
        ([" Go ", "Bash"], ["Go", "Bash", "Python", "SQL"]),
        ("Go, SQL", ["Go", "SQL", "Python", "Bash"]),
    ])
    def test_accepted(self, proposed, result):
        out = check_skill_items(proposed, self.ORIGINAL)
        assert out == result
        assert sorted(out) == sorted(self.ORIGINAL)  # never adds or removes

    @pytest.mark.parametrize("proposed", [
        ["Go", "Rust"],
        ["Go", "go"],
        [],
        "",
        None,
        {"Go": 1},
        ["Go", 3],
    ])
    def test_rejected(self, proposed):
        assert check_skill_items(proposed, self.ORIGINAL) is None


# ---------------------------------------------------------------------------
# parse_json_object
# ---------------------------------------------------------------------------

class TestParseJsonObject:
    @pytest.mark.parametrize("raw", [
        '{"a": 1}',
        '  {"a": 1}  \n',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '```JSON {"a": 1}```',
        'Here is the JSON:\n{"a": 1}',
        '{"a": 1}\nHope this helps!',
        'Sure. {"a": 1} Anything else?',
        'Sure:\n```json\n{"a": 1}\n```\nDone.',
        'Note [1, 2] then {"a": 1}',
    ])
    def test_variants(self, raw):
        assert parse_json_object(raw) == {"a": 1}

    def test_nested(self):
        raw = 'ok {"bullets": {"b1": "x {y}"}, "order": {"r1": ["b2", "b1"]}, "n": [1, {"k": 2}]} trailing }'
        assert parse_json_object(raw) == {
            "bullets": {"b1": "x {y}"}, "order": {"r1": ["b2", "b1"]}, "n": [1, {"k": 2}],
        }

    @pytest.mark.parametrize("raw, message", [
        ("", "empty"),
        (None, "empty"),
        ('[{"a": 1}]', "not a JSON object"),
        ('["a", "b"]', "not a JSON object"),
        ('"just a string"', "not a JSON object"),
        ("no json here", "no JSON object"),
        ('{"a": 1', "invalid JSON"),
        ("{not json}", "invalid JSON"),
    ])
    def test_errors(self, raw, message):
        with pytest.raises(ValueError, match=message):
            parse_json_object(raw)

    def test_brace_in_prose_before_object(self):
        assert parse_json_object('I kept the {summary} short:\n{"a": 1}') == {"a": 1}


# ---------------------------------------------------------------------------
# coverage, ranking, small helpers
# ---------------------------------------------------------------------------

class TestCoverage:
    def test_found_missing_score(self):
        cov = coverage("Python, Go and PostgreSQL on Kubernetes", ["Python", "Kafka", "Kubernetes"])
        assert cov == {"score": 0.67, "found": ["Python", "Kubernetes"], "missing": ["Kafka"]}

    def test_dedup_and_blank_keywords(self):
        cov = coverage("Python", ["Python", " Python ", "", "  ", None, "Rust"])
        assert cov == {"score": 0.5, "found": ["Python"], "missing": ["Rust"]}

    def test_no_keywords(self):
        assert coverage("Python", []) == {"score": None, "found": [], "missing": []}

    def test_full_and_zero(self):
        assert coverage("Go", ["Go"])["score"] == 1.0
        assert coverage("go live", ["Go"])["score"] == 0.0


class TestRanking:
    def test_ties_keep_original_order(self):
        ids = ["a", "b", "c", "d"]
        assert rank_by_relevance(ids, lambda i: i, [], []) == ids
        texts = {"a": "Bash", "b": "Python", "c": "Go", "d": "SQL"}
        assert rank_by_relevance(ids, texts.get, ["Rust"], ["Kafka"]) == ids

    def test_required_before_preferred(self):
        texts = {"a": "Bash", "b": "Kafka", "c": "Python", "d": "Go"}
        assert rank_by_relevance(list(texts), texts.get, ["Python", "Go"], ["Kafka"]) == ["c", "d", "b", "a"]

    def test_stable_among_equal_scores(self):
        texts = {"x1": "Python one", "x2": "nothing", "x3": "Python two", "x4": "Python three"}
        assert rank_by_relevance(list(texts), texts.get, ["Python"], []) == ["x1", "x3", "x4", "x2"]

    def test_repeatable(self):
        texts = {str(i): ("Go" if i % 3 == 0 else "Bash") for i in range(20)}
        first = rank_by_relevance(list(texts), texts.get, ["Go"], [])
        for _ in range(5):
            assert rank_by_relevance(list(texts), texts.get, ["Go"], []) == first


class TestHelpers:
    @pytest.mark.parametrize("value, result", [
        (["a", " b ", "a", "", 3, None, "c"], ["a", "b", "c"]),
        ("a, b,, a", ["a", "b"]),
        (None, []),
        (5, []),
        ({"a": 1}, []),
    ])
    def test_str_list(self, value, result):
        assert str_list(value) == result

    def test_str_list_limit(self):
        assert str_list([str(i) for i in range(50)], limit=3) == ["0", "1", "2"]

    @pytest.mark.parametrize("text, result", [
        ("One. Two! Three?", ["One.", "Two!", "Three?"]),
        ("Uses v1.2 daily. Next one.", ["Uses v1.2 daily.", "Next one."]),
        ("lower. case stays", ["lower. case stays"]),
        ("  spaced\n  out.  New  ", ["spaced out.", "New"]),
        ("", []),
    ])
    def test_split_sentences(self, text, result):
        assert split_sentences(text) == result


# ---------------------------------------------------------------------------
# Regressions for review findings
# ---------------------------------------------------------------------------

class TestReviewRegressions:
    def test_package_manager_is_not_a_seniority_claim(self):
        from cv_editor.tailoring import check_rewrite, TECH_TERMS
        orig = "Wrote Python tooling around the package manager for internal builds."
        new = "Wrote Python tooling around the package manager for all internal builds."
        text, reason = check_rewrite(orig, new, orig, set(TECH_TERMS))
        assert text is not None, reason

    def test_cleaner_keeps_number_ranges(self):
        from cv_editor.cleaner import clean_description
        assert "2019--2021" in clean_description("Ran it 2019--2021 for the team")
        assert clean_description("fast -- and cheap") == "fast, and cheap"

    def test_fit_level_ignores_commented_begin_document(self):
        from cover_letter.compiler import apply_fit_level
        tex = "% never add a second \\begin{document}\n\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n"
        out = apply_fit_level(tex, r"\linespread{0.9}", 1)
        assert out.index(r"\linespread{0.9}") > out.index("\\documentclass")
        assert out.splitlines()[0].startswith("% never add")


class TestTitleEscaping:
    def test_caret_and_tilde_removed(self):
        from cv_editor.editor import patch_job_title_in_tex
        out = patch_job_title_in_tex("\\newcommand{\\jobtitle}{X}\n", "C^2 ~Engineer")
        assert "\\newcommand{\\jobtitle}{C2 Engineer}" in out

    def test_legacy_tabular_keeps_space_before_ampersand(self):
        from cv_editor.editor import patch_job_title_in_tex
        header = ("\\begin{tabular*}{\\textwidth}{l@{\\extracolsep{\\fill}}r}\n"
                  "  \\textbf{\\huge Jane Sample} & Email: jane@example.com\\\\\n"
                  "    Cloud Engineer & { +1 555 000 0000} Linkedin\n\\end{tabular*}\n")
        out = patch_job_title_in_tex(header, "SRE")
        assert "SRE &" in out
        assert patch_job_title_in_tex(out, "SRE") == out


class TestCorrectnessReviewRegressions:
    def test_bare_dollar_escaped_when_original_has_escaped_dollar(self):
        from cv_editor.tailoring import check_rewrite, TECH_TERMS
        orig = r"Cut cloud spend by \$20k per month on AWS."
        new = "Cut AWS cloud spend by $20k per month."
        text, reason = check_rewrite(orig, new, orig, set(TECH_TERMS))
        assert text is not None, reason
        assert "\\$20k" in text and " $20k" not in text

    def test_case_sensitive_terms_are_guarded(self):
        from cv_editor.tailoring import build_vocabulary, check_rewrite
        orig = "Maintained Docker images and GitLab CI pipelines for 12 services."
        new = "Maintained Docker images and GitLab CI pipelines for 12 services with Lambda and Jira."
        text, reason = check_rewrite(orig, new, orig, build_vocabulary([]))
        assert text is None and "lambda" in reason.lower()

    def test_jd_keywords_join_vocabulary(self):
        from cv_editor.tailoring import build_vocabulary
        assert "payments" in build_vocabulary(["payments"])


class TestStructureReviewRegressions:
    def test_skill_items_split_on_top_level_commas(self):
        from cv_editor.structure import _split_items
        assert _split_items("AWS (EC2, S3), GCP, {a, b}") == ["AWS (EC2, S3)", "GCP", "{a, b}"]

    def test_two_argument_items_are_not_reordered(self):
        from cv_editor.structure import parse_cv, render
        tex = ("\\begin{document}\n\\resumeSubheading{Co}{Remote}{Engineer}{2020 -- 2021}\n"
               "\\resumeItem{Terraform}{Wrote modules.}\n\\resumeItem{Kubernetes}{Ran clusters.}\n"
               "\\end{document}\n")
        cv = parse_cv(tex)
        role = cv.roles[0]
        assert role.reorderable is False
        assert render(cv, bullet_order={role.id: list(reversed(role.bullet_ids))}) == tex
