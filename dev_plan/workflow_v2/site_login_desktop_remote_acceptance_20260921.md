# 消息源登录维护桌面工具：远端验收记录

- 验收日期：2026-09-21（Asia/Shanghai）
- 主机：Singapore `doxagent-sg`
- 仓库：`/home/ubuntu/doxagent`
- 部署提交：`ee087d9c`
- xRDP 用户：`doxagent-desktop`

## 部署结果

生产 Compose 全量重建并重建 15 个容器；14 个常驻服务运行，具备健康检查的
Codex Worker、API、CDECR Executor、Site Access、Web 均为 healthy，Migration
以 exit 0 完成。容器 `RestartCount=0`、`OOMKilled=false`。Site Access 在部署中暴露的
旧基镜像继承问题已由服务器 Compose 的 `BASE_IMAGE=doxagent-v2:server` 修复；异常重启
留下的 Xvfb display 锁也已限定在容器自身 display 编号内清理。

幂等安装器已安装 `python3-tk`、TigerVNC Viewer、root-owned 管理桥接和 GUI，sudoers
通过 `visudo -cf`。未重装或重构 xRDP；原 xRDP 继续监听 `127.0.0.1:3389`。

## 验收矩阵

| # | 验收项 | 结果与证据 |
|---|---|---|
| 1 | 桌面入口无终端启动 | 通过。`desktop-file-validate` 通过；以当前 xRDP `DISPLAY=:10` 执行 `gio launch` 后出现 Tk 窗口“消息源登录维护”，尺寸 980×560。 |
| 2 | 不输入 sudo 密码 | 通过。以 `doxagent-desktop` 执行 `sudo -n ... list/open/recover/close/verify` 均可用。 |
| 3 | 展示真实 Profile/出口/认证状态 | 通过。列出 Barron's、WSJ、Seeking Alpha、MarketWatch 共 8 个 Profile；主身份为美国节点、备用身份为日本节点，状态独立。 |
| 4 | 打开登录页约 5 秒 | 通过。`barrons-1` 5.05/3.02 秒，`barrons-2` 4.30 秒，`marketwatch-1` 3.56 秒。 |
| 5 | 操作员不接触 Docker/CLI | 通过。桌面流程由 GUI 按钮覆盖；桌面用户不属于 docker 组。 |
| 6 | 单击验证文章权限 | 工具链通过，账号结论待人工登录。未登录 Barron's 的默认验证返回 `challenge_required/UNKNOWN`，GUI 会显示中文验证失败；不能在无凭据时伪造 VALID。 |
| 7 | 验证后自动 login-close | 通过。未登录实测验证后 session 文件自动删除。 |
| 8 | 容器重启后 Profile 持久化 | 结构验收通过。Site Access 重启后 `barrons-1/2` 目录 inode 不变且 Registry 状态可读；真实登录 Cookie 的有效性待人工登录后复验。 |
| 9 | 两个 Profile Cookie 隔离 | 通过。`/site-data/profiles/barrons-1` 与 `barrons-2` 是两个独立目录（不同 inode），分别绑定 US/JP egress。 |
| 10 | 令牌与 Docker 权限隔离 | 通过。桌面用户读取管理令牌失败、任意 `sudo docker ps` 失败；sudo 仅放行 root-owned 固定桥接程序。桥接 JSON 不输出管理令牌、login_token 或 OAuth state。 |
| 11 | 异常中断恢复 | 通过。打开 `marketwatch-1` 后重启 Owner，`recover` 识别失效 token、清理 root 会话并返回中文结果；正常未完成会话也可继续或显式取消。 |
| 12 | VNC 仅宿主 loopback | 通过。宿主仅 `127.0.0.1:5900`；Viewer 实际完成 RFB 3.8/无认证的本机连接。 |
| 13 | 测试、记录、changelog | 通过。19 项定向测试、Ruff、mypy 通过；本文件和根目录 changelog 已更新。 |

## 权限与文件

- `/usr/local/sbin/doxagent-site-login-admin`：root:root `0755`
- `/usr/local/bin/doxagent-site-login-ui`：root:root `0755`
- `/etc/sudoers.d/doxagent-site-login`：root:root `0440`
- `/run/doxagent-site-login/session.json`：维护期间 root:root `0600`
- `/home/doxagent-desktop/Desktop/消息源登录维护.desktop`：`doxagent-desktop` `0755`

## 留给操作员的唯一人工步骤

实际账号、密码、MFA 和订阅权限不能在无人值守验收中代填。操作员只需在 xRDP 桌面
双击“消息源登录维护”，优先处理每个网站的 `*-1`，在 Viewer 页面完成登录后点击
“登录完成并验证”。若默认文章失效，可使用隐藏在“更换验证文章（高级）”后的同域
HTTPS 文章；服务端仍会执行站点归属校验。
