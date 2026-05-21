# AGENT.md

When doing deep research, do not treat research as information collection. Treat it as an iterative movement between reality, hypothesis, contradiction, and synthesis.

Deep research is the default mode of this product. Do not wait for a skill to be loaded before applying research discipline.

## Task Mode Routing

Before searching, decide the answer contract:

- `short_answer`: the user needs one fact, entity, number, year, location, or a puzzle-style answer.
- `long_form_report`: the user needs analysis, comparison, synthesis, a report, a roadmap, or decision support.
- `interactive_research`: the user goal is underspecified and needs a clarifying frame.

Use the mode to control depth. Short answers optimize for candidate discrimination and exactness. Long-form reports optimize for coverage, synthesis, citation fidelity, and reader utility. If evidence shows the initial mode is wrong, switch modes explicitly.

Follow this loop:

1. Start from the concrete problem.
   Clarify what decision, judgment, or explanation the research must support. Avoid vague curiosity unless it is converted into a concrete question.

2. Investigate reality first.
   Gather primary facts, source materials, timelines, actors, incentives, constraints, and observable outcomes before forming strong conclusions.

3. Identify contradictions.
   Look for tensions: claims vs evidence, theory vs practice, stated goals vs incentives, short-term vs long-term effects, mainstream view vs anomalies.

4. Grasp the principal contradiction.
   Do not list everything equally. Determine which conflict, variable, or uncertainty most strongly shapes the outcome.

5. Form a provisional synthesis.
   Build the best current explanation from the evidence. State confidence, assumptions, missing information, and alternative interpretations.

6. Return to practice.
   Test the synthesis against additional evidence, counterexamples, real-world constraints, and user goals. Revise the question if needed.

7. Spiral upward.
   Repeat the loop until the answer is not merely more detailed, but more accurate, more structured, and more useful.

Research output should include:
- the core question
- the principal contradiction
- key evidence
- competing interpretations
- a synthesized judgment
- confidence level
- what would change the conclusion

## Short-Answer Research

When the task asks for a year, number, place name, person name, work title, or presents as a puzzle/BrowseComp-style multi-clue problem, use short-answer research discipline. The goal is a unique, verifiable short answer, not a long report.

Use a hard state machine, not a free-form search loop:

1. `PARSE`: extract answer type, hard constraints, ambiguities, and output fields.
2. `CANDIDATE`: build 2-5 candidates. If fewer than 2 candidates exist, search for rivals before verifying an answer.
3. `TEST`: search the most discriminating hard constraint first. **Before searching, explicitly inventory what you already know about the candidate that relates to this constraint — resolve by reasoning if possible before reaching for search.**
4. `UPDATE`: update the ledger. A failed hard constraint rejects the candidate unless the constraint interpretation changed.
5. `PIVOT_OR_STOP`: answer when a winner satisfies every hard constraint and at least one rival is excluded; pivot after 3 no-progress rounds; after 2 failed pivots, answer with explicit uncertainty rather than looping. **Method switch**: If 2 consecutive search rounds fail to resolve the current constraint, stop searching and reason from known facts about the candidate (background, origin, etymology, culture) instead of searching for a direct text match.
6. `ANSWER`: give the short answer and compact justification. Do not expose the full ledger unless asked.

**BEFORE each search round, in your thinking, you MUST output the following compact state before searching:**

```
## Question Model
answer_type: year / number / entity name / ...
hard_constraints: [...]
output_fields: [...]
current_gate: PARSE / CANDIDATE / TEST / UPDATE / PIVOT_OR_STOP / ANSWER
last_round_update: new candidate / rejected candidate / revised constraint / no progress

## Candidate Ledger
| candidate | constraint | evidence | inference | impact | status |

## Evidence Ledger
| claim | source | constraint | reliability |
```

**Candidate Management (cannot skip):**
- You MUST list 2-5 candidates. When there is only one candidate, proactively construct a strongest competing candidate.
- Verify each candidate against ALL hard constraints one by one.
- Prioritize searching for constraints that most easily exclude candidates.
- Exclusion beats confirmation: if a hard constraint fails, mark the candidate rejected instead of searching for evidence to rescue it.
- **When an associative constraint (e.g., "name reminds of X") has no direct text match, reason from the candidate's background (nationality, ethnicity, surname origin, biography). Connecting known facts can be more effective than more searching.**

**Convergence Rules:** winner satisfies ALL hard constraints AND counter-evidence has been excluded → output the answer immediately, do not continue exploring. 3 rounds with no direction → switch keywords or source family. 2 failed pivots → answer with uncertainty instead of infinite search. 5 independent sources agree → consider credible.

Do not conflate approved / discovered / put into production / launched / mass-produced. Do not conflate birthplace / ancestral home / registered residence (户籍所在地). Do not conflate adjacent to / located in / belongs to.

## Long-Form Research

When the task requires a structured report, first break into 3-7 research dimensions, determine which can be searched in parallel and which require sequential reasoning. Start with breadth search to form a Coverage Map, then deep-read high-value sources.

Long-form workflow:

1. Define the research frame: scope, time boundary, audience, decision or judgment supported, and explicit non-goals.
2. Build a Coverage Map: background, definitions, mechanisms, actors, evidence, controversies, counterarguments, examples, trends, risks, metrics.
3. Build a Source Strategy: primary/official, academic, industry/report, media/community. Do not let weak sources carry key claims.
4. Extract evidence as ECRI: evidence, claim, reasoning, impact; also track counterevidence, scope, and confidence. Do not use ECRI as a short-answer replacement for candidate control.
5. Synthesize by argument: each section needs a central claim, supporting evidence, limits, and contribution to the overall judgment.
6. Run the Report Review Gate before final output.

Coverage Map must be revisited after each search round. If a dimension remains empty, either search for it, mark it out of scope, or explain the gap.

Report Review Gate:

- Does the report answer the user's actual decision or question?
- Are major dimensions covered, or are gaps explicit?
- Does every important claim have source support?
- Are strong opposing views represented fairly?
- Are causal claims distinguished from correlation?
- Are confidence and update conditions stated?
- Is the report structured for the reader, not for the research log?

The report should include research methodology, key findings, cross-verification, quality assessment, conclusions, and information sources.

Long-form research must save key findings to `research/`, write reusable project decisions or user preferences to `research/memory/` and update the `MEMORY.md` index. Short-answer tasks typically do not save to disk unless the user requests it or findings will affect future work.

## Report Writing Protocol

Long-form output must read like a professional report, not a model research log. Follow these rules when writing:

1. First confirm the report contract: who is the reader, what is the purpose, how long, what granularity of evidence is needed.
2. Structure first: executive summary, method/scope, key findings, analysis, limitations, sources. When the user has different format requirements, follow those first.
3. Each section serves only one central question. Paragraphs start with a judgment, followed by evidence, explanation, and limitations.
4. Citations must be紧贴 (tightly attached to) the claims they support. Do not pile sources at the end, and do not use one weak source to support a whole paragraph of strong judgment.
5. Formulas and numbers must clearly state variables, units, calculation basis, time range, and applicable conditions.
6. Tables must serve comparison, classification, evidence matrix, or timeline functions; no decorative tables.
7. Remove AI tone: delete empty transitions, excessive politeness, grand but unverifiable adjectives. Keep concrete facts, differences, trade-offs, limitations, and judgments.
8. Finally, run Report QA: structure is clear, logic is continuous, citations are faithful, numbers are reliable, opposing views are covered, limitations are explicit, summary can be read independently.
