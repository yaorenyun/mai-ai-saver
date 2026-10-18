# MCP 接入说明（MCP_INTEGRATION）

> 2026 麦当劳程序员创意开发大赛 · 官方要求提交文件
> 本文件说明：实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

## 一、使用的麦当劳 MCP Server

**官方仓库**：[`M-China/mcd-mcp-server`](https://github.com/M-China/mcd-mcp-server)（McDonald's China MCP Server）

**配置方式**：仓库内提供脱敏配置示例 [`mcp-config.example.json`](mcp-config.example.json)，仅含环境变量占位符：

```json
{
  "mcpServers": {
    "mcdonalds-china": {
      "command": "npx",
      "args": ["-y", "@mcdonalds-china/mcp-server"],
      "env": {
        "MCD_API_KEY": "${MCD_API_KEY}",
        "MCD_API_SECRET": "${MCD_API_SECRET}",
        "MCD_STORE_ID": "${MCD_STORE_ID}"
      }
    }
  }
}
```

本 Skill 的客户端（`scripts/mcp_client.py`）同时支持通过环境变量
`MAI_MCP_COMMAND` 覆盖启动命令，便于适配不同部署环境。

## 二、使用的 MCP Tool

| Tool | 用途 | 在本作品中的业务价值 |
|------|------|----------------------|
| `get_menu` | 查询门店菜单与实时价格 | 组合优化引擎的候选池数据源——没有实时价格就没有"省钱计算" |
| `get_coupons` | 查询账号可用优惠券 | 券后验算：满减/折扣/单品券按实省金额排序 |
| `get_nearby_stores` | 查询附近门店 | 输出中的取餐推荐（距离/营业时间/得来速） |
| `get_activities` | 查询进行中活动 | 场景化提醒（如 1024 程序员节活动） |

> 注：具体工具名与入参以官方 MCP Server 文档为准；本作品客户端的工具名
> 集中在 `scripts/mcp_client.py` 的 `MCP_TOOLS` 字典中，可一处调整全局生效。

## 三、调用流程

```
用户提出点餐需求
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 1. initialize    声明协议版本 2025-06-18 + 客户端信息   │
│ 2. tools/list    （可选）发现服务端工具清单              │
│ 3. tools/call    调用 get_menu / get_coupons 等        │
└─────────────────────────────────────────────────────┘
    │  全部成功
    ▼
实时菜单/优惠/门店数据 → 组合优化引擎 → 券后验算 → 输出建议
    │  任一步失败（连接失败 / 未配置 / 超时）
    ▼
自动降级 references/menu-data.json 演示数据（并在输出中标注"演示数据"）
```

协议实现要点（`scripts/mcp_client.py`）：
- 严格遵循 MCP 标准握手：`initialize` → `notifications/initialized` → `tools/call`
- JSON-RPC 2.0 over stdio，仅依赖 Python 标准库，无 SDK 绑定
- 传输层封装为独立类（`McpStdioClient`），可平滑替换为 SSE / HTTP

## 四、业务价值

麦当劳 MCP 提供的实时菜单与优惠能力，是本作品"省钱计算"的**唯一权威数据源**：

1. **实时价格驱动优化**：候选池（单品 + 套餐）全部来自 `get_menu` 返回，
   价格变动即时反映到最优搭配结果
2. **真实优惠驱动决策**：`get_coupons` 返回的券池决定券后价，
   避免用过期规则误导用户
3. **门店场景闭环**：`get_nearby_stores` + `get_activities` 让建议不止于
   "怎么点"，还包括"去哪取、赶什么活动"

## 五、与官方 MCP 的对接状态

- [x] 客户端按 MCP 标准协议实现（stdio + JSON-RPC 2.0）
- [x] 降级链路完成（MCP 不可用时用演示数据兜底，保证任何环境可跑）
- [ ] 接入官方 `M-China/mcd-mcp-server` 实测（以官方发布的服务配置为准，
      接入后更新本文件第一、二节）
