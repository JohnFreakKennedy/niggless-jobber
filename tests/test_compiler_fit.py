"""Tests for the one-page fitting in cover_letter/compiler.py."""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pytest

import cover_letter.compiler as compiler
from cover_letter.compiler import _FIT_LEVELS, _FIT_MARKER, _strip_fit, apply_fit_level, compile_pdf, page_count

_SAMPLE = Path(__file__).parent.parent / "templates" / "cv_sample.tex"
_DOC = "\\documentclass{article}\n\\usepackage{geometry}\n\\begin{document}\nHello\n\\end{document}\n"


# ---------------------------------------------------------------------------
# page_count
# ---------------------------------------------------------------------------

class TestPageCount:
    def _write_log(self, tmp_path, text):
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        (tmp_path / "cv.log").write_text(text)
        return tex

    @pytest.mark.parametrize("log_text, pages", [
        ("blah\nOutput written on cv.pdf (2 pages, 34567 bytes).\n", 2),
        ("Output written on cv.pdf (1 page).\n", 1),
        ("Output written on cv.pdf (1 page, 1234 bytes).\n", 1),
        ("Output written on cv.pdf (12 pages, 1 bytes).\n", 12),
        # TeX wraps log lines at 79 characters; a long path splits the line.
        ("Output written on /private/var/folders/xy/abcdefghijklmnop/T/pytest-of-user/py\n"
         "test-1/test_x0/Acme_cv.pdf (3 pages, 45678 bytes).\n", 3),
        ("Output written on /very/long/path/that/wraps/exactly/before/the/parenthesis/cv.pdf\n"
         "(2 pages, 12 bytes).\n", 2),
    ])
    def test_parses(self, tmp_path, log_text, pages):
        assert page_count(self._write_log(tmp_path, log_text)) == pages

    def test_missing_log(self, tmp_path):
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        assert page_count(tex) is None

    def test_log_without_output_line(self, tmp_path):
        assert page_count(self._write_log(tmp_path, "! Emergency stop.\nNo pages of output.\n")) is None

    def test_wrap_inside_page_word(self, tmp_path):
        line = "Output written on /tmp/" + "a" * 43 + "/cv.pdf (2 pages, 1 bytes)."
        wrapped = line[:79] + "\n" + line[79:] + "\n"
        assert wrapped.splitlines()[0].endswith("(2 pa")
        assert page_count(self._write_log(tmp_path, wrapped)) == 2


# ---------------------------------------------------------------------------
# apply_fit_level / _strip_fit
# ---------------------------------------------------------------------------

class TestApplyFitLevel:
    def test_inserts_before_begin_document(self):
        out = apply_fit_level(_DOC, _FIT_LEVELS[0], 1)
        head, _, _ = out.partition("\\begin{document}")
        assert head.endswith(f"{_FIT_MARKER} level 1\n{_FIT_LEVELS[0]}\n")
        assert out.count("\\begin{document}") == 1

    def test_idempotent(self):
        once = apply_fit_level(_DOC, _FIT_LEVELS[1], 2)
        assert apply_fit_level(once, _FIT_LEVELS[1], 2) == once

    def test_replaces_earlier_level(self):
        out = _DOC
        for level, preset in enumerate(_FIT_LEVELS, start=1):
            out = apply_fit_level(out, preset, level)
        assert out.count(_FIT_MARKER) == 1
        assert f"{_FIT_MARKER} level 3" in out
        assert _FIT_LEVELS[0] + "\n" not in out
        assert _strip_fit(out) == _DOC

    def test_no_begin_document_is_noop(self):
        tex = "\\section{x}\nNo document here.\n"
        assert apply_fit_level(tex, _FIT_LEVELS[0], 1) == tex

    def test_strip_fit_without_marker(self):
        assert _strip_fit(_DOC) == _DOC

    def test_levels_are_single_lines(self):
        # _strip_fit removes the marker line plus one line, so every preset must be one line.
        assert all("\n" not in p for p in _FIT_LEVELS)


# ---------------------------------------------------------------------------
# compile_pdf fit loop with a fake compiler
# ---------------------------------------------------------------------------

def _fake_compile(fits_at: int | None, calls: list):
    """A stand-in for _compile: writes a log whose page count depends on the fit level in the tex."""
    def fake(tex_path: Path) -> Path:
        tex = tex_path.read_text(encoding="utf-8")
        m = re.search(re.escape(_FIT_MARKER) + r" level (\d+)", tex)
        level = int(m.group(1)) if m else 0
        calls.append(level)
        pages = 1 if fits_at is not None and level >= fits_at else 2
        word = "page" if pages == 1 else "pages"
        tex_path.with_suffix(".log").write_text(f"Output written on {tex_path.stem}.pdf ({pages} {word}).\n")
        pdf = tex_path.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF")
        return pdf
    return fake


class TestCompileFitLoop:
    def test_fits_at_level_two(self, tmp_path, monkeypatch):
        calls: list = []
        monkeypatch.setattr(compiler, "_compile", _fake_compile(2, calls))
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        pdf = compile_pdf(tex, fit_one_page=True)
        assert pdf.name == "cv.pdf"
        assert calls == [0, 1, 2]
        content = tex.read_text()
        assert f"{_FIT_MARKER} level 2\n{_FIT_LEVELS[1]}\n" in content
        assert content.count(_FIT_MARKER) == 1
        assert page_count(tex) == 1

    def test_never_fits_warns_and_keeps_level_three(self, tmp_path, monkeypatch, caplog):
        calls: list = []
        monkeypatch.setattr(compiler, "_compile", _fake_compile(None, calls))
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        with caplog.at_level(logging.WARNING, logger="cover_letter.compiler"):
            compile_pdf(tex, fit_one_page=True)
        assert calls == [0, 1, 2, 3]
        assert f"{_FIT_MARKER} level 3" in tex.read_text()
        assert tex.read_text().count(_FIT_MARKER) == 1
        assert any("still 2 pages" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)

    def test_already_one_page_compiles_once(self, tmp_path, monkeypatch):
        calls: list = []
        monkeypatch.setattr(compiler, "_compile", _fake_compile(0, calls))
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        compile_pdf(tex, fit_one_page=True)
        assert calls == [0]
        assert tex.read_text() == _DOC

    def test_fit_disabled_never_recompiles(self, tmp_path, monkeypatch):
        calls: list = []
        monkeypatch.setattr(compiler, "_compile", _fake_compile(None, calls))
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        compile_pdf(tex, fit_one_page=False)
        compile_pdf(tex)
        assert calls == [0, 0]
        assert tex.read_text() == _DOC

    def test_stale_fit_level_is_replaced(self, tmp_path, monkeypatch):
        calls: list = []
        monkeypatch.setattr(compiler, "_compile", _fake_compile(2, calls))
        tex = tmp_path / "cv.tex"
        # A tex left at level 1 by an earlier run, still two pages: the old preset is replaced.
        tex.write_text(apply_fit_level(_DOC, _FIT_LEVELS[0], 1))
        compile_pdf(tex, fit_one_page=True)
        assert calls == [1, 1, 2]
        content = tex.read_text()
        assert content.count(_FIT_MARKER) == 1
        assert f"{_FIT_MARKER} level 2" in content

    def test_unknown_page_count_returns(self, tmp_path, monkeypatch):
        calls: list = []

        def no_log(tex_path):
            calls.append(1)
            return tex_path.with_suffix(".pdf")
        monkeypatch.setattr(compiler, "_compile", no_log)
        tex = tmp_path / "cv.tex"
        tex.write_text(_DOC)
        compile_pdf(tex, fit_one_page=True)
        assert calls == [1]


# ---------------------------------------------------------------------------
# Real xelatex
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("xelatex") is None and not Path("/Library/TeX/texbin/xelatex").exists(),
                    reason="xelatex not installed")
def test_overlong_sample_cv_fits_one_page(tmp_path):
    base = _SAMPLE.read_text(encoding="utf-8")
    anchor = "    \\resumeItem{Set up Prometheus"
    extra = "".join(
        f"    \\resumeItem{{Ran on-call rotation number {i} for the billing and payments platform "
        f"services, fixing alerts and writing runbooks.}}\n"
        for i in range(30)
    )
    tex = tmp_path / "cv.tex"
    tex.write_text(base.replace(anchor, extra + anchor), encoding="utf-8")

    # Without fitting it spills onto a second page.
    plain = tmp_path / "plain.tex"
    plain.write_text(tex.read_text(), encoding="utf-8")
    compile_pdf(plain, fit_one_page=False)
    assert page_count(plain) == 2

    pdf = compile_pdf(tex, fit_one_page=True)
    assert pdf.exists()
    assert page_count(tex) == 1
    assert _FIT_MARKER in tex.read_text()


def test_failing_fit_level_restores_original(tmp_path, monkeypatch):
    import cover_letter.compiler as comp
    tex = tmp_path / "cv.tex"
    original = "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n"
    tex.write_text(original)
    calls = []

    def fake_compile(p):
        calls.append(p.read_text())
        if "@fit-one-page" in p.read_text():
            raise RuntimeError("boom")
        p.with_suffix(".log").write_text("Output written on cv.pdf (2 pages, 100 bytes).")
        return p.with_suffix(".pdf")

    monkeypatch.setattr(comp, "_compile", fake_compile)
    pdf = comp.compile_pdf(tex, fit_one_page=True)
    assert pdf == tex.with_suffix(".pdf")
    assert tex.read_text() == original
    assert len(calls) == 3  # original, failing level 1, original again
