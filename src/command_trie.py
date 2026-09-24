from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class TrieStep:
    prefix: str
    command_count: int
    eliminated_count: int
    is_valid: bool


@dataclass(frozen=True)
class TrieBranch:
    prefix: str
    command_count: int


@dataclass(frozen=True)
class TrieSnapshot:
    prefix: str
    matched_prefix: str
    total_command_count: int
    command_count: int
    is_terminal: bool
    steps: tuple[TrieStep, ...]
    branch_count: int
    branches: tuple[TrieBranch, ...]


class TrieNode:
    def __init__(self):
        self.children: dict[str, TrieNode] = {}
        self.is_end: bool = False
        self.command_id: Optional[str] = None


class CommandTrie:
    def __init__(self, case_sensitive: bool = False):
        self.root = TrieNode()
        self.case_sensitive = case_sensitive

    def normalize(self, text: str) -> str:
        """Keywords and prefixes are compared in this form."""
        return text if self.case_sensitive else text.lower()

    def insert(self, keyword: str, command_id: str):
        """Insert a keyword that points to a command ID"""
        node = self.root

        for char in self.normalize(keyword):
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]

        node.is_end = True
        node.command_id = command_id

    def search_prefix(self, prefix: str, max_results: Optional[int] = 10) -> list[str]:
        """Search for keywords matching prefix, return unique command IDs.
        `max_results` None returns every match."""
        node = self.root

        for char in self.normalize(prefix):
            if char not in node.children:
                return []
            node = node.children[char]

        command_ids: list[str] = []
        seen: set[str] = set()
        self._collect_command_ids(node, command_ids, seen, max_results)
        return command_ids

    def snapshot(self, prefix: str, max_branches: Optional[int] = None) -> TrieSnapshot:
        """Describe how a prefix narrows commands, grouping branches to fit when requested."""
        node = self.root
        matched_characters = []
        steps: list[TrieStep] = []
        total_command_count = len(self._command_ids(self.root))
        previous_count = total_command_count
        is_dead_end = False

        for index, character in enumerate(self.normalize(prefix)):
            typed_prefix = prefix[: index + 1]
            if is_dead_end:
                steps.append(TrieStep(typed_prefix, 0, 0, False))
                continue

            child = node.children.get(character)
            if child is None:
                steps.append(TrieStep(typed_prefix, 0, previous_count, False))
                previous_count = 0
                is_dead_end = True
                continue

            node = child
            matched_characters.append(character)
            command_count = len(self._command_ids(node))
            steps.append(TrieStep(typed_prefix, command_count, previous_count - command_count, True))
            previous_count = command_count

        if is_dead_end:
            return TrieSnapshot(
                prefix=prefix,
                matched_prefix="".join(matched_characters),
                total_command_count=total_command_count,
                command_count=0,
                is_terminal=False,
                steps=tuple(steps),
                branch_count=0,
                branches=(),
            )

        command_ids = self._command_ids(node)
        matched_prefix = "".join(matched_characters)
        raw_branches = self._terminal_branches(matched_prefix, node)
        active_command_ids = frozenset((node.command_id,)) if node.is_end and node.command_id else frozenset()
        branches = self._fit_sibling_branches(
            raw_branches,
            active_command_ids,
            len(matched_prefix),
            max_branches,
        )

        return TrieSnapshot(
            prefix=prefix,
            matched_prefix=matched_prefix,
            total_command_count=total_command_count,
            command_count=len(command_ids),
            is_terminal=node.is_end,
            steps=tuple(steps),
            branch_count=len(branches),
            branches=tuple(branches),
        )

    def _terminal_branches(self, prefix: str, node: TrieNode) -> list[tuple[str, frozenset[str]]]:
        branches: list[tuple[str, frozenset[str]]] = []
        for character, child in node.children.items():
            branch_prefix = prefix + character
            if child.is_end and child.command_id:
                branches.append((branch_prefix.rstrip(), frozenset((child.command_id,))))
            branches.extend(self._terminal_branches(branch_prefix, child))
        return branches

    @classmethod
    def _fit_sibling_branches(
        cls,
        raw_branches: list[tuple[str, frozenset[str]]],
        excluded_command_ids: frozenset[str],
        matched_prefix_length: int,
        max_branches: Optional[int],
    ) -> list[TrieBranch]:
        terminal_branches = cls._dedupe_sibling_branches(raw_branches, excluded_command_ids)
        if max_branches is None or len(terminal_branches) <= max_branches:
            return terminal_branches
        if max_branches <= 0 or not raw_branches:
            return []

        fitted_branches: list[TrieBranch] = []
        longest_prefix = max(len(prefix) for prefix, _ in raw_branches)
        for prefix_length in range(matched_prefix_length + 1, longest_prefix + 1):
            grouped_command_ids: dict[str, set[str]] = {}
            for prefix, command_ids in raw_branches:
                grouped_prefix = prefix[:prefix_length]
                grouped_command_ids.setdefault(grouped_prefix, set()).update(command_ids)

            candidate_branches = cls._dedupe_sibling_branches(
                [
                    (prefix, frozenset(command_ids))
                    for prefix, command_ids in grouped_command_ids.items()
                ],
                excluded_command_ids,
            )
            if len(candidate_branches) <= max_branches:
                fitted_branches = candidate_branches

        return sorted(fitted_branches, key=lambda branch: branch.prefix) or terminal_branches

    @staticmethod
    def _dedupe_sibling_branches(
        raw_branches: list[tuple[str, frozenset[str]]],
        excluded_command_ids: frozenset[str] = frozenset(),
    ) -> list[TrieBranch]:
        """Credit each command to a single sibling branch so the counts add up.

        A command with multiple aliases (e.g. "TestTrack" via both "te..." and
        "tt") can be reachable through more than one branch at the same node.
        Without this, sibling counts could sum to more than the parent's count.
        """
        raw_branches = sorted(raw_branches, key=lambda branch: (-len(branch[1]), branch[0]))
        seen = set(excluded_command_ids)
        branches: list[TrieBranch] = []
        for branch_prefix, command_ids in raw_branches:
            unique_ids = command_ids - seen
            if not unique_ids:
                continue
            seen |= command_ids
            branches.append(TrieBranch(branch_prefix, len(unique_ids)))
        return branches

    def _command_ids(self, node: TrieNode) -> set[str]:
        command_ids: set[str] = set()
        if node.is_end and node.command_id:
            command_ids.add(node.command_id)
        for child in node.children.values():
            command_ids.update(self._command_ids(child))
        return command_ids

    def _collect_command_ids(self, node: TrieNode, command_ids: list[str], seen: set[str], max_results: Optional[int]):
        """Recursively collect command IDs, skipping duplicates"""
        if max_results is not None and len(command_ids) >= max_results:
            return

        if node.is_end and node.command_id:
            if node.command_id not in seen:
                seen.add(node.command_id)
                command_ids.append(node.command_id)

        if max_results is not None and len(command_ids) >= max_results:
            return

        for child in node.children.values():
            self._collect_command_ids(child, command_ids, seen, max_results)
            if max_results is not None and len(command_ids) >= max_results:
                break


def word_start_offsets(text: str) -> list[int]:
    """Where each word after the first begins in `text`: after a space,
    hyphen, dot, underscore or other separator, and at a capital that follows
    a lowercase letter ("OneNote", "PowerShell")."""
    offsets = []
    for index in range(1, len(text)):
        previous, character = text[index - 1], text[index]
        if not character.isalnum():
            continue
        if not previous.isalnum() or (character.isupper() and previous.islower()):
            offsets.append(index)
    return offsets


class WordStartIndex:
    """Commands by the start of any later word in their name, so "code"
    finds "Visual Studio Code" and "studio c" finds it too.

    Each word start is kept with the rest of the name after it, sorted, and a
    prefix is found by binary search. Several commands can share a word
    ("Adobe Reader", "Foxit Reader"), which the command trie's one command
    per keyword could not hold.
    """

    def __init__(self, case_sensitive: bool = False):
        self.case_sensitive = case_sensitive
        self._keys: list[str] = []
        self._command_ids: list[str] = []

    def normalize(self, text: str) -> str:
        return text if self.case_sensitive else text.lower()

    def build(self, entries: Iterable[tuple[str, str]]) -> None:
        """Index (name, command id) pairs, replacing what was there."""
        pairs = sorted(
            (self.normalize(text[offset:]), command_id)
            for text, command_id in entries
            for offset in word_start_offsets(text)
        )
        self._keys = [key for key, _command_id in pairs]
        self._command_ids = [command_id for _key, command_id in pairs]

    def search_prefix(self, prefix: str) -> list[str]:
        """Unique command IDs with a later word starting with `prefix`, in the
        index's own order (callers sort)."""
        wanted = self.normalize(prefix)
        if not wanted:
            return []
        found: list[str] = []
        seen: set[str] = set()
        index = bisect_left(self._keys, wanted)
        while index < len(self._keys) and self._keys[index].startswith(wanted):
            command_id = self._command_ids[index]
            if command_id not in seen:
                seen.add(command_id)
                found.append(command_id)
            index += 1
        return found
