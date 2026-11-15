"""通用工具模块，无明确领域归属。"""


def slugify(text: str) -> str:
    """将文本转为 slug。"""
    return text.lower().replace(" ", "-")
