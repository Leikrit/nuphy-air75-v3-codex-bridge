# Remote Codex to NuPhy Air75 V3 on Windows

This guide connects Codex running on a remote Linux host to the status lights
of a NuPhy Air75 V3 connected to a Windows PC by USB-C.

```text
remote Codex notify -> anonymous JSONL spool -> outbound SSH -> Air75 V3 USB HID
```

The Windows bridge is an experimental Air75 V3 path. NuNuBar's Windows daemon
currently targets V2 Raw HID firmware and does not identify Air75 V3. This
bridge reuses the lifecycle-event approach and implements the verified Air75 V3
S4 lighting commands directly. It only accepts the wired Air75 V3 configuration
interface (`VID 0x19F5`, `PID 0x1028`, HID usage `01:00`).

The bridge never sends firmware, keymap, factory-reset, or other destructive
commands. It reads the current lighting state, displays green side lights when
a turn completes, then restores the saved state.

## Requirements

- A remote Linux host running Codex and reachable by SSH.
- Windows with Python 3.11 and the built-in OpenSSH client.
- NuPhy Air75 V3 ANSI connected by a USB-C data cable.
- Official Air75 V3 firmware `1.0.14.6` or newer.
- NuPhyIO and other keyboard configurators closed while the bridge runs.

This is not a hardware-verified NuNuBar Windows release. The `describe` and
`test` commands below are mandatory physical checks on each target PC.

## Files

Copy these files to the Windows PC:

- `codex_notify_spool.py` — remote Codex `notify` helper;
- `air75_v3_windows_bridge.py` — local HID and SSH bridge;
- `air75_v3_bridge.example.json` — anonymous configuration template.

Do not commit a personal `air75_v3_bridge.json`, SSH private key, server address
that identifies a private system, or any event spool containing sensitive data.

## 1. Configure the remote Codex event spool

On the remote Linux account that runs Codex, add this to
`${CODEX_HOME:-$HOME/.codex}/env`:

```bash
env_file="${CODEX_HOME:-$HOME/.codex}/env"
spool_file="${CODEX_HOME:-$HOME/.codex}/codex-events.jsonl"
printf 'export CODEX_EVENT_SPOOL=%q\n' "$spool_file" >> "$env_file"
unset env_file spool_file
chmod 600 "${CODEX_HOME:-$HOME/.codex}/env"
```

Configure the top-level `notify` setting in `config.toml` to invoke the helper.
Use a path valid on the remote host; do not copy a path from another machine:

```toml
notify = ["python3", "/path/to/codex-air75-v3-windows/codex_notify_spool.py"]
```

Keep `notify` at the top level, not inside a project or model-provider table.
Fully restart Codex or its app-server after changing the environment file and
configuration. Existing notification integrations are outside this guide and
can remain configured independently.

The helper writes only records such as:

```json
{"type":"agent-turn-complete","timestamp":1700000000}
```

It omits prompts, responses, commands, tool output, paths, hostnames, session
IDs, and credentials. The spool file is created with mode `0600`.

Check the remote path with a harmless manual event:

```bash
source "${CODEX_HOME:-$HOME/.codex}/env"
python3 /path/to/codex-air75-v3-windows/codex_notify_spool.py \
  '{"type":"agent-turn-complete"}'
tail -n 1 "${CODEX_EVENT_SPOOL}"
```

The helper exits successfully even when the spool is unavailable, so a failed
indicator cannot turn a successful Codex turn into a failed turn.

## 2. Prepare Windows

Open PowerShell and create a private working directory:

```powershell
$WorkDir = Join-Path $HOME "CodexAir75"
New-Item -ItemType Directory -Force $WorkDir | Out-Null
Set-Location $WorkDir
```

Copy the three files into this directory using `scp`, a private artifact
channel, or the repository file browser. Then install the only Python
dependency:

```powershell
py -3.11 -m pip install --upgrade hidapi
Copy-Item .\air75_v3_bridge.example.json .\air75_v3_bridge.json
```

Edit the private `air75_v3_bridge.json` using the public template shape:

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

Use `-p` for the SSH port. The default is `22`; change it if your SSH service
uses another port. `ssh_target` must be `user@host`, not `user@host:port`.

For key-based login, add a private key path to `ssh_options`. Keep the key
outside the repository and use doubled backslashes in JSON:

```json
"ssh_options": [
  "-p", "22",
  "-i", "C:\\Users\\WINDOWS_USER\\.ssh\\codex-air75",
  "-o", "BatchMode=yes"
]
```

If the SSH client already selects the right key, omit the `-i` pair.

## 3. Verify SSH access

The listener uses `BatchMode=yes`, so it must be able to log in without a
password prompt:

```powershell
ssh -p 22 user@example.com
```

If needed, create a separate key without overwriting an existing one and add
its public half to the remote account:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\codex-air75"
Get-Content "$HOME\.ssh\codex-air75.pub" |
  ssh -p 22 user@example.com 'umask 077; mkdir -p "$HOME/.ssh"; cat >> "$HOME/.ssh/authorized_keys"'
```

Test the event file read. `tail -n 0 -F` starts at the current end, so old
events are not replayed:

```powershell
ssh -p 22 user@example.com "tail -n 0 -F -- ~/.codex/codex-events.jsonl"
```

Press `Ctrl+C` after the connection is confirmed.

## 4. Test the keyboard locally

Close NuPhyIO and connect the keyboard by USB-C. Run:

```powershell
py -3.11 .\air75_v3_windows_bridge.py describe
py -3.11 .\air75_v3_windows_bridge.py test
```

`describe` must find exactly one Air75 V3 configuration interface with usage
`01:00` and `VID:PID 19F5:1028`. `test` reads the official firmware version,
rejects versions older than `1.0.14.6`, changes the side lights to green for
about four seconds, verifies the write, and restores the previous state.

If `describe` finds zero or multiple interfaces, stop and fix the USB cable,
keyboard mode, or competing configurator before retrying. If state readback or
restoration fails, stop the bridge, reconnect the keyboard, and restore the
profile with the official configurator. Do not enter DFU or use an Air75 V2
firmware image.

## 5. Run the bridge

Start the listener:

```powershell
py -3.11 .\air75_v3_windows_bridge.py listen --config .\air75_v3_bridge.json
```

When a new `agent-turn-complete` line arrives, the bridge displays green side
lights for the configured duration and restores the saved state. SSH disconnects
are retried automatically.

Run a new Codex turn after restarting the remote Codex process. Confirm that:

1. the remote spool gains one anonymous JSON line;
2. the Windows listener prints a completion message;
3. the physical Air75 V3 side lights change to green and then recover.

The direct `test` command is only a hardware smoke test. A real Codex turn is
required for final acceptance.

## 6. Optional Windows startup shortcut

After the manual listener works reliably, stop it and create a shortcut in the
current user's Startup folder. This derives local paths at runtime:

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

Inspect the Startup folder and double-click the shortcut once before relying on
it at login:

```powershell
explorer.exe $Startup
```

Do not run the manual listener and the Startup shortcut at the same time. To
remove the shortcut:

```powershell
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Codex Air75 Bridge.lnk")
```

## Troubleshooting

- **No spool line:** restart Codex/app-server and verify the top-level `notify`
  command and `CODEX_EVENT_SPOOL` on the remote host.
- **Spool line exists but no Windows output:** test SSH key login, port, the
  `remote_event_file` value, and remote file permissions.
- **No Air75 interface:** use USB-C data mode, close NuPhyIO, and check the ANSI
  Air75 V3 identity (`19F5:1028`).
- **Old firmware:** export the profile and update only through the official
  configurator; the bridge will not bypass the minimum version.
- **Readback or restore failure:** stop the listener, reconnect the keyboard,
  and retry the local `test` command. Do not retry unknown writes indefinitely.

## Privacy and release checklist

Before publishing, verify that the repository contains none of the following:

- Webhook URLs, signing secrets, passwords, SSH private keys, or access tokens;
- personal usernames, hostnames, IP addresses, or absolute home-directory paths;
- private event spool files or logs;
- a personal `air75_v3_bridge.json` copied from a real machine.

Publish only the example JSON, generic scripts, and documentation using
placeholders such as `user@example.com`, `WINDOWS_USER`, and
`/path/to/this-repository`.
