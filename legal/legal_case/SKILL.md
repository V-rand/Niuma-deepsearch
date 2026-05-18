---
name: legal_case
description: 法律案件长期工作区。用于民事案件材料摄入、事实证据梳理、检索、文书、期限、阶段协作和归档。
when_to_use: 当用户开始一个新的法律案件或进入已有案件的长期工作流程
---

# Legal Case Skill

本 skill 为法律案件长期 session 提供工作区结构和行为边界。底层 AgentOS 只管理 session、工具、文件、检索、提醒和中断；案件阶段由公司侧外部 stage 状态机管理。Agent 可以读取和更新 `stage_state.md`，也可以根据材料提出阶段推进建议，但不得把阶段推进写成内核内的固定流程。

## 工作原则

1. 先建立“事实-证据-法律-策略”结构，再输出结论。
2. 区分已由证据支持的事实、客户陈述、推断和待核实事项。
3. 检索结论必须能回溯到 `raw_search/`、法规条文、案例、网页或工作区文件。
4. 期限是硬约束。发现送达日、开庭日、缴费日、答辩期、举证期、上诉期、再审期、执行期时，优先创建 `reminder_create` 并更新 `deadlines/calendar.md`。
5. 文件是案件状态的主载体。重要结论应落到 `facts.md`、`evidence/index.md`、`strategy.md`、`stage_state.md` 或对应 `pleadings/`、`research/` 文件中。
6. 主 Agent 负责最终判断和状态维护；sub-agent 只处理边界清晰的局部任务，如材料整理、法规检索、类案研究、庭审问题清单。

## 外部 Stage 协作

- 外部 stage 状态机负责决定当前阶段、阶段切换和公司业务流程。
- Agent 接收阶段信息后，应检查 `stage_state.md` 中的阶段输入、输出、风险和退出条件。
- Agent 可以向外部系统报告“建议进入下一阶段”“仍缺少哪些材料”“存在何种期限风险”，但不要自行假装阶段已经完成。
- 如果阶段信息与材料状态冲突，先记录冲突并通过 `question` 或对用户说明需要确认。

## 常规工作流

1. 材料进入 `uploads/` 后，优先解析并把派生内容写入 `drafts/derived/`，保留来源 lineage。
2. 更新 `facts.md`，把每个关键事实关联到证据路径。
3. 更新 `evidence/index.md`，标注证据强弱、缺口和补强方向。
4. 需要法律判断时，先查工作区，再用 `law_retrieve`、`case_retrieve` 或 `web_search/web_read`，并归档来源。
5. 输出文书草稿时写入 `pleadings/` 或 `drafts/`，并回读检查事实、请求、证据编号和期限。
6. 每轮重要推进后，用 `todowrite` 更新任务，用 `file_write` 将关键信息写入 `research/memory/` 并在 MEMORY.md 添加索引。

## 风险控制

- 不编造法律依据、案例号、法院意见或材料内容。
- 不把客户陈述直接写成已证事实。
- 不覆盖 `uploads/` 原始材料。
- 不用普通文件写入管理 todo；任务状态使用 `todowrite`。
- 对需要律师确认的策略、和解、上诉、执行、再审等选择，明确列出风险和待确认事项。
