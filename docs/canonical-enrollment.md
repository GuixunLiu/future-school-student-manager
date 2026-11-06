# 新连接基线授权与加密暂存（2026-11-01）

最新：[原生独立验收已实际通过](canonical-native-acceptance-2026-11-01.md)，测试包及临时转发已清理，日常App未变。后续转当前真实服务器/新鲜资料核对，不能把下文历史“USB未连接”当当前状态。

最新：新增[当前资料采集入口](canonical-live-capture.md)，原生容器直接接最小清单工具；独立原生验收脚本已经覆盖采集与暂存串联，但USB仍为空，不能声称真机通过。

后续更新：已补充管理员最小清单核对与暂存证明签发，详见[迁移核对接续](canonical-migration-review.md)。本页原“证明需迁移工具直接写入”仅为早期实现说明；当前走新鲜资料核对记录和管理员授权接口，仍未部署或执行真实授权，原生验收仍等待USB恢复。

## 已实现代码

服务器 `/v1/management/device/plan` 仅供已授权设备领取连接核对证明。它不是授权/激活接口；不创建账号、设备、学生绑定或新权限。显式迁移工具必须先写入已核对的 `management_enrollment_proofs`，包含机构/设备/代次/配置ID/设备nonce、平板备份摘要、服务器账本摘要、有效期。

领取时重新验证设备启用状态、当前代次、每名绑定学生的班级权限和证明有效期，取设备/授权/证明中最早的到期时间。账本摘要覆盖绑定、设备班级范围、签到事实、余额/版本、学生身份/在读状态、班级和教师任课。核对后发生变化会拒绝旧证明。没有通过新接口根据姓名自动匹配或授权。

原生 `CanonicalSyncTransport` 能准备Android Keystore RSA3072公钥/nonce申请，读取应用自身外部目录 `canonical-sync-connection.enc.json`，核对后暂存到独立Keystore AES配置。App后台设置中增加折叠的准备入口，需要解锁后台；导入只向原生层传当前已保存资料的SHA256，计算期间资料变化会停止。

连接文件采用混合加密：AES256-GCM保护整个配置，RSA-OAEP(SHA256、MGF1-SHA1)封装AES会话密钥，GCM附加认证数据为 `future-school-canonical-v1:` 加设备nonce。原生严格核对设备nonce、资料摘要、目标服务器固定公钥及主机名，使用新凭证查询服务器plan，对机构/设备/代次/配置ID/nonce/摘要/到期时间逐项比较。

成功后只设置 `staged=true, enabled=false`，对JS仍返回configured=false，不会启动新模式签到或停用原镜像。正式切换前仍需再次核对最新本地/服务器基线、准备规范映射及原子切换/回滚。不允许直接把原生或JS的enabled手工改true。

混合密文格式为 `protocol,nonce,wrapped_key,iv,ciphertext`。解密配置严格为 `e,t,d,p,o,g,id,b,n,x`；分别是固定HTTPS地址、设备凭证、设备ID、服务器公钥摘要、机构ID、代次、配置ID、平板资料摘要、设备nonce、到期Unix秒。凭证不能进入JavaScript、日志或Git。

本机 `server/ops/CanonicalEnvelope.java` 从stdin读取配置，参数只有设备公钥DER路径和nonce，stdout仅输出密文。**当前仅有加密辅助工具，没有可以绕过资料核对直接发放真实授权的一键工具。**

## 验证与当前阻塞

- 最终6项授权测试、7项交付回归及6项API回归共19项通过；包括服务器余额/身份/任课变化后旧证明失效。
- 实际App入口测试10项通过，新增后台权限、仅发送摘要、计算期间资料变化拒绝导入。
- Java密码学自检通过：超过直接RSA容量的虚构载荷往返、错误nonce、不同设备私钥、篡改密文被拒绝。这是桌面JCE验证，不是Android Keystore真机验证。
- Android debug/release构建通过。`CanonicalEnrollmentProbeActivity` 只在debug独立包中，正式release manifest已确认不包含它。
- 已准备 `server/tests/native-canonical-enrollment.cjs` 原生真机验收：本机固定身份TLS服务、虚构凭证，检查错误本地/服务器基线、Keystore暂存与重启、撤权后拒绝、且始终不激活；结束后清理独立测试App。调用前必须确认平板在线且没有已存在的独立测试App，拒绝覆盖未知测试资料。
- **本轮真实执行在安装前因HVA13XVR USB连接断开而退出。**复查设备列表为空；没有安装本轮测试包、没有运行原生加密/TLS验证、没有创建真实授权或改日常App。不能把脚本已准备或桌面JCE通过写成真机导入成功。

## 继续顺序

1. 平板恢复USB连接后运行独立原生验收，不需要教师/SSH密码，不使用真实学生或服务器账户；若报告失败保留错误，不能据此放宽服务器身份校验。
2. 完善迁移工具生成/授权证明、规范映射清单及双端基线冻结/再核对；当前08:21备份仍不能当作现在的新鲜资料。
3. 实现暂存连接到真实启用的独立确认协议：再次验证证明和服务器账本摘要、保存完整本机切换快照、持久映射与版本、停用旧镜像发送，失败可安全恢复。暂存的存在本身不是激活授权。
4. 当前真实服务器需要交互认证，尚未部署plan/新业务路由或迁移真实库。完成真实核对和整条隔离真机测试后才生成正式升级包。

实际测试说明：在仓库根目录先编译加密工具到仓库外 `outputs/canonical-enrollment-2026-11-01/java`，再运行 `node server/tests/native-canonical-enrollment.cjs`。测试复用仓库外已存在的固定服务器身份本机TLS测试材料；不得把私钥复制入仓库或输出到对话。
