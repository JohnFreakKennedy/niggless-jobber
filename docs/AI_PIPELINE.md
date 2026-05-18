# AI Pipeline

This document covers how the system uses AI to tailor your CV and generate cover letters for
each job, how prompts are designed, what guardrails prevent hallucination, and how the LaTeX
compilation step works.

---

## Overview

For every new job listing that passes deduplication, the AI pipeline runs two tasks in sequence:

```
JobListing
    |
    +--> cv_editor/editor.py
    |       reads templates/cv.tex
    |       calls AI API
    |       writes output/<job_id>/cv.tex
    |       compiles output/<job_id>/cv.pdf
    |
    +--> cover_letter/generator.py
            reads personal_info.json
            reads templates/cover_letter_sample.txt (if present)
            calls AI API
            writes output/<job_id>/cover_letter.tex
            compiles output/<job_id>/cover_letter.pdf
```

Both tasks share a single `AIClient` wrapper in `cv_editor/client.py` that handles provider
selection (OpenAI vs Anthropic), token counting, retries, and error logging.

---

## AI Provider Configuration

Set in `config.yaml`:

```yaml
ai:
  provider: openai          # primary provider: "openai" or "anthropic"
  model: gpt-4o             # model name used by primary provider
  fallback_provider: anthropic
  fallback_model: claude-3-5-sonnet-20241022
  cv_max_tokens: 4096
  cover_letter_max_tokens: 1024
  temperature: 0.3          # low temperature for factual fidelity
```

API keys are read from the Fernet vault:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

The `AIClient` tries the primary provider first. On `RateLimitError`, `APIConnectionError`, or
any 5xx response it waits 10 seconds and retries once, then falls back to the secondary provider.

---

## CV Tailoring

### Goal

Reorder and lightly rephrase the bullet points in your base CV so that the most relevant
experience appears near the top and the language mirrors the job description's vocabulary.
The AI must NOT invent new jobs, new skills, or new dates.

### Input

1. `templates/cv.tex` - your base CV in LaTeX
2. `description` field from `JobListing` - the cleaned plain-text job description
3. `skills` list from `JobListing` - extracted tech keywords

### System prompt (`cv_editor/prompts.py`)

```
You are an expert technical resume editor.

Rules you MUST follow without exception:
1. Never add any experience, skill, project, date, company, or job title that is not
   already present in the provided CV.
2. Never change dates, job titles, or company names.
3. Only reorder, rephrase, or emphasise existing content.
4. Preserve all LaTeX syntax exactly. Do not add or remove packages, commands, or
   environments.
5. Output ONLY the complete modified LaTeX source. No explanations. No markdown fences.
6. Keep the output within the same approximate page count as the input.

Your task:
- Read the job description below.
- Identify the top skills, technologies, and responsibilities the employer values.
- Reorder the bullet points within each job experience section so that the bullets most
  relevant to the job description appear first.
- Lightly rephrase up to 20% of bullet points to use vocabulary from the job description
  where the meaning is identical to the original.
- Do NOT add a "Skills" section item if it is not already in the CV.
- Do NOT remove existing bullet points; you may de-emphasise by moving them lower.
```

### User message format

```
=== JOB DESCRIPTION ===
{listing.description}

=== TOP KEYWORDS FROM JOB ===
{", ".join(listing.skills)}

=== YOUR CURRENT CV (LaTeX source) ===
{base_cv_content}
```

### Response handling

The response is expected to be valid LaTeX. The editor:

1. Checks that the response starts with `\documentclass` or `%` (comment) - if not, the run
   is aborted and the base CV is used unchanged (logged as a warning).
2. Diffs the response against the base CV using `difflib`. If more than 30% of lines differ
   it triggers a warning (possible hallucination) and keeps the base CV.
3. Writes the output to `output/<job_id>/cv.tex`.

### Token budget

| Component | Approx tokens |
|---|---|
| System prompt | ~350 |
| Job description | ~500-1500 |
| Base CV (LaTeX) | ~1500-3000 |
| Output (modified CV) | ~1500-3000 |
| Total | ~4000-8000 |

Use `gpt-4o` (128k context) or `claude-3-5-sonnet` (200k context). Both fit comfortably.

---

## Cover Letter Generation

### Goal

Write a personalised cover letter that references specific requirements from the job description,
connects them to relevant experience from your CV, and sounds human. Tone is professional but
direct (not sycophantic).

### Input

1. `personal_info.json` - your name, background summary, contact details
2. `listing.description` - job description
3. `listing.company` + `listing.title` - for salutation and opener
4. `templates/cover_letter_sample.txt` - your sample letter (optional but strongly recommended)

### System prompt (`cover_letter/prompts.py`)

```
You are a professional cover letter writer specialising in software engineering roles.

Rules:
1. The letter must be 3-4 paragraphs, 250-350 words total.
2. Paragraph 1: State the role, where you found it, and one strong reason you are a fit.
3. Paragraph 2: Describe the most relevant 2-3 experiences or projects from the candidate's
   background that map directly to requirements in the job description. Be specific.
4. Paragraph 3: Mention something specific about the company (mission, product, tech stack)
   that genuinely appeals to you. Do not be generic.
5. Paragraph 4 (closing): Call to action. One sentence.
6. Do NOT use the phrases "I am writing to", "I am excited", "passionate about", "leverage",
   "synergy", or any other cliche.
7. Output ONLY the LaTeX body content (the text that goes inside the letter environment).
   No preamble. No \begin{document}. No \end{document}.
8. Use the sample letter's tone and style as a guide if provided.
```

### User message format

```
=== CANDIDATE INFORMATION ===
Name: {personal_info.name}
Background: {personal_info.summary}
Key skills: {", ".join(personal_info.skills)}
Years of experience: {personal_info.years_of_experience}

=== JOB ===
Company: {listing.company}
Role: {listing.title}
Description:
{listing.description}

=== SAMPLE COVER LETTER (use as tone/style reference) ===
{sample_letter_content or "No sample provided."}
```

### Response handling

1. The response is inserted into `templates/cover_letter.tex` at the `%%BODY%%` placeholder.
2. A basic sanity check verifies word count is between 200 and 450 words.
3. If the check fails, the AI is called once more with an explicit length instruction prepended.
4. Result is written to `output/<job_id>/cover_letter.tex`.

---

## LaTeX Compilation

Handled by `cover_letter/compiler.py` (used for both CV and cover letter).

### Compilation command

```bash
xelatex -interaction=nonstopmode -output-directory=output/<job_id>/ output/<job_id>/cv.tex
```

`xelatex` is run **twice** to resolve cross-references and ToC entries (standard LaTeX practice).

### Font requirements

The base templates use common system fonts (e.g. `Libertine`, `TeX Gyre Termes`, or `Arial`
via `fontspec`). The exact font is set in the template. MacTeX includes all of these.

### Output structure

```
output/
  <job_id>/
    cv.tex                  AI-tailored CV source
    cv.pdf                  compiled PDF sent to employer
    cover_letter.tex        AI-generated cover letter source
    cover_letter.pdf        compiled PDF sent to employer
    cv.log                  xelatex log (kept for debugging)
    cover_letter.log
```

`<job_id>` is the SHA-256 fingerprint of the listing (first 12 hex chars used as directory name
to keep paths short).

### Compilation errors

If `xelatex` exits non-zero:

1. The `.log` file is inspected for common errors (missing package, undefined control sequence).
2. If a missing package is identified, `tlmgr install <package>` is attempted automatically.
3. If the error is in AI-generated content (e.g. invalid LaTeX syntax in the cover letter body),
   the body is stripped of all LaTeX commands and re-inserted as plain text, then recompiled.
4. If compilation still fails, the job is marked `failed` with `reason: latex_compile_error`.

---

## Prompt Versioning

All prompts are defined as module-level string constants in `cv_editor/prompts.py` and
`cover_letter/prompts.py`. Each constant has a version comment:

```python
# prompt version: cv-v3
CV_SYSTEM_PROMPT = """..."""
```

The version string is stored in the `applications` table alongside each application record so
you can correlate application outcomes with prompt versions and improve over time.

---

## Cost Estimates

Approximate OpenAI API costs per application (as of 2025 pricing, gpt-4o):

| Task | Input tokens | Output tokens | Cost (USD) |
|---|---|---|---|
| CV tailoring | ~4000 | ~2500 | ~$0.04 |
| Cover letter | ~2000 | ~400 | ~$0.01 |
| **Per application** | | | **~$0.05** |

At 20 applications per day: ~$1.00/day, ~$30/month.

---

## Dry Run Mode

When `python run.py --dry-run` is used:

- Scrapers run normally.
- AI pipeline runs normally and PDFs are generated.
- The application engine is skipped entirely.
- Results are logged to the DB with status `dry_run`.

This lets you inspect generated PDFs before enabling live submissions.
