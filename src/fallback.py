"""The fallback: things on this PC that are not commands yet, offered only
when none of the user's commands match what was typed.

It holds what "Find recommended commands" finds (installed programs, Store
apps, Windows tools, Quick Access folders, bookmarks, Settings pages and the
popular websites), gathered once in the background and kept in memory. A
lookup is the same prefix match commands use, over that list, so it costs
no more than a search of the user's own commands. Nothing is indexed.
"""

import time
from pathlib import Path
from typing import Callable, Iterable

from .command_trie import word_start_offsets
from .installed_programs import _path_key, is_link_location
from .personal_places import url_key

# A scan older than this is refreshed in the background on the next miss, so
# something installed since shows up within seconds of that miss. The last
# list keeps answering until the new one is in; a scan takes a few seconds.
MAX_AGE_SECONDS = 60

# Ranks, best first.
_EXACT, _PREFIX, _WORD = 0, 1, 2


def location_key(location: str) -> str:
    """One form for a location, so a candidate already saved as a command is
    recognised whether it is a path, a web address or a shell link."""
    text = str(location or "").strip()
    if not text:
        return ""
    if text.casefold().startswith(("http://", "https://")):
        return "url:" + url_key(text)
    if is_link_location(text):
        return "link:" + text.casefold()
    return "path:" + _path_key(Path(text).expanduser())


def command_location_keys(commands: Iterable[dict]) -> set[str]:
    """Location keys of the user's commands, including the program a shortcut runs."""
    keys = set()
    for command in commands:
        for key in ("location", "process_path"):
            value = command.get(key)
            if value:
                keys.add(location_key(str(value)))
    keys.discard("")
    return keys


class FallbackIndex:
    def __init__(self, normalize: Callable[[str], str] = str.casefold):
        self.normalize = normalize
        self.candidates: list[dict] = []
        self.loaded_at: float | None = None

    @property
    def loaded(self) -> bool:
        return self.loaded_at is not None

    def is_stale(self, now: float | None = None) -> bool:
        if self.loaded_at is None:
            return True
        return (time.monotonic() if now is None else now) - self.loaded_at > MAX_AGE_SECONDS

    def set_candidates(self, candidates: Iterable[dict], now: float | None = None) -> None:
        seen: set[str] = set()
        kept = []
        for candidate in candidates:
            name = str(candidate.get("name") or "").strip()
            key = location_key(str(candidate.get("location") or ""))
            if not name or not key or key in seen:
                continue
            seen.add(key)
            kept.append(candidate)
        self.candidates = kept
        self.loaded_at = time.monotonic() if now is None else now

    def search(self, text: str, known_locations: set[str] = frozenset(), limit: int = 5) -> list[dict]:
        """Candidates whose name or suggested alias starts with `text`, then
        those with a later word of the name starting with it. An exact name
        or alias leads; each group reads alphabetically, so the same letters
        always give the same list. Anything already a command is left out."""
        wanted = self.normalize(str(text).strip())
        if not wanted:
            return []
        ranked = []
        for candidate in self.candidates:
            rank = self._rank(candidate, wanted)
            if rank is None:
                continue
            if location_key(str(candidate.get("location") or "")) in known_locations:
                continue
            ranked.append((rank, str(candidate["name"]).casefold(), candidate))
        ranked.sort(key=lambda entry: entry[:2])
        return [candidate for _rank, _name, candidate in ranked[:limit]]

    def _rank(self, candidate: dict, wanted: str) -> int | None:
        name = str(candidate.get("name") or "")
        keywords = [self.normalize(name), *(self.normalize(str(alias)) for alias in candidate.get("aliases") or [])]
        if wanted in keywords:
            return _EXACT
        if any(keyword.startswith(wanted) for keyword in keywords):
            return _PREFIX
        if any(self.normalize(name[offset:]).startswith(wanted) for offset in word_start_offsets(name)):
            return _WORD
        return None
