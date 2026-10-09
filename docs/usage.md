# 使用与配置

## Actions 参数

手动运行可选择 `game_version`、`module_tag`、`game_check`、`allow_untested_version` 和 `publish`。`automatic` 供翻译仓库联动使用，开启后沿用下表的构建设置；人工运行保持关闭即可。

| Patcher 仓库 Variable | 默认值／作用 |
| --- | --- |
| `GAME_VERSION` | `6.0.2`，要求原包为此版本 |
| `GAME_CHECK` | `signature`；可改为 `exact` |
| `ALLOW_UNTESTED_VERSION` | `false`；尝试其他游戏版本时显式设为 `true` |
| `PATCH_KEY_ALIAS` | `idoly-patcher`，签名密钥别名 |

默认下载地址固定为 `https://d.apkpure.com/b/XAPK/game.qualiarts.idolypride?version=latest`，由 APKPure 跳转到当前版本文件。可通过 Secret `GAME_APKS_URL` 覆盖；页面地址、临时 CDN 地址和单个 APK 不适合作为长期来源。插件更新可复用原包下载地址。游戏升级时按需更新下载地址和 `GAME_VERSION`，确认插件兼容性；`exact` 模式还需对应版本的基准。允许未测试版本不会绕过签名校验。

自动联动由翻译仓库在插件发布成功后发送 `workflow_dispatch`。在 GitHub 创建仅授权此 Patcher 仓库、权限为 **Actions: Read and write** 的 fine-grained PAT，将其存入翻译仓库 `Settings → Environments → localization-release` 下的 `PATCHER_DISPATCH_TOKEN` Secret。Patcher 自身的附件发布使用内建 `GITHUB_TOKEN`；令牌到期后更新同名 Secret。

构建固定插件 APK 的 SHA-256，使用同一原版签名基准。全部验证通过后先上传草稿 Release，核对服务器端大小和 SHA-256，再正式发布；失败草稿可重试，不覆盖已发布版本。发布仅包含允许的安装文件和报告，不包含原始下载地址或私钥。

字体与 UI 汉化图片由插件提供，Patcher 校验嵌入插件和原版 APK 的字节，并检查外层游戏资源保持不变。图片更新随插件版本分发；关闭翻译并重启游戏可恢复原图。此前烘焙过图片的安装包需要用原版 APK 重新修补。

生成签名密钥的 Base64 中转文件，填入 Secret 后删除：

```sh
python3 -c "import base64,pathlib; p=pathlib.Path('secrets'); (p/'patcher.b64').write_bytes(base64.b64encode((p/'patcher.jks').read_bytes()))"
```

## 原版来源与校验

推荐从 [Google Play](https://play.google.com/store/apps/details?id=game.qualiarts.idolypride) 安装后提取原包。ZIP／XAPK 必须包含同版本的完整 APK 集合；不接受单个 APK 直链或 OBB 扩展包。支持 HTTPS 重定向，但不能处理需要登录、验证码的下载页。

`game-reference.json` 仅包含公有签名证书指纹和 APK 哈希：

- `signature`：验证 APK 数字签名有效，证书与可信基准一致。官方沿用同一密钥时可验证新版本，不要求文件相同。
- `exact`：另要求版本和全部拆分 APK 的 SHA-256 与基准一致；适用于同一分发变体。

签名相同不等于下载渠道是 Google Play。官方轮换签名密钥时需重新确认，不能自动信任下载包中的新证书。Google Play 公开页面未提供本工具可用的校验清单；[Developer API](https://developers.google.com/android-publisher/api-ref/rest/v3/generatedapks/list) 需要对应应用的开发者权限。

```sh
python3 game_source.py verify --archive /path/to/game.xapk
python3 game_source.py verify --archive /path/to/game.xapk --mode exact
python3 game_source.py record --serial 设备序列号 --output new-reference.json
```

记录基准时从设备只读复制 APK，并检查安装来源为 Play；来源记录仅作辅助，设备和安装过程也应可信。核对后替换 `game-reference.json`，不上传账号或设备信息。

## 本地可选参数

- `--module-tag`：指定正式插件版本；`--module /path/to/plugin.apk` 使用本地插件。
- `--game-check exact`：与 `--game-reference` 一起使用，要求精确匹配。
- `--allow-untested-version`：允许尝试其他游戏版本，不绕过其余校验。
- `--output output/new-version`：使用新的输出目录。

`inputs/`、`cache/`、`output/`、`dist/`、`secrets/` 与本地测试文件均由 `.gitignore` 排除。
