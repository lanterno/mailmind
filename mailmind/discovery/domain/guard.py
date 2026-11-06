"""What keeps a bulk command from reaching nearly everything. A filter must narrow (QuerySpec.narrows), and whatever
it says, one that still resolves to most of the mail is a whole-inbox sweep."""
BROAD_FRACTION = 0.6       # a filter resolving to >= this share of ALL mail is a whole-inbox sweep


def sweeps_whole_mailbox(matched, total):
    return bool(total) and matched >= BROAD_FRACTION * total
