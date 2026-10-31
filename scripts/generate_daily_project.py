#!/usr/bin/env python3
"""Generate a standalone web app and publish it to a new private GitHub repo."""
from __future__ import annotations
import base64
import datetime as dt
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_API = "https://api.github.com"
OPENAI_API = "https://api.openai.com/v1/responses"
GITHUB_API_VERSION = "2026-03-10"
MAX_HTML_BYTES = 300_000


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def request_json(url: str, method: str = "GET", token: str | None = None,
                 payload: dict | None = None, openai: bool = False) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if openai:
        headers["Content-Type"] = "application/json"
    else:
        headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as error:
        detail = error.read(2000).decode("utf-8", errors="replace")
        fail(f"Request to {url.split('?')[0]} failed with HTTP {error.code}: {detail}")
    except (TimeoutError, URLError) as error:
        fail(f"Request to {url.split('?')[0]} failed: {error}")
    raise AssertionError("unreachable")


def repository_exists(owner: str, name: str, token: str) -> bool:
    url = f"{GITHUB_API}/repos/{owner}/{name}"
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    })
    try:
        with urlopen(request, timeout=30) as response:
            return response.status == 200
    except HTTPError as error:
        if error.code == 404:
            return False
        detail = error.read(2000).decode("utf-8", errors="replace")
        fail(f"Could not check {owner}/{name} (HTTP {error.code}): {detail}")
    except (TimeoutError, URLError) as error:
        fail(f"Could not check {owner}/{name}: {error}")
    raise AssertionError("unreachable")


def project_response(api_key: str, date_id: str, model: str) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "html": {"type": "string"},
        },
        "required": ["title", "description", "html"],
        "additionalProperties": False,
    }
    system = (
        "You create original, beginner-friendly software projects. Produce a "
        "complete, polished, responsive, accessible single-page web app in one "
        "index.html file, using inline HTML, CSS, and JavaScript only. It must "
        "work offline, have a real interactive feature, clear instructions, "
        "validation, and useful empty states. Do not use external URLs, libraries, "
        "images, fonts, APIs, analytics, cookies, tracking, backend services, or "
        "credentials. Do not include dangerous or deceptive behavior. Avoid "
        "generic todo lists, calculators, clocks, and landing pages. Return JSON."
    )
    user = (
        f"Invent one fresh, practical mini-project for {date_id}. Choose the idea "
        "yourself. Make it meaningfully useful and distinctive. Return a complete "
        "HTML document with a doctype. Use semantic elements, keyboard-accessible "
        "controls, responsive styling, and no external resources. Do not include "
        "markdown fences."
    )
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "daily_project",
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": 9000,
        "store": False,
    }
    response = request_json(
        OPENAI_API, method="POST", token=api_key, payload=payload, openai=True
    )
    if response.get("status") != "completed":
        fail(f"OpenAI response did not complete: {response.get('status', 'unknown')}")
    for item in response.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                fail("The project request was refused by the model.")
            if part.get("type") == "output_text":
                try:
                    return json.loads(part["text"])
                except (KeyError, json.JSONDecodeError):
                    fail("The model returned invalid structured project data.")
    fail("The model returned no project content.")


def validate_project(project: dict) -> tuple[str, str, str]:
    title = project.get("title", "").strip()
    description = project.get("description", "").strip()
    html = project.get("html", "").strip()
    if not title or len(title) > 100:
        fail("Generated project title is missing or too long.")
    if not description or len(description) > 500:
        fail("Generated project description is missing or too long.")
    if not html or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        fail("Generated HTML is empty or exceeds the 300 KB limit.")
    if not re.match(r"(?is)^<!doctype html\\b", html):
        fail("Generated HTML is not a complete HTML document.")
    if not re.search(r"(?is)<html\\b", html) or not re.search(r"(?is)</html\\s*>", html):
        fail("Generated HTML is missing its document element.")
    blocked = [
        (r"(?is)<(?:iframe|object|embed)\\b", "embedded content"),
        (r"(?is)<script\\b[^>]*\\bsrc\\s*=", "external scripts"),
        (r"(?is)<link\\b[^>]*\\bhref\\s*=\\s*['\\"]https?://", "remote stylesheets"),
        (r"(?is)\\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\\b", "network calls"),
        (r"(?is)\\b(?:eval|Function)\\s*\\(", "dynamic code execution"),
        (r"(?is)javascript\\s*:", "javascript URLs"),
    ]
    for pattern, label in blocked:
        if re.search(pattern, html):
            fail(f"Generated HTML contains disallowed {label}; no repository was created.")
    return title, description, html


def create_repository(name: str, description: str, token: str) -> dict:
    payload = {
        "name": name,
        "description": description[:350],
        "private": True,
        "auto_init": True,
        "has_issues": False,
        "has_projects": False,
        "has_wiki": False,
    }
    return request_json(
        f"{GITHUB_API}/user/repos", method="POST", token=token, payload=payload
    )


def put_file(owner: str, repo: str, branch: str, path: str, content: str,
             message: str, token: str, sha: str | None = None) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    request_json(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        method="PUT",
        token=token,
        payload=payload,
    )


def publish_project(owner: str, repo: str, title: str, description: str,
                    html: str, token: str) -> None:
    repo_data = request_json(f"{GITHUB_API}/repos/{owner}/{repo}", token=token)
    branch = repo_data.get("default_branch") or "main"
    readme_url = (
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/README.md?ref={branch}"
    )
    readme_file = request_json(readme_url, token=token)
    readme = (
        f"# {title}\\n\\n{description}\\n\\n"
        f"Generated automatically on {dt.datetime.now(dt.timezone.utc).date()}. "
        "Review the code before using it.\\n"
    )
    put_file(owner, repo, branch, "README.md", readme,
             "Add generated project overview", token, readme_file.get("sha"))
    put_file(owner, repo, branch, "index.html", html,
             "Add generated standalone web app", token)


def main() -> None:
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    github_token = os.environ.get("GH_CREATE_REPO_TOKEN", "").strip()
    if not openai_key:
        fail("Add OPENAI_API_KEY as a repository Actions secret.")
    if not github_token:
        fail("Add GH_CREATE_REPO_TOKEN as a repository Actions secret.")
    owner = os.environ.get("GITHUB_OWNER", "eDDie-05").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner):
        fail("GITHUB_OWNER is not a valid GitHub login.")
    date_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    repo_name = f"daily-project-{date_id}"
    if repository_exists(owner, repo_name, github_token):
        print(f"{owner}/{repo_name} already exists; nothing to do.")
        return
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-6-astra"
    print(f"Generating project for {date_id} with {model}.")
    title, description, html = validate_project(
        project_response(openai_key, date_id, model)
    )
    if repository_exists(owner, repo_name, github_token):
        print(f"{owner}/{repo_name} was created by another run; skipping.")
        return
    create_repository(repo_name, description, github_token)
    print(f"Created private repository {owner}/{repo_name}.")
    publish_project(owner, repo_name, title, description, html, github_token)
    print(f"Published project files to https://github.com/{owner}/{repo_name}.")


if __name__ == "__main__":
    main()
