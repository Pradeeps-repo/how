import sys
import os
import threading
import time
import json
import base64
import datetime
import uuid
import getpass
import platform
import pyperclip
import shutil
import itertools
import logging
import concurrent.futures
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import httpx
import psutil

# Logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.expanduser("~/.how-cli")
HISTORY_FILE = os.path.join(CONFIG_DIR, "history.log")

# Default: Codex-branded model accepted for ChatGPT-account Codex (not generic gpt-5-mini, etc.).
MODEL_NAME = os.getenv("HOW_MODEL", "gpt-5.5")

CODEX_RESPONSES_PATH = "/codex/responses"


class ApiError(Exception):
    pass


class AuthError(ApiError):
    pass


class ContentError(ApiError):
    pass


class ApiTimeoutError(ApiError):
    pass


def header():
    print(
        "   __             \n"
        "  / /  ___ _    __\n"
        " / _ \\/ _ \\ |/|/ /\n"
        "/_//_/\\___/__,__/ \n"
    )
    print("Ask me how to do anything in your terminal!")


def clean_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        first_line = text.split("\n", 1)[0]
        text = text[len(first_line):-3].strip() if len(first_line) > 3 else text[3:-3].strip()
    elif text.startswith("`") and text.endswith("`"):
        text = text[1:-1].strip()
    return text.strip()


def spinner(stop_event, message="Generating"):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    for frame in itertools.cycle(frames):
        if stop_event.is_set():
            break
        sys.stdout.write(f"\r{frame} {message}")
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write("\r" + " " * (len(message) + 2) + "\r")
    sys.stdout.flush()


def log_history(question: str, commands: list):
    import datetime as dt
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] Q: {question}\nCommands:\n")
            f.writelines(f"{cmd}\n" for cmd in commands)
            f.write("\n")
    except OSError as e:
        logger.warning(f"Failed to write history: {e}")


def show_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                print(f.read())
        except OSError as e:
            print(f"Error reading history file: {e}")
    else:
        print("No history found.")


def get_installed_tools() -> str:
    tools = [t for t in ["git","npm","node","python","docker","pip","go","rustc","cargo","java","mvn","gradle"] if shutil.which(t)]
    return ", ".join(tools)


def get_current_terminal() -> str:
    try:
        parent_pid = os.getppid()
        parent_process = psutil.Process(parent_pid)
        return parent_process.name()
    except Exception:
        return "Unknown"


def _codex_home() -> str:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return os.path.expanduser(raw)
    return os.path.join(os.path.expanduser("~"), ".codex")


def _codex_responses_url() -> str:
    raw = os.getenv("HOW_CODEX_BASE_URL", "https://chatgpt.com/backend-api").strip().rstrip("/")
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}{CODEX_RESPONSES_PATH}"


def _decode_jwt_payload(jwt: str) -> Dict[str, Any]:
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ValueError("invalid JWT shape")
    payload_b64 = parts[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JWT payload must be an object")
    return data


def _jwt_claims_safe(jwt: str) -> Optional[Dict[str, Any]]:
    try:
        return _decode_jwt_payload(jwt)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _format_jwt_expiry_line(jwt: str, label: str) -> str:
    claims = _jwt_claims_safe(jwt)
    if not claims:
        return f"{label}: present but not a decodable JWT."
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return f"{label}: JWT has no exp claim."
    exp_i = int(exp)
    utc = datetime.timezone.utc
    exp_dt = datetime.datetime.fromtimestamp(exp_i, tz=utc)
    now = datetime.datetime.now(tz=utc)
    state = "expired — run: codex login" if now > exp_dt else "valid"
    return f"{label} JWT expires: {exp_dt.isoformat()} ({state})"


def _format_id_token_who(id_token: Optional[str]) -> Optional[str]:
    if not id_token:
        return None
    claims = _jwt_claims_safe(id_token)
    if not claims:
        return None
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    prof = claims.get("https://api.openai.com/profile")
    if isinstance(prof, dict):
        pm = prof.get("email")
        if isinstance(pm, str) and pm.strip():
            return pm.strip()
    return None


def _chatgpt_account_id_for_backend(access_token: str, file_account_id: Optional[str]) -> str:
    claims = _jwt_claims_safe(access_token)
    if claims:
        auth = claims.get("https://api.openai.com/auth")
        if isinstance(auth, dict):
            aid = auth.get("chatgpt_account_id")
            if isinstance(aid, str) and aid.strip():
                return aid.strip()
    if file_account_id and file_account_id.strip():
        return file_account_id.strip()
    raise AuthError(
        "Could not read chatgpt_account_id from OAuth access_token. "
        "Run: codex login (Sign in with ChatGPT)."
    )


def _iter_sse_data_objects(text_stream: Iterable[str]) -> Iterable[Dict[str, Any]]:
    buffer = ""
    for chunk in text_stream:
        buffer += chunk
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            data_lines: List[str] = []
            for line in raw_event.split("\n"):
                line = line.strip("\r")
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            data = "\n".join(data_lines).strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError as e:
                raise ApiError(f"Invalid Codex SSE JSON: {e}") from e
            if isinstance(obj, dict):
                yield obj


def _accumulate_codex_output_text(events: Iterable[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for evt in events:
        etype = evt.get("type")
        if etype == "response.output_text.delta":
            delta = evt.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
        elif etype == "response.refusal.delta":
            delta = evt.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
        elif etype == "error":
            msg = evt.get("message") or evt.get("code") or json.dumps(evt)
            raise ApiError(f"Codex stream error: {msg}")
        elif etype == "response.failed":
            resp = evt.get("response") if isinstance(evt.get("response"), dict) else {}
            err = resp.get("error") if isinstance(resp.get("error"), dict) else {}
            msg = err.get("message") or err.get("code") or json.dumps(evt)
            raise ApiError(f"Codex response failed: {msg}")
    return "".join(parts).strip()


def _execute_codex_responses_once(
    access_token: str,
    chatgpt_account_id: str,
    is_fedramp: bool,
    instructions: str,
    user_text: str,
) -> str:
    url = _codex_responses_url()
    session_id = str(uuid.uuid4())

    ua_bits = [platform.system(), platform.release()]
    ua = f"how-cli-assist ({' '.join(ua_bits)})"

    headers: Dict[str, str] = {
        "authorization": f"Bearer {access_token}",
        "chatgpt-account-id": chatgpt_account_id,
        # Match Codex CLI / pi-ai conventions for ChatGPT-backend routing:
        "originator": os.getenv("HOW_CODEX_ORIGINATOR", "codex_cli_rs"),
        "openai-beta": "responses=experimental",
        "accept": "text/event-stream",
        "content-type": "application/json",
        "user-agent": ua,
        "session_id": session_id,
        "x-client-request-id": session_id,
    }
    if is_fedramp:
        headers["x-openai-fedramp"] = "true"

    body: Dict[str, Any] = {
        "model": MODEL_NAME,
        "store": False,
        "stream": True,
        "instructions": instructions.strip(),
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_text.strip()}],
            }
        ],
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "none",
        "parallel_tool_calls": False,
        "prompt_cache_key": session_id,
    }

    timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code == 401:
                raise AuthError(
                    "Codex session rejected (401). Run: codex login — OAuth token may be expired."
                )
            if response.status_code == 429:
                body_preview = ""
                try:
                    body_preview = response.read().decode("utf-8", errors="replace")[:800]
                except Exception:
                    pass
                raise ApiError(
                    f"ChatGPT/Codex backend returned HTTP 429 — rate limit or plan usage cap. {body_preview}"
                )
            if not response.is_success:
                preview = ""
                try:
                    preview = response.read().decode("utf-8", errors="replace")[:1200]
                except Exception:
                    preview = ""
                msg = f"Codex Responses HTTP {response.status_code}: {preview or response.reason_phrase}"
                if response.status_code == 400 and "not supported" in preview.lower() and "chatgpt" in preview.lower():
                    msg += (
                        "\nHint: with a ChatGPT account, HOW_MODEL must be a Codex-eligible id "
                        "(for example gpt-5.2-codex or gpt-5.1-codex-mini), not generic chat models like gpt-5-mini."
                    )
                raise ApiError(msg)

            text = _accumulate_codex_output_text(_iter_sse_data_objects(response.iter_text()))

    if not text:
        raise ContentError("Empty model output from Codex Responses stream.")
    return text


def run_auth_check() -> int:
    home = _codex_home()
    auth_path = os.path.join(home, "auth.json")
    print(f"CODEX_HOME: {home}")
    print(f"auth.json:  {auth_path}")
    print(f"Exists:    {os.path.isfile(auth_path)}")
    try:
        access, account_id_file, is_fedramp = load_codex_chatgpt_session()
    except AuthError as e:
        print(f"\nSession: not loadable ({e})")
        return 1

    try:
        acc_hdr = _chatgpt_account_id_for_backend(access, account_id_file)
    except AuthError as e:
        print(f"\n{e}")
        return 1

    masked = "(empty)" if not access else f"{access[:8]}… (len={len(access)})"
    print(f"\nOAuth access_token loaded: yes  {masked}")
    print(_format_jwt_expiry_line(access, "access_token"))

    tok_path_notes = ""
    try:
        with open(auth_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("tokens"), dict):
            id_tok = raw["tokens"].get("id_token")
            who = _format_id_token_who(id_tok if isinstance(id_tok, str) else None)
            print(f"id_token hints — signed-in ChatGPT identity: {who or '(unknown)'}")
            if isinstance(id_tok, str) and id_tok.strip():
                print(_format_jwt_expiry_line(id_tok, "id_token"))
    except OSError:
        tok_path_notes = " (could not re-read auth.json for id_token)"

    print(
        "\nIf access_token JWT is valid, ChatGPT OAuth can work even when id_token shows expired"
        " (Codex may refresh tokens independently)."
    )

    print(f"\nResolved chatgpt-account-id (for API): {acc_hdr}")
    print(f"FedRAMP routing (x-openai-fedramp): {'true' if is_fedramp else '(not sent)'}")
    print(f"\nCodex Responses URL: {_codex_responses_url()}")
    print(f"Model: HOW_MODEL={MODEL_NAME!r}{tok_path_notes}")
    print(
        "\nOAuth flow (same family as OpenClaw / @mariozechner/pi-ai): browser opens "
        "https://auth.openai.com/oauth/authorize … then callback on localhost:1455 or paste "
        "the redirect URL; tokens are exchanged at https://auth.openai.com/oauth/token."
        "\nRequests use ChatGPT backend POST …/codex/responses — not api.openai.com/v1/chat/completions."
    )
    return 0


def _fedramp_from_id_token(id_token: Optional[str]) -> bool:
    if not id_token or not isinstance(id_token, str):
        return False
    try:
        claims = _decode_jwt_payload(id_token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return False
    return bool(auth.get("chatgpt_account_is_fedramp"))


def _require_str(d: Mapping[str, Any], key: str, ctx: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise AuthError(f"Invalid Codex auth data ({ctx}): missing or invalid {key!r}.")
    return v


def load_codex_chatgpt_session() -> Tuple[str, Optional[str], bool]:
    auth_path = os.path.join(_codex_home(), "auth.json")
    if not os.path.isfile(auth_path):
        raise AuthError(
            f"Codex auth not found at {auth_path}. "
            "Install the Codex CLI and run: codex login (choose Sign in with ChatGPT). "
            "Use file-based credentials (cli_auth_credentials_store = file) if your OS stores "
            "tokens only in the keyring — see https://developers.openai.com/codex/auth"
        )
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise AuthError(f"Cannot read Codex auth file {auth_path}: {e}") from e
    except json.JSONDecodeError as e:
        raise AuthError(f"Invalid JSON in {auth_path}: {e}") from e

    if not isinstance(raw, dict):
        raise AuthError(f"Invalid Codex auth file {auth_path}: expected a JSON object.")

    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise AuthError(
            "No ChatGPT OAuth tokens in Codex auth. "
            "Run: codex login — this tool only supports Sign in with ChatGPT (not API-key-only flow)."
        )

    access = _require_str(tokens, "access_token", "tokens")
    account_id = tokens.get("account_id")
    if account_id is not None and not isinstance(account_id, str):
        raise AuthError("Invalid Codex auth data (tokens): account_id must be a string when present.")

    id_tok = tokens.get("id_token")
    if id_tok is not None and not isinstance(id_tok, str):
        raise AuthError("Invalid Codex auth data (tokens): id_token must be a string when present.")

    fedramp = _fedramp_from_id_token(id_tok)

    account_id_clean = account_id.strip() if isinstance(account_id, str) and account_id.strip() else None
    return access, account_id_clean, fedramp


def generate_response(
    instructions: str,
    user_text: str,
    session: Tuple[str, Optional[str], bool],
    silent: bool = False,
    max_retries: int = 3,
) -> str:
    access_token, file_account_id, is_fedramp = session
    chatgpt_account_id = _chatgpt_account_id_for_backend(access_token, file_account_id)

    stop_event = threading.Event()
    spinner_thread = None
    if not silent:
        spinner_thread = threading.Thread(target=spinner, args=(stop_event,), daemon=True)
        spinner_thread.start()

    def _call() -> str:
        return _execute_codex_responses_once(
            access_token, chatgpt_account_id, is_fedramp, instructions, user_text
        )

    try:
        for attempt in range(max_retries):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_call)
                    return future.result(timeout=150)
            except concurrent.futures.TimeoutError:
                if attempt == max_retries - 1:
                    raise ApiTimeoutError("Codex Responses request timed out.") from None
                time.sleep(2 ** attempt)
            except AuthError:
                raise
            except ContentError:
                raise
            except ApiError as e:
                msg = str(e)
                if "429" in msg and attempt < max_retries - 1:
                    time.sleep((2 ** attempt) + 1)
                    continue
                raise
    finally:
        if not silent and spinner_thread:
            stop_event.set()
            spinner_thread.join()


def main():
    if len(sys.argv) < 2 or "--help" in sys.argv:
        header()
        print("Usage: how <question> [--silent] [--history] [--type] [--auth-check] [--help]")
        print("\nPrerequisites: Codex CLI + Sign in with ChatGPT (see README).")
        print("\nOptions:")
        print("  --silent      Suppress spinner and typewriter effect")
        print("  --type        Show output with typewriter effect")
        print("  --history     Show command/question history")
        print("  --auth-check  Show Codex OAuth / backend diagnostics (offline)")
        print("  --help        Show this help message and exit")
        sys.exit(0)

    if "--auth-check" in sys.argv:
        header()
        sys.exit(run_auth_check())

    silent = "--silent" in sys.argv
    type_effect = "--type" in sys.argv and not silent
    if "--history" in sys.argv:
        show_history()
        sys.exit(0)

    args = [
        arg
        for arg in sys.argv[1:]
        if arg not in ["--silent", "--history", "--type", "--auth-check"]
    ]
    if not args:
        print("Error: No question provided.")
        sys.exit(1)
    question = " ".join(args)

    try:
        codex_session = load_codex_chatgpt_session()
    except AuthError as e:
        print(f"❌ Authentication Error: {e}")
        sys.exit(1)

    current_dir = os.getcwd()
    current_user = getpass.getuser()
    current_os = f"{platform.system()} {platform.release()}"
    try:
        files_list = os.listdir(current_dir)
        files = ", ".join(files_list[:20]) + ("..." if len(files_list) > 20 else "")
    except OSError:
        files = "Error listing files"
    git_repo = "Yes" if os.path.exists(os.path.join(current_dir, ".git")) else "No"
    tools = get_installed_tools()
    shell = get_current_terminal()

    instructions = f"""SYSTEM:
    You are an expert, concise shell assistant. Your goal is to provide accurate, executable shell commands.

    CONTEXT:
    -   **OS:** {current_os}
    -   **Shell:** {shell}
    -   **CWD:** {current_dir}
    -   **User:** {current_user}
    -   **Git Repo:** {git_repo}
    -   **Files (top 20):** {files}
    -   **Available Tools:** {tools}

    RULES:
    1.  **Primary Goal:** Generate *only* the exact, executable shell command(s) for the `{shell}` environment.
    2.  **Context is Key:** Use the CONTEXT (CWD, Files, OS) to write specific, correct commands.
    3.  **No Banter:** Do NOT include greetings, sign-offs, or conversational filler (e.g., "Here is the command:").
    4.  **Safety:** If a command is complex or destructive (e.g., `rm -rf`, `find -delete`), add a single-line comment (`# ...`) *after* the command explaining what it does.
    5.  **Questions:** If the user asks a question (e.g., "what is `ls`?"), provide a concise, one-line answer. Do not output a command.
    6.  **Ambiguity:** If the request is unclear, ask a single, direct clarifying question. Start the line with `#`.
"""

    user_text = f"""REQUEST:
    {question}

    RESPONSE:
"""

    try:
        text = generate_response(instructions, user_text, codex_session, silent)
    except (AuthError, ContentError, ApiTimeoutError, ApiError) as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

    raw_commands = clean_response(text)
    commands = [line.strip() for line in raw_commands.splitlines() if line.strip()]

    if not commands:
        print("⚠️ No valid commands generated.")
        sys.exit(1)
    full_command = "\n".join(commands)

    if type_effect:
        for c in full_command:
            sys.stdout.write(c)
            sys.stdout.flush()
            time.sleep(0.01)
        print()
    else:
        print(full_command)

    try:
        pyperclip.copy(full_command)
    except pyperclip.PyperclipException as e:
        logger.warning(f"Clipboard copy failed: {e}")

    log_history(question, commands)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Interrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 Unexpected error: {type(e).__name__}: {e}")
        logger.exception("Unexpected error")
        sys.exit(1)
