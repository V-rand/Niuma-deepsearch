---
name: retrieval-strategy
description: Use when the task requires multi-step retrieval (finding specific papers, entities, or facts across multiple sources). Teaches staged search recipes, domain-locking, reference fingerprinting, and progressive drill-down — avoiding broad keyword soup.
---

# Retrieval Strategy

Retrieval is not "type keywords → get results." It is a staged, progressive narrowing process — like a human researcher who starts broad, locks onto an authoritative source, and drills deeper within it.

## The Core Rule

**Never search the entire web when you can lock to an authoritative domain. Never use keywords when you have structured fields. Never search again what you can reason from constraints.**

## Stage 1: Choose the Right Tool

| What you need | Tool | Why |
|---|---|---|
| Papers by author/venue/year | `arxiv_search` | Structured fields (author/venue/year/title/category). arXiv has comment/journal_ref with conference info. |
| Papers by author/institution/topic across all sources | `openalex_works` | 270M+ papers. Auto name→ID resolution. Reference fingerprint search. |
| Papers by exact reference fingerprint | `openalex_works(references="paper1,paper2")` | Find papers that cite specific papers. Uniquely identifies a paper by its citation list. |
| Entity lookup (author/institution ID) | `openalex_entity` | Get OpenAlex IDs for filtering. |
| Entity facts (birth, cast, year) | `wikipedia_lookup` | Structured infobox data. Exact title match. |
| Paper DOI/publisher | `crossref_search` | CrossRef metadata. |
| General web (news, blogs, discovery) | `web_search` | Last resort. Use with site: and "exact phrase" operators. |
| Already-saved materials | `workspace_search` | Always check FIRST. |

## Stage 2: Domain-Locked Discovery

When using `web_search`, NEVER search the entire web for academic content. Lock to authoritative domains:

```
# Instead of: "conference-2022 paper countryX countryY"
# Use: site:proceedings-site "countryX" "universityY"

# Instead of: "what is the capital of France"
# Use: site:en.wikipedia.org "capital" "France"
```

For `arxiv_search`, the structured fields ARE your domain lock:
```
arxiv_search(author="known_author", venue="venue_name", year="2022")
```

For `openalex_works`, combine filters to narrow:
```
openalex_works(institution="Stanford", topic="Graph Neural Networks", year="2022-2024", source_type="conference")
```

## Stage 3: Progressive Drill-Down (Human-Like Retrieval)

Do not stop at the first search. Use results to inform the next step:

1. **Start broad**: Find the candidate pool. Lock domain + 1-2 constraints.
2. **Read results**: NOT all results. Pick the 2-3 most promising. `web_read` the actual page content.
3. **Identify key entities**: From the read content, extract specific author names, institution names, DOIs, reference titles.
4. **Verify with structured tools**: Take those entities to arxiv_search or openalex_works for precise verification.
5. **Cross-verify**: Confirm with a second authoritative source.

Example chain for finding a specific conference paper:
```
Step 1: arxiv_search(author="known_author", venue="venue_name", year="2022") → returns candidates
Step 2: web_read most promising result → extract full author list, reference info
Step 3: openalex_works(references="ref_paper1,ref_paper2", year="2022") → verify by reference fingerprint
Step 4: openalex_works(doi="extracted DOI") → final confirmation
```

## Stage 4: Reference Fingerprint (Most Powerful)

A paper's reference list is its DNA. If you know specific references:
```
openalex_works(
    references="Language Models are Few-Shot Learners, A Simple Framework for Contrastive Learning of Visual Representations",
    year="2022"
)
```
This finds ALL papers that cite BOTH papers — usually a handful. Combined with author/venue/year filters, this uniquely identifies the target paper without needing the title.

## Google Operators (for web_search)

Both Tavily and Serper support these:
- `site:domain/path` — domain lock
- `"exact phrase"` — precise match
- `-word` — exclusion
- `OR` — alternatives (uppercase)
- `intitle:keyword` — page title must contain keyword
- `inurl:keyword` — URL must contain keyword
- `filetype:pdf` — find PDFs only
- `AROUND(N)` — proximity: `word1 AROUND(3) word2` (nearby words)

## OpenAlex Boolean and Proximity Search

When using `openalex_works(title=...)`, the title parameter supports:
- `AND, OR, NOT` (uppercase) — Boolean logic: `(graph AND contrastive) NOT supervised`
- `"exact phrase"` — phrase matching: `"graph contrastive learning"`
- `"phrase"~N` — proximity within N words: `"graph learning"~5`
- `word*` — wildcard: `contrast*` matches contrastive, contrasting
- `wom?n` — single-char wildcard
- `word~N` — fuzzy (edit distance): `transformar~1` matches transformer

## Tavily Advanced Features

`web_search` supports these Tavily-only params:
- `include_domains` / `exclude_domains` — domain filtering
- `exact_match=true` — require exact phrase match (for names, entities; returns fewer results)
- `time_range` — "day", "week", "month", "year" for recency filtering
- `source="scholar"` — Google Scholar via Serper (academic papers)
- `source="news"` — news-only search
- `search_depth` — "advanced" for highest quality (already the default)

## When You're Stuck

If 3+ searches produce no progress:
1. **Check premises** — is a fundamental assumption wrong?
2. **Change tool** — if web_search isn't working, try arxiv_search or openalex_works
3. **Try reference fingerprint** — if you know specific references, use openalex_works(references=...)
4. **Try domain-locking** — if searching broadly, lock to an authoritative domain with site:
5. **Stop searching** — if 2 pivots fail, answer with uncertainty rather than infinite search

## Anti-Patterns (Never Do These)

- ❌ `web_search("conference2022 countryX countryY N authors M references")` — keyword soup
- ❌ `web_search("what paper has 6 authors from China and Singapore")` — natural language question as query
- ❌ Repeating the same web_search with slightly different keywords 10+ times
- ❌ Using web_search when arxiv_search or openalex_works is clearly more appropriate
- ❌ Reading all 10 search results instead of picking the 2-3 most relevant
- ❌ Searching without first reasoning about what constraints imply
