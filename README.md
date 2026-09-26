# MowerRelease

Mower 的完整安装包、跨版本 OTA 差异包与版本索引集中发布于此。正式版与公测版安装包从 [Mower 主仓库](https://github.com/ArkMowers/arknights-mower)已发布资产复制，逐个核对大小和 SHA-256；Windows 开发版在本仓库从主仓库 `alpha` 源码构建。源代码与正式发布记录仍在主仓库。

目前为 Windows x64、Linux x64、Linux ARM64 和 Android ARM64 的 Mower 包生成文件级 OTA。Android OTA 仅更新 Mower 程序和随包 Python 环境，不更新 APK 或 MAA；宿主从已在线安装的 Mower 包重建目标包。macOS DMG 仍使用完整包。发布流程从主仓库已发布的完整包构建最近五个旧版本直达当前版本的差异包。差异包达到完整包 85% 大小时不发布，客户端改用完整包。

`version/summary.json` 指向已有安装包的渠道索引。每个索引列出本仓库同一 Release 中的完整包与 OTA 包，以及同渠道最近六个历史版本，供已安装较旧版本的用户选择前三个可回退版本。Release tag 与主仓库相同。索引只在完整包复制并校验、目标 Release 成功公开后更新。主仓库当前正式版 v4.1.5 没有安装包，因此暂不生成正式版索引。

开发版使用 `vX.Y.Z-alpha.N.g<8 位提交短码>` 标记，例如 `v4.1.6-alpha.9.g40ac54e4`。本仓库每天北京时间 06:00 检查主仓库 `alpha` 分支；提交变化时，在本仓库的 Windows runner 构建 x64 完整包，并直接发布到本仓库的 Release，随后生成 OTA 和独立的 `version/dev.json` 索引。主仓库不发布 nightly Release。每个 nightly 最多从五个旧开发版和两个较新的普通公测版构建直达 OTA，方便公测版用户切换；普通 `alpha.N` 继续写入 `version/beta.json`。

OTA 清单 `ota.json` 包含起点、目标、平台、目标文件摘要及差异文件名。`payload/` 只存新增或变化的文件；不在目标清单中的旧文件自然删除。安装器在新目录重建、逐文件校验，再替换程序。当前文件不匹配、下载失败或校验失败时改用同一 Release 的完整包。

Windows 和 Linux 大文件使用 BSDIFF40 差分，生成 `_v2.zip` 附件。旧格式同名附件继续保留，旧客户端仍可使用；新版客户端优先下载 `_v2.zip`。

主仓库正式版或公测版 Release 构建完成并上传全部安装包后，会发送 `mower_release_published` 事件，立即触发本仓库打包。主仓库需要配置 `MOWER_RELEASE_TOKEN` Actions secret：使用仅授权本仓库、具有 Contents 写权限的细粒度令牌。若未配置，主仓库发布任务跳过即时触发，本仓库每五分钟检查最新可用 Release 并自动补发。手动补发可在 [Publish Mower OTA](https://github.com/ArkMowers/MowerRelease/actions/workflows/publish-ota.yml) 输入已发布的 tag。nightly 由本仓库的 [Windows Nightly](https://github.com/ArkMowers/MowerRelease/actions/workflows/nightly-windows.yml) 独立构建与发布，不依赖跨仓库令牌。本仓库构建和上传使用自身的 `GITHUB_TOKEN`。
