# 单班试运行独立待启用服务

2026-10-28。原教师HTTP只读网页已验收，单班离线备份和候选库也已实际准备成功。此步骤在该候选之外再建工作库，注册新的内部只读服务，为后续实际课次和设备连接准备运行位置。

## 已核对的离线副本

真实副本目录为`/opt/wxnotify/app/single-class-offline-20261028121655-da40e468`，已确认16名试点学生、345课时。9个原账号保留，其中包括8位正式教师及原测试账号；没有重新开户或重置密码。该回执SHA256为`a3f6787aa9a88136b32b23e6eef39aaa483a9a48c2afb08f318e4b19fcb3625e`。

原始备份SHA256为`e577c39e786da8fd2ab83a07834710301142f03a8496f959082033c4b88a44be`，候选SHA256为`e5ff11a1a9d7b8310d666f78c3b6ffc4c2cd233af17b89e6b397cb6c084d770d`。安装时逐项重新核对；文件变化或已有同名单位/目录时停止，不能覆盖重建。

## 本轮工具与状态

- `server/ops/pilot-standby.py`：核对27项冻结源码和私有配置，加载固定v0.5只读API；启动及健康检查核对业务摘要、试点名单/余额，以及设备/通知关闭状态。登录会话可写入独立工作库，业务写入始终由原只读守卫拒绝。
- `server/ops/install-pilot-standby.py`：核对真实stage回执、备份/候选指纹、原服务和空闲新端口，再创建`standby/`私有目录及工作库。注册新用户单位，失败只停禁此新增单位，保留现场；不替换原数据库或原单位。
- `server/ops/deploy-pilot-standby.cjs`及CMD：固定安装器、运行器、辅助程序、真实stage回执4项指纹，一次负责人SSH；提供安装及独立暂停模式，不读取或发送教师密码。

仓库外入口为`outputs/pilot-standby-2026-10-28-ready/DEPLOY-STANDBY.cmd`，暂停入口为同目录`PAUSE-STANDBY.cmd`。**2026-10-28 20:46已实际安装成功并取得回执**：active/running/enabled、NRestarts=0，只监听回环18084，16人基线和运行器指纹匹配，原候选保持。安装回执SHA256`e0e85fd6829189d94dd5dcef46efbd1e4a2789e76c5d2a68efb21f9bbd72dc05`，位于`outputs/private-pilot-standby-2026-10-28/standby-installed-2026-10-28T12-46-48-872Z.json`；已再次用固定计划验证。7项虚构资料测试、JS及Windows自检为此前准备证据。本次核对未重新连接SSH、重启服务、暂停单位或使用真实教师凭证；不重复安装。

最初本机ASGI测试因Windows事件循环的回环套接字限制未完成，开放本机测试权限后运行；暂停测试的Windows路径表示改为PurePosixPath后通过。没有把这些本机问题作为服务器故障或重启既有服务。

## 固定运行位置与关闭状态

新单位`wxnotify-single-class-standby.service`，只监听`127.0.0.1:18084`；工作目录为原副本目录下的`standby/`，新工作库为`standby/business.sqlite`。备份及`pilot-offline.sqlite`保持，登录不会改变它们。复用原Python环境及冻结v0.5源码，不安装依赖或修改27项发布文件。

健康预期为`mode=pilot_standby`、`pilot_phase=standby_read_only`、`roster_review_only=true`；business_ready、设备注册、单班写入、平板同步、微信和家长绑定均false。运行器指纹为`ec43969834469317ca9c2cff21d3a6c5dbb11c8cd927a4e93f95387f8d4bf3d1`。服务仅保留既有账号读权限，没有宣称只读列表已缩减为单班；未来单班写入及设备范围守卫尚未实现。

本轮不创建10月31日课次、不启用设备、不上传签到/余额、不更新平板、不将新服务接到18083公网入口。不得把它当作已经可用的真实同步服务，也不直接把冻结local_test接口挂到公网。现有HTTP许可仍只用于原教师只读网页。

## 暂停及后续

暂停工具先确认该新单位WorkingDirectory恰为已核对的standby目录，再只执行本单位的disable/stop，并验证inactive/disabled。原18080—18083服务保留；数据及备份不删除、不恢复覆盖。

实际安装回执已核对。下一步实现单班写入/事件范围守卫及可回退的受控切换，再建立已确认课次和核对安全设备连接。当前运行器校验原业务摘要，且要求所选班尚无课次，**不能直接向运行中的standby工作库插入课次或调整业务资料**；那会使健康校验失败。后续应先准备允许明确业务变化的独立阶段及备份，不直接删掉当前只读守卫。仅有standby健康不代表真实同步已上线。
