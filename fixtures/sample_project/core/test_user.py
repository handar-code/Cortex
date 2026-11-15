"""用户模块测试（被 triage 判为 test，不计入有效文档）。"""


def test_is_active():
    """测试激活判断。"""
    from core.user import User

    assert User(1, "alice").is_active()
    assert not User(0, "ghost").is_active()
