"""用户领域模块。

@domain user
包含用户实体与基础校验逻辑。
"""


class User:
    """用户实体。

    @domain user
    表示一个注册用户，含 uid 与 name。
    """

    def __init__(self, uid: int, name: str):
        """构造用户。

        @domain user
        uid: 用户唯一标识；name: 展示名。
        """
        self.uid = uid
        self.name = name

    def is_active(self) -> bool:
        """判断用户是否激活。

        @domain user
        仅当 uid 大于 0 视为激活。
        """
        return self.uid > 0
