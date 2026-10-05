"""MkDocs hooks.

README.md and CHANGELOG.md are symlinked into docs/, so their links are written for GitHub, from the repository root
(`docs/get-started.md`, `CONTRIBUTING.md`). On the docs site they live inside docs/, so links into docs/ lose the
prefix and links to other repository files point at GitHub.
"""

import re

REPO = "https://github.com/adeelahmad/lense/blob/main/"
FROM_ROOT = {"README.md", "CHANGELOG.md"}

# A markdown link target, or an HTML src/srcset, that is a relative path
LINK = re.compile(r'(\]\(|src="|srcset=")(?!https?:|mailto:|#|/)([^)"\s]+)')


def _rewrite(match):
    lead, target = match.groups()
    if target.startswith("docs/"):
        target = target[len("docs/") :] or "README.md"
    else:
        target = REPO + target
    return lead + target


def on_page_markdown(markdown, page, **kwargs):
    if page.file.src_uri in FROM_ROOT:
        return LINK.sub(_rewrite, markdown)
    return markdown
