"""订单领域模块。

@domain order
包含订单创建与金额计算。
"""


def create_order(uid: int, amount: float):
    """创建订单。

    @domain order
    uid: 用户ID；amount: 订单金额。
    """
    return {"uid": uid, "amount": amount, "paid": False}


class Order:
    """订单实体。

    @domain order
    聚合订单项并计算总额。
    """

    def total(self, items):
        """计算订单总额。"""
        return sum(items)
