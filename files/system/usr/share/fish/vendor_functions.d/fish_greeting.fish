# Monolith's greeting in new terminals: its mark, the build, anything that
# waits for you, and a tip (/usr/libexec/monolith/greeting).
#
# Turn it off with: set -U fish_greeting
# Text you put in that variable, or your own fish_greeting function, replaces it.
function fish_greeting
    if set -q fish_greeting
        # What fish itself does with the variable.
        test -n "$fish_greeting"
        and echo $fish_greeting
        return
    end
    test -x /usr/libexec/monolith/greeting
    and /usr/libexec/monolith/greeting
end
