"""Find skills whose descriptions compete for the same requests.

Agents pick skills by matching the request against each skill's
name + description. When two descriptions are very similar, the agent has to
guess, and research shows selection accuracy drops sharply as skill pools grow.
We flag pairs with high TF-IDF cosine similarity. It's cheap and dependency-free.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from placebo_cli.skills.model import Skill

_WORD_RE = re.compile(r"[a-z][a-z0-9+#]{2,}")
STOPWORDS = set(
    """
    the and for with that this from your you use used using when what which will into are was were
    can could should would about any all also has have had not but only more most other such than then
    them they their there these those its it's our out over under very via per each both same own just
    skill skills agent agents claude codex user users task tasks help helps helping work working make
    making create creating need needs needed like want wants file files code whenever trigger triggers
    triggered invoke invoked request requests ask asks asked mention mentions mentioned including include
    includes e.g i.e etc based new existing specific specifically support supports provide provides
    """.split()
)


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf) and not w.endswith("ss"):
            w = w[: -len(suf)] + ("y" if suf == "ies" else "")
            break
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]  # bundle / bundled / bundles -> bundl
    return w


# stem -> surface form counts, so reports show real words ("tracing", not "trac")
_SURFACE: dict[str, Counter] = {}


def _terms(text: str) -> list[str]:
    out = []
    for w in _WORD_RE.findall(text.lower()):
        if w in STOPWORDS:
            continue
        s = _stem(w)
        _SURFACE.setdefault(s, Counter())[w] += 1
        out.append(s)
    return out


def _surface(stem: str) -> str:
    c = _SURFACE.get(stem)
    return c.most_common(1)[0][0] if c else stem


@dataclass
class Collision:
    a: str
    b: str
    a_path: str
    b_path: str
    similarity: float
    shared_terms: list[str]


@dataclass
class Duplicate:
    name: str
    paths: list[str]
    identical: bool


def find_collisions(skills: list[Skill], threshold: float = 0.35, max_pairs: int = 200) -> list[Collision]:
    docs = []
    for s in skills:
        # Name counts double: agents weigh it heavily when matching.
        text = f"{s.display_name.replace('-', ' ')} {s.display_name.replace('-', ' ')} {s.description}"
        docs.append(Counter(_terms(text)))
    n = len(docs)
    if n < 2:
        return []
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d.keys())
    idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
    vecs = []
    for d in docs:
        v = {t: (1 + math.log(c)) * idf[t] for t, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    out: list[Collision] = []
    for i in range(n):
        vi = vecs[i]
        for j in range(i + 1, n):
            if skills[i].content_hash == skills[j].content_hash:
                continue  # identical copies are reported as duplicates instead
            vj = vecs[j]
            small, big = (vi, vj) if len(vi) < len(vj) else (vj, vi)
            sim = sum(x * big.get(t, 0.0) for t, x in small.items())
            if sim >= threshold:
                shared = [_surface(t) for t in sorted(set(vi) & set(vj), key=lambda t: -(vi[t] * vj[t]))[:6]]
                out.append(Collision(
                    skills[i].display_name, skills[j].display_name,
                    str(skills[i].path), str(skills[j].path), round(sim, 3), shared,
                ))
    out.sort(key=lambda c: -c.similarity)
    return out[:max_pairs]


def find_duplicates(skills: list[Skill]) -> list[Duplicate]:
    by_name: dict[str, list[Skill]] = {}
    for s in skills:
        by_name.setdefault(s.display_name, []).append(s)
    out = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        hashes = {s.content_hash for s in group}
        out.append(Duplicate(name, [str(s.path) for s in group], identical=len(hashes) == 1))
    return out
