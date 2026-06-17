# Wio Terminal AI 用量仪表盘

在 Wio Terminal(320×240 横屏)上实时显示 **Claude / Codex 的 AI 用量额度**:5 小时窗口、7 天窗口的已用百分比与重置时间,以及今日 token 消耗和折算成本。

数据由电脑本地的 Python 服务端获取并通过**蓝牙(BLE)**推送到设备。额度来自本机登录账号的真实数据,无需手动填写。

---

## 功能特性

- 📊 显示 Claude / Codex 各自的 **5h / 7d** 额度进度条 + 重置时间
- 🔄 从本机登录账号**自动拉取真实额度**(Codex 走官方 `wham/usage` 接口)
- 🧮 按本地会话日志统计**今日 / 近7天 token 用量**,并按模型 API 报价折算 **Today 成本**
- 📡 通过 BLE 自动推送到 Wio Terminal,屏幕局部刷新、低闪烁
- 🌐 附带网页预览页(`preview.html`)
- ⏱️ 定时自动循环:拉取 → 写数据 → 推送,一条龙

---

## 项目结构

```text
wio-terminal-ai-quota-dashboard/
├─ arduino/ai_quota_dashboard/ai_quota_dashboard.ino   Arduino IDE 固件
├─ src/main.cpp                                        PlatformIO 固件
├─ platformio.ini                                      PlatformIO 配置
├─ preview.html                                        网页预览
├─ server.py                                           Python 服务端(HTTP API + BLE 自动推送)
├─ start_auto_ble.py                                   服务端启动器(带 bleak 检查)
├─ requirements.txt                                    Python 依赖(bleak)
├─ data/quota.json                                     仪表盘数据文件
├─ tools/
│  ├─ codex_usage.py            拉取 Codex 真实额度(5h/7d % + 重置时间)
│  ├─ claude_usage.py           拉取 Claude 真实额度(5h/7d % + 重置时间)
│  ├─ token_usage.py            从本地日志统计今日/近7天 token
│  ├─ auto_sync.py              编排:拉取→折算→写 quota.json→推送
│  ├─ run-server-auto-ble.ps1   一键启动 server.py --ble
│  └─ send-test-ble.ps1         不依赖 Python 的 BLE 直发测试
├─ README.md                    本文档(总览)
├─ USAGE.md                     固件烧录 / BLE / 网页详细教程
└─ READ.md                      真实额度自动同步专题文档
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
                  ├ 折算 Today 成本(按模型报价)
                  └ 写 data/quota.json + POST /api/quota
                               ▼
                     server.py --ble  (常驻)
                  └ 收到 POST → 立即 BLE 推送;另每 15s 兜底推
                               ▼  蓝牙
                         Wio Terminal 屏幕
```

数据源分工:

| 数据 | 来源 | 成本 |
|---|---|---|
| Codex 5h / 7d 百分比、重置 | 官方 `GET /backend-api/wham/usage` | 零 token |
| Claude 5h / 7d 百分比、重置 | 官方 `GET /api/oauth/usage` | 零 token(限流) |
| 今日 / 近7天 token 数 | 本地会话日志 JSONL 累加 | 纯本地 |
| Today 成本(美金) | token 数 × 模型 API 报价 | 等值折算 |

---

## 一、硬件与固件

### 设备
- Seeed **Wio Terminal**(320×240 LCD)

### 依赖库(必须用 Seeed 维护版)
```text
Seeed_Arduino_LCD / Seeed_GFX     屏幕(提供 TFT_eSPI.h,已含 Wio 引脚配置)
Seeed_Arduino_FS
Seeed_Arduino_rpcUnified
Seeed_Arduino_rpcBLE
ArduinoJson
```
> ⚠️ 不要用 Bodmer 原版 `TFT_eSPI` 替代 `Seeed_Arduino_LCD`,否则会白屏/尺寸错乱。

### 烧录方式

**Arduino IDE**:打开 `arduino/ai_quota_dashboard/ai_quota_dashboard.ino`,开发板选 `Seeed Wio Terminal`,装齐上面的库,编译上传。

**PlatformIO**(依赖已固定在 `platformio.ini`):
```powershell
pio run -t upload
```

---

## 二、服务端

### 安装依赖
```powershell
pip install -r requirements.txt
```

### 启动(带 BLE 自动推送)
```powershell
py -u server.py --ble --ble-interval 15
```
- `--ble`:启用蓝牙自动推送
- `--ble-interval`:推送间隔(秒)
- `--ble-name`:目标设备名(默认 `Wio AI Quota`)
- 端口:`py server.py 8876 --ble`(默认 8765)

也可用启动器或脚本:
```powershell
py start_auto_ble.py
powershell -ExecutionPolicy Bypass -File .\tools\run-server-auto-ble.ps1
```

网页预览:`http://127.0.0.1:8765/preview.html`

---

## 三、真实额度自动同步

### 前置:Codex 必须官方直连

`~/.codex/config.toml` 不能指向第三方中转(中转会吃掉额度响应头)。应使用默认 provider:
```toml
model = "gpt-5.5"
model_reasoning_effort = "high"
disable_response_storage = true
```
> 若有 `model_provider = "custom"` + `[model_providers.custom]`(指向如 `agentic.seeed.cc`),删除即可。

### 四个工具

```powershell
py tools\codex_usage.py            # Codex 5h/7d 额度(GET wham/usage,零 token)
py tools\codex_usage.py --post     # 顺便写入仪表盘

py tools\claude_usage.py           # Claude 5h/7d 额度(GET oauth/usage)
py tools\claude_usage.py --post    # 顺便写入仪表盘

py tools\token_usage.py            # 今日/近7天 token(默认北京时区)
py tools\token_usage.py --json     # 机器可读

py tools\auto_sync.py --once       # 拉取(Codex+Claude)+折算+推送 一次
py tools\auto_sync.py --interval 60  # 每 60s 循环
py tools\auto_sync.py --no-claude    # 只拉 Codex,跳过 Claude
```

### Claude 真实额度说明

`claude_usage.py` 复刻了 Claude Code 自身使用的接口
`GET https://api.anthropic.com/api/oauth/usage`,返回 `five_hour` / `seven_day`
两个窗口的 `utilization`(0-100)和 `resets_at`。请求头需带:

```text
Authorization: Bearer <accessToken>
User-Agent:    claude-code/<version>
anthropic-beta: oauth-2025-04-20
```

OAuth token 读取位置:

| 平台 | 来源 |
|---|---|
| Windows / Linux | `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`(可用 `CLAUDE_CONFIG_DIR` 覆盖目录) |
| macOS | 钥匙串项 `Claude Code-credentials`(文件副本常已过期),失败回退到上面的文件 |

注意:

- 该接口**未公开且限流严重**,轮询请保持 ≥ 60s(`auto_sync` 默认 60s 正合适)。
- 未登录 Claude / token 过期 / 被限流时,`auto_sync` 会**跳过 Claude**并保留上次值,不影响 Codex 推送(终端会打印 `[claude] usage fetch skipped: ...`)。
- token 过期时:打开一次 Claude Code(或运行 `claude`)即可刷新。
- 方法参考开源项目 [Rida2000/wio-claude-buddy](https://github.com/Rida2000/wio-claude-buddy) 的 `host/buddy_ble_bridge.py`。

### 标准启动流程(各只起一个)

```powershell
# 终端 1:服务器
py -u server.py --ble --ble-interval 15
# 终端 2:自动循环(同时拉 Codex + Claude)
py -u tools\auto_sync.py --interval 60
```

---

## 四、成本折算

`tools/auto_sync.py` 顶部 `PRICING` 定义每 token 单价。当前按 `gpt-5.5` 标准短上下文档:

| 项 | 单价 / 1M token |
|---|---|
| 输入 | $5.00 |
| 缓存输入 | $0.50 |
| 输出 | $30.00 |

```text
Today = (input - cached) × $5/1M + cached × $0.5/1M + output × $30/1M   (仅今日窗口)
```
> 这是按 API 标准报价的**等值成本**,订阅制账号并非按 token 真实扣费;暂未区分 >272K 长上下文翻倍档位。`footer.tokens` 与 `Today` 均为 Codex + Claude 之和。

---

## 五、数据格式 `data/quota.json`

```json
{
  "updatedAt": "11:36",
  "footer": { "cost": "$0.66", "tokens": "153.35KToken", "time": "11:36" },
  "platforms": {
    "claude": { "remaining": 0, "short": {"label":"5h","pct":0,"reset":"--"},
                "week": {"label":"7d --","pct":0,"reset":"--"} },
    "codex":  { "remaining": 2, "short": {"label":"5h","pct":2,"reset":"15:29"},
                "week": {"label":"7d 1%","pct":1,"reset":"06/13 11:13"} }
  }
}
```

| 字段 | 含义 |
|---|---|
| `updatedAt` | 数据更新时间 |
| `footer.cost` | 底部左:今日成本(Today) |
| `footer.tokens` | 底部中:今日 token 总量 |
| `footer.time` | 底部右:时间 |
| `platforms.{claude,codex}` | 各平台数据 |
| `remaining` | 主百分比 |
| `short` / `week` | 5h 窗口 / 7d 窗口 |
| `label` / `pct` / `reset` | 文案 / 百分比(0-100,已用) / 重置时间 |

---

## 六、HTTP 接口(server.py)

```text
GET  /preview.html       网页预览
GET  /api/quota          读取 data/quota.json
POST /api/quota          写入 data/quota.json 并触发 BLE 推送
GET  /api/ble/status     查看 BLE 同步状态(status/lastSentAt/lastError/lastDevices)
POST /api/ble/sync       立即触发一次 BLE 发送
```

---

## 七、BLE 参数

```text
Device name:  Wio AI Quota
Service UUID: 6e400001-b5a3-f393-e0a9-e50e24dcca9e
RX UUID:      6e400002-b5a3-f393-e0a9-e50e24dcca9e
TX UUID:      6e400003-b5a3-f393-e0a9-e50e24dcca9e
```
> 不要在 Windows“添加蓝牙设备”里配对它,这是自定义 GATT 设备。通过服务端、网页 `Connect Wio` 或 `send-test-ble.ps1` 连接。

不依赖 Python 的蓝牙直发测试:
```powershell
.\tools\send-test-ble.ps1            # 发一帧默认测试数据
.\tools\send-test-ble.ps1 -ListOnly  # 只列出广播该服务的设备
.\tools\send-test-ble.ps1 -ListAllBle # 列出所有可见 BLE 设备
```

---

## 八、进程管理(重要)

`Stop-Process -Id <PID>` 可能只杀 `py` 启动器,真正的 `python.exe` 子进程会残留 → 多个循环同时推送、设备数值跳变。请按命令行匹配清理:

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
运行时应确认 **只有 1 个 server + 1 个 auto_sync**。

---

## 九、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `token_invalidated` / `app_session_terminated` | Codex 本地令牌被别处登录顶掉,需 `codex login` 重登 |
| Codex 额度全 0 或拿不到 | config 仍指向中转,改回官方默认 provider |
| Claude 额度不更新 / `usage endpoint error (401)` | token 过期,打开一次 Claude Code 刷新;或被限流(降低轮询频率) |
| Claude 始终为占位 0 | 本机未登录 Claude Code(无 `~/.claude/.credentials.json`),属正常 |
| 设备数值在两个数之间跳变 | 有残留多进程,按第八节清理 |
| 服务器有 `[BLE] synced` 但设备不更新 | 看固件串口(115200)是否有 `JSON_OK dashboard updated` |
| 找不到 bleak | `pip install -r requirements.txt` |
| 找不到设备 | 确认设备开机、广播 `Wio AI Quota`、电脑蓝牙已开、网页与服务端不要同时抢连接 |
| Arduino 编译缺库 | `Seeed_Arduino_FS.h`→装 Seeed_Arduino_FS;`seeed_rpcUnified.h`→rpcUnified;`rpcBLEDevice.h`→rpcBLE |

---

## 十、快速开始

```powershell
# 1. 装依赖
pip install -r requirements.txt

# 2. 烧录固件到 Wio Terminal(Arduino IDE 或 pio run -t upload)

# 3. 确认 Codex 官方直连(~/.codex/config.toml 无 custom provider)

# 4. 启动服务器 + 自动循环(两个终端)
py -u server.py --ble --ble-interval 15
py -u tools\auto_sync.py --interval 60
```

设备屏幕即显示真实额度,每 60 秒自动刷新。

> 更细的固件/BLE 教程见 [`USAGE.md`](./USAGE.md);自动同步专题见 [`READ.md`](./READ.md)。
#   W i o - T e r m i n a l - A I - T o k e n - D a s h b o a r d  
 