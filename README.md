# MowerRelease

Mower 的跨版本 OTA 差异包与版本索引。完整安装包、源代码和更新说明仍在 [Mower 主仓库](https://github.com/ArkMowers/arknights-mower)。

目前为 Windows x64、Linux x64、Linux ARM64 和 Android ARM64 的 Mower 包生成文件级 OTA。Android OTA 仅更新 Mower 程序和随包 Python 环境，不更新 APK 或 MAA；宿主从已在线安装的 Mower 包重建目标包。macOS DMG 仍使用完整包。发布流程从主仓库已发布的完整包构建最近五个旧版本直达当前版本的差异包。差异包达到完整包 85% 大小时不发布，客户端改用完整包。

`version/summary.json` 指向正式版和公测版索引。每个索引列出主仓库完整包与本仓库 OTA 包。OTA 的 Release tag 与主仓库相同。正式版、公测版索引仅在对应 OTA Release 成功公开后更新。

OTA 清单 `ota.json` 包含起点、目标、平台、目标文件摘要及差异文件名。`payload/` 只存新增或变化的文件；不在目标清单中的旧文件自然删除。安装器在新目录重建、逐文件校验，再替换程序。当前文件不匹配、下载失败或校验失败时退回主仓库完整包。

手动补发：在 [Publish Mower OTA](https://github.com/ArkMowers/MowerRelease/actions/workflows/publish-ota.yml) 工作流输入主仓库已发布的 tag。定时流程每半小时检查最新正式版和公测版。发布账号使用本仓库 `GITHUB_TOKEN`；无需主仓库跨仓库写入密钥。
