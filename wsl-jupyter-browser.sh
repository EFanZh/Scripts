#!/bin/sh -e

init_file=$(echo "$1" | sed 's|^file://||')
url=$(grep -m 1 -oP 'http:[^"]+' "$init_file")

exec cmd.exe /c "START $url"
