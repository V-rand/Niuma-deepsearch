---
name: short-answer-research
description: Use when the answer_contract mode is short_answer — the user needs one fact, entity, number, year, location, or a BrowseComp-style multi-clue puzzle answer. Load this skill BEFORE any search or tool call for short-answer tasks.
---

# Short-Answer Research Discipline

The goal is a unique, verifiable short answer. The method is constraint-driven reasoning with search as verification, not keyword-driven browsing with search as the primary engine. This discipline is mandatory for all short-answer tasks — do not skip gates.

## Reason-Before-Search Principle

Research is constraint-driven reasoning, not keyword-driven browsing. Before ANY tool call:
1. Extract every constraint the question gives you — numbers, dates, positional clues (Nth author, Mth reference), relationships, categories, counts
2. Reason about what these constraints IMPLY — what domain, subfield, entity type, relationship pattern, or structural rule do they point to?
3. Only THEN formulate search queries. The query must be the CONCLUSION of your reasoning, not a rephrasing of the question into keywords.

Bad: see "6 authors, author 3 from country X, conference 2022" → search `web_search("conference 2022 countryX countryY collaboration")` — this is keyword soup, not reasoning.
Good: see same constraints → "author 6 is senior professor in field F, authors 1-2-4-5 from one university, N refs with ref1=venueA2020+ref4=venueB2020 → deduce subfield S → search `arxiv_search(author="known_author", venue="venue", year="2022")`" — this is reasoning output, not question rephrasing.

When you find yourself searching 3+ times without progress, the problem is almost certainly in your reasoning framework, not in your keywords. Stop searching. Check your premises.

## State Machine (Hard Gates)

Use a state machine, not a free-form search loop. Each gate determines the next action. Do not skip gates.

**0. PREMISE_CHECK** — before building any candidates, after extracting constraints:
- List every "I know X" fact you plan to build candidate search on
- Tag each: verified | assumed (needs check) | uncertain
- If any top-priority assumption being wrong would make your candidate set miss the answer, verify it BEFORE searching. A 30-second premise check prevents 20 rounds of wasted search.
- Examples of premises to check: "华东五校 includes which schools?", "Is USTC in 华东五校?", "What is the capital of X?", "Is this person's birthplace X or Y?"
- When positional clues exist (author 3, author 6, reference 4), do positional reasoning: author-1 is usually the lead contributor (often PhD student), author-last is usually the senior PI. Reference positions form a field fingerprint — the first few references define the subfield the paper builds on.

**1. PARSE** — extract answer_type, hard_constraints, soft_clues, ambiguous_terms, output_fields, and premises. Treat wording differences as potential traps: approved vs launched, birthplace vs registered residence, adjacent to vs located in, belongs to vs located in. Output the Question Model (at minimum: answer_type and hard_constraints) as your first visible action. Do not proceed to search without this.

**2. CANDIDATE** — build 2-5 candidates before answer verification. If fewer than 2 candidates exist, the next action is candidate discovery. If the question names a relation such as "female lead", enumerate ALL role members instead of choosing the most famous one. When clues involve "substance/drug", treat each implied substance as an independent candidate.

**3. TEST** — choose the hard constraint most likely to split candidates. For associative, linguistic, geographic, or inference-heavy clues, call `research_state(operation="analyze_constraint")` before broad search. If one search round on an associative constraint produces no direct high-quality match, switch to known-fact reasoning (`constraint_reasoning` skill) before more retrieval. Only continue retrieval when you can state a specific expected gain — what candidate will be split and how.

**4. UPDATE** — update the ledgers after every tool batch. A failed hard constraint rejects a candidate — do not rescue it unless the constraint interpretation changed. Surviving candidates must have every hard constraint either matched or listed as missing. Evidence must bind to a specific hard constraint. Do not accept "looks reasonable" overall as evidence.

**5. PIVOT_OR_STOP** — count progress and pivots explicitly. Progress means: new candidate, rejected candidate, verified hard constraint, or revised ambiguity. No-progress means: search returned results but candidate discrimination did not improve (no new candidate, no rejection, no verified constraint, no revised ambiguity). Three no-progress rounds = one failed pivot.

Before pivoting, run a premise re-check: list 3 assumptions you've been relying on. Could any be wrong? Pick the most questionable one and verify it. A wrong premise is the most common cause of extended search failure — changing keywords without checking premises is wasted effort.

After one failed pivot: change query family or frame (search by excluded alternatives, legal category, date/number pattern, source database, citation chain, or synonym family). Do not rotate keywords in the same frame.
After two failed pivots AND no candidate satisfies all hard constraints: stop searching. Answer with the best candidate plus explicit uncertainty, or say evidence is insufficient. "One more search" after two failed pivots is infinite search, not diligence.

**6. ANSWER** — answer immediately when one candidate satisfies ALL hard constraints AND the strongest rival is explicitly excluded. Output only the final answer and compact justification. Do not expose the full ledger unless asked. Only output a definite answer when the winner satisfies all hard constraints — otherwise output the most likely answer with uncertainty noted.

## State Externalization (Runtime-Enforceable)

Use `research_state` as the external control surface for short-answer work. Before repeated search/read calls, externalize the active constraint, known facts, reasoning paths, candidates, evidence, and round progress. If `research_state` returns an `action_card`, follow it before searching.

Operational minimum:
- Before first retrieval: call `research_state(operation="start", question_model=...)`.
- Before each new retrieval round: call `research_state(operation="next_action")` or `research_state(operation="analyze_constraint")`.
- After each retrieval round: call `research_state(operation="round_update", progress=..., progress_note=...)`.

Externalize and maintain these fields in `research_state` (not in hidden reasoning text):
- `Question Model`: answer_type, hard_constraints, soft_clues, ambiguities, premises, output_fields
- `Candidate Ledger`: candidate, matched, failed, missing, status
- `Evidence Ledger`: claim, source, constraint, verdict, reliability
- `round_control`: progress, no_progress_rounds, failed_pivots

## Retrieval Recipe

When searching for candidates, use staged, domain-locked queries — not broad keyword soup across the entire web.

**Stage 1 — Candidate Discovery:**
Lock the search to the authoritative domain for your problem. For conference papers: PMLR proceedings, OpenReview, arXiv. For entity facts: Wikipedia. Use `site:` to restrict scope.
Example: `site:proceedings.mlr.press/v162 "Singapore" "University of Science and Technology of China"` — instantly filters from 1200+ papers to a handful matching the constraint combination.

**Stage 2 — Fingerprint Verification:**
Once you have candidates, use the most specific known facts (reference titles, DOI, exact author names) as verification queries within the same locked domain.
Example: `site:proceedings.mlr.press/v162 "Language Models are Few-Shot Learners" "A Simple Framework for Contrastive Learning"` — uses reference fingerprints to uniquely identify the paper.

**Stage 3 — Confirmation:**
Cross-verify the candidate with another authoritative source: `"candidate title" PMLR` or `arxiv_search(title="candidate title")`.

**Google operators (supported by both Tavily and Serper):**
- `site:domain/path` — domain lock
- `"exact phrase"` — precise matching
- `-word` — exclusion
- `OR` — alternatives (uppercase)

**Site lock targets for common tasks:**
| Task | Lock to |
|---|---|
| Conference papers (ICML/NeurIPS/ICLR etc.) | `site:proceedings.mlr.press/v{NUM}` or `site:openreview.net` or the conference's official proceedings site |
| Wikipedia facts | `site:en.wikipedia.org` |
| Government/official data | `site:gov.cn` or domain of the relevant agency |
| Company/product info | `site:company.com` |

Articulate which mode each query serves:
- discovery query: discover candidates
- expansion query: expand candidate information along aliases / people / places
- discriminating query: search for constraints that most easily exclude a candidate
- answer query: verify the final output field

You CANNOT jump from discovery query directly to answer query. You are PROHIBITED from locking onto a single candidate after the first search round.

## Supplementary Rules

- Search snippets are discovery hints, not final proof. Use web_read or authoritative sources for hard constraints when available.
- When a constraint involves an abstract association (name reminds of, relates to, evokes), treat exact-match retrieval as discovery only. If one search round does not produce a direct high-quality match, switch to known-fact reasoning with the `constraint_reasoning` skill before more retrieval.
- Associative clue preference is soft, not absolute: if reasoning remains weak, you may run another targeted query, but record why this query can discriminate candidates.
- Prefer explanation simplicity: when two interpretations both fit, prefer the one with fewer assumptions and shorter reasoning chain. If chain-length gap is >=2 steps, default to the shorter chain unless clear counter-evidence exists.
- Do not conflate similar concepts: discovery / approved / put into production / launched / mass-produced; birthplace / ancestral home / registered residence (户籍所在地); adjacent to / located in / belongs to.

## Final Review Gate

Before outputting any answer (even if you think no search is needed):
- Does the winner satisfy ALL hard constraints? → If yes, prepare to output. If no, keep searching or mark uncertainty.
- Has at least one competing candidate been explicitly excluded? What is the exclusion reason?
- What is the weakest piece of evidence? Is it supported by two independent sources or one authoritative source?
- Are you conflating similar concepts? (See the list in Supplementary Rules.)

## Convergence Rules (Hard Stop Signals)

- Winner satisfies ALL hard constraints AND competing candidates are excluded → output answer immediately. One more source will not add value.
- 2 rounds on the same active constraint produce no discriminating gain → stop rotating keywords. Write current interpretation and one competing interpretation, compare with constraint_reasoning, continue with the better frame.
- 3 no-progress rounds = 1 failed pivot. Before pivoting, re-check premises. Change query family or frame (not just keywords).
- 2 failed pivots AND no candidate satisfies all constraints → stop. Answer with best candidate + uncertainty, or say evidence insufficient.
- 5 independent sources point to the same conclusion → consider credible, answer directly.

Only output a definite answer when the winner satisfies ALL hard constraints. Otherwise output the most likely answer with uncertainty noted. Keep the final response concise; do not show the full ledger unless the user asks.
