---
name: paper-reading
description: 从论文全文提取研究问题、方法、结论与可定位证据
---

# Paper Reading

## 全文获取规则

- 任务提供本地 PDF 时调用 `extract_pdf_text`。
- 其余情况使用真实 paper_id、doi、url 调用一次 `fetch_paper_text`。
- `fetch_paper_text` 已直接返回页码文本，成功后不要再调用 `extract_pdf_text`。
- 获取失败后禁止缩写 paper_id、猜测 arXiv ID 或改写 URL；直接使用摘要证据继续。
- 禁止猜测文件路径、编写下载脚本或反复请求。

## 步骤

1. 调用相应全文工具获取带页码文本。
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
