#!/bin/sh

WAV2AGB="python3 $(dirname "$0")/wav2agb.py"

for l in $(seq 1 8)
do
    echo lookahead="$l":
    $WAV2AGB "$1" -c -l "$l" --verbose
    echo lookahead="$l" fast:
    $WAV2AGB "$1" -f -l "$l" --verbose
done
