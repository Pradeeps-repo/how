# Zsh helpers for how-cli-assist.
#
# Load once from ~/.zshrc:
#   source /path/to/how/contrib/how.zsh
#
# A standalone Python process cannot inject text into your interactive prompt; these
# snippets do it from inside your shell.

# howp — run `how`, add the generated command to zsh history, then press ↑ once
# to recall it at the prompt (edit before Enter if you like).
function howp() {
  emulate -L zsh
  local cmds
  cmds=$(command how --silent "$@") || return 1
  print -s -- "$cmds"
  print -r -- '[how] Press ↑ (up-arrow) then Enter — command is pre-loaded in history.'
}

# Optional: quick alias (comment out if you prefer the name `howp` only).
# alias how-queue='howp'
