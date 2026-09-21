"""Finding the tables a task is about, before the model starts guessing at names.

An analytics request names things in business language ("orders", "churned
customers"); the project names them `stg_jaffle__orders`. Closing that gap by
letting the model grep around costs turns and invites invented `ref()`s, so the
runner does it once, deterministically, from dbt's own manifest -- which is also
where lineage already lives, so the agent learns what a change will break before
it makes it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r'[a-z0-9]+')
NOISE = (
    'a an and the for with from into to of on in by add new create build make '
    'model models table tables column columns test tests data please'
)
STOPWORDS = frozenset(NOISE.split())
TOP_CANDIDATES = 5
MAX_MATCHES = 10


@dataclass(frozen=True)
class Resource:
    """One thing in the project the task could be about."""

    uid: str
    name: str
    kind: str
    """model, seed, source, or snapshot."""

    path: str
    description: str
    columns: tuple[str, ...]

    @property
    def label(self) -> str:
        return f'{self.name} ({self.kind})'


@dataclass(frozen=True)
class Manifest:
    """dbt's manifest, reduced to what exploration needs."""

    resources: dict[str, Resource]
    parents: dict[str, list[str]]
    children: dict[str, list[str]]

    @classmethod
    def load(cls, path: Path) -> Manifest | None:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return None

        resources: dict[str, Resource] = {}
        for uid, node in {**raw.get('nodes', {}), **raw.get('sources', {})}.items():
            kind = uid.split('.', 1)[0]
            if kind not in {'model', 'seed', 'snapshot', 'source'}:
                continue
            name = f'{node.get("source_name")}.{node["name"]}' if kind == 'source' else node['name']
            resources[uid] = Resource(
                uid=uid,
                name=name,
                kind=kind,
                path=node.get('original_file_path', ''),
                description=(node.get('description') or '').strip(),
                columns=tuple(node.get('columns', {})),
            )
        return cls(
            resources=resources,
            parents=raw.get('parent_map', {}),
            children=raw.get('child_map', {}),
        )

    def of_kind(self, kind: str) -> list[Resource]:
        return sorted((r for r in self.resources.values() if r.kind == kind), key=lambda r: r.name)

    def find(self, term: str) -> list[Resource]:
        """Resources whose name, description, or columns mention `term`."""
        needle = term.lower().strip()
        matches = [
            resource
            for resource in self.resources.values()
            if needle in resource.name.lower()
            or needle in resource.description.lower()
            or any(needle in column.lower() for column in resource.columns)
        ]
        return sorted(matches, key=lambda r: (needle not in r.name.lower(), r.name))[:MAX_MATCHES]

    def related(self, uid: str) -> tuple[list[str], list[str]]:
        """Immediate upstream and downstream names, as a person would read them.

        Tests and other non-resource nodes are dropped: a reader wants to know
        what breaks, not which assertion hangs off the model.
        """
        return (self._names(self.parents.get(uid, [])), self._names(self.children.get(uid, [])))

    def _names(self, uids: list[str]) -> list[str]:
        return [self.resources[uid].name for uid in uids if uid in self.resources]

    def candidates(self, goal: str) -> list[Resource]:
        """The resources a goal most likely refers to, best first."""
        wanted = {word for word in WORD.findall(goal.lower()) if word not in STOPWORDS and len(word) > 2}
        scored = [(self._score(resource, wanted), resource) for resource in self.resources.values()]
        ranked = sorted((pair for pair in scored if pair[0]), key=lambda pair: (-pair[0], pair[1].name))
        return [resource for _, resource in ranked[:TOP_CANDIDATES]]

    @staticmethod
    def _score(resource: Resource, wanted: set[str]) -> int:
        name_words = set(WORD.findall(resource.name.lower()))
        column_words = {word for column in resource.columns for word in WORD.findall(column.lower())}
        description_words = set(WORD.findall(resource.description.lower()))
        return 4 * len(wanted & name_words) + 2 * len(wanted & column_words) + len(wanted & description_words)


def survey(manifest: Manifest, goal: str) -> str:
    """A short briefing: what exists, and which of it the goal is probably about."""
    lines = []
    for kind in ('source', 'seed', 'model', 'snapshot'):
        resources = manifest.of_kind(kind)
        if resources:
            lines.append(f'{kind}s ({len(resources)}): {", ".join(r.name for r in resources)}')

    candidates = manifest.candidates(goal)
    if not candidates:
        lines.append('no resource matches this request by name; it is probably new work.')
        return '\n'.join(lines)

    lines.append('\nmost likely relevant, with lineage:')
    for resource in candidates:
        upstream, downstream = manifest.related(resource.uid)
        lines.append(f'  {resource.label} {resource.path}')
        if resource.columns:
            lines.append(f'    columns: {", ".join(resource.columns)}')
        lines.append(f'    upstream: {", ".join(upstream) or "none"}')
        lines.append(f'    downstream: {", ".join(downstream) or "none"}')
    return '\n'.join(lines)


def describe(manifest: Manifest, resources: list[Resource]) -> str:
    """Render matches for the agent, lineage included so one call is usually enough."""
    if not resources:
        return 'no match'
    lines = []
    for resource in resources:
        upstream, downstream = manifest.related(resource.uid)
        lines.append(f'{resource.label} {resource.path}')
        if resource.description:
            lines.append(f'  {resource.description}')
        if resource.columns:
            lines.append(f'  columns: {", ".join(resource.columns)}')
        lines.append(f'  upstream: {", ".join(upstream) or "none"}')
        lines.append(f'  downstream: {", ".join(downstream) or "none"}')
    return '\n'.join(lines)
