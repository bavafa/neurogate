#!/usr/bin/env bash
# Filter: replace this machine's absolute paths in tool output with neutral
# placeholders, so logs kept in the repository do not carry home or checkout paths.
#   <repo>   the repository root          <tools>   the tool install directory (NG_TOOLS)
#   <home>   the user's home directory
# Usage: some_command 2>&1 | scripts/strip_paths.sh | tee log
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tools="${NG_TOOLS:-$HOME/tools}"
exec sed -e "s#$root#<repo>#g" -e "s#$tools#<tools>#g" -e "s#$HOME#<home>#g"
