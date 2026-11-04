"""Checks and keyword scoring for AI-proposed CV edits.

The model proposes edits as JSON; nothing it writes reaches the CV until it
passes these checks. A rejected edit keeps the original text for that unit
only, so one bad bullet never throws away the rest of the tailoring.
"""
from __future__ import annotations

import json
import re

from cv_editor.cleaner import clean_description

_TECH_WORDS = """
python java javascript typescript go golang rust c++ c# .net kotlin swift scala ruby php
perl matlab bash powershell sql nosql graphql elixir haskell clojure dart lua groovy
aws gcp azure openstack heroku digitalocean cloudflare vercel
kubernetes k8s docker podman helm kustomize argocd istio linkerd envoy nomad consul vault
terraform terragrunt pulumi ansible chef puppet saltstack cloudformation packer vagrant
jenkins circleci travis teamcity bamboo spinnaker tekton
prometheus grafana loki datadog splunk elk elasticsearch logstash kibana
opentelemetry jaeger zipkin sentry pagerduty opsgenie nagios zabbix
postgresql postgres mysql mariadb mongodb redis memcached cassandra dynamodb couchbase
clickhouse snowflake bigquery redshift oracle sqlite neo4j
kafka rabbitmq activemq sqs sns pubsub nats celery airflow spark hadoop flink dbt
react angular vue svelte next.js node.js nodejs django flask fastapi spring
rails laravel asp.net grpc nginx haproxy traefik
ec2 s3 eks ecs fargate rds cloudwatch gke aks
pytest junit selenium playwright cypress jest
tensorflow pytorch pandas numpy scikit-learn
jira confluence
"""

# Concrete technologies. A rewritten unit may mention one of these only if the
# CV already mentions it somewhere: this is what stops the model from copying
# the job's stack into the CV. JD skills are added to this list per job.
TECH_TERMS: frozenset[str] = frozenset(_TECH_WORDS.split()) | frozenset({
    "github actions", "gitlab ci", "new relic", "spring boot", "google cloud",
    "azure devops", "argo cd", "cloud run", "aws lambda",
})

# Practices and concepts: the job's wording for them is fair to reuse, since
# they describe work rather than claim a tool. Never enforced as "new".
GENERIC_TERMS: frozenset[str] = frozenset({
    "ci/cd", "ci", "cd", "devops", "sre", "microservices", "agile", "scrum", "kanban",
    "rest", "api", "apis", "rest api", "cloud", "automation", "testing", "monitoring",
    "observability", "infrastructure as code", "iac", "containers", "containerization",
    "linux", "security", "networking", "on-call", "incident response", "backend",
    "frontend", "distributed systems", "high availability", "scalability", "tdd",
    "code review", "documentation", "english", "communication", "teamwork",
})

# Short or common-word terms are matched case-sensitively so "go live" does not
# count as Go and "r" is never matched inside prose.
_CASE_SENSITIVE = {"go": "Go", "rust": "Rust", "swift": "Swift",
                   "spark": "Spark", "chef": "Chef", "puppet": "Puppet", "vault": "Vault",
                   "consul": "Consul", "express": "Express", "flask": "Flask",
                   "lambda": "Lambda", "flux": "Flux", "argo": "Argo", "tempo": "Tempo",
                   "nomad": "Nomad", "envoy": "Envoy", "oracle": "Oracle", "git": "Git",
                   "rails": "Rails", "react": "React", "spring": "Spring", "next.js": "Next.js"}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_UNESCAPED_SPECIAL_RE = re.compile(r"(?<!\\)([&%#_])")
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")

# A rewrite may grow at most this much, so the CV keeps fitting on one page.
MAX_GROWTH_RATIO = 1.25
MAX_GROWTH_CHARS = 40
MIN_SHRINK_RATIO = 0.4
# A rewritten bullet must keep at least this share of the original's content
# words; below it the model has swapped in another bullet's facts.
MIN_BULLET_OVERLAP = 0.5

# Words that claim a level. A rewrite may use one only if the CV already does.
SENIORITY_WORDS = ("senior", "lead", "staff", "principal", "head", "chief",
                   "architect", "director", "expert")

_STOPWORDS = frozenset("""
a an the and or of for to in on at by with from into over as is are was were be been
this that these those it its our their his her my we i you they them
""".split())
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#./-]*")


def _term_pattern(term: str) -> re.Pattern:
    body = re.escape(term).replace(r"\ ", r"[\s-]+")
    flags = 0 if term in _CASE_SENSITIVE else re.IGNORECASE
    if term in _CASE_SENSITIVE:
        body = re.escape(_CASE_SENSITIVE[term])
    return re.compile(r"(?<![A-Za-z0-9+#.])" + body + r"(?![A-Za-z0-9+#])", flags)


_PATTERN_CACHE: dict[str, re.Pattern] = {}


def mentions(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    pat = _PATTERN_CACHE.get(term)
    if pat is None:
        pat = _PATTERN_CACHE[term] = _term_pattern(term)
    # LaTeX escapes (C\#, R\&D) should match their plain spelling.
    return bool(pat.search(text.replace("\\", "")))


def build_vocabulary(jd_terms: list[str]) -> set[str]:
    vocab = set(TECH_TERMS) | set(_CASE_SENSITIVE) | {"lambda", "aws lambda"}
    for term in jd_terms:
        t = term.strip().lower()
        if t and t not in GENERIC_TERMS and len(t) <= 40:
            vocab.add(t)
    return vocab


def latex_escape(text: str) -> str:
    """Escape bare LaTeX specials the model wrote as plain text (40% -> 40\\%)."""
    return _UNESCAPED_SPECIAL_RE.sub(r"\\\1", text)


def _braces_balanced(text: str) -> bool:
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def check_rewrite(
    original: str,
    proposed: str,
    cv_text: str,
    vocabulary: set[str],
    allowed_numbers: set[str] = frozenset(),
    number_source: str | None = None,
    term_source: str | None = None,
    min_overlap: float = 0.0,
    max_chars: int | None = None,
    min_chars: int | None = None,
) -> tuple[str | None, str]:
    """
    Validate one rewritten unit. Returns (clean_text, "") when accepted or
    (None, reason) when rejected.

    *number_source* is the text whose numbers the rewrite may use (the
    original bullet by default; the whole CV for the summary).
    *term_source* is the text whose technologies the rewrite may name
    (the whole CV by default; the bullet's own role for a bullet).
    *min_overlap* is the share of the original's content words the rewrite
    must keep. *max_chars* and *min_chars* replace the default length limits.
    """
    if not isinstance(proposed, str):
        return None, "not a string"
    text = " ".join(proposed.split())
    if not text:
        return None, "empty"
    if text == " ".join(original.split()):
        return original, ""

    text = latex_escape(text)
    if not _UNESCAPED_DOLLAR_RE.search(original):
        text = _UNESCAPED_DOLLAR_RE.sub(r"\\$", text)
    text = clean_description(text)

    if not _braces_balanced(text):
        return None, "unbalanced braces"
    new_commands = set(_COMMAND_RE.findall(text)) - set(_COMMAND_RE.findall(original))
    if new_commands:
        return None, f"adds LaTeX commands {sorted(new_commands)}"

    limit = max_chars or max(len(original) * MAX_GROWTH_RATIO, len(original) + MAX_GROWTH_CHARS)
    if len(text) > limit:
        return None, f"too long ({len(text)} chars, limit {int(limit)})"
    if len(text) < (len(original) * MIN_SHRINK_RATIO if min_chars is None else min_chars):
        return None, f"too short ({len(text)} chars from {len(original)})"

    allowed_terms = term_source if term_source is not None else cv_text
    invented = sorted(t for t in vocabulary if mentions(text, t) and not mentions(allowed_terms, t))
    if invented:
        where = "this role" if term_source is not None else "the CV"
        return None, f"mentions skills not in {where}: {', '.join(invented)}"

    claimed = sorted(w for w in SENIORITY_WORDS if mentions(text, w) and not mentions(cv_text, w))
    if claimed:
        return None, f"claims seniority not in the CV: {', '.join(claimed)}"

    if min_overlap:
        kept = word_overlap(original, text)
        if kept < min_overlap:
            return None, f"keeps only {kept:.0%} of the original wording (facts swapped?)"

    source_numbers = set(_NUMBER_RE.findall(number_source if number_source is not None else original))
    new_numbers = set(_NUMBER_RE.findall(text)) - source_numbers - set(allowed_numbers)
    if new_numbers:
        return None, f"adds numbers {sorted(new_numbers)}"

    return text, ""


def content_words(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower().replace("\\", ""))
    return {w.strip(".,;:") for w in words if len(w) >= 3 and w not in _STOPWORDS}


def word_overlap(original: str, rewritten: str) -> float:
    """Share of the original's content words (or their stems) the rewrite keeps."""
    orig = content_words(original)
    if not orig:
        return 1.0
    new = content_words(rewritten)
    new_stems = {w[:5] for w in new}
    kept = sum(1 for w in orig if w in new or w[:5] in new_stems)
    return kept / len(orig)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_sentences(text: str) -> list[str]:
    return [p for p in _SENTENCE_SPLIT_RE.split(" ".join(text.split())) if p]


def check_permutation(proposed, expected: list[str]) -> list[str] | None:
    """
    Return the new order when *proposed* is a reorder of *expected*, else None.

    Ids the model left out keep their original relative order after the ones
    it listed (small models often drop the last few). Unknown or repeated ids
    mean the model confused the lists, so the whole order is rejected.
    """
    if isinstance(proposed, str):
        proposed = [proposed]
    if not isinstance(proposed, list) or not all(isinstance(x, str) for x in proposed):
        return None
    if not proposed or len(set(proposed)) != len(proposed) or not set(proposed) <= set(expected):
        return None
    return proposed + [x for x in expected if x not in proposed]


def check_skill_items(proposed, original: list[str]) -> list[str] | None:
    """
    Accept a reorder of a skill line's items (matched case-insensitively,
    original spelling kept). Items the model left out are kept, after the
    listed ones in their original order: tailoring never removes a skill.
    Unknown or repeated items reject the whole line.
    """
    if isinstance(proposed, str):
        proposed = [s.strip() for s in proposed.split(",") if s.strip()]
    if not isinstance(proposed, list) or not all(isinstance(x, str) for x in proposed):
        return None
    by_key = {o.strip().lower(): o for o in original}
    keys = [p.strip().lower() for p in proposed]
    if not keys or len(set(keys)) != len(keys) or not set(keys) <= set(by_key):
        return None
    listed = [by_key[k] for k in keys]
    return listed + [o for o in original if o not in listed]


# ---------------------------------------------------------------------------
# Keyword relevance (deterministic, used for scoring and as a fallback order)
# ---------------------------------------------------------------------------

def relevance(text: str, required: list[str], preferred: list[str]) -> int:
    return 2 * sum(mentions(text, k) for k in required) + sum(mentions(text, k) for k in preferred)


def rank_by_relevance(ids: list[str], text_of, required: list[str], preferred: list[str]) -> list[str]:
    """Stable sort: most relevant first, ties keep their original order."""
    return sorted(ids, key=lambda i: -relevance(text_of(i), required, preferred))


def coverage(text: str, keywords: list[str]) -> dict:
    keywords = list(dict.fromkeys(k.strip() for k in keywords if k and k.strip()))
    found = [k for k in keywords if mentions(text, k)]
    missing = [k for k in keywords if k not in found]
    score = round(len(found) / len(keywords), 2) if keywords else None
    return {"score": score, "found": found, "missing": missing}


# ---------------------------------------------------------------------------
# JSON from model output
# ---------------------------------------------------------------------------

def parse_json_object(raw: str) -> dict:
    """
    Parse the first JSON object in *raw*. Tolerates markdown fences and
    prose around the object, which small local models often add.
    Raises ValueError when no object can be parsed.
    """
    if not raw:
        raise ValueError("empty response")
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj, error = None, "no JSON object in response"
        decoder = json.JSONDecoder()
        for m in re.finditer(r"\{", text):
            try:
                obj, _ = decoder.raw_decode(text[m.start():])
                break
            except json.JSONDecodeError as exc:
                error = f"invalid JSON: {exc}"
        if obj is None:
            raise ValueError(error)
    if not isinstance(obj, dict):
        raise ValueError("response is not a JSON object")
    return obj


def str_list(value, limit: int = 30) -> list[str]:
    """Coerce a model-provided list (or comma string) into a clean list of strings."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out[:limit]
