"""Opt-in end-to-end checks of CV and cover letter generation against a real model.

Skipped unless NIGGLESS_LIVE=1. Pick the model with NIGGLESS_LIVE_PROVIDER
(ollama | openai | gemini | anthropic; default ollama) and NIGGLESS_LIVE_MODEL
(default gemma3:latest for ollama, the provider's default otherwise). Hosted
providers read their API key from the vault, as the app does; each run makes
two calls per CV and one or two per cover letter.

    NIGGLESS_LIVE=1 .venv/bin/python -m pytest tests/test_live_models.py -s
    NIGGLESS_LIVE=1 NIGGLESS_LIVE_PROVIDER=openai NIGGLESS_LIVE_MODEL=gpt-4o \
        .venv/bin/python -m pytest tests/test_live_models.py -s

These do not grade the prose (it varies run to run). They check the
guarantees that must hold whatever the model writes: no skill from the job
that the CV lacks, no new numbers, every original bullet and skill still
present, title and years enforced, the CV fits one page, and the cover letter
invents no skills or numbers.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NIGGLESS_LIVE") != "1",
    reason="set NIGGLESS_LIVE=1 to run against a real model",
)

_PROVIDER = os.environ.get("NIGGLESS_LIVE_PROVIDER", "ollama")
_MODEL = os.environ.get("NIGGLESS_LIVE_MODEL") or ("gemma3:latest" if _PROVIDER == "ollama" else "")
_HISTORY = [{"start": "2020-06", "end": "2024-06", "title": "Software Engineer"}]

_JOBS = {
    "platform": (
        "Senior Platform Engineer",
        """We run a payments platform processing millions of transactions a day.
Requirements: 4+ years of experience as DevOps / Platform engineer; production Kubernetes
(EKS); Terraform; AWS (EKS, RDS, IAM); CI/CD with GitHub Actions or ArgoCD; Prometheus,
Grafana, alerting, incident response and on-call; Python or Go for automation.
Nice to have: Helm, ArgoCD, Datadog, PostgreSQL tuning.""",
    ),
    "backend": (
        "Python Backend Developer",
        """Build REST APIs for our logistics product. Must have: Python, FastAPI or Django,
PostgreSQL, Redis, Docker, writing tests with pytest. Nice to have: Kafka, AWS, GraphQL.
You will design APIs, own services in production and review code.""",
    ),
    "data": (
        "Data Engineer",
        """Required: Scala, Apache Spark, Airflow, Snowflake, dbt, 3 years of experience.
Preferred: Kafka, Databricks. You will build batch and streaming pipelines.""",
    ),
}


def _ollama_up() -> bool:
    import httpx
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        tags = httpx.get(host.rstrip("/") + "/api/tags", timeout=3).json()
    except Exception:
        return False
    return any(m.get("name") == _MODEL for m in tags.get("models", []))


@pytest.fixture(scope="module")
def live_client():
    from cv_editor.client import _DEFAULT_MODELS, AIClient, AIConfig
    if _PROVIDER == "ollama" and not _ollama_up():
        pytest.skip(f"Ollama not reachable or model {_MODEL} not pulled")
    model = _MODEL or _DEFAULT_MODELS[_PROVIDER]
    return AIClient(AIConfig(provider=_PROVIDER, model=model, fallback_provider="none",
                             temperature=0.2, fallback_delay=0))


@pytest.mark.parametrize("job", sorted(_JOBS))
async def test_tailoring_keeps_guarantees(job, live_client, tmp_path):
    from cv_editor.editor import _resolve_base_cv, tailor_cv
    from cv_editor.structure import parse_cv, plain_text
    from cv_editor.tailoring import mentions

    title, description = _JOBS[job]
    listing = SimpleNamespace(id=f"live-{job}-0000", title=title, company="Example Co",
                              description=description, skills=[], source="test")
    with patch("cv_editor.editor._load_personal_info", return_value={"work_history": _HISTORY}):
        tex_path = await tailor_cv(listing, tmp_path, live_client)

    base = parse_cv(_resolve_base_cv().read_text(encoding="utf-8"))
    out_tex = tex_path.read_text(encoding="utf-8")
    out = parse_cv(out_tex)
    report = json.loads(tex_path.with_name(tex_path.stem + "_tailoring.json").read_text())
    print(f"\n[{_PROVIDER}:{live_client.config.model} {job}] status={report['status']} edited={report['edited_units']} "
          f"rejected={[r['reason'][:60] for r in report['rejected']]} "
          f"coverage {report['coverage_before']['score']} -> {report['coverage_after']['score']} "
          f"up-front {report['prominence_before']} -> {report['prominence_after']}")

    assert report["status"] == "tailored"
    assert f"\\newcommand{{\\jobtitle}}{{{title}}}" in out_tex
    assert "4 years of experience" in out_tex

    # No job skill the base CV lacks appears anywhere in the output.
    base_text = plain_text(base)
    for skill in report["jd"]["required_skills"] + report["jd"]["preferred_skills"]:
        if not mentions(base_text, skill):
            assert not mentions(plain_text(out), skill), f"invented skill {skill!r}"

    # No new numbers anywhere in the editable text.
    base_numbers = set(re.findall(r"\d+", base.source))
    out_numbers = set(re.findall(r"\d+", plain_text(out)))
    assert out_numbers <= base_numbers | {"4"}

    # Same bullets per role and the same skill items, possibly reordered.
    assert [len(r.bullet_ids) for r in out.roles] == [len(r.bullet_ids) for r in base.roles]
    base_items = sorted(i for k in base.skill_ids for i in base.units[k].items)
    out_items = sorted(i for k in out.skill_ids for i in out.units[k].items)
    assert out_items == base_items

    if shutil.which("xelatex"):
        from cover_letter.compiler import compile_pdf, page_count
        compile_pdf(tex_path, fit_one_page=True)
        assert page_count(tex_path) == 1


@pytest.mark.parametrize("job", sorted(_JOBS))
async def test_cover_letter_keeps_guarantees(job, live_client, tmp_path):
    from cover_letter.generator import _cv_facts, _years, generate_cover_letter
    from cv_editor.tailoring import build_vocabulary, mentions

    title, description = _JOBS[job]
    listing = SimpleNamespace(id=f"live-cl-{job}-0000", title=title, company="Example Co",
                              description=description, skills=[], source="test")
    info = {"name": {"full": "Jane Sample"}, "email": "jane@example.com",
            "phone": "+1 555 000 0000", "address": {"city": "Anytown"}, "work_history": _HISTORY}
    with patch("cover_letter.generator._load_personal_info", return_value=info):
        tex_path = await generate_cover_letter(listing, tmp_path, live_client)
        facts, _ = _cv_facts(info)
    tex = tex_path.read_text(encoding="utf-8")
    body = tex.split("position at", 1)[-1]
    body = body[: body.rfind("Best regards")] if "Best regards" in body else body
    plain = body.replace("\\", "")
    print(f"\n[{_PROVIDER}:{live_client.config.model} cover letter {job}] "
          f"{len(plain.split())} words:\n{plain.strip()[:1500]}")

    invented = [t for t in build_vocabulary([]) if mentions(plain, t) and not mentions(facts, t)]
    assert not invented, f"cover letter claims {invented}"
    allowed = set(re.findall(r"\d+", facts + description)) | {_years(info)}
    numbers = set(re.findall(r"\d+", plain)) - allowed
    assert not numbers, f"cover letter invents numbers {numbers}"

    if shutil.which("xelatex"):
        from cover_letter.compiler import compile_pdf
        assert compile_pdf(tex_path).exists()
