"""Tests for cv_editor/cleaner.py -- AI phrase blacklist post-processor."""
import pytest
from cv_editor.cleaner import clean_description, clean_cv_tex


# ---------------------------------------------------------------------------
# clean_description
# ---------------------------------------------------------------------------

class TestCleanDescription:
    def test_spearheaded_replaced(self):
        result = clean_description("Spearheaded the migration to Kubernetes.")
        assert "spearheaded" not in result.lower()
        assert "led" in result.lower()

    def test_leveraged_replaced(self):
        result = clean_description("Leveraged Python to build data pipelines.")
        assert "leveraged" not in result.lower()
        assert "used" in result.lower()

    def test_utilized_replaced(self):
        result = clean_description("Utilized Docker for containerization.")
        assert "utilized" not in result.lower()
        assert "used" in result.lower()

    def test_orchestrated_replaced(self):
        result = clean_description("Orchestrated deployment of 12 services.")
        assert "orchestrated" not in result.lower()
        assert "coordinated" in result.lower()

    def test_proactively_replaced(self):
        result = clean_description("Proactively identified performance bottlenecks.")
        assert "proactively" not in result.lower()
        assert "actively" in result.lower()

    def test_in_order_to_replaced(self):
        result = clean_description("Refactored code in order to reduce latency.")
        assert "in order to" not in result.lower()
        assert "to" in result

    def test_em_dash_replaced_with_comma(self):
        result = clean_description("Fast runtime\u2014reduced costs.")
        assert "\u2014" not in result
        assert "," in result

    def test_double_hyphen_em_dash(self):
        result = clean_description("Fast runtime -- reduced costs.")
        assert " -- " not in result

    def test_remove_phrase_moving_forward(self):
        result = clean_description("Moving forward, we improved the pipeline.")
        assert "moving forward" not in result.lower()

    def test_unchanged_normal_text(self):
        original = "Built a REST API using Go and PostgreSQL."
        assert clean_description(original) == original

    def test_case_insensitive(self):
        result = clean_description("LEVERAGED cloud services for scaling.")
        assert "LEVERAGED" not in result


# ---------------------------------------------------------------------------
# clean_cv_tex
# ---------------------------------------------------------------------------

class TestCleanCvTex:
    _SAMPLE = r"""\documentclass[10pt]{article}
\begin{document}

\resumeSubheadingSimple{Software Engineer}{TechCorp}
Spearheaded the migration of the monolith to microservices.
Leveraged Kubernetes for container orchestration, proactively reducing costs.

\resumeSubheadingSimple{Junior Developer}{StartupCo}
Utilized Python and PostgreSQL to build internal tooling.

\end{document}
"""

    def test_latex_commands_untouched(self):
        result = clean_cv_tex(self._SAMPLE)
        assert r"\documentclass[10pt]{article}" in result
        assert r"\begin{document}" in result
        assert r"\resumeSubheadingSimple{Software Engineer}{TechCorp}" in result
        assert r"\end{document}" in result

    def test_buzzwords_removed_from_prose(self):
        result = clean_cv_tex(self._SAMPLE)
        assert "Spearheaded" not in result
        assert "Leveraged" not in result
        assert "Utilized" not in result
        assert "proactively" not in result

    def test_prose_still_meaningful(self):
        result = clean_cv_tex(self._SAMPLE)
        assert "migration" in result
        assert "microservices" in result
        assert "Python" in result
