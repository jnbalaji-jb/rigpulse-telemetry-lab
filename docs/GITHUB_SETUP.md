# GitHub Development & Release Workflow

This guide documents the standard branching, local verification, and pull request workflow for `rigpulse-telemetry-lab`.

## Local Environment Setup

Clone the repository and install development dependencies in a virtual environment:

```bash
git clone https://github.com/jnbalaji-jb/rigpulse-telemetry-lab.git
cd rigpulse-telemetry-lab
python -m venv .venv
```

Activate the virtual environment and install the package in editable mode:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

```bash
# macOS / Linux
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

**Do not commit** `.env`, credentials, local SQLite databases (`*.db`), generated `demo_data/`, or `reports/`. `.gitignore` already excludes these paths.

## Feature Branch & Pull Request Workflow

1. **Create a feature branch from `main`:**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feat/your-feature-name
   ```
2. **Implement changes and verify locally:**
   Run the full test suite and linter before committing:
   ```bash
   python -m pytest -q
   python -m ruff check src tests
   ```
3. **Commit and push your branch:**
   ```bash
   git add .
   git commit -m "feat: describe your change"
   git push -u origin feat/your-feature-name
   ```
4. **Open and merge a Pull Request:**
   - Open a Pull Request targeting `main` on GitHub and reference the related issue (e.g., `Closes #2`).
   - Verify that the **Python CI** workflow (`Python 3.11` and `3.12`) passes all checks.
   - Merge into `main`, delete the merged feature branch, and pull the latest `main` locally.
