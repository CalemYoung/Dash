"""Instant aliases: aliases that open their command the moment they are
typed, with no Enter.

That is only safe when the letters can't be the start of anything else, so
an instant alias is held to one rule: no other command's name or alias (or,
with word-start matching on, a later word of its name) may start with it.
Typing it then can only ever mean that command. The rule is checked when a
command is saved and again whenever commands load, so adding a command later
can never quietly change what an instant alias does.
"""

from dataclasses import dataclass
from typing import Callable, Iterable

from .browsers import is_search_link
from .command_trie import word_start_offsets

# Why an instant alias was refused:
#   "search"   its command searches a site when a space follows the alias
#   "prefix"   another command's name or alias starts with it
#   "word"     a later word of another command's name starts with it
#   "shadowed" a shorter instant alias is the start of it, so it is never reached
#   "missing"  it is not one of the command's aliases
CONFLICT_KINDS = ("search", "prefix", "word", "shadowed", "missing")


@dataclass(frozen=True)
class InstantConflict:
    alias: str  # the instant alias that was refused
    owner: str  # the command it belongs to
    kind: str
    keyword: str = ""  # what it clashed with
    keyword_owner: str = ""  # the command that keyword belongs to

    def describe(self, from_command: str | None = None) -> str:
        """The problem in plain words. `from_command` is the command being
        edited: when the clash is caused by one of its keywords rather than
        its own instant alias, the message says what to change in it."""
        alias, owner, keyword, other = self.alias, self.owner, self.keyword, self.keyword_owner
        blamed_keyword = from_command is not None and from_command == other and from_command != owner
        if self.kind == "search":
            return f"'{alias}' can't open instantly: {owner} searches when you type a space after it."
        if self.kind == "missing":
            return f"'{alias}' can't open instantly: it is not an alias of {owner}."
        if self.kind == "shadowed":
            return f"'{alias}' can't open instantly: typing '{keyword}' already opens {other}."
        if self.kind == "word":
            if blamed_keyword:
                return f"A word in '{keyword}' starts with '{alias}', which opens {owner} instantly. Rename it, or turn off instant for '{alias}'."
            return f"'{alias}' can't open instantly: a word in '{keyword}' starts with it."
        if blamed_keyword:
            return f"'{keyword}' starts with '{alias}', which opens {owner} instantly. Choose another, or turn off instant for '{alias}'."
        return f"'{alias}' can't open instantly: '{keyword}' ({other}) starts with it too."


def instant_aliases_of(command: dict) -> list[str]:
    return [str(alias) for alias in command.get("instant_aliases") or [] if str(alias).strip()]


def check_instant_aliases(
    commands: Iterable[dict],
    normalize: Callable[[str], str],
    match_word_starts: bool = False,
) -> tuple[dict[str, str], list[InstantConflict]]:
    """``(accepted, refused)`` for the instant aliases of `commands`.

    `accepted` maps each usable instant alias, in `normalize`d form, to its
    command's name. Commands are taken in order and an earlier instant alias
    wins over a later one, so a command being saved goes last.
    """
    commands = list(commands)
    keywords: list[tuple[str, str]] = []  # (normalized keyword, owner)
    words: list[tuple[str, str, str]] = []  # (normalized later-word tail, name, owner)
    for command in commands:
        name = str(command.get("name", "")).strip()
        for keyword in (name, *(command.get("aliases") or [])):
            text = str(keyword).strip()
            if text:
                keywords.append((normalize(text), name))
        if match_word_starts:
            words.extend((normalize(name[offset:]), name, name) for offset in word_start_offsets(name))

    original_text = {}
    for command in commands:
        for keyword in (command.get("name", ""), *(command.get("aliases") or [])):
            original_text.setdefault((normalize(str(keyword).strip()), str(command.get("name", "")).strip()), str(keyword).strip())

    accepted: dict[str, str] = {}
    refused: list[InstantConflict] = []
    for command in commands:
        owner = str(command.get("name", "")).strip()
        own_aliases = {normalize(str(alias).strip()) for alias in command.get("aliases") or []}
        is_search = command.get("type") == "url" and is_search_link(str(command.get("location", "")))
        for alias_text in instant_aliases_of(command):
            alias_text = alias_text.strip()
            alias = normalize(alias_text)
            conflict = None
            if alias not in own_aliases:
                conflict = InstantConflict(alias_text, owner, "missing")
            elif is_search:
                conflict = InstantConflict(alias_text, owner, "search")
            if conflict is None:
                for keyword, keyword_owner in keywords:
                    if keyword_owner != owner and keyword != alias and keyword.startswith(alias):
                        conflict = InstantConflict(alias_text, owner, "prefix", original_text.get((keyword, keyword_owner), keyword), keyword_owner)
                        break
            if conflict is None:
                for shorter, shorter_owner in accepted.items():
                    if shorter != alias and alias.startswith(shorter):
                        conflict = InstantConflict(alias_text, owner, "shadowed", shorter, shorter_owner)
                        break
            if conflict is None:
                for tail, name, word_owner in words:
                    if word_owner != owner and tail.startswith(alias):
                        conflict = InstantConflict(alias_text, owner, "word", name, word_owner)
                        break
            if conflict is None:
                accepted.setdefault(alias, owner)
            else:
                refused.append(conflict)
    return accepted, refused
