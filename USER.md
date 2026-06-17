# 使用教程

把 Wio Terminal 变成桌面上的 AI 用量小屏:实时显示 Claude / Codex 的 **5 小时**和 **7 天**额度,以及**今日 token 消耗和折算成本**。数据由电脑自动获取并通过蓝牙推送到设备。

> 只想快速跑起来,看「[三分钟上手](#三分钟上手)」即可。完整原理见 [`README.md`](./README.md)。

---

## 准备清单

**硬件**
- Seeed Wio Terminal 一台
- USB-C 数据线(能传数据,不是只能充电的那种)

**电脑(运行 Codex / Claude Code 的这台)**
- 已安装 Python 3.9+
- 电脑蓝牙已打开
- 已登录 Codex(`codex login`),如需 Claude 额度则也登录过 Claude Code

---

## 三分钟上手

### 第 1 步:装依赖(只需一次)

在项目根目录打开 PowerShell:

```powershell
pip install -r requirements.txt
```

### 第 2 步:给设备烧录固件(只需一次)

用 Arduino IDE 打开 `arduino\ai_quota_dashboard\ai_quota_dashboard.ino`,开发板选 **Seeed Wio Terminal**,装齐依赖库后上传。
(或用 PlatformIO:`pio run -t upload`)

烧录细节见 [`USAGE.md`](./USAGE.md)。

### 第 3 步:确认账号直连(只需一次)

- **Codex**:`~/.codex/config.toml` 里**不能**有指向中转的 `model_provider = "custom"`,用默认官方直连。
- **Claude**:登录过 Claude Code 即可,无需额外配置。

### 第 4 步:每次使用 —— 开两个终端

```powershell
# 终端 1:启动服务器(带蓝牙自动推送)
py -u server.py --ble --ble-interval 15
```

```powershell
# 终端 2:启动自动循环(每 60 秒拉取 Codex + Claude 并推送)
py -u tools\auto_sync.py --interval 60
```

把 Wio Terminal 开机。屏幕显示仪表盘、右上角连接点变绿,就表示连上了,数值会每 60 秒自动刷新。

---

## 屏幕上显示什么

- **Claude / Codex 两块区域**:各自的 5h、7d 已用百分比 + 重置时间
- **底部 Today**:今日花费(美元),= Codex + Claude 今日 token 按 API 报价折算
- **底部 Token**:今日 token 总量(两个账号相加)
- **底部时间**:最近更新时间

---

## 网页预览(可选)

不想看设备也可以用浏览器看同一份数据:

```text
http://127.0.0.1:8765/preview.html
```

---

## 重新启动前,先清理旧进程

如果之前已经跑过,直接再开会出现**多个程序同时推送、数值来回跳变**。重启前先清干净:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'server\.py|auto_sync\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

> 正常运行时应该**只有 1 个 server + 1 个 auto_sync**。

---

## 怎么确认正常工作

- **终端 2** 每分钟打印一行,例如:

```text
[13:57:01] codex 5h=2% (reset 15:29) 7d=1% (reset 06/13 11:13) claude 5h=30% 7d=43% today=$0.66 tokens=153.35KToken -> posted+BLE
```

- **终端 1** 出现 `[BLE] synced`
- 设备屏幕数值随之更新

---

## 常用命令速查

```powershell
# 只看一次数据(不推送循环)
py tools\codex_usage.py            # Codex 5h/7d 额度
py tools\claude_usage.py           # Claude 5h/7d 额度
py tools\token_usage.py            # 今日 / 近 7 天 token

# 自动循环
py tools\auto_sync.py --once          # 拉取 + 推送一次
py tools\auto_sync.py --interval 60   # 每 60 秒循环
py tools\auto_sync.py --no-claude     # 只拉 Codex,跳过 Claude

# 蓝牙直发测试(不依赖循环)
.\tools\send-test-ble.ps1
```

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 设备数值在两个数之间反复跳 | 有残留多进程,按上面「清理旧进程」执行后再启动 |
| Codex 额度全是 0 | config 还指向中转,改回官方默认 provider |
| Claude 额度不更新 / 报 401 | token 过期:打开一次 Claude Code 刷新;或被限流,降低频率 |
| Claude 一直是 0 | 本机没登录过 Claude Code,属正常 |
| 提示找不到 bleak | 重新执行 `pip install -r requirements.txt` |
| 找不到设备 | 确认设备开机、电脑蓝牙已开;不要在系统蓝牙里手动配对它;网页和服务端不要同时抢连接 |
| 服务器显示已同步但设备不变 | 看设备串口(115200)是否出现 `JSON_OK dashboard updated` |

---

更深入的硬件 / 蓝牙细节见 [`USAGE.md`](./USAGE.md);完整架构与开发说明见 [`README.md`](./README.md)。
