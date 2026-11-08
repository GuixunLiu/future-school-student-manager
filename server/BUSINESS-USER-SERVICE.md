# v0.4.0业务内部验收服务：普通账号后台托管

此配置只用于当前内部验收，不开放公网，也不发送真实微信。不修改现有`wxnotify-infrastructure.service`、顶层Compose、其他项目或系统授权。

专用脚本：`ops/install-business-user-service.py`。固定代码目录`/opt/wxnotify/app/business-v0.4.0-c59dab1f`，固定数据库`/opt/wxnotify/app/business-data-v0.4.0/pilot.sqlite`，新用户单位`wxnotify-business-pilot.service`，业务仅监听127.0.0.1:18081。没有root、Docker、系统级服务或开放端口步骤。

## 注册前检查

默认运行只打印配置。`--install --backup-sha256 <已回传的备份SHA-256>`才会注册。脚本检查普通Linux账号、独立代码及虚拟环境、固定v0.4包manifest哈希和全部19项源码、数据库与备份的私有权限/所有者/格式/完整性/有效管理员、备份内容匹配、Linger状态、用户管理器与依赖一致性，再用`systemd-analyze --user verify`检查配置。

任何同名单位、drop-in或配置路径软链接均拒绝覆盖。只独占创建调用账号自己的配置文件并重新加载定义；不自动启动、启用、停止、重启、发信号或处理占用进程。若daemon-reload失败，配置文件可能已写入，保留现场，不重复覆盖或删除。校验读取SQLite连接显式关闭，不写业务事实或恢复数据库。

固定256MiB内存、0.5CPU和64任务，禁止提权、core dump，私有umask，失败重试有限。配置值加载与内核资源限额是否实际生效需在远程分别核验；用户服务不是完整容器隔离。此阶段没有后台通知调度作业，托管HTTP进程不等同于自动微信推送。

## 分步操作

1. 将脚本与其独立SHA-256校验文件上传至已授权的`/opt/wxnotify/app/`。核对SHA-256后，以`python3`运行脚本的`--install`，传入已完成备份的哈希。看到`Installed user unit (not enabled or started)`后再进入切换阶段。原前台业务窗口和SSH隧道继续保留。
2. 在**原本运行v0.4业务服务的那个窗口**按一次Ctrl+C，等待应用退出并返回shell。不要停止v0.1基础服务，不使用killall、pkill或结束整个账号。若原窗口断开，先读取18081监听及进程身份，不猜测其已停止。
3. 在另一已登录服务器的窗口检查`ss -ltn 'sport = :18081'`。确认无监听后才执行：

```bash
systemctl --user enable --now wxnotify-business-pilot.service
curl --fail --silent --show-error --retry 5 --retry-delay 1 --retry-connrefused --max-time 5 http://127.0.0.1:18081/health/ready
systemctl --user show wxnotify-business-pilot.service --no-pager -p ActiveState -p SubState -p UnitFileState -p MainPID -p NRestarts -p MemoryMax -p CPUQuotaPerSecUSec -p TasksMax -p NoNewPrivileges -p ControlGroup
```

启动失败时停止后续切换操作，读取本单位的`journalctl --user -u wxnotify-business-pilot.service -n 30 --no-pager`。不因端口被占用就停止其他进程。启动过程第一次连接拒绝、随后重试ready可以是正常启动时序；持续失败不能视为通过。

4. 健康应为version0.4.0、status ready、mode local_test，真实业务/微信/家长绑定开关仍为false。核对只监听127.0.0.1:18081。配置资源限制的显示值不是实际cgroup执行证明，不为验证整机恢复而重启共享服务器。
5. SSH隧道窗口继续打开，浏览器刷新并重新登录后，核对既有测试教师、班级、2名学生、10课时余额及撤假结果、课堂草稿。只有页面重读和后台健康均正常，才结束旧前台SSH会话；另开独立SSH连接核对PID及NRestarts，验证退出登录不终止业务。

关闭**隧道窗口**仍会让电脑上的127.0.0.1:18081暂不可访问，这不代表服务器后台停止。需要使用网页时重新建立SSH隧道。公网访问仍等待域名/HTTPS与访问边界验收。

## 本地验证与远程状态

2026-10-25：新增7项业务托管测试及原6项基础托管测试，共13项通过，使用虚构数据库、模拟systemd调用，未触碰真实服务器或任何运行服务。验证源码/备份变化、数据库身份/管理员、所有者和私有权限、已有配置、Linger、依赖、验证警告、加载失败及独占注册；数据库前后内容保持一致，无自动启停操作。早期测试发现只读SQLite连接需显式关闭，已修复并重测。

正式业务ZIP保持原SHA-256，脚本独立交付。实际注册、切换、健康、退出登录持续性及资源执行的结果须由远程回传确认，不能把这13项本地测试记作后台已启用。进度见[业务远程验收记录](BUSINESS-DEPLOYMENT.md)。

后续远程回传已确认脚本校验/注册、单位启用/启动、v0.4.0健康ready及127.0.0.1:18081监听；独立新SSH连接仍是PID1817038、NRestarts=0、active/running/enabled，与前次回传相隔258秒，当前后台持续性验收通过。用户再回复“都在”，确认后台接管后重新登录，测试名册、10课时余额、撤假结果及课堂草稿仍保留；实际cgroup执行、长时间稳定及真实机器重启恢复尚未实测。详见[本轮验收结果](BUSINESS-ACCEPTANCE-2026-10-25.md)。

独立交付目录：`outputs/business-user-service-v0.4.0/`。文件`wxnotify-business-user-service-v0.4.0.py`为8453字节，SHA-256为`c991a05e1baa6522dca6befcf37e271ad9cc259e16ab173ea2d9391e22a00dbc`，附同名`.sha256`。脚本含专用目录和校验逻辑，不包含账号密码、设备令牌、数据库或用户备份文件。
