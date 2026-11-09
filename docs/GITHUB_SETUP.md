# Publish RigPulse to GitHub

This starter is ready to publish as a **new repository**. Use your existing GitHub account even if the GitHub login email differs from the Alignerr email. Review the code before publishing, and continue real engineering work after the initial commit.

## Option A — GitHub website (simplest)

1. Download and extract `rigpulse-telemetry-lab.zip`.
2. Open [New repository](https://github.com/new) while signed in to `jnbalaji-jb`.
3. Name it `rigpulse-telemetry-lab` and choose **Public** if you want reviewers to access it without being invited. Do **not** initialize it with a new README, `.gitignore`, or license (the folder already contains them).
4. Once the repository exists, use **uploading an existing file** to add the project files, including `.github` and `src` folders, or use Option B (Git CLI) for a complete upload.
5. Verify that the README is visible and check the GitHub Actions tab for successful CI.

**Recommended:** Option B, because it preserves all nested files and starts a usable Git history.

## Option B — Git CLI (recommended)

Open PowerShell or Git Bash inside the **extracted `rigpulse-telemetry-lab` directory**:

```bash
git init
git branch -M main
git config user.name "Your preferred Git display name"
git config user.email "your-verified-github-email-or-noreply-email"
git add .
git commit -m "feat: initialize RigPulse telemetry reliability prototype"
git remote add origin https://github.com/jnbalaji-jb/rigpulse-telemetry-lab.git
git push -u origin main
```

Create the empty public GitHub repository first. GitHub authentication might open a browser or require a credential manager; use your **GitHub** account credentials. The Git commit email can be a verified GitHub email or GitHub-provided `noreply` address. It does **not** need to be your Alignerr email.

**Do not upload** `.env`, API keys, confidential telemetry, virtual environments, or large databases. `.gitignore` already excludes common examples.

## First genuine development task

After publishing, open `docs/BACKLOG.md` and create a GitHub Issue named **`feat: build time-aligned multivariate signal windows`**. Implement some of its acceptance criteria yourself with an AI coding assistant, test it, then push a second meaningful commit:

```bash
python -m pytest -q
git add .
git commit -m "feat: add sensor alignment baseline and edge-case tests"
git push
```

Only use the second commit message **after you actually implement and test that work**. Do not fabricate history or claim completed milestones.

## What to share with Alignerr

Share the **actual published repository URL** and your next engineering milestone. Use `docs/PROJECT_PROPOSAL.md` as an editable project-survey draft, but accurately describe any AI assistance and the work you have personally reviewed or completed. Acceptance is not guaranteed.
