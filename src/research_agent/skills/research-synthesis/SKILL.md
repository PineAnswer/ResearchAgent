---
name: research-synthesis
description: 基于论文卡片进行共识、冲突、方法与研究空白分析
---

# Research Synthesis

## 步骤

1. 调用一次 `get_active_research_project`，读取当前运行绑定项目中的全部 PaperCard 和 Evidence；该工具不接收 project_id。
2. 按研究问题、方法、数据集和结论建立比较维度。
3. 分别写出共识、冲突和方法差异。
4. 每个研究空白通过 `supporting_paper_ids` 引用论文，并通过 `evidence_ids` 引用正式 Evidence。
5. 为每个空白给出置信度和可证伪假设。
6. 输出符合 `SynthesisReport` 的结构化结果。
7. consensus、conflicts、method_comparison 每项使用 `{statement, evidence_ids}`。

## 证据ID硬约束

- `get_active_research_project` 返回的 `valid_evidence_ids` 是唯一合法引用清单。
- `limitations`、`datasets`、`paper_id` 和 `artifact_id` 都不能作为 `evidence_id`。
- 无法由 `findings` 中正式 Evidence 支撑的判断必须删除，不能使用 `paper_id:limitations` 等临时编号。

## 约束

- 置信度使用 LOW、MEDIUM、HIGH；一篇论文或摘要级证据通常只能标记 LOW。
- 禁止加入Evidence原文没有出现的百分比、倍数或其他精确数字。
- 论文没有研究某问题，不自动等于该问题具有创新性。
- 明确区分来源事实与综合推断。
- 只有元数据且findings为空的PaperCard不能支撑综合结论。
- 禁止猜测、生成或改写 project_id。项目读取工具返回结构化错误时立即停止综合，并将错误交还 Supervisor。
