# 统一运行隔离上线操作单

本操作单只用于未来经明确授权的目标机操作。当前交付仅在 Windows 本机用虚构路径测试生成器，未在 Linux／systemd 实测，未连接或修改真实服务器。

## 边界

- 新版本使用独立目录、独立虚拟环境、独立回环端口和独立 systemd 单元，不替换旧服务。
- 当前只允许服务器本机或 SSH 隧道访问，不是公网 HTTPS 入口；不得关闭 Host、Origin、身份或班级权限检查。
- 私有环境文件只保存必要变量。生成器不接收 DSN、密码、人脸密钥、设备令牌或微信密钥。
- 数据库初始化、真实资料处理、备份恢复和生产切换都是独立停止点；生成一个单元文件不代表已获部署授权。

## 一、核验并解压候选包

1. 对照发布回执重新计算 ZIP 的 SHA-256，不一致即停止。
2. 解压到全新版本目录，不覆盖旧目录；核对 `MANIFEST.json` 的逐文件摘要和候选边界。
3. 在独立虚拟环境按 `requirements-runtime.txt` 安装固定依赖，不修改系统 Python。
4. 按安全流程准备私有环境文件；不要把它复制进候选目录、公开日志或操作回执。

## 二、生成仅供审阅的单元文件

以下路径均为示例，执行前必须由负责人替换并审阅；输出必须是不存在的新文件：

```bash
python -B ops/render-runtime-service.py \
  --runtime-root /opt/future-school/releases/candidate \
  --python /opt/future-school/venv/bin/python \
  --environment-file /etc/future-school/runtime.env \
  --port 18090 \
  --output ./future-school-runtime.review.service
```

- 生成器只接受简单绝对 Linux 路径和 1–65535 的端口，拒绝路径穿越、空白、换行和 `%` 等展开字符。
- 输出沿用回环绑定、一个工作进程、私有环境文件、失败有限重启和资源隔离设置。
- 回执只含本地输出路径、字节数和 SHA-256，`installed=false`、`started=false`。
- 已有输出、符号链接或不存在的父目录均拒绝；失败时删除本次未写完的新文件，不碰其他文件。

## 三、目标机只读预检

```bash
python -B ops/runtime-preflight.py \
  --runtime-root /opt/future-school/releases/candidate \
  --python /opt/future-school/venv/bin/python \
  --environment-file /etc/future-school/runtime.env \
  --port 18090
```

任何失败项均停止。需要数据库连通检查时，先由私有会话注入 `RUNTIME_DATABASE_DSN`，再增加 `--database`；不要把 DSN 放进命令行。数据库检查仅执行只读事务、`SELECT 1` 和回滚。

## 四、安装前审阅

1. 逐行审阅生成的 `.service`，确认没有 `@PLACEHOLDER@`、真实密钥或非回环监听。
2. 对渲染后的文件执行 `systemd-analyze verify`；警告或错误须先处理。
3. 再次核对端口占用、数据库恢复点、旧服务状态和业务停止窗口。
4. 记录候选包摘要、单元文件摘要、审阅人和操作范围。未获明确授权时到此停止。

## 五、获授权后的隔离启动

只有负责人明确批准该次隔离启动后，才可由操作人员安装新单元、加载 systemd 并启动这个候选单元。不要启用公网端口，不要停止旧服务，不要设置开机自启。

启动后立即：

- 检查单元状态和日志，连续重启或失败即停止排查。
- 请求 `/health/ready`，确认结构版本、初始化和候选边界；`production_ready=false` 不得被改成通过标记。
- 通过 SSH 隧道打开 `/runtime`，使用专用测试账号验收，不先使用真实教师或学生资料。
- 验证签到、丢回执重试、数据库中断、进程重启、备份和独立恢复。

## 六、停止与回退

- 回退只停止本次明确标识的候选单元并切回经核验的旧代码；不得猜测服务名或批量停止进程。
- 不得用旧数据库备份覆盖候选运行后产生的新业务；恢复前必须停止写入并另行确认恢复时点。
- 保留日志、回执和审计，不删除失败证据，不把隔离验收写成正式上线完成。
- 微信端等待域名、HTTPS 和备案完成后另行开发，本操作单不启用微信发送。
