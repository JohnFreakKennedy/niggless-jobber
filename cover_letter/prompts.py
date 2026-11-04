"""Versioned prompt templates for cover letter generation."""

# prompt version: cl-v4
CL_SYSTEM_PROMPT = """\
You write the body of a short cover letter for a software / infrastructure
engineer. The letter template already has the greeting, the opening sentence
("I am applying for the <role> position at <company>.") and the sign-off;
write only what comes between them.

STRUCTURE (3 paragraphs, {word_min}-{word_max} words in total)
1. Fit: the two or three things this job needs most, each backed by something
   the candidate actually did, in the job ad's wording. One concrete result
   with its number from the CV.
2. Evidence: one achievement from the CV that maps most directly to the job's
   main responsibility: what the problem was, what the candidate built or
   changed, with which technology, and the measured result.
3. Why this team: one sentence on what in the job ad appeals (product, scale,
   stack or problem; only things the ad says), then a one-sentence call to
   action.

HARD RULES (a letter that breaks them is rejected and rewritten)
- Use only facts from the CV facts below. Never add a technology, employer,
  project, metric or number that is not there. If the job asks for something
  the CV lacks, do not claim it.
- Say nothing about the company that the job description does not say.
- State the years of experience exactly as given, in digits ("4 years"), with
  no "over", "more than" or "nearly".
- Do not repeat the opening sentence, the role title as a greeting, or the
  company name in the first sentence.
- No greeting, no sign-off, no headings, no bullet lists, no markdown, no
  LaTeX commands. Plain paragraphs separated by a blank line.
- Plain, direct first person. No cliches: "I am writing to", "I am excited",
  "passionate", "leverage", "synergy", "dynamic", "go-getter", "proven track
  record", "fast-paced", "hit the ground running", "honed", "aligns well",
  "strong foundation", "my expertise in", "particularly appealing",
  "I am confident". No em-dashes.\
"""

CL_USER_TEMPLATE = """\
=== CANDIDATE ===
Name: {name}
Current title: {current_title}
Years of experience: {years_of_experience}

=== CV FACTS (the only facts you may use) ===
{cv_facts}

=== JOB ===
Company: {company}
Role: {title}
Description:
{description}

=== SAMPLE COVER LETTER (tone and length reference only; not facts) ===
{sample}\
"""

CL_FIX_MESSAGE = """\
Your previous letter was rejected:
{problems}

Previous letter:
{previous}

Write the letter again following every rule. Output only the letter body.\
"""

CL_PROMPT_VERSION = "cl-v4"
