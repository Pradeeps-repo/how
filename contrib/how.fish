# Fish helpers for how-cli-assist.
#
# Add to ~/.config/fish/config.fish:
#   source /path/to/how/contrib/how.fish

function howp
    set -l cmds (command how --silent $argv)
    or return 1
    commandline -r "$cmds"
    commandline -f repaint
    echo '[how] Command placed on your prompt — review and press Enter.'
end
