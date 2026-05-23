---
name: short-answer-research
description: Use when the answer_contract mode is short_answer — the user needs one fact, entity, number, year, location, or a BrowseComp-style multi-clue puzzle answer. Load this skill BEFORE any search or tool call for short-answer tasks.
---

# Short-Answer Research Discipline

The goal is a unique, verifiable short answer. The method is constraint-driven reasoning with retrieval as discovery and verification, not keyword-driven browsing. Keep the protocol small: parse the question, discover/build candidates, test hard constraints, update state, then answer or pivot.

## Minimal Protocol (Reason-Before-Search)

Use this as the execution spine:

1. **Parse** — extract answer_type, hard constraints, soft clues, ambiguities, premises, and output fields.
2. **Discover / Build Candidates** — build 2-5 candidates when possible. If the answer cannot be inferred (paper/document/law/case lookup), use targeted retrieval to discover candidates.
3. **Test** — choose the hard constraint most likely to split candidates. Prefer checks that can exclude a candidate.
4. **Update** — bind evidence to a claim or constraint. A failed hard constraint rejects a candidate unless the constraint interpretation changes.
5. **Pivot / Answer** — if no progress, repair premise/frame/tool choice. If one candidate satisfies all hard constraints and rivals are handled, answer concisely.

Before retrieval, you need a strategy, not necessarily a candidate. The query must be produced by constraint analysis or a high-entropy fingerprint plan, not by pasting the user question into search.

When you find yourself searching 3+ times without progress, the problem is almost certainly in your reasoning framework, not in your keywords. Stop searching. Check your premises.

### Discovery-First Exception: Literature / Document Lookup

Some short-answer tasks ask you to find a specific paper, article, publication, journal, venue, report, dataset, law, case, or document from a fingerprint of constraints. In these tasks, search is not merely final verification — search is the candidate-discovery mechanism.

For literature/document lookup:
- Do NOT force pure known-fact reasoning to produce a candidate that cannot be inferred.
- Reason first only enough to construct a high-signal retrieval plan: exact phrases, numeric fingerprints, method terms, population/sample clues, author/year hints, source families, and likely databases.
- Prefer high-entropy fingerprints over vague keywords. Good first query units are exact numbers, unusual phrases, method names, identifiers, and rare co-occurrences. Avoid weak words like "study", "analysis", "paper", "factors", "relationship" unless paired with a strong fingerprint.
- Then run targeted discovery retrieval early. Examples: exact phrase search, `web_search` with quotes/domain locks, `openalex_works`, `crossref_search`, `arxiv_search`, publisher/proceedings sites, or domain-site lookup.
- Use reasoning after retrieval to rank, exclude, and verify candidates against every hard constraint.

Good: constraints include `"approximately 1.7 million"`, `employed`, `Population and Housing Census`, `multinomial logistic regression` → search the exact numeric/method fingerprint early, then verify the discovered paper and publication.

Bad: spend many rounds trying to infer the country or journal without searching. You cannot reason a hidden publication title out of thin air.

## Detailed Gates (State Machine)

Use these gates when the task is multi-clue, ambiguous, or starting to drift. For simple lookups, the Minimal Protocol is enough.

**0. PREMISE_CHECK** — before building any candidates, after extracting constraints:
- List every "I know X" fact you plan to build candidate search on
- Tag each: verified | assumed (needs check) | uncertain
- If any top-priority assumption being wrong would make your candidate set miss the answer, verify it BEFORE searching. A 30-second premise check prevents 20 rounds of wasted search.
- Examples of premises to check: "华东五校 includes which schools?", "Is USTC in 华东五校?", "What is the capital of X?", "Is this person's birthplace X or Y?"
- When positional clues exist (author 3, author 6, reference 4), do positional reasoning: author-1 is usually the lead contributor (often PhD student), author-last is usually the senior PI. Reference positions form a field fingerprint — the first few references define the subfield the paper builds on.

**1. PARSE** — extract answer_type, hard_constraints, soft_clues, ambiguous_terms, output_fields, and premises. Treat wording differences as potential traps: approved vs launched, birthplace vs registered residence, adjacent to vs located in, belongs to vs located in. Externalize the Question Model through `research_state` before repeated retrieval. Keep final output concise and do not expose the full ledger unless asked.

**2. CANDIDATE** — build 2-5 candidates before answer verification when the task allows it. If fewer than 2 candidates exist, the next action is candidate discovery. For literature/document lookup, candidate discovery may be a targeted retrieval round; do not block it behind pure reasoning. If the question names a relation such as "female lead", enumerate role members rather than choosing the most famous one. When clues involve "substance/drug", treat each implied substance as an independent candidate.

**3. TEST** — choose the hard constraint most likely to split candidates. For associative, linguistic, geographic, or inference-heavy clues, call `research_state(operation="analyze_constraint")` before broad search. If one search round on an associative constraint produces no direct high-quality match, switch to known-fact reasoning (`constraint_reasoning` skill) before more retrieval. Only continue retrieval when you can state a specific expected gain — what candidate will be split and how.

**4. UPDATE** — update the ledgers after every tool batch. A failed hard constraint rejects a candidate — do not rescue it unless the constraint interpretation changed. Surviving candidates must have every hard constraint either matched or listed as missing. Evidence must bind to a specific hard constraint. Do not accept "looks reasonable" overall as evidence.

**5. PIVOT_OR_STOP** — count progress and pivots explicitly. Progress means: new candidate, rejected candidate, verified hard constraint, or revised ambiguity. No-progress means: search returned results but candidate discrimination did not improve (no new candidate, no rejection, no verified constraint, no revised ambiguity). Three no-progress rounds = one failed pivot.

Before pivoting, run a premise re-check: list 3 assumptions you've been relying on. Could any be wrong? Pick the most questionable one and verify it. A wrong premise is the most common cause of extended search failure — changing keywords without checking premises is wasted effort.

After one failed pivot: change query family or frame (search by excluded alternatives, legal category, date/number pattern, source database, citation chain, or synonym family). Do not rotate keywords in the same frame.
After two failed pivots AND no candidate satisfies all hard constraints: stop searching. Answer with the best candidate plus explicit uncertainty, or say evidence is insufficient. "One more search" after two failed pivots is infinite search, not diligence.

**6. ANSWER** — answer immediately when one candidate satisfies ALL hard constraints AND the strongest rival is explicitly excluded. Output only the final answer and compact justification. Do not expose the full ledger unless asked. Only output a definite answer when the winner satisfies all hard constraints — otherwise output the most likely answer with uncertainty noted.

## State Externalization (Runtime-Enforceable)

Use `research_state` as the external control surface for short-answer work. Before repeated search/read calls, externalize the active constraint, known facts, reasoning paths, candidates, evidence, and round progress. If `research_state` returns an `action_card`, treat it as control guidance; only reset/terminal states are hard stops. For discovery-first literature/document lookup, an early targeted search is allowed when no candidate can be inferred.

Operational minimum:
- Before first retrieval: call `research_state(operation="start", question_model=...)`.
- Before each new retrieval round: call `research_state(operation="next_action")` or `research_state(operation="analyze_constraint")`.
- After each retrieval round: call `research_state(operation="round_update", progress=..., progress_note=...)`.
- When search or reasoning starts to repeat: call `research_state(operation="working_notes", question_type=..., active_goal=..., current_action=..., known=[...], unknown=[...], failed_paths=[...], evidence_target=..., exit_condition=..., next_move=...)`.

Working notes are the lightweight visible substitute for relying on hidden thinking. Keep them short: 1 task type, 1 active goal, 1 current action, 2-4 known facts, unresolved unknowns, failed paths, evidence target, exit condition, and the next move. Update them every few retrieval/reasoning steps, after no-progress retrieval, when changing frames, and before the final answer. Do not expose these notes in the final answer unless the user asks.

Use `current_action` to manage attention:
- `reason`: connect known facts or compare interpretations without new evidence.
- `lookup`: consult an authoritative structured source for a known entity/fact.
- `match`: find similar cases, papers, entities, or historical patterns.
- `search`: discover unknown candidates with high-entropy fingerprints.
- `verify`: confirm or falsify a candidate/output field with independent evidence.
- `answer`: stop and produce the concise final answer.

Externalize and maintain these fields in `research_state` (not in hidden reasoning text):
- `Working Notes`: question_type, active_goal, current_action, known, unknown, failed_paths, evidence_target, exit_condition, next_move
- `Question Model`: answer_type, hard_constraints, soft_clues, ambiguities, premises, output_fields
- `Candidate Ledger`: candidate, matched, failed, missing, status
- `Evidence Ledger`: claim, source, constraint, verdict, reliability
- `round_control`: progress, no_progress_rounds, failed_pivots

## Retrieval Recipe

When searching for candidates, use staged, domain-locked queries — not broad keyword soup across the entire web.

**Stage 1 — Candidate Discovery:**
Lock the search to the authoritative domain for your problem. For conference papers: official proceedings, OpenReview, arXiv, publisher pages, or the venue's official site. For entity facts: Wikipedia or the relevant official/source database. Use `site:` or `include_domains` to restrict scope.
Example: `site:authoritative-proceedings-domain "known country" "known institution"` — filters a large corpus to a handful matching the constraint combination.

**Stage 2 — Fingerprint Verification:**
Once you have candidates, use the most specific known facts (reference titles, DOI, exact author names) as verification queries within the same locked domain.
Example: `site:authoritative-proceedings-domain "known reference title 1" "known reference title 2"` — uses reference fingerprints to identify the candidate.

**Stage 3 — Confirmation:**
Cross-verify the candidate with another authoritative source: `"candidate title" authoritative source` or `arxiv_search(title="candidate title")`.

**Google operators (supported by both Tavily and Serper):**
- `site:domain/path` — domain lock
- `"exact phrase"` — precise matching
- `-word` — exclusion
- `OR` — alternatives (uppercase)

**Site lock targets for common tasks:**
| Task | Lock to |
|---|---|
| Conference papers | official proceedings site, OpenReview, arXiv, publisher pages |
| Wikipedia facts | `site:en.wikipedia.org` |
| Government/official data | `site:gov.cn` or domain of the relevant agency |
| Company/product info | `site:company.com` |

Articulate which mode each query serves:
- discovery query: discover candidates
- expansion query: expand candidate information along aliases / people / places
- discriminating query: search for constraints that most easily exclude a candidate
- answer query: verify the final output field

Avoid jumping from discovery query directly to answer query. Do not lock onto a single candidate after the first search round unless the task is a simple lookup or the source is clearly authoritative.

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
