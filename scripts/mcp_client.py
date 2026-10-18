#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · 麦当劳 MCP 客户端（含离线降级）
================================================
封装麦当劳 MCP server 的 JSON-RPC 2.0 调用，提供统一的数据入口：

    get_menu()         查询菜单与价格
    get_coupons()      查询当前可用优惠券
    get_nearby_stores() 查询附近门店
    get_activities()   查询进行中的活动

设计原则
--------
1. 协议优先：严格按 MCP 标准（initialize / tools/list / tools/call）实现，
   不依赖任何特定 SDK，便于适配不同传输层（stdio / SSE）。
2. 优雅降级：MCP 连接失败或未配置时，自动回落到 references/menu-data.json
   演示数据，保证 Skill 在任何环境都能跑通 Demo。
3. 配置外置：MCP server 启动命令通过环境变量 MAI_MCP_COMMAND 注入，
   默认 ["npx", "-y", "@mcdonalds-china/mcp-server"]（以官方仓库为准）。

用法
----
    python3 mcp_client.py --demo          # 演示：降级链路 + 各查询
    python3 mcp_client.py --tool get_menu # 调用指定工具
    MAI_MCP_COMMAND="node server.js" python3 mcp_client.py --demo

参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "references" / "menu-data.json"

# 麦当劳 MCP 预期工具名（以官方 McDonald's China (M-China) GitHub 仓库文档为准）
MCP_TOOLS = {
    "get_menu": "查询门店菜单与实时价格",
    "get_coupons": "查询当前账号可用优惠券",
    "get_nearby_stores": "查询附近门店（距离/营业时间/得来速）",
    "get_activities": "查询进行中的营销活动",
}


# --------------------------------------------------------------------------- #
# MCP 传输层（stdio + JSON-RPC 2.0）
# --------------------------------------------------------------------------- #
class McpStdioClient:
    """最小可用的 MCP stdio 客户端实现。"""

    def __init__(self, command: list[str]):
        self.command = command
        self._proc: subprocess.Popen | None = None
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        if self._proc is None:
            raise RuntimeError("MCP server 未启动")
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server 无响应")
        return json.loads(line)

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8",
        )
        # MCP 握手
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mai-ai-saver", "version": "1.0.0"},
        })
        self._notify("notifications/initialized")

    def _notify(self, method: str) -> None:
        if self._proc is None:
            return
        self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self._proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        resp = self._rpc("tools/list")
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc = None


# --------------------------------------------------------------------------- #
# 降级数据源（离线演示）
# --------------------------------------------------------------------------- #
def offline_query(tool: str) -> dict:
    """MCP 不可用时，用本地演示数据模拟返回。"""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    mapping = {
        "get_menu": {"items": data["items"], "combos": data["combos"]},
        "get_coupons": {"coupons": data["coupons"]},
        "get_nearby_stores": {"stores": data["stores"]},
        "get_activities": {"activities": data["activities"]},
    }
    return {"source": "offline-mock", "tool": tool, "result": mapping.get(tool, {})}


# --------------------------------------------------------------------------- #
# 统一入口：优先 MCP，失败自动降级
# --------------------------------------------------------------------------- #
def query(tool: str, arguments: dict | None = None) -> dict:
    cmd_env = os.environ.get("MAI_MCP_COMMAND")
    if cmd_env:
        client = McpStdioClient(cmd_env.split())
        try:
            client.start()
            resp = client.call_tool(tool, arguments)
            return {"source": "mcp", "tool": tool, "result": resp}
        except Exception as exc:  # noqa: BLE001 - 降级需要兜住一切
            print(f"[降级] MCP 调用失败（{exc}），改用离线演示数据", file=sys.stderr)
        finally:
            client.close()
    return offline_query(tool)


def main() -> None:
    ap = argparse.ArgumentParser(description="麦麦省钱助手 · MCP 客户端")
    ap.add_argument("--tool", choices=list(MCP_TOOLS), help="调用的工具名")
    ap.add_argument("--demo", action="store_true", help="演示全部查询与降级链路")
    args = ap.parse_args()

    if args.demo:
        print("🔌 麦当劳 MCP 客户端演示\n" + "-" * 40)
        for tool, desc in MCP_TOOLS.items():
            print(f"\n▶ {tool} — {desc}")
            resp = query(tool)
            print(f"  数据源: {resp['source']}")
            preview = json.dumps(resp["result"], ensure_ascii=False)
            print(f"  返回预览: {preview[:120]}{'...' if len(preview) > 120 else ''}")
        print("\n" + "-" * 40)
        print("✅ Demo 完成。配置 MAI_MCP_COMMAND 后即可切换到麦当劳真实 MCP 数据。")
        return

    if args.tool:
        print(json.dumps(query(args.tool), ensure_ascii=False, indent=2))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
