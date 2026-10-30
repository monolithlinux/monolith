# Tab completion for monolith. monolith itself lists what can follow the words
# typed so far (monolith __complete), one VALUE<TAB>DESCRIPTION per line.
complete --command monolith --no-files \
    --arguments '(monolith __complete (commandline --current-process --tokens-expanded --cut-at-cursor)[2..-1] 2>/dev/null)'
