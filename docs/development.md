# 开发参考

换电脑继续开发，先看[接续工作说明](../CONTINUE-HERE.md)和[下一步实施与验收](../NEXT-STEPS.md)。版本变更记录见[开发日志](../CHANGELOG.md)。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `app/src/main/java` | 安卓原生功能、备份和提醒 |
| `app/src/main/assets` | 平板界面、离线人脸模型和 Excel 组件 |
| `app/src/main/res` | 安卓图标、主题和资源 |
| `server` | 网页工作台、服务端接口、部署说明与测试 |
| `docs/screenshots` | 使用演示数据的界面截图 |

## 构建安卓安装包

环境需要 JDK 17、Android SDK 35、Android Build Tools 35.0.0 和 Gradle 8.9。

```powershell
gradle assembleRelease
```

安装包输出到 `app/build/outputs/apk/release/`。发布前需要签名，覆盖现有安装时使用原应用签名。

## 回归验证

仓库根目录的 `weekly-report-smoke-test.js` 和 `*-regression.cjs` 覆盖出勤统计、排课、课时、备份及微信通知准备。安卓提醒时间的回归入口为 `tests/ClassReminderTimeTest.java`。服务端的运行与测试说明见 [server/README.md](../server/README.md)。
