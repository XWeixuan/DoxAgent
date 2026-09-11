# DoxAgent 云服务器 SSH 连接指南

本文记录 Windows 工作站连接 DoxAgent 云服务器的固定入口。所有连接均通过
Clash Verge 的本地 SOCKS5/mixed 端口转发；文档不保存服务器密码、私钥内容或
其他凭据。

## 前置条件

- Clash Verge/mihomo 正在运行，并监听 `127.0.0.1:7897`。
- Ncat 位于 `C:/Program Files (x86)/Nmap/ncat.exe`。
- OpenSSH 配置位于 `C:/Users/WEIXUANXIE/.ssh/config`。
- 私钥只保存在本机 `.ssh` 目录，不提交到仓库。

## 已配置的服务器

| SSH alias | 地区 | 目标 | 用户 | 本地私钥 | 已验证能力 |
| --- | --- | --- | --- | --- | --- |
| `doxagent-hk` | 香港 | `43.135.22.202:22` | `root` | `~/.ssh/id_ed25519_doxagent_hk` | 密钥 SSH；既有 DoxAgent/DoxAtlas 部署 |
| `doxagent-sg` | 新加坡 | `43.163.67.97:22` | `ubuntu` | `~/.ssh/id_ed25519_doxagent_sg` | 密钥 SSH；免密码 `sudo` |

新加坡服务器于 2026-09-10 验证时的主机名是 `VM-0-15-ubuntu`，SSH 主机密钥
指纹是：

```text
SHA256:WM49QSeLUspJjbehtg268fCs/1qeUsswljsmbJkUkSM
```

如果服务器重装或主机指纹发生变化，必须先通过腾讯云控制台核对新指纹，不能
直接删除 `known_hosts` 记录并跳过校验。

## 日常连接

```powershell
ssh doxagent-sg
```

执行单条远端命令：

```powershell
ssh doxagent-sg 'whoami && hostname && uname -srmo'
```

以管理员权限执行命令：

```powershell
ssh doxagent-sg 'sudo systemctl status ssh --no-pager'
```

复制文件：

```powershell
scp <local-file> doxagent-sg:/home/ubuntu/
scp doxagent-sg:/home/ubuntu/<remote-file> .
```

新加坡服务器目前只确认了连接、身份和 `sudo` 能力。执行部署操作前，应先检查
实际目录、磁盘、服务和工作树状态；不要套用香港服务器的 `/root/doxagent` 路径
或 Compose 服务名。

## SSH 配置结构

新加坡别名的有效配置如下：

```sshconfig
Host doxagent-sg
    HostName 43.163.67.97
    User ubuntu
    Port 22

    IdentityFile ~/.ssh/id_ed25519_doxagent_sg
    ProxyCommand "C:/Program Files (x86)/Nmap/ncat.exe" --proxy-type socks5 --proxy 127.0.0.1:7897 %h %p
    ServerAliveInterval 30
    ServerAliveCountMax 3
    TCPKeepAlive yes
    PubkeyAuthentication yes
    PasswordAuthentication yes
    StrictHostKeyChecking accept-new
```

`PasswordAuthentication yes` 只允许 SSH 客户端在密钥不可用时回退，不代表密码
被写入配置。日常自动化应使用密钥，并用下面的命令确认没有发生密码回退：

```powershell
ssh -o BatchMode=yes -o PasswordAuthentication=no doxagent-sg `
  'printf "SSH_KEY_OK\n"; whoami; hostname; sudo -n true && printf "SUDO_OK\n"'
```

预期输出包含：

```text
SSH_KEY_OK
ubuntu
VM-0-15-ubuntu
SUDO_OK
```

## 代理与连接排查

先查看别名展开后的有效配置：

```powershell
ssh -G doxagent-sg | Select-String `
  -Pattern '^(hostname|user|port|identityfile|proxycommand|stricthostkeychecking) '
```

再查看详细握手过程：

```powershell
ssh -vv -o BatchMode=yes doxagent-sg true
```

日志中应出现执行 Ncat `ProxyCommand` 和使用 `publickey` 认证。如果出现
`Connection timed out during banner exchange`，先确认 Clash 的 `7897` 监听和
当前代理节点，再判断服务器 SSH 服务；已知某些 Clash 节点可能只对特定目标路径
失败。

如果代理正常但主机指纹异常，应停止连接并在腾讯云控制台核验。不要使用
`StrictHostKeyChecking=no` 作为长期解决方案。

## 安全边界

- 不在仓库、文档、命令历史或聊天总结中保存服务器密码及私钥内容。
- 密钥登录已经可用，服务器密码应在泄露或共享后及时轮换。
- 禁用服务端密码登录、修改防火墙或调整 SSH 端口属于独立加固操作，实施前需确认
  控制台救援路径和现有密钥登录均可用。
- 任何远端写操作前先用 `whoami`、`hostname` 和工作目录检查确认目标，避免误操作
  香港与新加坡服务器。
