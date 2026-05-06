<p align="center">
  <img src="./screenshot.png" alt="How-CLI" />
</p>
 <h1 align="center">How-CLI</h1>
    <p align="center">A Terminal-Based Assistant for Generating Shell Commands</p>

**How-CLI** reads the same OAuth session as **[OpenAI Codex](https://developers.openai.com/codex/auth)** (`~/.codex/auth.json`): **Sign in with ChatGPT**. It sends prompts to **`https://chatgpt.com/backend-api/codex/responses`** (streaming Responses), matching how **[OpenClaw](https://github.com/openclaw/openclaw)** / **`@mariozechner/pi-ai`** attach Codex OAuth to the **ChatGPT backend** — **not** to `api.openai.com/v1/chat/completions` (OpenAI Platform billing).

---

## How this relates to OpenClaw

OpenClaw documents **OpenAI Codex (ChatGPT OAuth)** as PKCE against `https://auth.openai.com/oauth/authorize`, a callback on **`http://127.0.0.1:1455/auth/callback`**, and if that port is unavailable (SSH, containers), **past the full redirect URL** into the CLI; tokens are exchanged at `https://auth.openai.com/oauth/token`. See [OpenClaw OAuth](https://github.com/openclaw/openclaw/blob/main/docs/concepts/oauth.md).

**This project does not re-implement that login wizard.** Use the Codex CLI (`codex login`) or OpenClaw’s onboarding to obtain `auth.json`; How-CLI only **consumes** the cached OAuth tokens Codex wrote.

---

## Breaking change (v2)

Gemini / `how --api-key` were removed. Pin `how-cli-assist<2` if you need the old Gemini tool.

---

## Prerequisites

1. **[Codex CLI](https://github.com/openai/codex)** with **Sign in with ChatGPT** (`codex login`). That performs the OAuth / browser flow (or pasted redirect URL when localhost callback is unavailable), same ecosystem as OpenClaw.

2. **File-backed** `auth.json` at **`$CODEX_HOME/auth.json`** (default **`~/.codex`**). Keyring-only mode is unsupported here; set Codex **`cli_auth_credentials_store = file`** if needed ([Codex authentication](https://developers.openai.com/codex/auth)).

3. Treat `auth.json` as a secret (`0600`).

---

## Features

- Commands use your **ChatGPT-backed Codex OAuth** + **Codex Responses** backend (not Platform Chat Completions).
- **`how --history`**, **`--type`**, **`--silent`**, clipboard copy.

---

### Disclaimer

```
Small CLI hack — same auth family as OpenClaw Codex OAuth, thinner than running the full gateway.
```

## Installation

```bash
pip install how-cli-assist
```

## Quick Start

```bash
how to create a Python virtual environment
how --auth-check
```

### Environment

| Variable | Meaning |
| -------- | ------- |
| `CODEX_HOME` | Directory containing `auth.json` (default `~/.codex`) |
| `HOW_MODEL` | Codex model id (default **`gpt-5.5`** in this repo). With a **ChatGPT** account, use Codex-eligible ids (for example **`gpt-5.2-codex`**, **`gpt-5.1-codex-mini`**) — not plain **`gpt-5-mini`**, which the backend rejects for this route. |
| `HOW_CODEX_BASE_URL` | Override ChatGPT backend base (default `https://chatgpt.com/backend-api`) |
| `HOW_CODEX_ORIGINATOR` | `originator` header (default `codex_cli_rs`, matching Codex CLI) |

## Options

`--silent` : Suppress spinner and typewriter effect.

`--type` : Show output with typewriter effect.

`--history` : Display previous questions and generated commands.

`--auth-check` : Offline checks: `auth.json`, JWT expiry, resolved `chatgpt-account-id`, target Codex Responses URL.

`--help` : Show help message and exit.

### Command on the prompt (no paste)

A subprocess cannot put text into your shell’s edit buffer. Use a tiny shell helper:

- **Fish:** source [`contrib/how.fish`](contrib/how.fish) — `howp` runs `how` and **`commandline -r`** loads the result on your prompt (review, then Enter).
- **Zsh:** source [`contrib/how.zsh`](contrib/how.zsh) — run `howp …`; the command is **`print -s`**’d into history — press **↑** once and it appears on your line.

```bash
# Example (zsh): add to ~/.zshrc after adjusting the path
source ~/exp/how/contrib/how.zsh
howp to create a Python virtual environment   # then ↑ Enter
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
