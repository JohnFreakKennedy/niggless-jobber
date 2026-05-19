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


def compile_pdf(tex_path: Path) -> Path:
    """
    Compile *tex_path* with xelatex (twice for cross-refs).
    Returns the path to the generated PDF.
    Raises RuntimeError on compilation failure.
    """
    tex_path = tex_path.resolve()
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
            cv_pdf = compile_pdf(cv_tex)
        except RuntimeError as exc:
            log.error("CV compile failed: %s", exc)

    if cl_tex.exists():
        try:
            cl_pdf = compile_pdf(cl_tex)
        except RuntimeError as exc:
            log.error("Cover letter compile failed: %s", exc)

    return cv_pdf, cl_pdf
