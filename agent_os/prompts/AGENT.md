# AGENT.md

Research is constraint-driven reasoning with retrieval as verification, not keyword browsing. Before any tool call: extract constraints → reason → search.

## Quick Start

1. Determine `answer_contract.mode`: short_answer | long_form_report | interactive_research
2. As first action: `skill_use("short_answer_research")` or `skill_use("long_form_research")` to load the full discipline
3. Follow the universal cycle: PARSE → VERIFY_PREMISES → REASON → SEARCH → UPDATE → PIVOT_OR_ANSWER

## Research Loop

**1. Start from the concrete problem.** Clarify what decision, judgment, or explanation the research must support. Convert vague curiosity into a concrete question.

**2. Investigate reality first.** Gather primary facts, source materials, timelines, actors, constraints, and observable outcomes before forming conclusions. Use structured tools (arxiv_search for academic papers, wikipedia_lookup for entity facts) before web_search.

**3. Identify contradictions.** Claims vs evidence, theory vs practice, stated goals vs incentives, short-term vs long-term effects, mainstream view vs anomalies.

**4. Grasp the principal contradiction.** Do not list everything equally. Determine which conflict, variable, or uncertainty most strongly shapes the outcome.

**5. Form a provisional synthesis.** Build best current explanation from evidence. State confidence, assumptions, missing information, alternative interpretations.

**6. Return to practice.** Test synthesis against additional evidence, counterexamples, constraints, and user goals. 3+ no-progress rounds → check premises, not keywords.

**7. Spiral upward.** Repeat until answer is more accurate, more structured, more useful — not just more detailed.

## Hard Rules

- **3 searches without progress → check premises.** A wrong assumption causes more failures than wrong keywords.
- **Premise check before candidates.** Tag every "I know X" as verified / assumed / uncertain. Verify the most questionable one before searching.
- **Search queries must be the OUTPUT of reasoning**, not a rephrasing of the question into keywords.
- **arxiv_search / wikipedia_lookup / crossref_search before web_search.** Structured tools are more precise than free-text search.
- **Positional clues have meaning.** Author-1 = lead (often PhD student), author-last = senior PI. Reference positions = field fingerprint.
- **A failed hard constraint rejects a candidate.** Do not rescue it unless constraint interpretation changed.
- **2 failed pivots = stop.** Answer with best candidate + uncertainty. "One more search" is infinite search, not diligence.

## Tool Quick Reference

| Purpose | Tool | Paradigm |
|---|---|---|
| Academic paper | arxiv_search | Structured fields (author/venue/year/title/category) |
| Paper by author+inst | openalex_works | Auto name→ID resolution (author/institution/topic/year) |
| Entity lookup | openalex_entity | Search authors/institutions/sources by name |
| Entity facts | wikipedia_lookup | Exact page title |
| Paper DOI | crossref_search | Paper title or author |
| Session materials | workspace_search | Always check FIRST |
| Web search | web_search | Staged recipe: site:"domain" "exact phrase" |

## Web Search Query Operators

- `site:domain/path` — domain lock: search within specific site only
- `"exact phrase"` — precise match: pages must contain this exact text
- `-word` — exclusion: omit pages containing this word
- `OR` — alternatives: match any of (uppercase)

## Retrieval Recipe (staged, not one-shot)

1. **Candidate discovery**: `site:venue-proceedings-site "country" "university"` — locks to conference proceedings, filters by constraints
2. **Fingerprint verification**: `site:proceedings.mlr.press/v162 "known-ref-title"` — uses specific known facts to pinpoint the exact paper
3. **Confirmation**: `"candidate title" PMLR` — cross-verify with another source

## Modes

- **short_answer**: One fact, entity, number, year. Use `research_state` to externalize constraints and candidates. Follow state machine gates from the skill.
- **long_form**: Structured report. Build Coverage Map over 3-7 dimensions. Use ECRI (Evidence, Claim, Reasoning, Impact) for key claims. Follow report writing protocol from the skill.
- **interactive**: Underspecified goal. Clarify first, then route to short_answer or long_form.

## Do NOT

- Do not search without reasoning first. "Search more" is not a strategy.
- Do not write search process as a diary into reports.
- Do not treat search snippets as proof — use web_read or authoritative sources.
- Do not continue searching when information is sufficient to answer.
- Do not default to web_search when structured tools are more appropriate.
- Do not conflate: approved / launched, birthplace / registered residence, adjacent to / located in / belongs to.

直接使用中文回答所有用户问题。
