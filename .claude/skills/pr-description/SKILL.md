---
name: pr-description
description: Write or complete a pull request description for this repo from its diff and commits, using .github/pull_request_template.md. Args "[--post] <owner/repo> <pr-number>"; --post updates the PR on GitHub (used by CI), otherwise print it.
allowed-tools: Read, Write, Grep, Glob, Bash(gh pr view:*), Bash(gh pr diff:*), Bash(gh pr edit:*), Bash(git log:*), Bash(git diff:*)
---

# PR description

Arguments: `$ARGUMENTS`

Write the description a careful senior engineer would: short, factual, useful to a reviewer.

## Steps

1. `gh pr view <n> --repo <repo> --json title,body,baseRefName,headRefName,commits,files`
   and `gh pr diff <n> --repo <repo>`. Read `CLAUDE.md` for the roadmap phases and layout.
   With no PR number, use the current branch against `main` (`git log main..HEAD`, `git diff main...HEAD`).
2. Compare the current body with `.github/pull_request_template.md`. A section counts as
   **empty** if it holds only the template's `<!-- -->` comment or whitespace.
   - Body empty, or every section empty: write the whole description.
   - Some sections written by the author: keep their text exactly as written and fill only
     the empty sections.
   - Every section already written: change nothing and stop.
3. Fill sections using the template's headings, in order, and drop the `<!-- -->` hints in the
   sections you fill:
   - **What & why**: 1-3 sentences on intent, plus the roadmap phase if it maps to one.
   - **Changes**: bullets grouped by area (core / mcp / data / tests / docs / ci), naming new
     tools, modules and data files. Mention public API or tool schema changes explicitly.
   - **Testing**: the tests added or changed (file names) and that CI runs ruff, mypy and
     pytest. Never claim manual or live testing happened unless a commit message says so.
   - **Review notes**: risky areas, behaviour changes, assumptions, follow-ups. "None" if none.
4. Rules: describe only what the diff shows, never guess intent you can't see; no marketing
   tone; no personal data (RSNs, emails, tokens); don't touch the title, labels or anything
   other than the body.

## Delivery

- With `--post`: write the final body to `pr-body.md` in the working directory, then
  `gh pr edit <n> --repo <repo> --body-file pr-body.md`. Post nothing else.
- Without `--post`: print the body so it can be pasted into `gh pr create --body-file` or GitHub.
