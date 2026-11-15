"""读侧：Skill 读指南生成（设计稿 §13.7 Q-1/Q-2）。

Skill 只告诉 Agent **怎么读 KB**，自身零写能力（不暴露任何写工具）。
内容：入口 llms.txt 路由、分片用法、三件套索引用法、首次接手步骤。
生成的 SKILL.md 放在目标项目 `.doc_system/skill/SKILL.md`（仅读指南，无写权限）。
"""
from __future__ import annotations

from pathlib import Path

from ..model import Catalog


def gen_skill_md(
    catalog: Catalog,
    llms_root: str = "llms/llms.txt",
    index_path: str = "index/index.json",
) -> str:
    """生成读指南 SKILL.md 文本（零写能力）。"""
    by_domain: dict[str, int] = {}
    for s in catalog.symbols:
        by_domain[s.domain or "_uncategorized"] = by_domain.get(s.domain or "_uncategorized", 0) + 1
    domain_list = "\n".join(f"  - `@{d}` — {n} symbols" for d, n in sorted(by_domain.items()))

    return f"""---
name: project-knowledge-base-reader
description: 读取项目知识库（catalog / llms.txt 分片 / 三件套索引）。仅读，无写能力。
---

# 项目知识库 · 读指南

你是接手本项目的 Agent。本指南只教你**读取**知识库，不授予任何写权限。

## 入口
1. 先读 `{llms_root}`（llms.txt 根路由）。它列出各域分片 `llms-<domain>.txt` 与全量 `llms-full.txt`。
2. 按需拉取对应分片；不确定域时直接读 `llms-full.txt`。

## 三件套索引（向量方向 ‖ 文本注释方向 并列）
- 索引文件：`{index_path}`。每条 payload 三件套**成对并列**，系统不做服务端预融合，由你裁决权重与冲突：
  - `vector`：**向量方向**（Embedding 相似度 / 归属软分布）；
  - `annotation`：**注释**（`@domain` 显式键，人工信号；真实项目常为 `∅`，属正常）；
  - `annotation_text`：**注释文本**（docstring / 可读 note，文本方向产物）。
- 另有域裁决元数据（§12.7）：`domain`（裁决后归属）、`domain_source`（explicit / implicit / structural / both_agree / conflict / uncertain）、`domain_confidence`、`domain_distribution`（向量软分布）、`conflict_flag` + `conflict_sources`。
- `vector_neighbors`：向量最近的前 N 个符号（各带相似度）；`source_ref`：源位置。
- **`domain` 永不为空**：真实项目通常没有手写 `@domain`，系统用目录树 scope（§13.4 Q5）作稳定归属 + 向量隐式轨自动建议软归属，二者并列；缺显式键不代表知识缺失。

## 检索：三通道并列返回，互不为主
1. `annotation_hits` — 注释通道：`@domain` 显式键 / 裁决域精确命中。
2. `annotation_text_hits` — **注释文本通道**：docstring / note 的词项命中（独立并行主线，不并入向量）。
3. `vector_hits` — 向量通道：语义 top-k。
三者一起返回（`channels` 字段给出各通道命中数），**不要事前融合**；文本注释方向与向量方向地位平等（§12.1），冲突（`conflict_flag`）一律交你裁决，系统不自动偏向任一方（§12.6）。

## 关键约定
- 读侧永不阻塞：即便工作区有未提交改动（catalog 标 `behind`），仍服务**已发布** catalog，旧信息不消失。
- 不要尝试修改 catalog / index / llms 文件——写操作由 MCP 提交通道负责，本 Skill 不提供。

## 本库概况
- 项目：`{catalog.project}`
- 符号总数：{len(catalog.symbols)}
- 域分布：
{domain_list}
"""


def write_skill_md(catalog: Catalog, out_dir: str | Path, **kw) -> str:
    out = Path(out_dir) / "SKILL.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = gen_skill_md(catalog, **kw)
    out.write_text(text, encoding="utf-8")
    return str(out)
