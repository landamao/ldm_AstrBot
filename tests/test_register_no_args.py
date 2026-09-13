"""验证：注册子指令 / 普通指令 / 指令组不填参数 → 全部报错（插件加载失败）。

四种空参场景都应 raise ValueError：
1. @test.command()            子指令不填指令名
2. @filter.command()          普通（裸）指令不填指令名
3. @filter.command_group()    指令组不填指令名
4. @test.group()              子指令组不填指令名

同时验证正常注册（组/子指令/裸指令/* 兜底）不受影响。
"""
import sys

sys.path.insert(0, "/home/ldm/ldmbot_code")  # 源码树优先

from astrbot.api.event import filter  # noqa: E402
from astrbot.core.star.register.star_handler import RegisteringCommandable  # noqa: E402


def 测试空参():
    results = {}

    # 场景 1：子指令不填指令名
    try:
        @filter.command_group("test")
        def test(self, event):
            yield event.plain_result("111")

        @test.command()
        async def meme_on(self, event):
            yield event.plain_result("666")

        results["子指令空参"] = "未报错（异常！）"
    except ValueError as e:
        results["子指令空参"] = f"raise: {e}"

    # 场景 2：裸指令不填指令名
    try:
        @filter.command()
        async def bare_no_name(self, event):
            yield event.plain_result("x")

        results["裸指令空参"] = "未报错（异常！）"
    except ValueError as e:
        results["裸指令空参"] = f"raise: {e}"

    # 场景 3：指令组不填指令名
    try:
        @filter.command_group()
        def group_no_name(self, event):
            yield event.plain_result("x")

        results["指令组空参"] = "未报错（异常！）"
    except ValueError as e:
        results["指令组空参"] = f"raise: {e}"

    # 场景 4：子指令组不填指令名（需要一个正常组）
    try:
        @filter.command_group("subtest")
        def subtest(self, event):
            yield event.plain_result("x")

        @subtest.group()
        async def sub_group_no_name(self, event):
            yield event.plain_result("x")

        results["子指令组空参"] = "未报错（异常！）"
    except ValueError as e:
        results["子指令组空参"] = f"raise: {e}"

    return results


def 测试正常注册():
    results = {}

    # 正常：指令组 + 子指令 + * 兜底 + 裸指令 + 子指令组
    try:
        @filter.command_group("ok")
        def ok(self, event):
            yield event.plain_result("ok组")

        @ok.command("on")
        async def ok_on(self, event):
            yield event.plain_result("ok on")

        @ok.command("*")
        async def ok_fallback(self, event, cmd: str = None):
            yield event.plain_result("ok 兜底")

        @ok.group("sub")
        async def ok_sub(self, event):
            yield event.plain_result("ok 子组")

        @ok_sub.command("x")
        async def ok_sub_x(self, event):
            yield event.plain_result("ok 子组 x")

        @filter.command("okbare")
        async def ok_bare(self, event):
            yield event.plain_result("ok 裸指令")

        results["正常注册"] = "成功（组 ok + 子指令 on + 兜底 * + 子组 sub + 裸指令 okbare）"
    except Exception as e:
        results["正常注册"] = f"失败: {type(e).__name__}: {e}"

    return results


def main():
    print("=== 空参场景（应全部 raise）===")
    for k, v in 测试空参().items():
        print(f"  {k}: {v}")

    print("\n=== 正常注册（应全部成功）===")
    for k, v in 测试正常注册().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
