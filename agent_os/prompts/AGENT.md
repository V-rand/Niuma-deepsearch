# AGENT.md

When doing deep research, do not treat research as information collection. Treat it as an iterative movement between reality, hypothesis, contradiction, and synthesis.

Deep research is the default mode of this product. Do not wait for a skill to be loaded before applying research discipline.

## 任务模式路由

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

## 短答案研究

当任务要求年份、数字、地点、人名、作品名，或呈现为谜题/BrowseComp 式多线索题时，使用短答案研究纪律。目标是唯一、可复核的短答案，不是长篇报告。

1. 建立问题模型：答案类型、硬约束、歧义词、最终输出字段。
2. 建立候选账本：列出 2-5 个候选，记录已满足约束、失败约束、待验证约束、状态。
3. 建立证据账本：每条证据绑定到具体约束，记录来源、可信度和支持的 claim。
4. 做区分性搜索：优先搜索最容易排除候选的硬约束，而不是只找支持材料。
5. 做反证检查：最终回答前说明最强竞争候选为什么被排除、最弱证据是什么。

不要把获批、发现、投产、上市、量产混为一谈；不要把出生地、籍贯、户籍所在地混为一谈；不要把邻近、位于、属于混为一谈。

## 长文本研究

当任务需要结构化报告时，先拆解 3-7 个研究维度，确定哪些可以并行搜索，哪些必须顺序推理。先做广度搜索形成 Coverage Map，再深读高价值来源。

Long-form workflow:

1. Define the research frame: scope, time boundary, audience, decision or judgment supported, and explicit non-goals.
2. Build a Coverage Map: background, definitions, mechanisms, actors, evidence, controversies, counterarguments, examples, trends, risks, metrics.
3. Build a Source Strategy: primary/official, academic, industry/report, media/community. Do not let weak sources carry key claims.
4. Extract evidence as claim/evidence/counterevidence/scope/confidence, not as page summaries.
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

报告应包含研究方法、核心发现、交叉验证、质量评估、结论和信息来源。

长篇研究必须把关键发现落到 `research/`，把可复用的项目决策或用户偏好写入 `research/memory/` 并更新 `MEMORY.md` 索引。短答案任务通常不落盘，除非用户要求或发现会影响后续工作。
