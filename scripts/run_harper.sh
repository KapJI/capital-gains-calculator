#!/usr/bin/env bash

# Run the Harper grammar checker over the given files.
# Project vocabulary lives in .harper-dictionary.txt. Lints that Harper
# cannot be taught to accept via the dictionary (e.g. US spellings inside
# product names, which are dialect-gated in Harper's base dictionary) are
# filtered out via the regexes in .harper-ignore.txt.
# Usage: scripts/run_harper.sh FILE...

set -uo pipefail

output=$(uv run harper lint \
    --dialect gb \
    --user-dict-path .harper-dictionary.txt \
    --ignore UnclosedQuotes,OrthographicConsistency,SentenceCapitalization,DisjointPrefixes,CommaFixes,Dashes,NumericRangeEnDash,ExpandArgument,LongSentences,MassNouns,ExpandStandardInputAndOutput,MissingTo,SplitWords,UseEllipsisCharacter,ExpandDirectory,QuoteSpacing,AdjectiveOfA,ToTwoToo,UseTitleCase,OxfordComma,MissingDeterminer \
    --format compact --quiet "$@" 2>&1)
status=$?

if [ "$status" -eq 0 ]; then
    exit 0
fi

# Drop status lines and any lints accepted via .harper-ignore.txt.
patterns=$(grep -vE '^\s*(#|$)' .harper-ignore.txt || true)
remaining=$(printf '%s\n' "$output" | grep -vE '^(Note: |Error: Lints were found$)')
if [ -n "$patterns" ]; then
    remaining=$(printf '%s\n' "$remaining" | grep -vEf <(printf '%s\n' "$patterns") || true)
fi

if [ -n "$remaining" ]; then
    printf '%s\n' "$remaining"
    exit 1
fi
exit 0
