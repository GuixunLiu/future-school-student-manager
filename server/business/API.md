# v0.4.0 工作台与平板事件接口约定

本版新增网页入口 `GET /` 及两项固定资源 `/workspace.js`、`/workspace.css`；静态页面不含账号密码或任何业务资料，登录后再读取有权限的数据。没有目录浏览或任意文件访问。静态页只允许本来源脚本/样式/接口，无内联脚本及外部资源；业务API保留默认禁止加载内容的策略。限制Host为本机回环主机名/地址，拒绝外站及`null`来源，且不信任代理来源头；不得通过加代理头绕过本地访问限制。

所有业务接口须使用 `Authorization: Bearer <本地登录令牌或设备令牌>`。机构从令牌确定，客户端不可指定角色、所属机构或任意OpenID。只接受本地回环请求；JSON正文总长16KiB以内，字段多余、类型错误、无时区或超限返回422/413。无权限403，跨机构编号404，冲突409，登录失效401，登录限流429，存储忙503。错误不回显密码和原始请求。

标识为32位小写十六进制字符串。列表通过 `after` 和 `limit` 翻页（最多100，默认40），按ID游标扫描，不承诺时间排序。出现 `next_cursor` 时继续翻页，即使本页因撤权隐藏而为空。审计元数据不保存密码、令牌或请假理由正文。

## 身份和资料

| 方法及路径 | 身份 | 正文/行为 |
| --- | --- | --- |
| GET /health/ready | 无 | 测试模式与关闭的真实业务能力 |
| POST /v1/auth/login | 无 | `organization,login,password`；返回会话令牌一次 |
| GET /v1/me | 用户/设备 | 当前身份和角色/设备授权班级 |
| POST /v1/auth/logout | 用户 | 撤销本次会话 |
| POST /v1/admin/people | 管理员 | `login,display_name,password,roles[]` |
| GET /v1/admin/people | 管理员 | 分页，不返回密码验证值 |
| PATCH /v1/admin/people/{id} | 管理员 | `roles[]`及/或`active`；保护最后一个管理员；撤销旧会话及通知意向 |
| POST /v1/admin/people/{id}/reset-password | 管理员 | `password`；旧会话失效 |
| POST /v1/admin/classes | 管理员 | `name,course_name`，课程仅展示分组 |
| GET /v1/classes | 用户 | 管理员全机构/老师授权班级；家长不获取班级名册 |
| PUT /v1/admin/classes/{id}/teachers | 管理员 | `teacher_ids[]`，全部替换；必须有教师身份 |
| GET /v1/admin/classes/{id}/teachers | 管理员 | 当前已授权教师编号，仅本机构 |
| POST /v1/admin/students | 管理员 | `class_id,name,external_ref,remaining_units`，重名可并存，external_ref机构内唯一 |
| GET /v1/students[/{id}] | 用户 | 老师授权范围与家长所绑孩子的并集，管理员全机构 |
| POST/DELETE /v1/test/parent-links | 管理员 | `parent_id,student_id`；仅虚构资料测试关联，不是真实微信绑定 |
| POST /v1/admin/devices | 管理员 | `name,class_ids[]`；设备令牌只在签发时显示 |
| PUT /v1/admin/devices/{id}/access | 管理员 | `active,class_ids[]`；立即生效 |
| POST /v1/admin/devices/{id}/rotate | 管理员 | 使旧令牌失效；新令牌只显示一次 |
| GET /v1/admin/audit | 管理员 | 分页审计 |

`remaining_units` 为整数，100=1课时；范围-1,000,000至10,000,000。创建资料只建立测试源数据，不会生成历史通知、续费或新生统计。不存在修改角色的自助接口。

网页提交人员权限时附带 `expected_roles` 和 `expected_active`，任课授权附带 `expected_teacher_ids`。服务器在同一事务中比较旧值，已被修改则返回409；不会恢复已经撤销的身份或覆盖别人的新任课授权。这些为向后兼容的可选字段，网页始终携带；只传比较字段而无实际权限字段时拒绝请求。

## 课堂、请假和设备事件

| 方法及路径 | 身份 | 正文/行为 |
| --- | --- | --- |
| POST /v1/admin/lessons | 管理员 | `class_id,teacher_id,starts_at,ends_at,student_ids?`；未指定名单时保存当前在用学生快照；空数组表示明确空名单 |
| GET /v1/lessons/{id} | 老师/管理员 | 本次应到、实到、请假、未到名单、到课率 |
| GET /v1/lessons?day=YYYY-MM-DD | 老师/管理员 | 分页日期课堂列表；`scope=authorized`为授权范围，`scope=teaching`只限本人任课且仍获班级授权，即使兼任管理员也不扩大 |
| GET /v1/lessons/{id}/workspace | 老师/管理员 | 当次名单、核对编号、有效请假编号/理由/办理人、内容草稿和可确认未到提醒的时间状态；家长不可访问 |
| POST /v1/lessons/{id}/leaves | 老师/管理员 | `student_id,reason`；需本班且在本课名单中，已签到不可请假 |
| POST /v1/leaves/{id}/cancel | 老师/管理员 | `reason`；记录撤销者和时间，不自动签到 |
| POST /v1/device/attendance | 授权设备 | 下方签到事件；人类账号不可使用 |
| POST /v1/device/balance | 授权设备 | `event_ref,student_id,occurred_at,remaining_units,student_revision,source` |
| POST /v1/device/attendance/{id}/revoke | 原设备 | `event_ref,reason`；取消未模拟的签到通知，无自动返还课时 |

时间示例 `2026-10-24T14:00:00+08:00`；支持2000–2100年、同一中国日期内的课次，结束晚于开始且不超过12小时。事件最多允许60秒超前容差，超前事件不生成提前通知。

签到字段：`event_ref,lesson_id,student_id,occurred_at,consumed_units,remaining_units,student_revision,source`。`source` 是 `live|makeup|historical`；余额事件是 `live|historical`。来源由获授权设备提供，正式接入前还须平板可信适配。没有可接收任意整库、照片、人脸或任意文件的上传接口。

同一机构/设备/event_ref只接受完全相同内容重试，返回同一结果并标记 `duplicate=true`；改内容或跨事件类型复用返回409。设备撤权后，旧事件回执也不能绕过新权限读取。余额只接受更大 `student_revision`，第一次确定源设备；旧版本可记出勤但不能倒退余额。撤销后重新签到需新event_ref，保留旧撤销历史；返还余额由设备另发更高版本余额事件，服务器不猜测金额或课时。

## 内容与通知模拟

| 方法及路径 | 身份 | 正文/行为 |
| --- | --- | --- |
| GET/PUT /v1/me/notification-preferences | 用户 | PUT `preferences:{类型:布尔值}`；只可启用当前身份允许类型 |
| PUT /v1/lessons/{id}/content | 老师/管理员 | `body,audience,publish_after_class`；默认audience=arrived、publish=false |
| GET /v1/lessons/{id}/content | 老师/管理员，或发布后符合对象的测试家长 | 家长不可查看草稿/别班资料 |
| POST /v1/lessons/{id}/absence-notices | 老师/管理员 | `student_ids[]`；5分钟后明确选择真正未到且未请假者 |
| GET/PUT /v1/admin/notification-schedule | 管理员 | 作业配置，下方字段；默认关闭 |
| POST /v1/admin/jobs/tick | 管理员 | 手动生成到期的模拟通知，无后台定时器 |
| POST /v1/test/simulate | 管理员 | 每次最多200条，复核后标记simulated/cancelled/expired，真实微信发送始终false |
| GET /v1/me/notifications | 用户 | 仅本人且仍获授权的模拟记录，分页 |
| GET /v1/admin/outbox | 管理员 | 本机构全部模拟记录，分页 |

家长类型：`attendance,low_hours_parent,lesson_content,absence_parent`。老师类型：`absence_teacher,low_hours_teacher,weekly_teacher`。管理员类型：`weekly_admin,monthly_admin`。

作业字段：`enabled`（默认false）、`weekly_weekday`（0周一至6周日，默认6）、`weekly_hour`（默认21）、`weekly_minute`（默认0）、`monthly_delay_minutes`（默认0）、`low_hours_units`（默认1500即15课时）。阈值和时刻是测试默认值，正式启用前需用户确认。配置变更开启新窗口，不回填旧通知。通知接收意向与周期作业开关分别控制，前者也控制签到和课时不足通知。

所有出站记录均含 `channel=simulation,identity_verification=test_only`，不会出现sent/delivered或伪造微信绑定成功状态。课堂内容生成一次后锁定，尚未提供更正重发流程。历史模拟记录不能当成真实发送记录。

## v0.4平板新事件协议

| 方法及路径 | 身份 | 行为 |
| --- | --- | --- |
| GET /v1/admin/devices | 管理员 | 本机构设备及同步/本地请假委托状态，最多100台，不返回凭证 |
| PUT /v1/admin/devices/{id}/sync-access | 管理员 | `enabled,allow_local_leaves`；网页附带`expected_enabled,expected_allow_local_leaves`作并发保护，冲突409；默认未授权 |
| GET /v1/device/sync-plan | 获明确同步授权的设备 | 返回`protocol_version=1,org_id,device_id,server_time,access,classes,students,lessons`，仅设备班级范围 |
| POST /v1/device/events | 获明确同步授权的设备 | 严格正文`{kind,payload}`；kind为`attendance,balance,revoke,leave,cancel_leave` |

计划学生字段仅`id,name,external_ref,class_id,remaining_units,source_revision,source_device_id`，不包含照片/联系人。课次包含`id,class_id,starts_at,ends_at,student_ids`，窗口为过去14日至未来31日；上限2000学生、500课次、10000名单关系，超出明确报错。平板按学号对应external_ref、姓名、完整班级名及起始余额精确绑定并明确确认，不上传既有历史。

attendance及balance沿用原设备事件模型；revoke为`attendance_id,event_ref,reason`，之后另传带递增版本的最终余额。签到的`occurred_at`仅在`source=makeup`时可为null，用课次开始时间索引日期，审计`occurred_at_known=false`；不伪造真实发生时刻、不生成通知。现场签到和其他有时刻事件仍必须是带时区时间。

leave需要`event_ref,lesson_id,student_id,local_leave_ref,occurred_at,reason,operator_label`；cancel_leave需要`event_ref,leave_id,occurred_at,reason,operator_label`。两者还需额外设备委托，办理姓名只作未核验备注，审计`identity_verification=device_delegation_only`。撤假仅限同设备来源，人工已撤销时返回already_cancelled而不覆盖原办理人。

成功回执包含`accepted=true,device_id,event_ref,kind`和业务结果，签到/请假包括其`id`，重试可包含`duplicate`。客户端必须核对完整回执，不能只凭HTTP200删除待办。每次包括重复请求都重新验证凭证过期/撤销、班级授权与同步委托，401/403后保留待办并暂停。服务器只接收余额快照，不再次扣课；新生及续费标记仍须独立登记。

此协议只上传明确启用后的新事件，没有名册/历史自动迁移、教师独立身份验证或双向下载合并。当前API仍是本地回环联调模式；未来正式HTTPS/反向代理须单独配置访问边界，不能直接把该包当成已开放的公网接口。详见[平板同步说明](../../docs/tablet-sync.md)。

## 报表

| 方法及路径 | 身份 | 正文/行为 |
| --- | --- | --- |
| GET /v1/reports/weekly?week_start=YYYY-MM-DD | 管理员 | 周一日期，已结束确认课次、总体/每班/每老师、课时不足学生及任课关系 |
| GET /v1/me/reports/weekly?week_start=YYYY-MM-DD | 老师 | 本人且当前仍获班级授权的课次，不能借兼任管理员扩大教师统计 |
| POST /v1/admin/student-activity | 管理员 | `student_id,kind,occurred_at,source_ref`，kind=new或renewal；同source_ref重试去重，改内容409；不改课时 |
| GET /v1/reports/monthly?month=YYYY-MM | 管理员 | 中国自然月，新生人数、续费人数及笔数；未完整月份final_period=false |

月报中的新生和续费是明确登记的业务标记，不是交易支付记录或资金到账证明。所有报告注明已确认课次范围，未接平板时不能代表真实全校数据。对外消息摘要最多20条未到记录，完整名单保留在经授权的报告接口。
