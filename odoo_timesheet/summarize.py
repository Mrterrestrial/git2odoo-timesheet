"""Turn a day's commits into a timesheet description.

Two engines:
  * "simple": cleans commit subjects and joins them (deterministic, no dependency).
  * "claude": one call to the `claude` CLI for the whole plan, returning JSON.
Mode "auto" uses claude when the CLI is available and falls back to simple on any failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

PREFIX = re.compile(
    r"^\s*(?:\[(?P<tag>[A-Za-z]+)\]|(?P<type>feat|feature|fix|bugfix|chore|refactor|docs|doc|test|tests|perf|build|ci|style|add|change|remove)(?:\([^)]*\))?!?:)\s*",
    re.I,
)
VERBS = {
    "add": "added", "feat": "added", "feature": "added", "new": "added",
    "fix": "fixed", "bugfix": "fixed", "hotfix": "fixed",
    "change": "changed", "update": "updated", "refactor": "refactored", "remove": "removed", "delete": "removed",
    "perf": "optimised", "docs": "documented", "doc": "documented", "test": "tested", "tests": "tested",
    "chore": "maintained", "build": "updated build of", "ci": "updated CI for", "style": "restyled",
}


def clean_subject(subject: str) -> str:
    """'[FIX] Dossier PDF prints notes' -> 'fixed dossier PDF prints notes'."""
    m = PREFIX.match(subject)
    text = subject[m.end():] if m else subject
    text = text.strip().rstrip(".")
    if not text:
        return ""
    verb = VERBS.get(((m.group("tag") or m.group("type")) if m else "").lower())
    text = text[0].lower() + text[1:] if verb else text[0].upper() + text[1:]
    return f"{verb} {text}" if verb else text


def simple_group_description(repos: list[str], commits: list, limit: int) -> str:
    parts = []
    for c in commits:
        s = clean_subject(c.subject)
        if s and s not in parts:
            parts.append(s)
    prefix = f"{', '.join(repos)}: " if repos else ""
    text = prefix + "; ".join(parts) + "."
    if len(text) > limit:
        text = text[: limit - 1].rsplit(";", 1)[0].rstrip(" ;,") + "."
    return text


def simple_gap_description(neighbour_commits: list, limit: int) -> str:
    topics = []
    for c in neighbour_commits[:4]:
        s = clean_subject(c.subject)
        if s:
            topics.append(re.sub(r"^(added|fixed|changed|updated|refactored|removed|optimised|documented|tested|maintained)\s+", "", s))
    if not topics:
        return "Development work (no commits on this day; please describe)."
    text = "Work in progress on: " + "; ".join(topics) + "."
    return text if len(text) <= limit else text[: limit - 1].rsplit(";", 1)[0] + "."


# ------------------------------------------------------------------ claude
def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("no JSON object in claude output")
    return json.loads(text[a:b + 1])


def claude_descriptions(items: list[dict], context: list[dict], settings, profile: str = "",
                        catalogue: str = "", allowed_tasks: list[str] | None = None) -> dict[str, dict]:
    """items: [{key, date, repos, task_guess, commits:[{repo,subject,body}], gap: bool}].

    Returns {key: {"task": str, "description": str}}. The task is one of `allowed_tasks`
    (falls back to the entry's task_guess).
    """
    binary = settings.resolve_claude()
    if not binary:
        raise RuntimeError("claude CLI not found")
    payload = {
        "language": settings.language,
        "max_chars": settings.max_description_chars,
        "allowed_tasks": allowed_tasks or [],
        "author_profile_markdown": profile or "(no profile given)",
        "task_catalogue_markdown": catalogue or "(no catalogue given; keep task_guess)",
        "entries": items,
        "context_commits_outside_period": context,
    }
    prompt = (
        "You write daily timesheet entries for a member of a software project, to be read by a project manager.\n"
        "`author_profile_markdown` says who the person is, what they are responsible for and what they are working "
        "on these days; `task_catalogue_markdown` explains what belongs to each Odoo task. Use both.\n"
        "For every entry in `entries` return an object with:\n"
        "  - `task`: the best task from `allowed_tasks` for that work, judged with the catalogue and the profile; "
        "keep `task_guess` unless the catalogue clearly points elsewhere; for `gap: true` entries the guess is only a "
        "default, so pick the task that matches the work you infer.\n"
        "  - `description`: 1 to 2 sentences in the requested language, past tense, plain business wording, at most "
        "max_chars characters, no markdown, no commit hashes, no bullet points. Name concrete features or fixes, "
        "not file names. When `repos` lists several repositories, prefix with their names. Follow the profile's "
        "writing preferences.\n"
        "Entries with `gap: true` have no commits that day: infer from the profile's current focus, the neighbouring "
        "entries and `context_commits_outside_period` what was most likely in progress, and start the text with "
        "'Work in progress on'.\n"
        "Answer with ONLY a JSON object mapping each entry `key` to its object, nothing else.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    cmd = [binary, "-p", prompt, "--output-format", "text"]
    if settings.claude_model:
        cmd += ["--model", settings.claude_model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.summarizer_timeout, env=_clean_env())
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    data = _extract_json(proc.stdout)
    out: dict[str, dict] = {}
    allowed = set(allowed_tasks or [])
    for it in items:
        val = data.get(it["key"], "")
        if isinstance(val, dict):
            txt, task = str(val.get("description", "")).strip(), str(val.get("task", "")).strip()
        else:
            txt, task = str(val).strip(), ""
        if txt:
            if task not in allowed:
                task = it.get("task_guess", "")
            out[it["key"]] = {"task": task, "description": txt[: settings.max_description_chars]}
    missing = [it["key"] for it in items if it["key"] not in out]
    if missing:
        raise RuntimeError(f"claude answer lacks {len(missing)} entries: {missing[:5]}")
    return out
