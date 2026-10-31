# Daily Project Generator

This repository schedules one project generation each day and creates a separate private repository named `daily-project-YYYYMMDD` under `eDDie-05`.

Each generated repository contains a self-contained `index.html` project and a README. The workflow runs in GitHub Actions, so your computer does not need to be online.

## One-time setup

Add these repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `OPENAI_API_KEY`: an OpenAI API key with API billing enabled.
- `GH_CREATE_REPO_TOKEN`: a GitHub token for `eDDie-05` that can create private repositories and write repository contents. Keep the token in GitHub Secrets; do not put it in a file or send it in chat.

For a classic personal access token, private repository creation requires the `repo` scope. A fine-grained token needs repository creation or administration write permission and contents write permission for the account's repositories.

The workflow uses the `gpt-6-astra` model by default. You can override it with an Actions variable named `OPENAI_MODEL`.

## Run it

The workflow runs daily at 06:00 UTC (09:00 Africa/Mogadishu). You can also start it manually from **Actions → Daily project generator → Run workflow**.

The generator creates private repositories, skips a date if its repository already exists, and only uploads `README.md` and `index.html`. Generated code is not executed by the workflow. Review each project before using it.

If either secret is missing or lacks the required access, the workflow stops with a setup error rather than making a partial project.
