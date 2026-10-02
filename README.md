# Idoly-Patcher

将《IDOLY PRIDE／偶像荣耀》日服原版 APK 与 [Idoly-localify](https://github.com/DreamGallery/Idoly-localify-translations) 插件封装为无需 Root 的安装包，支持本地命令和 GitHub Actions。

字体由插件在运行时提供，默认加入已审核的首页、扭蛋圆形按钮、已选择标签及若恋 UI 汉化图片。支持 ARM64 游戏包，已验证打包版本为 **6.0.2**；其他版本需确认插件兼容性，x86 模拟器需支持 ARM64 转译。

## 本地使用

需要 Python 3.11+、JDK 17+ 和 Android SDK Build Tools 35.0.0+（`aapt`、`apksigner`、`zipalign`）。设置 `JAVA_HOME`、`ANDROID_HOME`；提取和安装时还需要 PATH 中有 `adb`。

1. 将原版 `base.apk` 和 **同版本的全部拆分 APK** 放入 `inputs/game/`，或从已安装原版游戏的设备提取：

   ```sh
   python3 patcher.py pull --serial 设备序列号
   ```

   **设备序列号**是 ADB 区分手机或模拟器的标识，不是游戏账号 ID。连接设备后运行 `adb devices`：

   ```text
   List of devices attached
   emulator-5560    device
   ```

   此例中填写 `--serial emulator-5560`。安装命令的 `-s` 也填写同一标识；模拟器重启后可能变化，以当前查询结果为准。

2. 在本机设置 `IDOLY_KS_PASS` 环境变量作为签名密码，然后生成一次自己的密钥：

   ```sh
   python3 patcher.py init-key
   ```

   密钥保存于 `secrets/patcher.jks`。请备份密钥和密码，后续覆盖升级必须沿用。

3. 安装图片修补依赖后，一条命令修补：

   ```sh
   python3 -m pip install -r requirements-images.txt
   python3 patcher.py patch
   ```

   自动下载并校验 LSPatch 和最新正式插件。可加 `--module-tag v0.2.4` 指定插件版本，或 `--module /path/to/plugin.apk` 使用本地插件。

   图片只接受游戏 6.0.2 中哈希完全匹配的原始资源；资源缺失或不兼容会停止，`--allow-untested-version` 不会绕过此检查。加 `--no-ui-images` 可保留原图，且无需图片依赖。只安装插件不会替换这些图片。

   不下载、不签名的图片预检：`python3 patcher.py check-images --base inputs/game/base.apk`。更换经审核的图片包可用 `--image-manifest /path/to/manifest.json`；图片包格式与来源见 [说明](image-patches/NOTICE.md)。

4. 结果位于 `output/`，包含 APK、校验报告和安装脚本：

   ```sh
   sh output/install.sh -s 设备序列号
   ```

   Windows 使用 PowerShell：`./output/install.ps1 -s 设备序列号`。有多份拆分 APK 时必须一起安装，不能只装 `base.apk`。

修补包签名与商店原版不同，首次不能覆盖安装。请先绑定账号并确认能重新登录，再自行卸载原版；工具不会自动卸载。再次打包可用 `--output output/new-version` 避免覆盖已有产物。

## GitHub Actions

Fork 或复制本仓库，在自己的仓库中打开 Actions → **Patch game APKs** → Run workflow。使用 GitHub 托管运行器，无需自托管服务器。

将原版 APK 集合打成可通过 HTTPS 直链下载的 ZIP，在 Settings → Secrets and variables → Actions 配置：

| Secret | 内容 |
| --- | --- |
| `GAME_APKS_URL` | APK ZIP 的下载地址，可使用短期签名地址 |
| `PATCH_KEYSTORE_BASE64` | 上面生成的 `patcher.jks` 的 Base64 内容 |
| `PATCH_KEYSTORE_PASSWORD` | 密钥库密码，与本机 `IDOLY_KS_PASS` 相同 |
| `PATCH_KEY_PASSWORD` | 可选，私钥密码与密钥库密码不同时设置 |

密钥别名默认 `idoly-patcher`，可用仓库 Variable `PATCH_KEY_ALIAS` 修改。请使用自己的签名密钥。

生成 Base64 文件，填入 Secret 后删除中转文件：

```sh
python3 -c "import base64,pathlib; p=pathlib.Path('secrets'); (p/'patcher.b64').write_bytes(base64.b64encode((p/'patcher.jks').read_bytes()))"
```

插件版本留空即选最新正式版。`ui_images` 默认开启，关闭等同本地 `--no-ui-images`。完成后下载 `idoly-patched-*` Artifact，按上述步骤安装；产物保留 **1 天**。公开仓库的 Artifact 可被他人下载，含游戏文件的打包操作建议使用私有副本或在本地完成。

`inputs/`、`cache/`、`output/`、`secrets/` 均排除 Git 提交。

代码采用 GPL-3.0；图片区域、字体及原游戏美术的归属见 [来源说明](image-patches/NOTICE.md)。使用 [JingMatrix/LSPatch](https://github.com/JingMatrix/LSPatch) 的官方发布包，未内置完整游戏 APK、完整图集、字体文件或个人密钥。
