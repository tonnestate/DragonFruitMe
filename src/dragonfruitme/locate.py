"""Relevance search over the page graph.

A small BM25F-style ranker: term frequency is saturated per block, weighted by
on-page signals (heading level, region, emphasis) and boosted when the query
matches the block's section headings or the page title. The result is the
minimal sub-graph an agent needs instead of the whole page.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .graph import TITLE_WEIGHT, Block, PageGraph
from .values import tokenize

K1 = 1.2
B = 0.75
EMPHASIS_BONUS = 1.0
SECTION_BONUS = 0.6
TITLE_BONUS = 0.15


def _block_tokens(block: Block) -> list[str]:
    return tokenize(block.text)


def rank(graph: PageGraph, query: str) -> list[tuple[float, Block, list[str]]]:
    q_tokens = list(dict.fromkeys(tokenize(query)))
    if not q_tokens or not graph.blocks:
        return []
    docs = [(block, _block_tokens(block)) for block in graph.blocks]
    n_docs = len(docs)
    avg_len = sum(len(t) for _, t in docs) / n_docs or 1.0
    df: Counter[str] = Counter()
    for _, tokens in docs:
        df.update(set(tokens))
    title_tokens = set(tokenize(graph.title))
    results: list[tuple[float, Block, list[str]]] = []
    for block, tokens in docs:
        if not tokens:
            continue
        tf = Counter(tokens)
        emphasis_tokens = set(tokenize(" ".join(block.emphasis)))
        section_tokens: dict[str, float] = {}
        for depth, heading in enumerate(block.section):
            # deeper (closer) headings count more
            weight = 0.5 + 0.5 * (depth + 1) / len(block.section)
            for token in tokenize(heading):
                section_tokens[token] = max(section_tokens.get(token, 0.0), weight)
        score = 0.0
        matched: list[str] = []
        length_norm = 1 - B + B * len(tokens) / avg_len
        for term in q_tokens:
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf.get(term, 0)
            term_score = 0.0
            if freq:
                saturated = freq * (K1 + 1) / (freq + K1 * length_norm)
                term_score += idf * saturated * block.weight
                if term in emphasis_tokens:
                    term_score += idf * EMPHASIS_BONUS
                matched.append(term)
            if term in section_tokens:
                term_score += idf * SECTION_BONUS * section_tokens[term]
                if not freq:
                    matched.append(term)
            if term in title_tokens and freq:
                term_score += idf * TITLE_BONUS * TITLE_WEIGHT
            score += term_score
        if score > 0:
            coverage = len(set(matched)) / len(q_tokens)
            results.append((score * (0.5 + 0.5 * coverage), block, sorted(set(matched))))
    results.sort(key=lambda item: (-item[0], item[1].id))
    return results


def locate(
    graph: PageGraph,
    query: str,
    *,
    max_results: int = 8,
    max_chars_per_block: int = 400,
    max_context_chars: int = 4000,
    include_neighbors: bool = True,
) -> dict[str, Any]:
    ranked = rank(graph, query)
    by_id = {b.id: b for b in graph.blocks}
    hits: list[dict[str, Any]] = []
    used = 0
    context_truncated = False
    for score, block, matched in ranked[:max_results]:
        item = block.as_dict(max_chars_per_block)
        item["score"] = round(score, 4)
        item["matched"] = matched
        links = [
            {"href": l.href, "text": l.text, "internal": l.internal}
            for l in graph.links
            if l.block_id == block.id
        ][:5]
        if links:
            item["links"] = links
        if include_neighbors:
            nxt = by_id.get(block.id + 1)
            if nxt is not None and nxt.section == block.section and len(block.text) < 80:
                # label-like hit: the value usually lives in the next block
                item["next"] = nxt.as_dict(max_chars_per_block // 2)
        size = len(item["text"]) + len(item.get("next", {}).get("text", ""))
        if used + size > max_context_chars and hits:
            context_truncated = True
            break
        used += size
        hits.append(item)
    return {
        "query": query,
        "hits": hits,
        "total_candidates": len(ranked),
        "results_truncated": len(ranked) > len(hits),
        "context_truncated": context_truncated,
    }
