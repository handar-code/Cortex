name: Pull Request
description: 提交一个改动
body:
  - type: textarea
    id: what
    attributes:
      label: 改了什么
      placeholder: |
        - 修复：切换向量端点时归因轨与服务配置不一致（attribution.py）
        - 新增：支持把 index.json 用环境变量传入（rebuild_hnsw.py）
    validations:
      required: true
  - type: textarea
    id: why
    attributes:
      label: 为什么
      description: 关联的 issue 或要解决的场景
    validations:
      required: true
  - type: checkboxes
    id: checklist
    attributes:
      label: 自检
      options:
        - label: pytest -q 通过（无 key，43 passed）
        - label: cortex --help 可用
        - label: 未提交 .env / 索引产物 / 本机绝对路径
        - label: 未引入对特定项目的硬编码
        - label: 若涉及核心机制，已更新 docs/design.md
