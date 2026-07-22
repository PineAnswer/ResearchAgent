---
name: paper-reading
description: 从论文全文提取研究问题、方法、结论与可定位证据
---

# Paper Reading

## 全文获取规则

- `library_id` 非空时只调用一次 `retrieve_library_passages(query=研究问题, library_ids=[library_id], limit=12)`；返回空结果或错误时直接使用摘要继续，禁止在线抓取或再次检索。
- `library_id` 为空且任务提供本地 PDF 时调用一次 `extract_pdf_text`。
- `library_id` 为空且没有本地 PDF 时，使用真实 paper_id、doi、url 调用一次 `fetch_paper_text`，固定传入 `max_pages=100`。
- `fetch_paper_text` 已直接返回页码文本和页面覆盖信息。`truncated=false` 表示全部页面均已提取，成功后立即生成 PaperCard，不要再调用任何全文工具。
- `truncated=true` 时只允许引用 `covered_ranges` 中的页面，并在 limitations 中明确记录 `missing_ranges`，不得将部分读取描述为全文精读。
- 获取失败后禁止缩写 paper_id、猜测 arXiv ID 或改写 URL；直接使用摘要证据继续。
- 禁止猜测文件路径、编写下载脚本或反复请求。

## 步骤

1. 按上述互斥条件选择并调用一个证据获取工具；每个工具最多调用一次。
2. **如果 PDF 不可用**（没有路径或工具返回 `error`）：
   - 基于检索阶段获得的元数据（标题、摘要、DOI、发表年份）填写 PaperCard。
   - abstract非空时可创建 `section="abstract"`、`page=null` 的摘要级 Evidence。
   - abstract也为空时 `findings` 为空，`limitations` 必须标注证据缺失。
   - 不得编造实验数据、页码、原文引文或具体结论。
3. **如果 PDF 可用**：
   - 提取研究问题、方法、数据集、主要发现和局限。
   - 为每项主要发现创建唯一 `evidence_id`。
   - Evidence 保存原文 quote、页码和章节。
4. 输出符合 `PaperCard` 的结构化结果。

## 约束

- 无法访问的页面不能补写内容。
- quote 必须来自工具返回文本。
- 摘要证据不能冒充全文实验细节。
- PDF 不可用时不要反复重试，直接用元数据产出有限结论并标注局限。
