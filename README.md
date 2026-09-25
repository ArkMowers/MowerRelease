# MowerRelease

Mower 的完整安装包、跨版本 OTA 差异包与版本索引集中发布于此。安装包从 [Mower 主仓库](https://github.com/ArkMowers/arknights-mower)已发布资产复制，逐个核对大小和 SHA-256；源代码与原始发布记录仍在主仓库。

目前为 Windows x64、Linux x64、Linux ARM64 和 Android ARM64 的 Mower 包生成文件级 OTA。Android OTA 仅更新 Mower 程序和随包 Python 环境，不更新 APK 或 MAA；宿主从已在线安装的 Mower 包重建目标包。macOS DMG 仍使用完整包。发布流程从主仓库已发布的完整包构建最近五个旧版本直达当前版本的差异包。差异包达到完整包 85% 大小时不发布，客户端改用完整包。

`version/summary.json` 指向已有安装包的渠道索引。每个索引列出本仓库同一 Release 中的完整包与 OTA 包，以及同渠道最近六个历史版本，供已安装较旧版本的用户选择前三个可回退版本。Release tag 与主仓库相同。索引只在完整包复制并校验、目标 Release 成功公开后更新。主仓库当前正式版 v4.1.5 没有安装包，因此暂不生成正式版索引。

OTA 清单 `ota.json` 包含起点、目标、平台、目标文件摘要及差异文件名。`payload/` 只存新增或变化的文件；不在目标清单中的旧文件自然删除。安装器在新目录重建、逐文件校验，再替换程序。当前文件不匹配、下载失败或校验失败时改用同一 Release 的完整包。

Windows 和 Linux 大文件使用 BSDIFF40 差分，生成 `_v2.zip` 附件。旧格式同名附件继续保留，旧客户端仍可使用；新版客户端优先下载 `_v2.zip`。

手动补发：在 [Publish Mower OTA](https://github.com/ArkMowers/MowerRelease/actions/workflows/publish-ota.yml) 工作流输入主仓库已发布的 tag。定时流程每半小时检查最新正式版和公测版。发布账号使用本仓库 `GITHUB_TOKEN`；无需主仓库跨仓库写入密钥。
