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
  provider: openai          # primary provider: openai | anthropic | gemini | ollama
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

The `AIClient` tries the primary provider first. If that call raises, it waits
`fallback_delay` seconds (default 10) and tries `fallback_provider` once. With
`fallback_provider: none`, or a fallback identical to the primary (same provider and model),
the original error is raised instead.

Environment overrides: `NIGGLESS_AI_PROVIDER` replaces the provider and `NIGGLESS_AI_MODEL`
the model. When only the provider is overridden to a different one, that provider's default
model is used (`gpt-4o`, `claude-3-5-sonnet-20241022`, `gemini-3.8-flash`, `llama3.1:8b`).

`AIClient.complete(system_prompt, user_message, max_tokens=None, json_mode=False)` —
`json_mode=True` asks for a JSON object: OpenAI `response_format={"type": "json_object"}`,
Gemini `response_mime_type="application/json"`, Ollama `"format": "json"`. Anthropic has no
such flag; the prompt must ask for JSON.

### Local models (Ollama)

`provider: ollama` sends a non-streaming `POST {ollama_base_url}/api/chat` with
`options.temperature`, `options.num_predict` (= max tokens) and `options.num_ctx`
(`ollama_num_ctx`, default 8192). No API key is needed.

```yaml
ai:
  provider: ollama
  model: llama3.1:8b
  fallback_provider: none
  ollama_base_url: http://localhost:11434   # OLLAMA_HOST env wins; scheme optional
  ollama_num_ctx: 8192
  request_timeout: 300                      # seconds
```

Run `ollama serve` and `ollama pull <model>` first. If Ollama is not reachable the error asks
whether `ollama serve` is running; an HTTP 404 means the model is not pulled and the error
prints the `ollama pull` command.

---

## CV Tailoring

### Goal

Make the match between the CV and the job obvious at a glance, without adding a single fact.
The summary is rewritten to name the strongest overlaps, bullets are reworded with the job's
vocabulary and reordered most-relevant-first, and skills are reordered so the ones the job asks
for come first. Nothing is ever invented or removed.

### How it works (`cv_editor/editor.py::tailor_cv`)

The model never sees or writes LaTeX structure. It works on JSON:

1. **Pre-patch.** `\jobtitle` is set to the job's title (LaTeX-escaped) and every
   "N years of experience" gets the value computed from `personal_info.json` `work_history`.
2. **Parse** (`cv_editor/structure.py`). The editable units are the summary (between
   `% @summary-begin` and `% @summary-end`), each `\resumeItem{...}` bullet grouped by its
   `\resumeSubheading` role, prose paragraphs under `\resumeSubheadingSimple` (older
   templates), and each `\skillline{Category}{a, b, c}` row. Everything else is copied byte
   for byte. A template with none of these is used as is, with a warning.
3. **Job analysis** (AI call 1, JSON mode). It extracts required and preferred skills,
   seniority, responsibilities and domain keywords. On failure, `listing.skills` is used.
   Required skills the CV does not mention are the **gaps**. They are passed to the model
   as "never mention".
4. **Edits** (AI call 2, JSON mode, one retry on broken JSON). The model returns `summary`,
   `bullets` (by id), `bullet_order` (per role), `skills` (per line) and `skill_order`.
5. **Checks** (`cv_editor/tailoring.py`). Each edit is checked on its own. A failing edit
   keeps the original text for that unit only:
   - no technology the CV does not already mention, and for a bullet none its own role
     does not mention;
   - no number that is not in the original bullet (the whole CV, for the summary);
   - no seniority word (senior, lead, architect, ...) the CV does not use;
   - a bullet keeps at least 50% of its original content words, which catches facts
     swapped between bullets;
   - a bullet may grow at most 25% (or 40 characters), the summary at most 60% (or 360
     characters);
   - balanced braces, no new LaTeX commands, and bare `& % # _` are escaped;
   - orders must be reorders. Ids left out keep their place after the listed ones, and an
     unknown or repeated id rejects the whole order. Skill items are never added or removed.
   For the summary, a leading "Senior/Lead" the CV does not support is dropped. A failing
   sentence is removed and the rest is kept, as long as the opening sentence survives.
   The buzzword cleaner (`cv_editor/cleaner.py`) runs on AI-written text only.
6. **Fallback.** When there are no valid AI edits, the skills are still reordered by job
   relevance, which is deterministic.
7. **Post-patch** title and years again, then write `<Company>_cv.tex`.

### Tailoring report

Next to every CV, `<Company>_cv_tailoring.json` records:
- the job analysis and the gaps;
- keyword coverage before and after (`score`, `found`, `missing`): whether the CV names
  the job's skills anywhere;
- prominence before and after: the share of the job's skills named in the summary, the
  first bullet of each role and the top two skill lines. This is what tailoring raises;
- which units were edited or reordered;
- every discarded edit with its reason;
- the raw AI edits.

Read it to see why a CV came out the way it did.

### Template requirements

Start from `templates/cv_sample.tex`. Keep `\newcommand{\jobtitle}{...}`, the summary markers,
`\resumeItem` bullets and `\skillline` rows. Write the full CV you would be happy to send:
tailoring only re-weights it.

### One page

`compile_pdf(tex, fit_one_page=True)` (used for every CV) reads the page count from the xelatex
log. If the CV spills onto a second page, it recompiles with tighter spacing presets: line
spread first, then a smaller body font, then smaller margins. It stops at the first preset
that fits and writes it into the `.tex`. If even the tightest preset is not enough, a warning
asks you to shorten the base CV.

### Local models

The checks are what make small local models (Ollama, 4-8B) safe: they often return
partial orders, copy the job's title or name skills the CV lacks, and those parts are dropped
while the rest is used. Expect fewer rewrites from them than from a large hosted model.
`tests/test_live_models.py` runs the whole flow (CV and cover letter) against a real model
when `NIGGLESS_LIVE=1`; choose it with `NIGGLESS_LIVE_PROVIDER` and `NIGGLESS_LIVE_MODEL`.

### Token budget

| Component | Approx tokens |
|---|---|
| JD analysis (in + out) | ~1500-2500 |
| Tailoring prompt + JD excerpt | ~1500-2000 |
| CV units (JSON) | ~500-1200 |
| Output (edits JSON) | ~400-1200 |

This fits easily in an 8k-token context, which is the Ollama default here (`ollama_num_ctx`).

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
# prompt version: cv-v6
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
