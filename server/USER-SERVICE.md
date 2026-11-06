# 普通账号后台托管：安装与切换

仅针对服务器基础验证版 v0.1.0，不是微信业务上线。前提是用户已经核实基础包SHA-256、在独立目录完成虚拟环境安装、前台HTTP健康检查成功，且当前账号 `Linger=yes`。本方案不使用root、不访问Docker、不修改顶层Compose文件或原项目。

## 本次进度

用户截图已确认开启自身常驻成功。后续脚本和校验清单上传、SHA-256检查、服务器配置校验及注册均成功。前台SSH重置后，经只读核对和单进程身份检查正常退出流程，确认18080已释放；随后 `enable --now` 创建用户自启链接，健康请求初次连接被拒、自动重试后返回ready，状态为active/running/enabled，MainPID为1563554。CPUQuotaPerSecUSec=500ms、MemoryMax=268435456、TasksMax=64、NoNewPrivileges=yes显示配置已加载。用户随后在新SSH连接中再次检查，回传ready、MainPID=1563554、NRestarts=0、ActiveState=active、UnitFileState=enabled，确认本轮后台接管和独立连接的进程连续性。内核cgroup实际限额和真实机器重启恢复尚未实测，不为此重启共用服务器，也不据基础健康宣称微信业务上线。

已准备脚本 `ops/install-user-service.py`，默认只打印配置；带 `--install` 才会注册配置。服务名固定 `wxnotify-infrastructure.service`，代码目录固定为 `/opt/wxnotify/app/infrastructure-v0.1.0-9eecbcbf`。文件仅写到调用账号自己的 `~/.config/systemd/user`，不覆盖同名服务、软链接或现有drop-in目录；已有加载服务也会拒绝替换。

安装之前检查非root Linux账号、所需代码/虚拟环境、工具、Linger状态和用户服务管理器，执行独立环境的依赖一致性检查，再用 `systemd-analyze --user verify` 验证候选配置。错误或警告均停止；验证通过才独占创建权限0600的配置并重新加载定义。**不自动启用、启动、停止、重启或杀掉任何服务**。若最后重新加载失败，已写配置可能仍在，不能盲目重跑、删除或覆盖，先查看错误。

## 配置边界

- 使用当前普通用户运行，单进程，仅监听服务器回环 `127.0.0.1:18080`，禁用访问日志、代理头信任和WebSocket；微信/同步开关保持关闭。
- 配置256MiB内存、0.5CPU、64任务、禁止提权、禁止core dump和有限失败重试；这些需在实际服务运行后核对服务器cgroup支持及生效情况，不能凭文件存在就声称已经限制资源。
- 日志交给系统既有journal，不改全机journal留存或系统服务；不要向日志输入密码或微信密钥。
- 这是同账号下的用户服务，不等同于容器只读文件系统或完整沙箱；仍需后续安全验收。没有数据库、学生资料或微信访问。

## 分步切换（由用户执行，不一次性跳过检查）

1. 上传安装脚本并比对提供的SHA-256。始终在已有目标服务器登录上下文下操作，不在Windows本机误执行服务管理命令。
2. 用服务器Python执行 `python3 <已校验的安装脚本路径> --install`。看到 `Installed user unit (not enabled or started)` 才表示注册完成；旧前台窗口暂时保留，安装本身不会占用端口。
3. 注册完成后，回到原本运行我们前台测试进程的窗口，按一次Ctrl+C，确认该进程完成退出并返回提示符。这只停止用户此前手动启动的本测试进程；不要使用pkill、killall、kill-user、结束全部Python进程或重启Docker。
4. 再单独执行：

```bash
systemctl --user enable --now wxnotify-infrastructure.service
systemctl --user status wxnotify-infrastructure.service --no-pager
curl --fail --max-time 5 http://127.0.0.1:18080/health/ready
```

如果第一条失败，停止后续操作并核对本服务日志，不因其他进程占端口就强制终止它。上述命令不是安装脚本的自动行为，须在前台停止和用户确认切换之后才执行。

5. 另行核对配置及状态：

```bash
systemctl --user is-enabled wxnotify-infrastructure.service
systemctl --user show wxnotify-infrastructure.service -p ActiveState -p SubState -p MainPID -p ControlGroup -p MemoryMax -p CPUQuotaPerSecUSec -p TasksMax -p NoNewPrivileges
journalctl --user -u wxnotify-infrastructure.service -n 30 --no-pager
```

配置限额的显示值不是cgroup实际执行的完整证明，实际控制器和限额需按本服务的ControlGroup核对。用户级服务在当前机器上的启动、健康、托管和限额生效均应分别记录。

6. 当前运行验证通过后，再由用户关闭自己的旧SSH会话，另开连接检查健康和MainPID是否保持，验证退出登录不会中断服务。`is-enabled`只能证明自启配置，不应为测试而重启共用服务器；尚未验证真实机器重启恢复时须明确说明。

## 出错时

用户在前台切换阶段出现SSH连接重置。后续只读证据显示用户单位尚为inactive/dead/disabled，18080仍由原先手动启动时的同一测试PID监听；因此不能将断线视为正常停止，也不能重复启动一份进程争抢端口。需要先核对当前PID所属用户和完整启动路径/模块与本测试一致，再只向该单一进程发送SIGINT，并等待及重新检查端口；进程身份不匹配时不发信号。不要把此处历史PID当成将来可反复使用的固定进程号。

保留错误，仅查看本服务状态及日志，不重试部署、不改系统授权、不改其他用户、不关闭Linger或终止整个账号。遇到同名单位、已有覆盖配置、用户管理器不可用、校验警告或资源控制器不足时先停止处理，不自动降低安全约束。

如果用户明确要求回退并确认此单位确由本轮新增，可针对这一个用户单位执行 `systemctl --user disable --now wxnotify-infrastructure.service`；不自动删除配置，不影响其他服务。恢复前台测试前仍要确认18080空闲，不对其他监听进程动手。

## 参考

- [systemd服务配置与失败重启](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [systemd用户服务资源控制与cgroup](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [NoNewPrivileges等执行设置](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
