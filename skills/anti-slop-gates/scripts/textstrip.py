"""What a template RENDERS, separated from what its author wrote to the next developer.

A note in a comment block is not a surface a user sees, and template control flow is an
instruction to the renderer, not text in the output. Every gate that reads a template needs this
same distinction: `class="tag basis{% if x %} unset{% endif %}"` must read as the three real
classes and never as the template keywords `if`/`x`.

Covers HTML comments plus the Django/Jinja/Nunjucks/Twig family of `{# #}`, `{% %}` and `{{ }}`
blocks. JSX expressions are NOT handled here — the naming gate reads className attributes with
its own regex and the DOM gate scans real HTML templates only.

Developer comments must not be mistaken for rendered content.
"""

from __future__ import annotations

import re

COMMENT_RE = re.compile(
    r"<!--.*?-->|{#.*?#}|{%\s*comment(?:\s+[^%]*)?%}.*?{%\s*endcomment\s*%}",
    re.DOTALL | re.IGNORECASE,
)

TAG_RE = re.compile(r"{%.*?%}|{{.*?}}", re.DOTALL)


def strip_comments(text: str, *, keep_lines: bool = False) -> str:
    """Return the template with every comment removed.

    keep_lines replaces each comment with its own newlines so reported line numbers still match
    the raw file — callers that report path:line want this.
    """
    if keep_lines:
        return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return COMMENT_RE.sub(" ", text)


def strip_tags(text: str) -> str:
    """Return the template with `{% ... %}` and `{{ ... }}` blanked to a space.

    A space, not an empty string: `class="a{% if x %}b{% endif %}"` must read as the two classes
    `a` and `b`, never as the single class `ab`.
    """
    return TAG_RE.sub(" ", text)


def rendered_text(text: str) -> str:
    """Return only what a template actually puts on the page — no comments, no template tags."""
    return strip_tags(strip_comments(text))
