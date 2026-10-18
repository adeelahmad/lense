# Changelog fragments

A pull request that deserves more than its title in the changelog (what users notice, what to do when upgrading) adds
one Markdown file here, named after its branch or number, such as `feeds-page.md` or `555.md`. Write the text as it
should read under the next version's heading in [CHANGELOG.md](../CHANGELOG.md): a bold lead, then what changed, with
links to the docs.

The Release workflow folds the fragments into the new version's section, oldest commit first, and deletes them
(`.github/scripts/release.py cut`). A change with a fragment isn't listed again by its title.

Fragments replace writing under `## Unreleased` in `CHANGELOG.md`: every pull request editing that one spot
conflicted with every other, so each had to merge `main` again after each merge. One file per change never
conflicts. This README stays.
