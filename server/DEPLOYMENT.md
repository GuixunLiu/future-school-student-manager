# 服务器基础验证包 v0.1.0

本包只验证独立服务的构建、运行和健康检查，**不是已经完成的微信通知后台**。没有学生上传、微信授权、家长绑定、教师/管理员账号、请假同步、报表或微信发送接口。不要配置任何微信密钥，不要上传学生备份。安卓 App 仍为 v4.9.4，无需因本验证包更新或重新安装。

## 已确认的权限与当前阻碍

2026-10-24 用户提供的终端截图显示：主目录 `/opt/wxnotify` 为 root 所有、部署账号组只读；`app`、`logs` 属于部署账号；`backups` 属于 root。主目录尚未显示 `docker-compose.yml`。预设部署脚本固定使用 `/opt/wxnotify/docker-compose.yml`，并执行 `docker compose -p wxnotify ... up -d --build`。

因此不能凭“能写 app”推断能够创建主配置。本包不尝试提权、修改目录权限、访问 Docker socket 或利用部署脚本绕过此限制。账号密码、服务器 IP、SSH 指纹及真实学生资料不包含在包内。

## 包内文件与分工

- `app/`：普通部署账号上传到 `/opt/wxnotify/app/` 的基础服务文件。
- `for-admin/docker-compose.yml`：**交给负责人审核并放到 `/opt/wxnotify/docker-compose.yml`**，不是让普通账号写主目录。
- 本说明：无需上传为应用配置。

负责人只需协助放置一次主配置，无需提供 root 密码、开放 Docker 账号组、修改全机权限或调整现有项目。建议主配置 root 所有、组为部署账号组、权限0640；主目录继续保持普通部署账号不可写。请负责人自行审核，不直接照抄执行未经其确认的管理命令。

## 负责人审核事项

1. 先确认 `/opt/wxnotify/app` 当前无其他需保留应用文件，上传仅更新本包明确列出的文件，不递归删除目录、不覆盖原有数据库或配置；不改 `backups`、`logs` 的权限与内容。
2. 审核配置中的构建目录 `./app`、项目名 `wxnotify`、资源上限（0.5 CPU、256MiB内存）、只读根文件系统、无宿主机目录挂载及非 root 进程。
3. 确认服务器已有 Docker Compose 可正常构建，回环端口18080空闲，镜像仓库及 PyPI 可达；拉取镜像与构建会使用网络、磁盘和临时构建资源，运行期资源上限不限制构建过程。不要为此运行系统升级或重启 Docker。
4. 仅绑定 `127.0.0.1:18080`，暂不新增公网安全组端口，不占80/443。正式反向代理及域名另行确认；旧版 Docker 的回环发布存在历史安全注意事项，由负责人核对引擎版本及防火墙，不将回环绑定当成完整安全边界。
5. 本包未附 Nginx、TLS、域名解析或微信回调配置；不要覆盖现有网站配置。域名需用户与纪校长确认。

## 部署前与验收（满足前提后才执行）

目前未上传、未在远程服务器构建或运行。不要因文件名存在就直接执行部署；必须先完成负责人主配置放置、文件核对和端口检查。

普通账号先执行只读检查：

```bash
python3 /opt/wxnotify/app/preflight.py
```

仅当返回 `ok: true`、配置已获负责人审核，且用户确认本次启动后，才使用现有受限入口：

```bash
sudo /usr/local/bin/wxnotify-deploy
```

然后在服务器本机检查：

```bash
curl --fail --max-time 5 http://127.0.0.1:18080/health/ready
```

预期 `status=ready`、`mode=infrastructure_only`、`business_ready=false`，且微信发送、学生同步、家长绑定均为false。这只说明基础服务运行，不代表微信接入成功、消息送达或数据同步完成。

不要将该基础版本登记为微信服务器回调 URL，不要让 App 上传学生或历史待办，不要通过改环境变量强行开启尚未实现的功能。先验收基础服务，再开发设备身份、家长关系核验、事件去重/过期、微信模板字段及回调、权限审计等功能。

## 停止、故障与后续

本轮未验证 `wxnotify-restart`、`wxnotify-logs` 的脚本内容，不自动调用它们。首次启动或构建失败时保留错误，先核对原因，不循环部署、不删除容器/镜像/卷、不操作其他项目。基础包没有数据库或持久化学生数据；但后续业务版本必须另行设计备份、迁移和回滚，不把重建容器当成数据备份。

域名及HTTPS、资源分配、数据库与日志保留、依赖安全审查和更新仍需在业务上线前落实。镜像基础标签和间接依赖并未全部按摘要锁定，不将本包作为未经复核即可投入学生业务的生产发布。

## 技术参考（2026-10-24 核对）

本地 Python 3.12 下11项测试通过，另通过依赖一致性检查；包含真实本机 HTTP 启停及响应、YAML解析和配置断言、预检权限模拟及包内白名单检查。未运行 Linux/Python3.11 容器构建，未验证远程端口空闲、镜像仓库连通性、资源限制或反向代理。只有后续在目标服务器验收后，才能记录为服务器基础服务已部署。

- [FastAPI 官方容器部署](https://fastapi.tiangolo.com/deployment/docker/)
- [Docker Compose 服务配置](https://docs.docker.com/reference/compose-file/services/)
- [Docker 发布端口及回环安全注意事项](https://docs.docker.com/engine/network/port-publishing/)
- [Uvicorn 运行参数](https://uvicorn.dev/settings/)
- [FastAPI 官方包索引](https://pypi.org/project/fastapi/)、[Uvicorn 官方包索引](https://pypi.org/project/uvicorn/)
