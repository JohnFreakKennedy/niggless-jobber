"""LaTeX compilation for CV and cover letter."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# BasicTeX installs to /Library/TeX/texbin on macOS; add it to the search path
# so subprocess calls find xelatex even when the shell PATH does not include it.
_TEX_EXTRA_PATHS = ["/Library/TeX/texbin", "/usr/local/texlive/bin"]
_ENV = os.environ.copy()
_ENV["PATH"] = os.pathsep.join(_TEX_EXTRA_PATHS) + os.pathsep + _ENV.get("PATH", "")

_XELATEX = shutil.which("xelatex", path=_ENV["PATH"]) or "xelatex"


# Spacing presets tried in order when a CV spills onto a second page. Each is
# inserted just before \begin{document}; the first that fits one page wins.
_FIT_MARKER = "% @fit-one-page"
_FIT_LEVELS = [
    r"\linespread{0.96}",
    r"\linespread{0.93}\AtBeginDocument{\small}",
    r"\linespread{0.92}\AtBeginDocument{\small}"
    r"\makeatletter\@ifpackageloaded{geometry}"
    r"{\AtBeginDocument{\newgeometry{left=0.5in,right=0.5in,top=0.4in,bottom=0.4in}}}{}"
    r"\makeatother",
]
_PAGES_RE = re.compile(r"Output written on .*?\((\d+) pages?[,)]")


def compile_pdf(tex_path: Path, fit_one_page: bool = False) -> Path:
    """
    Compile *tex_path* with xelatex (twice for cross-refs).
    Returns the path to the generated PDF.
    Raises RuntimeError on compilation failure.

    With *fit_one_page*, a document that comes out longer than one page is
    recompiled with progressively tighter spacing (_FIT_LEVELS). The .tex file
    is rewritten with the preset that fit, so it always matches the PDF. If
    none fits, the tightest result is kept and a warning is logged.
    """
    tex_path = tex_path.resolve()
    pdf_path = _compile(tex_path)
    if not fit_one_page:
        return pdf_path

    pages = page_count(tex_path)
    if pages is None or pages <= 1:
        return pdf_path
    original = _strip_fit(tex_path.read_text(encoding="utf-8"))
    for level, preset in enumerate(_FIT_LEVELS, start=1):
        tex_path.write_text(apply_fit_level(original, preset, level), encoding="utf-8")
        try:
            pdf_path = _compile(tex_path)
        except RuntimeError as exc:
            log.warning("%s: fit level %d failed to compile (%s); keeping the original",
                        tex_path.name, level, exc)
            tex_path.write_text(original, encoding="utf-8")
            return _compile(tex_path)
        pages = page_count(tex_path)
        log.info("%s: %s page(s) at fit level %d", tex_path.name, pages, level)
        if pages is not None and pages <= 1:
            return pdf_path
    log.warning(
        "%s is still %s pages after the tightest spacing; shorten the base CV",
        tex_path.name, pages,
    )
    return pdf_path


def page_count(tex_path: Path) -> int | None:
    """Pages of the last compile, read from the xelatex .log next to *tex_path*."""
    log_path = tex_path.with_suffix(".log")
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # TeX wraps log lines at 79 characters, which can split a long output path
    # or even the word "pages"; join the lines before searching.
    m = _PAGES_RE.search(text.replace("\r", "").replace("\n", ""))
    return int(m.group(1)) if m else None


def apply_fit_level(tex: str, preset: str, level: int) -> str:
    """Insert *preset* right before \\begin{document} (replacing an earlier one)."""
    tex = _strip_fit(tex)
    m = re.search(r"^[ \t]*\\begin\{document\}", tex, re.MULTILINE)
    if m is None:
        return tex
    idx = m.start()
    return tex[:idx] + f"{_FIT_MARKER} level {level}\n{preset}\n" + tex[idx:]


def _strip_fit(tex: str) -> str:
    return re.sub(re.escape(_FIT_MARKER) + r"[^\n]*\n[^\n]*\n", "", tex)


def _compile(tex_path: Path) -> Path:
    output_dir = tex_path.parent
    cmd = [
        _XELATEX,
        "-interaction=nonstopmode",
        f"-output-directory={output_dir}",
        str(tex_path),
    ]

    for pass_num in (1, 2):
        log.debug("xelatex pass %d for %s", pass_num, tex_path.name)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=output_dir,
            env=_ENV,
        )
        if result.returncode != 0:
            log.debug("xelatex stderr:\n%s", result.stdout[-3000:])
            error = _diagnose(result.stdout, output_dir, tex_path)
            if error:
                raise RuntimeError(f"xelatex failed: {error}")

    pdf_path = tex_path.with_suffix(".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF not produced at expected path: {pdf_path}")
    log.info("Compiled %s -> %s", tex_path.name, pdf_path.name)
    return pdf_path


def _diagnose(log_output: str, output_dir: Path, tex_path: Path) -> str | None:
    """
    Try to identify and fix the compile error.
    Returns a human-readable error string if unrecoverable.
    """
    missing_pkg = re.search(r"! LaTeX Error: File `([^']+)' not found", log_output)
    if missing_pkg:
        pkg = missing_pkg.group(1).split(".")[0]
        log.info("Attempting tlmgr install %s", pkg)
        r = subprocess.run(["tlmgr", "install", pkg], capture_output=True, text=True)
        if r.returncode == 0:
            log.info("Installed missing package %s; retrying compile", pkg)
            return None
        return f"Missing LaTeX package: {pkg}"

    undefined_cs = re.search(r"! Undefined control sequence.*\\([a-zA-Z]+)", log_output)
    if undefined_cs:
        cs = undefined_cs.group(1)
        return f"Undefined LaTeX control sequence: \\{cs}"

    # Generic: return last error line
    for line in reversed(log_output.splitlines()):
        if line.startswith("!"):
            return line
    return "xelatex compilation failed (see .log for details)"


def compile_all(output_dir: Path) -> tuple[Path | None, Path | None]:
    """Compile cv.tex and cover_letter.tex in *output_dir*. Returns (cv_pdf, cl_pdf)."""
    cv_pdf = cl_pdf = None
    cv_tex = output_dir / "cv.tex"
    cl_tex = output_dir / "cover_letter.tex"

    if cv_tex.exists():
        try:
            cv_pdf = compile_pdf(cv_tex, fit_one_page=True)
        except RuntimeError as exc:
            log.error("CV compile failed: %s", exc)

    if cl_tex.exists():
        try:
            cl_pdf = compile_pdf(cl_tex)
        except RuntimeError as exc:
            log.error("Cover letter compile failed: %s", exc)

    return cv_pdf, cl_pdf
