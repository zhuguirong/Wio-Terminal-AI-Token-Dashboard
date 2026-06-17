# Wio Terminal AI Token Dashboard

在 **Wio Terminal**（320×240 横屏）上实时显示 **Claude / Codex** 的 AI 用量额度：5 小时窗口、7 天窗口的已用百分比与重置时间，以及今日 token 消耗和折算成本。

数据由电脑本地的 Python 服务端获取，并通过 **蓝牙（BLE）** 推送到设备。额度来自本机登录账号的真实数据，无需手动填写。

---

## 目录

- [功能特性](#功能特性)
- [项目结构](#项目结构)
- [数据链路](#数据链路)
- [快速开始](#快速开始)
- [硬件与固件](#硬件与固件)
- [服务端](#服务端)
- [真实额度自动同步](#真实额度自动同步)
- [成本折算](#成本折算)
- [数据格式](#数据格式)
- [HTTP 接口](#http-接口)
- [BLE 参数](#ble-参数)
- [进程管理](#进程管理)
- [常见问题](#常见问题)
- [更多文档](#更多文档)

---

## 功能特性

| 功能 | 说明 |
| --- | --- |
| 额度进度条 | 显示 Claude / Codex 各自的 **5h / 7d** 额度进度与重置时间 |
| 自动拉取 | 从本机登录账号拉取真实额度（Codex 走官方 `wham/usage` 接口） |
| Token 统计 | 按本地会话日志统计 **今日 / 近 7 天** token 用量 |
| 成本折算 | 按模型 API 报价折算 **Today** 成本 |
| BLE 推送 | 通过蓝牙自动推送到 Wio Terminal，屏幕局部刷新、低闪烁 |
| 网页预览 | 附带 `preview.html` 网页预览页 |
| 定时循环 | 拉取 → 写数据 → 推送，全自动 |

---

## 项目结构

```text
wio-terminal-ai-quota-dashboard/
├── arduino/ai_quota_dashboard/ai_quota_dashboard.ino   # Arduino IDE 固件
├── src/main.cpp                                        # PlatformIO 固件
├── platformio.ini                                      # PlatformIO 配置
├── preview.html                                        # 网页预览
├── server.py                                           # Python 服务端（HTTP API + BLE 自动推送）
├── start_auto_ble.py                                   # 服务端启动器（带 bleak 检查）
├── requirements.txt                                    # Python 依赖
├── data/quota.json                                     # 仪表盘数据文件
├── tools/
│   ├── codex_usage.py                                  # 拉取 Codex 真实额度
│   ├── claude_usage.py                                 # 拉取 Claude 真实额度
│   ├── token_usage.py                                  # 统计今日/近 7 天 token
│   ├── auto_sync.py                                    # 编排：拉取 → 折算 → 写 JSON → 推送
│   ├── run-server-auto-ble.ps1                         # 一键启动 server.py --ble
│   └── send-test-ble.ps1                               # BLE 直发测试
├── README.md                                           # 项目总览（本文档）
└── USER.md                                             # 使用教程
```

---

## 数据链路

```text
Codex 账号        Claude 账号           本地会话日志 (~/.codex, ~/.claude)
   │ wham/usage      │ oauth/usage          │ token_count / message.usage
   ▼                 ▼                      ▼
codex_usage.py   claude_usage.py     token_usage.py (今日/7天 token)
 (5h/7d %,重置)   (5h/7d %,重置)
   └────────────────┴──────────┬───────────┘
                               ▼
                  tools/auto_sync.py  (每 60s)
                  ├ 折算 Today 成本（按模型报价）
                  └ 写 data/quota.json + POST /api/quota
                               ▼
                     server.py --ble  (常驻)
                  └ 收到 POST → 立即 BLE 推送；另每 15s 兜底推
                               ▼  蓝牙
                         Wio Terminal 屏幕
```

| 数据 | 来源 | 成本 |
| --- | --- | --- |
| Codex 5h / 7d 百分比、重置 | 官方 `GET /backend-api/wham/usage` | 零 token |
| Claude 5h / 7d 百分比、重置 | 官方 `GET /api/oauth/usage` | 零 token（限流） |
| 今日 / 近 7 天 token 数 | 本地会话日志 JSONL 累加 | 纯本地 |
| Today 成本（美金） | token 数 × 模型 API 报价 | 等值折算 |

---

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 烧录固件

用 **Arduino IDE** 打开 `arduino/ai_quota_dashboard/ai_quota_dashboard.ino`，开发板选 **Seeed Wio Terminal**，装齐依赖库后上传。

或使用 **PlatformIO**：

```powershell
pio run -t upload
```

### 3. 确认 Codex 官方直连

`~/.codex/config.toml` 不能指向第三方中转。应使用默认 provider：

```toml
model = "gpt-5.5"
model_reasoning_effort = "high"
disable_response_storage = true
```

> 若有 `model_provider = "custom"` 指向中转地址，请删除。

### 4. 启动服务（两个终端）

```powershell
# 终端 1：服务器
py -u server.py --ble --ble-interval 15

# 终端 2：自动循环
py -u tools\auto_sync.py --interval 60
```

设备屏幕即显示真实额度，每 60 秒自动刷新。

网页预览：http://127.0.0.1:8765/preview.html

---

## 硬件与固件

### 设备

- Seeed **Wio Terminal**（320×240 LCD）

### 依赖库（必须使用 Seeed 维护版）

| 库 | 用途 |
| --- | --- |
| `Seeed_Arduino_LCD` / `Seeed_GFX` | 屏幕（提供 `TFT_eSPI.h`，已含 Wio 引脚配置） |
| `Seeed_Arduino_FS` | 文件系统 |
| `Seeed_Arduino_rpcUnified` | RPC 统一层 |
| `Seeed_Arduino_rpcBLE` | BLE 通信 |
| `ArduinoJson` | JSON 解析 |

> **注意：** 不要用 Bodmer 原版 `TFT_eSPI` 替代 `Seeed_Arduino_LCD`，否则会白屏或尺寸错乱。

---

## 服务端

### 启动（带 BLE 自动推送）

```powershell
py -u server.py --ble --ble-interval 15
```

| 参数 | 说明 |
| --- | --- |
| `--ble` | 启用蓝牙自动推送 |
| `--ble-interval` | 推送间隔（秒，默认 15） |
| `--ble-name` | 目标设备名（默认 `Wio AI Quota`） |

指定端口：

```powershell
py server.py 8876 --ble
```

其他启动方式：

```powershell
py start_auto_ble.py
powershell -ExecutionPolicy Bypass -File .\tools\run-server-auto-ble.ps1
```

---

## 真实额度自动同步

### 工具命令

```powershell
# Codex 额度
py tools\codex_usage.py
py tools\codex_usage.py --post

# Claude 额度
py tools\claude_usage.py
py tools\claude_usage.py --post

# Token 统计
py tools\token_usage.py
py tools\token_usage.py --json

# 自动同步
py tools\auto_sync.py --once
py tools\auto_sync.py --interval 60
py tools\auto_sync.py --no-claude
```

### Claude 额度说明

`claude_usage.py` 调用 `GET https://api.anthropic.com/api/oauth/usage`，返回 `five_hour` / `seven_day` 窗口的 `utilization`（0–100）和 `resets_at`。

OAuth token 读取位置：

| 平台 | 来源 |
| --- | --- |
| Windows / Linux | `~/.claude/.credentials.json` → `claudeAiOauth.accessToken` |
| macOS | 钥匙串项 `Claude Code-credentials`，失败时回退到上述文件 |

注意：

- 该接口**未公开且限流严重**，轮询请保持 ≥ 60s
- 未登录 / token 过期 / 被限流时，`auto_sync` 会跳过 Claude 并保留上次值
- token 过期时：打开一次 Claude Code 即可刷新

---

## 成本折算

`tools/auto_sync.py` 顶部 `PRICING` 定义每 token 单价（当前按 `gpt-5.5` 标准短上下文）：

| 项 | 单价 / 1M token |
| --- | --- |
| 输入 | $5.00 |
| 缓存输入 | $0.50 |
| 输出 | $30.00 |

```text
Today = (input - cached) × $5/1M + cached × $0.5/1M + output × $30/1M
```

> 这是按 API 标准报价的**等值成本**，订阅制账号并非按 token 真实扣费。

---

## 数据格式

文件路径：`data/quota.json`

```json
{
  "updatedAt": "11:36",
  "footer": {
    "cost": "$0.66",
    "tokens": "153.35KToken",
    "time": "11:36"
  },
  "platforms": {
    "claude": {
      "remaining": 0,
      "short": { "label": "5h", "pct": 0, "reset": "--" },
      "week": { "label": "7d --", "pct": 0, "reset": "--" }
    },
    "codex": {
      "remaining": 2,
      "short": { "label": "5h", "pct": 2, "reset": "15:29" },
      "week": { "label": "7d 1%", "pct": 1, "reset": "06/13 11:13" }
    }
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `updatedAt` | 数据更新时间 |
| `footer.cost` | 底部左：今日成本（Today） |
| `footer.tokens` | 底部中：今日 token 总量 |
| `footer.time` | 底部右：时间 |
| `platforms.{claude,codex}` | 各平台数据 |
| `short` / `week` | 5h 窗口 / 7d 窗口 |
| `label` / `pct` / `reset` | 文案 / 百分比（0–100，已用） / 重置时间 |

---

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/preview.html` | 网页预览 |
| `GET` | `/api/quota` | 读取 `data/quota.json` |
| `POST` | `/api/quota` | 写入 `data/quota.json` 并触发 BLE 推送 |
| `GET` | `/api/ble/status` | 查看 BLE 同步状态 |
| `POST` | `/api/ble/sync` | 立即触发一次 BLE 发送 |

---

## BLE 参数

| 项 | 值 |
| --- | --- |
| Device name | `Wio AI Quota` |
| Service UUID | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| RX UUID | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
| TX UUID | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |

> 不要在 Windows「添加蓝牙设备」里配对它。通过服务端、网页 `Connect Wio` 或 `send-test-ble.ps1` 连接。

蓝牙直发测试：

```powershell
.\tools\send-test-ble.ps1
.\tools\send-test-ble.ps1 -ListOnly
.\tools\send-test-ble.ps1 -ListAllBle
```

---

## 进程管理

`Stop-Process -Id <PID>` 可能只杀 `py` 启动器，真正的 `python.exe` 子进程会残留，导致多个循环同时推送、设备数值跳变。

```powershell
# 查看
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'server\.py|auto_sync\.py' } |
  Select-Object ProcessId, CommandLine

# 全部停止
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'server\.py|auto_sync\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

正常运行时应确认 **只有 1 个 server + 1 个 auto_sync**。

---

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `token_invalidated` / `app_session_terminated` | Codex 本地令牌被顶掉，需 `codex login` 重登 |
| Codex 额度全 0 或拿不到 | config 仍指向中转，改回官方默认 provider |
| Claude 额度不更新 / `401` | token 过期，打开 Claude Code 刷新；或被限流 |
| Claude 始终为占位 0 | 本机未登录 Claude Code，属正常 |
| 设备数值在两个数之间跳变 | 有残留多进程，按上文清理 |
| 服务器有 `[BLE] synced` 但设备不更新 | 看固件串口（115200）是否有 `JSON_OK dashboard updated` |
| 找不到 bleak | `pip install -r requirements.txt` |
| 找不到设备 | 确认设备开机、广播 `Wio AI Quota`、电脑蓝牙已开 |
| Arduino 编译缺库 | 安装对应的 Seeed 库（见「硬件与固件」） |

---

## 更多文档

- [USER.md](./USER.md) — 三分钟上手与日常使用教程
