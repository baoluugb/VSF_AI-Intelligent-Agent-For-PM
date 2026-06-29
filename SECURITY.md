# Security

## Secrets handling

- **Never commit secrets.** All credentials live in `.env`, which is gitignored
  (see `.gitignore`). Use `.env.example` as the template: `cp .env.example .env`.
- Secrets used by this project: `OPENAI_API_KEY`, `MCP_API_KEY`, and (roadmap P1,
  for live ingestion) `JIRA_API_TOKEN` / `CONFLUENCE_API_TOKEN`, plus
  `SLACK_WEBHOOK_URL` for delivery.
- The Report Agent output passes through `OutputSanitizer`
  (`src/guardrail/sanitizer.py`), which redacts API-key / bearer-token / PEM
  patterns from generated reports as a defence-in-depth backstop.

## ⚠️ Action required: rotate the leaked API key

An OpenAI-compatible API key was committed to git history in commit
[`d2657ea`](.) ("fix: fix API_KEY when using in Agent"). Even though the current
key now lives only in an untracked `.env`, **the old key remains recoverable
from history and must be treated as compromised.**

This is a **user action** (it is your provider account — the maintainer cannot
rotate it):

### Step 1 — Rotate (the real fix)

Revoke the leaked key in your provider dashboard (ckey.vn / OpenAI) and issue a
new one; put the new key in `.env` only. **Do this even if you skip the scrub** —
once a secret is in history it must be treated as compromised forever.

### Step 2 — Scrub it from git history (optional, destructive)

This **rewrites history and force-pushes `main`** — every collaborator must
re-clone afterward. Run it yourself when ready (copy-paste runbook):

```bash
# 0) One-time install
pip install git-filter-repo

# 1) Back up first — history rewrite is irreversible
git clone --mirror . ../VSF-backup.git

# 2) Put the OLD (leaked) key in a replacements file. NEVER commit this file.
#    Format: <literal-secret>==>[REDACTED]   (one line per secret)
printf '%s==>[REDACTED]\n' 'sk-PASTE-OLD-LEAKED-KEY-HERE' > ../replacements.txt

# 3) Scrub it from ALL commits
git filter-repo --replace-text ../replacements.txt

# 4) Re-add the remote (filter-repo drops it) and force-push everything
git remote add origin https://github.com/baoluugb/VSF_AI-Intelligent-Agent-For-PM.git
git push --force --all origin
git push --force --tags origin

# 5) Tell every collaborator to re-clone (old clones still contain the key)
# 6) Delete ../replacements.txt and ../VSF-backup.git once verified
```

> Rotation (Step 1) is the actual mitigation; the scrub only reduces residual
> exposure of an already-compromised key.

> **Gate before going live:** P1 adds real Jira/Confluence API tokens. Finish the
> key rotation above **before** wiring live credentials, so secrets hygiene is in
> place before there are more secrets to leak.

## Reporting

Found a vulnerability? Open a private issue or contact the maintainer directly —
do not include live secrets in the report.
