#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · 组合优化引擎
================================
给定预算与饮食偏好，在候选池（单品 + 套餐）中搜索"满足度最高"的搭配方案。

设计要点
--------
1. 候选池 = 单品 + 套餐。套餐自带 contains（组成明细），
   "单点 vs 套餐"的对比由 combo_original_price 天然支持。
2. 搜索算法：小规模候选池（约 20 项）下采用"约束过滤 + 有界枚举"，
   可解释、可复现，避免过设计；规模增大可替换为背包 DP（见 README）。
3. 满足度 satisfaction 为启发式评分：主食 6-10 / 小食 3-5 / 饮料 2-5 / 甜品 4，
   套餐分值为各组成之和的略打折（留一点单点自由度溢价空间）。

用法
----
    python3 price_optimizer.py --budget 40 --must big_mac
    python3 price_optimizer.py --budget 30 --exclude-tags 辣
    python3 price_optimizer.py --budget 50 --json

参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "references" / "menu-data.json"


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    """可选单元：单品或套餐。"""
    cid: str
    name: str
    category: str          # 主食/小食/饮料/甜品/套餐
    price: float
    satisfaction: int
    tags: list[str] = field(default_factory=list)
    original_price: float | None = None   # 套餐原单点价（用于展示"省了多少"）
    contains: list[str] = field(default_factory=list)  # 套餐组成（单品 id 列表）
    note: str = ""

    @property
    def saving_vs_single(self) -> float:
        """相比单点的节省金额（套餐才有意义）。"""
        if self.original_price and self.original_price > self.price:
            return round(self.original_price - self.price, 2)
        return 0.0

    @property
    def value_ratio(self) -> float:
        """性价比 = 满足度 / 元。"""
        return self.satisfaction / self.price


@dataclass
class Plan:
    """一个搭配方案。"""
    picks: list[Candidate]
    total_price: float
    total_satisfaction: int
    saving_vs_single: float
    completeness: int = 0      # 餐食结构完整度：主食/饮料/小食齐全度加分

    def score(self) -> int:
        """综合得分 = 满足度 + 结构完整度。"""
        return self.total_satisfaction + self.completeness

    def to_dict(self) -> dict:
        return {
            "items": [
                {
                    "id": p.cid,
                    "name": p.name,
                    "category": p.category,
                    "price": p.price,
                    "satisfaction": p.satisfaction,
                    "saving_vs_single": p.saving_vs_single,
                }
                for p in self.picks
            ],
            "total_price": round(self.total_price, 2),
            "total_satisfaction": self.total_satisfaction,
            "completeness": self.completeness,
            "score": self.score(),
            "saving_vs_single": round(self.saving_vs_single, 2),
            "avg_value_ratio": round(self.total_satisfaction / self.total_price, 2) if self.total_price else 0,
        }


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def load_candidates(path: Path = DATA_FILE) -> list[Candidate]:
    """从菜单数据构造候选池（单品 + 套餐）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates: list[Candidate] = []

    for it in data.get("items", []):
        candidates.append(Candidate(
            cid=it["id"], name=it["name"], category=it["category"],
            price=it["price"], satisfaction=it["satisfaction"], tags=it.get("tags", []),
        ))
    for cb in data.get("combos", []):
        candidates.append(Candidate(
            cid=cb["id"], name=cb["name"], category=cb["category"],
            price=cb["price"], satisfaction=cb["satisfaction"], tags=cb.get("tags", []),
            original_price=cb.get("original_price"), contains=cb.get("contains", []),
            note=cb.get("note", ""),
        ))
    return candidates


# --------------------------------------------------------------------------- #
# 必点意图解析：单品可自动升级为"包含它的套餐"（省钱核心逻辑）
# --------------------------------------------------------------------------- #
def resolve_must(
    candidates: list[Candidate], must: list[str], budget: float
) -> tuple[list[Candidate], list[str]]:
    """
    用户说"想吃巨无霸"时，等价满足它的候选包括：巨无霸单品 + 所有含巨无霸的套餐。
    组内优先选套餐（省得更多），只要装得下预算。

    返回 (必选项, 提示信息)。
    """
    warnings: list[str] = []
    resolved: list[Candidate] = []

    for mid in must:
        target = next((c for c in candidates if c.cid == mid), None)
        if target is None:
            warnings.append(f"菜单中未找到「{mid}」")
            continue
        if target.category == "套餐":
            resolved.append(target)
            continue

        # 单品 → 收集包含它的套餐，套餐优先（按节省降序）
        group = sorted(
            (c for c in candidates if c.category == "套餐" and mid in c.contains),
            key=lambda c: (-c.saving_vs_single, c.price),
        )
        group.append(target)  # 单品兜底

        spent = sum(c.price for c in resolved)
        chosen = next((c for c in group if spent + c.price <= budget), None)
        if chosen is None:
            chosen = target
        if chosen is not target:
            warnings.append(
                f"「{target.name}」已自动升级为「{chosen.name}」"
                f"（套餐比单点省 ¥{chosen.saving_vs_single:.2f}）"
            )
        resolved.append(chosen)

    return resolved, warnings


# --------------------------------------------------------------------------- #
# 约束过滤
# --------------------------------------------------------------------------- #
def filter_candidates(
    candidates: list[Candidate],
    budget: float,
    must: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    max_picks: int = 4,
) -> tuple[list[Candidate], list[Candidate], list[str]]:
    """
    返回 (必选项, 可选池, 过滤说明)。
    - must 中的单品会自动升级为包含它的套餐（见 resolve_must）。
    - exclude_tags：命中任一标签即剔除（如 ["辣"]）。
    """
    exclude_tags = [t for t in (exclude_tags or []) if t]
    must_candidates, warnings = resolve_must(candidates, must or [], budget)

    def excluded(c: Candidate) -> bool:
        return any(t in c.tags for t in exclude_tags)

    # 必选项命中排除标签的剔除（升级后的套餐同样检查）
    kept_must = []
    for c in must_candidates:
        if excluded(c):
            warnings.append(f"「{c.name}」命中排除标签，已跳过")
        else:
            kept_must.append(c)
    must_candidates = kept_must

    must_ids = {c.cid for c in must_candidates}
    pool = [
        c for c in candidates
        if c.cid not in must_ids and not excluded(c) and c.price <= budget
    ]

    must_total = sum(c.price for c in must_candidates)
    if must_total > budget:
        warnings.append(
            f"必选项合计 ¥{must_total:.2f} 已超预算 ¥{budget:.2f}，结果将超支，请确认"
        )

    if len(must_candidates) > max_picks:
        warnings.append(f"必选项超过 {max_picks} 件，已全部保留但不再追加搭配")
    return must_candidates, pool, warnings


# --------------------------------------------------------------------------- #
# 有界枚举搜索
# --------------------------------------------------------------------------- #
def search_best_plans(
    candidates: list[Candidate],
    budget: float,
    must: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    top_n: int = 3,
) -> list[Plan]:
    """枚举可行组合，按 满足度 desc / 价格 asc 排序取前 top_n。"""
    must_candidates, pool, warnings = filter_candidates(
        candidates, budget, must, exclude_tags
    )
    for w in warnings:
        print(f"[提示] {w}", file=sys.stderr)

    must_price = sum(c.price for c in must_candidates)
    must_sat = sum(c.satisfaction for c in must_candidates)
    must_saving = sum(c.saving_vs_single for c in must_candidates)

    # 可选池最多补 3 件（必选之外的额度）
    extra_slots = max(0, 4 - len(must_candidates))
    remaining = budget - must_price

    combos: list[Plan] = []

    # 先收入"仅必选"方案
    combos.append(Plan(
        picks=list(must_candidates), total_price=must_price,
        total_satisfaction=must_sat, saving_vs_single=must_saving,
        completeness=meal_completeness(must_candidates),
    ))

    if remaining >= 0 and extra_slots > 0:
        # 按性价比预筛，控制枚举规模
        shortlist = sorted(pool, key=lambda c: c.value_ratio, reverse=True)[:12]
        for k in range(1, min(extra_slots, 3) + 1):
            for combo in itertools.combinations(shortlist, k):
                price = sum(c.price for c in combo)
                if price > remaining + 1e-9:
                    continue
                picks = list(must_candidates) + list(combo)
                combos.append(Plan(
                    picks=picks,
                    total_price=must_price + price,
                    total_satisfaction=must_sat + sum(c.satisfaction for c in combo),
                    saving_vs_single=must_saving + sum(c.saving_vs_single for c in combo),
                    completeness=meal_completeness(picks),
                ))

    # 超预算的兜底：必选就已超预算时，保留方案但标注
    combos = [p for p in combos if p.picks]
    combos.sort(key=lambda p: (-p.score(), p.total_price))
    return combos[:top_n]


# --------------------------------------------------------------------------- #
# 餐食结构完整度（让方案像"一顿像样的饭"，而不是小食拼盘）
# --------------------------------------------------------------------------- #
def meal_completeness(picks: list[Candidate]) -> int:
    """
    结构加分：
      - 有正经主食（单品主食 或 非1+1套餐）+6；仅有 1+1 轻食档 +3
      - 有饮料 +3
      - 有小食 +2
    总分 11。防止"冰淇淋+随心配"类高性价比碎片组合霸榜。
    """
    has_staple = any(p.category == "主食" or (p.category == "套餐" and p.cid != "one_plus_one")
                     for p in picks)
    has_light = any(p.cid == "one_plus_one" for p in picks)
    has_drink = any(p.category == "饮料" or (p.category == "套餐" and p.cid != "one_plus_one")
                    for p in picks)
    has_snack = any(p.category == "小食" or (p.category == "套餐" and p.cid != "one_plus_one")
                    for p in picks)

    score = 0
    if has_staple:
        score += 6
    elif has_light:
        score += 3
    if has_drink:
        score += 3
    if has_snack:
        score += 2
    return score


# --------------------------------------------------------------------------- #
# 输出渲染
# --------------------------------------------------------------------------- #
def render(plans: list[Plan], budget: float) -> str:
    if not plans:
        return "没有找到可行方案，试试放宽预算或减少排除条件。"
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(plans):
        medal = medals[i] if i < len(medals) else "  "
        over = " ⚠️ 超预算" if p.total_price > budget + 1e-9 else ""
        badge = "🍽️ 完整正餐" if p.completeness >= 11 else ("🥤 轻食档" if p.completeness >= 6 else "🍟 小食档")
        lines.append(f"\n{medal} 方案 {i + 1}｜合计 ¥{p.total_price:.2f}"
                     f"｜综合得分 {p.score()}（满足度 {p.total_satisfaction} + 完整度 {p.completeness}）"
                     f"｜性价比 {p.total_satisfaction / p.total_price:.2f} 分/元 {badge}{over}")
        for c in p.picks:
            save = f"（比单点省 ¥{c.saving_vs_single:.2f}）" if c.saving_vs_single else ""
            lines.append(f"   · {c.name}  ¥{c.price:.2f}  [{c.category}] {save}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="麦麦省钱助手 · 组合优化引擎")
    ap.add_argument("--budget", type=float, required=True, help="预算（元）")
    ap.add_argument("--must", nargs="*", default=[], help="必选项 id，如 big_mac")
    ap.add_argument("--exclude-tags", nargs="*", default=[], help="排除标签，如 辣")
    ap.add_argument("--top", type=int, default=3, help="输出方案数")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    candidates = load_candidates()
    plans = search_best_plans(
        candidates, args.budget, must=args.must,
        exclude_tags=args.exclude_tags, top_n=args.top,
    )

    if args.json:
        print(json.dumps([p.to_dict() for p in plans], ensure_ascii=False, indent=2))
    else:
        print(f"\n🍔 麦麦省钱助手｜预算 ¥{args.budget:.2f}"
              f"｜必点 {args.must or '无'}｜排除 {args.exclude_tags or '无'}")
        print(render(plans, args.budget))


if __name__ == "__main__":
    main()
