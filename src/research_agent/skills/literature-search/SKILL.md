---
name: literature-search
description: 学术检索、去重与候选论文筛选方法
---

# Literature Search

外部搜索由 `search_multi_source` 统一执行。它会把每条短查询分别发送到
OpenAlex、Crossref、Semantic Scholar 和 arXiv，再按 DOI 或规范化标题合并。

## 步骤

1. 将研究问题拆成核心任务、方法、数据集/基准和评价方向。
2. 设计 2–5 条互补的简短英文查询。禁止把研究问题原句或所有限定词拼成唯一查询。
3. 先调用 `search_library` 复用本地论文和历史证据。
4. 把互补查询作为 `queries` 列表一次传给 `search_multi_source`；年份和场馆质量
   限制使用独立参数，不要拼入查询文本。
5. 使用工具已经合并的 `sources`、`matched_queries` 和 `relevance_score`，
   再结合标题、摘要进行 include / exclude / uncertain 初筛。
6. 若出现明确覆盖盲区，可以再设计一组真正互补的短查询；不要只换词序重复搜索。
7. 分析覆盖盲区，填入 `coverage_gaps`，并输出符合 response_format 的 SearchReport。

## 自动迭代

如果任务描述包含前端限制，必须在单次 literature-scout 子任务内执行完整循环：

```text
检索 → 标题摘要级筛选 → 生成本轮意见 → 根据意见改写下一轮检索词 → 再检索
```

每次多源调用本身已经覆盖多条查询和四个来源。不要把每轮中间结果交给用户等待反馈；
用户只在最终候选集持久化后手动筛选。

候选数量、查询轮次和单个来源失败都不是拒绝输出的门槛。无论返回多少真实结果，
都使用已获得的结果生成 SearchReport，并把覆盖不足或来源失败写入 `selection_notes`。

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
