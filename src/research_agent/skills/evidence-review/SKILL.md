---
name: evidence-review
description: 对科研综合结论进行只读证据审查
---

# Evidence Review

## 步骤

1. 读取 SynthesisReport、PaperCard 和 Evidence。
2. 检查每个关键结论引用的 evidence_id 是否存在。
3. 核对 quote 与 claim 的语义关系。
4. DOI只作为论文记录中的标识和去重字段，不进行联网核验。
5. 检查因果夸大、选择性引用和证据不足。
6. 输出符合 `ReviewResult` 的 PASS 或 REVISE。
7. `verified_evidence_ids` 只填写 PaperCard findings 中真实存在的 evidence_id；禁止填写 artifact_id。

## 一票 REVISE

- 引用不存在或无法定位。
- 原文不能支持关键结论。
- Evidence所属PaperCard与其paper_id不一致。
- 研究空白只来自模型推测。
