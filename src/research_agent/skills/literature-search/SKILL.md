---
name: literature-search
description: 学术检索、去重与候选论文筛选方法
---

# Literature Search

搜索次数由运行环境配置并由中间件强制执行。默认预算是 OpenAlex 3次、Crossref 1次，修改环境变量后会自动变化。

## 步骤

1. 将研究问题拆成主题词、方法词、对象词和限制条件。
2. 设计互补查询：直接主题、英文扩展、局限或挑战。若任务描述给出系统检索-筛选迭代轮数上限，查询轮数不得超过该限制和工具预算。
3. 每次只调用一个搜索工具，观察结果后分析分布并决定下一次查询。
4. 优先使用 OpenAlex；只有确实需要 DOI/标题交叉核对时使用一次 Crossref。
5. 按 DOI、paper_id 和规范化标题依次去重（在上下文中完成）。
6. 对每篇论文做标题摘要级初筛，给出 include / exclude / uncertain 决定和简短理由。
7. 把本轮初筛意见、uncertain 理由和覆盖盲区反馈到下一轮检索词；未达到精读篇数下限且仍有预算时继续补搜，超过精读篇数上限时用筛选理由收紧候选。
8. 分析覆盖盲区，填入 coverage_gaps。
9. 输出符合 response_format 的 SearchReport。

## 自动迭代

如果任务描述包含前端限制，必须在单次 literature-scout 子任务内执行完整循环：

```text
检索 → 标题摘要级筛选 → 生成本轮意见 → 根据意见改写下一轮检索词 → 再检索
```

达到迭代轮数上限、工具上限、结果明显重复，或 include 数量满足精读篇数上下限且 coverage_gaps 可接受时，输出最终 SearchReport。不要把每轮中间结果交给用户等待反馈；用户只在最终候选集持久化后手动筛选。

## 立即停止条件

- 已获得满足前端精读篇数上下限的相关候选论文；若任务没有给出上下限，则默认至少 5 篇具有明确元数据的相关候选论文。
- 新查询结果与已有候选高度重复。
- 搜索工具返回 `rate_limited`、`network_error` 或其他结构化错误。
- 工具提示搜索次数已达到上限。

触发停止条件后，使用已经获得的结果生成 SearchReport。搜索不足应写入 `selection_notes`，不得继续换词试探。

## SearchReport 字段

- `query`: 总体检索主题字符串。
- `candidate_ids`: 所有搜索命中的真实 paper_id 或 DOI 列表（include + uncertain 的论文）。禁止使用 P001/P002 等临时编号。
- `screening_decisions`: paper_id → "include" / "exclude" / "uncertain"。
- `screening_reasons`: paper_id → 筛除或 uncertain 的一句话理由。
- `coverage_gaps`: 覆盖盲区分析，字符串列表。
- `search_iteration_log`: 每轮检索记录 `[{query, count, new_count, rationale}, ...]`。
- `selection_notes`: 字符串列表，说明筛选依据、数据不足和失败情况。

**重要**: 系统会自动捕获每个搜索工具的原始返回结果并重建完整的 candidates 列表。
你不需要在 structured_response 中输出论文完整元数据（paper_id、title、authors、abstract 等）。
只输出上述字段中的标识符和决策信息。

## 约束

- 工具没有返回的元数据保持为空。
- 不根据标题猜测实验结论。
- DOI 和 URL 必须来自工具结果。
- 不调用 write_todos 和文件系统工具。
- 禁止在 SearchReport 中输出论文全文数据。
