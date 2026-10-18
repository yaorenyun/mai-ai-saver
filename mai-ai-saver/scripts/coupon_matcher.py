#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · 优惠券匹配器
================================
对一笔订单，从可用券池中找出"实省最多"的券，并给出备选。

支持的券型
----------
- threshold  满减券：满 X 减 Y（如 满50减8）
- percentage 折扣券：全场 N 折，可设 max_discount 封顶（如 85折、最高减10）
- item       单品券：指定商品立减（如 巨无霸减6）

抵扣顺序约定（与麦当劳 App 常见规则一致，可在 SKILL.md 中说明）
--------------------------------------------------------------
单品券优先于满减/折扣券；同一订单默认取"实省最多"的一张，
多张是否叠加以 MCP 返回的订单试算结果为准。

用法
----
    python3 coupon_matcher.py --items big_mac fries_l coke_l
    python3 coupon_matcher.py --items big_mac fries_l coke_l --json

参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "references" / "menu-data.json"


def load_coupons(path: Path = DATA_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("coupons", [])


def match(order_items: list[str], order_total: float, coupons: list[dict]) -> list[dict]:
    """
    返回按实省金额降序排列的可用券列表，每项附带实省金额与原因。
    单品券在订单包含对应商品时才可用。
    """
    results = []
    for cp in coupons:
        ctype = cp.get("type")
        if ctype == "threshold":
            if order_total >= cp["threshold"]:
                results.append({**cp, "actual_saving": cp["discount"],
                                "reason": f"订单 ¥{order_total:.2f} 满 ¥{cp['threshold']:.0f}"})
        elif ctype == "percentage":
            raw = order_total * (1 - cp["rate"])
            actual = min(raw, cp.get("max_discount", raw))
            if actual > 0:
                results.append({**cp, "actual_saving": round(actual, 2),
                                "reason": f"订单 ¥{order_total:.2f} × {cp['rate']}"
                                          + (f"（封顶 ¥{cp['max_discount']:.0f}）" if "max_discount" in cp else "")})
        elif ctype == "item":
            if cp["item_id"] in order_items:
                results.append({**cp, "actual_saving": cp["discount"],
                                "reason": f"订单包含「{cp['item_id']}」"})
    results.sort(key=lambda x: -x["actual_saving"])
    return results


def render(order_items: list[str], order_total: float, matched: list[dict]) -> str:
    lines = [f"\n🎟️ 优惠券匹配｜订单 {' + '.join(order_items)}｜小计 ¥{order_total:.2f}"]
    if not matched:
        lines.append("   当前没有可用券，试试凑单到满减门槛。")
        return "\n".join(lines)
    for i, cp in enumerate(matched):
        mark = "👑 最优" if i == 0 else "  备选"
        lines.append(
            f"   {mark} {cp['name']}｜实省 ¥{cp['actual_saving']:.2f}"
            f"｜{cp['reason']}｜券后 ¥{order_total - cp['actual_saving']:.2f}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="麦麦省钱助手 · 优惠券匹配器")
    ap.add_argument("--items", nargs="+", required=True, help="订单商品 id 列表")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    coupons = load_coupons()
    # 简化：订单小计由菜单数据求和（演示环境）
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    price_map = {it["id"]: it["price"] for it in data.get("items", [])}
    price_map.update({cb["id"]: cb["price"] for cb in data.get("combos", [])})
    total = round(sum(price_map.get(i, 0) for i in args.items), 2)

    matched = match(args.items, total, coupons)
    if args.json:
        print(json.dumps({"order_total": total, "matched": matched}, ensure_ascii=False, indent=2))
    else:
        print(render(args.items, total, matched))


if __name__ == "__main__":
    main()
