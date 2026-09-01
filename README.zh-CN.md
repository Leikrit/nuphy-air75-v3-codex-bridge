# 将远程 Codex 通知映射到 Windows 上的 NuPhy Air75 V3

[English](README.md) | 简体中文

本指南用于将远程 Linux 主机上运行的 Codex，与通过 USB-C 连接到 Windows
电脑的 NuPhy Air75 V3 状态灯连接起来。

```text
远程 Codex notify -> 匿名 JSONL 事件队列 -> 主动发起的 SSH 连接 -> Air75 V3 USB HID
```

Windows 桥接程序是一个实验性的 Air75 V3 适配方案。NuNuBar 的 Windows
守护程序目前面向 V2 Raw HID 固件，且无法识别 Air75 V3。本桥接程序沿用其生命周期
事件思路，并直接实现经过验证的 Air75 V3 S4 灯光命令。程序只接受有线连接的 Air75 V3
配置接口（`VID 0x19F5`、`PID 0x1028`、HID usage `01:00`）。

桥接程序绝不会发送固件、键位映射、恢复出厂设置或其他破坏性命令。它会读取当前灯光
状态，在一次 Codex turn 完成时将侧灯显示为绿色，然后恢复之前保存的状态。

## 环境要求

- 一台运行 Codex 且可通过 SSH 访问的远程 Linux 主机；
- 安装了 Python 3.11 和系统自带 OpenSSH 客户端的 Windows 电脑；
- 通过 USB-C 数据线连接的 NuPhy Air75 V3 ANSI 键盘；
- `1.0.14.6` 或更高版本的 Air75 V3 官方固件；
- 桥接程序运行期间，关闭 NuPhyIO 和其他键盘配置工具。

这不是经过 NuNuBar 官方 Windows 实机验证的版本。下文中的 `describe` 和 `test`
命令是每台目标电脑都必须执行的硬件检查。

## 文件说明

将以下文件复制到 Windows 电脑：

- `codex_notify_spool.py`：远程 Codex 的 `notify` 辅助程序；
- `air75_v3_windows_bridge.py`：本地 HID 和 SSH 桥接程序；
- `air75_v3_bridge.example.json`：匿名配置模板。

不要提交个人使用的 `air75_v3_bridge.json`、SSH 私钥、能够识别私人系统的服务器地址，
也不要提交任何可能包含敏感数据的事件队列文件。

## 1. 配置远程 Codex 事件队列

在运行 Codex 的远程 Linux 账户中，将以下内容添加到
`${CODEX_HOME:-$HOME/.codex}/env`：

```bash
env_file="${CODEX_HOME:-$HOME/.codex}/env"
spool_file="${CODEX_HOME:-$HOME/.codex}/codex-events.jsonl"
printf 'export CODEX_EVENT_SPOOL=%q\n' "$spool_file" >> "$env_file"
unset env_file spool_file
chmod 600 "${CODEX_HOME:-$HOME/.codex}/env"
```

在 `config.toml` 中配置顶层 `notify`，让它调用辅助程序。请填写远程主机上的有效
路径，不要照搬另一台机器的路径：

```toml
notify = ["python3", "/path/to/codex-air75-v3-windows/codex_notify_spool.py"]
```

`notify` 必须位于配置文件顶层，不能放在项目或模型提供商的配置表中。修改环境文件和
配置后，需要完全重启 Codex 或其 app-server。已有的其他通知集成不属于本指南范围，
可以继续独立配置。

辅助程序只会写入如下记录：

```json
{"type":"agent-turn-complete","timestamp":1700000000}
```

记录中不会包含提示词、回复、命令、工具输出、路径、主机名、会话 ID 或凭据。事件队列
文件会以 `0600` 权限创建。

使用一条无害的手动事件检查远程路径：

```bash
source "${CODEX_HOME:-$HOME/.codex}/env"
python3 /path/to/codex-air75-v3-windows/codex_notify_spool.py \
  '{"type":"agent-turn-complete"}'
tail -n 1 "${CODEX_EVENT_SPOOL}"
```

即使事件队列不可用，辅助程序也会以成功状态退出，因此通知灯故障不会把一次成功的
Codex turn 变成失败状态。

## 2. 准备 Windows 环境

打开 PowerShell，创建一个仅供个人使用的工作目录：

```powershell
$WorkDir = Join-Path $HOME "CodexAir75"
New-Item -ItemType Directory -Force $WorkDir | Out-Null
Set-Location $WorkDir
```

通过 `scp`、私有制品传输渠道或仓库文件浏览器，将上述三个文件复制到该目录。然后安装
唯一的 Python 依赖：

```powershell
py -3.11 -m pip install --upgrade hidapi
Copy-Item .\air75_v3_bridge.example.json .\air75_v3_bridge.json
```

参照公开模板的结构，编辑仅供个人使用的 `air75_v3_bridge.json`：

```json
{
  "ssh_target": "user@example.com",
  "remote_event_file": "~/.codex/codex-events.jsonl",
  "ssh_options": [
    "-p", "22",
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3"
  ],
  "complete_hold_seconds": 4,
  "reconnect_seconds": 3
}
```

SSH 端口使用 `-p` 指定。默认端口为 `22`；如果 SSH 服务使用其他端口，请修改这里。
`ssh_target` 必须采用 `user@host` 格式，不能写成 `user@host:port`。

如果使用密钥登录，请在 `ssh_options` 中加入私钥路径。私钥必须保存在仓库之外，JSON
中的反斜杠需要写两次：

```json
"ssh_options": [
  "-p", "22",
  "-i", "C:\\Users\\WINDOWS_USER\\.ssh\\codex-air75",
  "-o", "BatchMode=yes"
]
```

如果 SSH 客户端已经能够自动选择正确的密钥，可以省略 `-i` 及其后的路径。

## 3. 验证 SSH 访问

监听程序使用 `BatchMode=yes`，因此必须能够在不弹出密码提示的情况下登录：

```powershell
ssh -p 22 user@example.com
```

如有需要，可以在不覆盖现有密钥的前提下创建一把独立密钥，并将公钥添加到远程账户：

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\codex-air75"
Get-Content "$HOME\.ssh\codex-air75.pub" |
  ssh -p 22 user@example.com 'umask 077; mkdir -p "$HOME/.ssh"; cat >> "$HOME/.ssh/authorized_keys"'
```

测试是否能够读取事件文件。`tail -n 0 -F` 会从文件当前末尾开始监听，因此不会重放
旧事件：

```powershell
ssh -p 22 user@example.com "tail -n 0 -F -- ~/.codex/codex-events.jsonl"
```

确认连接成功后，按 `Ctrl+C` 退出。

## 4. 在本地测试键盘

关闭 NuPhyIO，并通过 USB-C 连接键盘。运行：

```powershell
py -3.11 .\air75_v3_windows_bridge.py describe
py -3.11 .\air75_v3_windows_bridge.py test
```

`describe` 必须只找到一个 usage 为 `01:00`、`VID:PID` 为 `19F5:1028` 的
Air75 V3 配置接口。`test` 会读取官方固件版本，拒绝低于 `1.0.14.6` 的版本，将侧灯
变为绿色约四秒，验证写入结果，然后恢复之前的状态。

如果 `describe` 找不到接口或找到多个接口，请停止操作，并检查 USB 数据线、键盘模式或
是否有其他配置工具占用设备，然后再重试。如果状态回读或恢复失败，请停止桥接程序，
重新连接键盘，并用官方配置工具恢复配置。不要进入 DFU 模式，也不要使用 Air75 V2
固件镜像。

## 5. 运行桥接程序

启动监听程序：

```powershell
py -3.11 .\air75_v3_windows_bridge.py listen --config .\air75_v3_bridge.json
```

收到新的 `agent-turn-complete` 记录后，桥接程序会让侧灯保持绿色一段配置的时间，随后
恢复保存的状态。SSH 断开后，程序会自动重试连接。

重启远程 Codex 进程后，发起一次新的 Codex turn，并确认：

1. 远程事件队列新增了一行匿名 JSON 记录；
2. Windows 监听程序打印了完成消息；
3. Air75 V3 实体键盘的侧灯变为绿色，随后恢复原状。

直接执行 `test` 只能完成硬件冒烟测试。最终验收必须使用一次真实的 Codex turn。

## 6. 可选：创建 Windows 开机启动快捷方式

手动运行监听程序确认其工作稳定后，先将其停止，再在当前用户的“启动”文件夹中创建
快捷方式。以下脚本会在运行时自动生成本机路径：

```powershell
$WorkDir = Join-Path $HOME "CodexAir75"
$Startup = [Environment]::GetFolderPath("Startup")
$PythonLauncher = (Get-Command py.exe).Source
$Script = Join-Path $WorkDir "air75_v3_windows_bridge.py"
$Config = Join-Path $WorkDir "air75_v3_bridge.json"

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut((Join-Path $Startup "Codex Air75 Bridge.lnk"))
$Shortcut.TargetPath = $PythonLauncher
$Shortcut.Arguments = "-3.11 `"$Script`" listen --config `"$Config`""
$Shortcut.WorkingDirectory = $WorkDir
$Shortcut.WindowStyle = 7
$Shortcut.Description = "Listen for remote Codex completion events"
$Shortcut.Save()
```

检查“启动”文件夹，并手动双击一次快捷方式，确认可用后再依赖它在登录时自动运行：

```powershell
explorer.exe $Startup
```

不要同时运行手动启动的监听程序和“启动”文件夹中的快捷方式。如需删除快捷方式：

```powershell
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Codex Air75 Bridge.lnk")
```

## 故障排查

- **事件队列没有新增记录：** 重启 Codex/app-server，并检查远程主机上的顶层
  `notify` 命令和 `CODEX_EVENT_SPOOL`；
- **事件队列已有记录，但 Windows 没有输出：** 检查 SSH 密钥登录、端口、
  `remote_event_file` 配置值和远程文件权限；
- **找不到 Air75 接口：** 确认 USB-C 处于数据连接模式、关闭 NuPhyIO，并检查
  Air75 V3 ANSI 的设备标识（`19F5:1028`）；
- **固件版本过旧：** 导出当前配置，并且只通过官方配置工具更新；桥接程序不会绕过
  最低版本限制；
- **回读或恢复失败：** 停止监听程序，重新连接键盘，然后再次执行本地 `test` 命令。
  不要无限重试未知写入。
