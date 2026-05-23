---
name: long-form-research
description: Use when the answer_contract mode is long_form_report — the user needs analysis, comparison, synthesis, a structured report, roadmap, or decision support. Load this skill BEFORE any search for long-form tasks.
---

# Long-Form Research Discipline

The goal is a structured, verifiable report with sufficient coverage, coherent argumentation, faithful citations, and reader utility. The method is the same constraint-driven reasoning cycle as short-answer work (PARSE → VERIFY_PREMISES → REASON → SEARCH → UPDATE → PIVOT_OR_ANSWER), organized over multiple dimensions. Long-form does NOT mean "search broadly first and reason later" — it means synthesizing more dimensions, each requiring the same reasoning discipline.

## Research Lifecycle

Follow the 7-step research loop. Each step produces output that feeds the next. The loop applies to every dimension and every search decision within the report.

**1. Start from the concrete problem.**
Clarify what decision, judgment, or explanation the research must support. Audience, scope, time boundary, what is NOT answered. Avoid vague curiosity — convert into a concrete question.

**2. Investigate reality first.**
Gather primary facts, source materials, timelines, actors, incentives, constraints, and observable outcomes before forming strong conclusions. Do not build conclusions on weak sources (media/community) — they can prompt investigation, not support it.

**3. Identify contradictions.**
Look for tensions: claims vs evidence, theory vs practice, stated goals vs incentives, short-term vs long-term effects, mainstream view vs anomalies. A report without contradictions is a summary, not research.

**4. Grasp the principal contradiction.**
Do not list everything equally. Determine which conflict, variable, or uncertainty most strongly shapes the outcome. The report's structure should lead from this principal contradiction.

**5. Form a provisional synthesis.**
Build the best current explanation from evidence. State confidence, assumptions, missing information, and alternative interpretations. Cite sources that actually support each claim.

**6. Return to practice.**
Test the synthesis against additional evidence, counterexamples, real-world constraints, and user goals. Revise the question if needed. 3+ search rounds without progress → check premises, not keywords.

**7. Spiral upward.**
Repeat until the answer is not merely more detailed, but more accurate, more structured, and more useful.

## Research Architecture

**Research Frame:**
Clarify the judgment type: explanation, comparison, decision support, systematic review, trend judgment, risk assessment, roadmap. Write explicit scope, time boundary, object boundary, and non-goals.

**Coverage Map:**
Break into 3-7 research dimensions: background, definitions, mechanisms, key actors, evidence, controversies, counterarguments, cases, trends, risks, evaluation metrics. Track each dimension as covered / weak / out-of-scope. Revisit after each search round — if a dimension remains empty, either search for it, mark it out of scope, or explain the gap. The Coverage Map is a control surface, not a table of contents.

**Source Strategy:**
Match source types to dimensions:
- primary/official sources → for facts, rules, and legal constraints
- academic sources → for mechanisms, evidence, and theoretical foundations
- industry/report sources → for market data, practice, and trends
- media/community sources → for phenomena, clues, and opinions only. Cannot alone support key conclusions.

Each key claim in the report must trace back to its source. Do not use one weak source to support a paragraph of strong judgment.

## Evidence Synthesis

For key claims, extract as ECRI (Evidence, Claim, Reasoning, Impact), plus counterevidence, scope, and confidence. ECRI is a synthesis discipline, not a visible template that every sentence must follow. Do not pile web summaries into a report — each key claim must have:
- What specific evidence supports it (with source)
- What the claim actually means (not just re-stating evidence)
- How the evidence leads to the claim (reasoning chain)
- Why this claim matters for the overall judgment (impact)

## Argument Structure

Each section must have: a central claim → supporting evidence → limitations → contribution to overall judgment. Sections must form an inference chain, not a parallel material library. If evidence is mixed, state what is established, what is plausible, and what remains unresolved. Distinguish facts, interpretations, inferences, and recommendations. Distinguish correlation from causation. Conclusion strength must not exceed evidence strength.

## Report Review Gate

Before output, check:
- Does the report answer the user's actual decision or question?
- Are major dimensions covered, or are gaps explicit?
- Does every important claim have source support?
- Are strong opposing views represented fairly?
- Are causal claims distinguished from correlation?
- Are confidence and update conditions stated?
- Is the report structured for the reader, not for the research log?

## Report Output

Default structure: executive summary, method/scope, key findings, analysis, uncertainties, sources, next questions. Adjust to user format requirements. Long-form content should be saved to `research/`. If conclusions or user preferences affect future work, also write to `research/memory/` and update `MEMORY.md`.

## Report Writing Protocol

When writing the final report, follow these rules:

1. **Report Contract**: Confirm report type, audience, purpose, length, tone. Default to professional readers, concise but complete.
2. **Structure Rules**: Headings establish logical hierarchy. Each section answers one central question. Start with a claim, then evidence, explanation, limitations. Do not write research process as a diary.
3. **Citation Rules**: Citations tightly attached to claims they support. Format: `[source: title/organization, URL or archive path]`. Separate sources for multiple claims in the same paragraph.
4. **Formula and Number Rules**: Formulas must define variables, units, applicable conditions, calculation basis. Numbers must explain time, region, sample, or statistical basis. Do not compare data with different bases.
5. **Tables and Figures**: Tables serve comparison, classification, timelines, or evidence matrices — not decoration. Headers must be comparable dimensions. Each row traceable to a source.
6. **Logic Rules**: Distinguish facts, interpretations, inferences, recommendations. Distinguish correlation from causation. Conclusion strength ≤ evidence strength. Present opposing views fairly.
7. **Professional Style**: Delete empty clichés ("值得注意的是", "深入探讨", "综上所述"). Use fewer adjectives, more verifiable facts, comparisons, limitations, judgments. Prefer precise transitions: because, however, therefore, under this scope.
8. **Final QA Gate**: Structure clear? Claims have evidence? Citations correct? Numbers/formulas have bases? Opposing views covered? Limitations explicit? Summary can be read independently?
