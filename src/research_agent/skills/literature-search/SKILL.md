---
name: literature-search
description: 学术检索、去重与候选论文筛选方法
---

# Literature Search

搜索次数由运行环境配置并由中间件强制执行。默认预算是 OpenAlex 3次、Crossref 1次，修改环境变量后会自动变化。

## 步骤

1. 将研究问题拆成主题词、方法词、对象词和限制条件。
2. 设计最多三组互补查询：直接主题、英文扩展、局限或挑战。
3. 每次只调用一个搜索工具，观察结果后再决定下一次查询。
4. 优先使用 OpenAlex；只有确实需要 DOI/标题交叉核对时使用一次 Crossref。
5. 按 DOI、标准化标题和年份去重。
6. 输出符合 response_format 的 SearchReport。

## 立即停止条件

- 已获得至少5篇具有明确元数据的相关候选论文。
- 新查询结果与已有候选高度重复。
- 搜索工具返回 `rate_limited`、`network_error` 或其他结构化错误。
- 工具提示搜索次数已达到上限。

触发停止条件后，使用已经获得的结果生成 SearchReport。搜索不足应写入 `selection_notes`，不得继续换词试探。

## SearchReport字段

- `query`: 总体检索主题字符串。
- `search_terms`: 实际执行过的查询词列表。
- `candidates`: PaperCandidate列表；字段仅限 paper_id、title、authors、year、abstract、doi、url、source。
- `selection_notes`: 字符串列表，说明筛选依据、数据不足和失败情况。

不得增加 relevance、reason 等额外字段。authors 必须是字符串列表，selection_notes 必须是字符串列表。

## 约束

- 工具没有返回的元数据保持为空。
- 不根据标题猜测实验结论。
- DOI 和 URL 必须来自工具结果。
- 不调用 write_todos 和文件系统工具。
