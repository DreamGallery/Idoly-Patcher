# Idoly-Patcher

将《IDOLY PRIDE／偶像荣耀》日服原版 APK 与 [Idoly-localify](https://github.com/DreamGallery/Idoly-localify-translations) 插件封装为无需 Root 的安装包，支持本地修补和 GitHub Actions 发布。

字体与 UI 汉化图片由插件在运行时提供。Patcher 保留游戏原始资源，只负责嵌入插件、签名和校验。适配游戏版本为 **6.0.2**；其他版本需确认插件兼容性，x86 模拟器需支持 ARM64 转译。

## GitHub Actions

Fork 或复制本仓库，打开 **Actions → Patch game APKs → Run workflow**。使用 GitHub 托管运行器，可选择游戏版本、插件版本及是否发布。

在 **Settings → Secrets and variables → Actions** 配置：

| Secret | 内容 |
| --- | --- |
| `GAME_APKS_URL` | 可选，覆盖默认 APKPure 最新版来源的 ZIP／XAPK HTTPS 直链 |
| `PATCH_KEYSTORE_BASE64` | 自己生成的签名密钥 `patcher.jks` 的 Base64 内容 |
| `PATCH_KEYSTORE_PASSWORD` | 密钥库密码 |
| `PATCH_KEY_PASSWORD` | 可选，私钥密码与密钥库密码不同时填写 |

插件默认使用最新正式版；游戏默认从 [APKPure 最新版入口](https://d.apkpure.com/b/XAPK/game.qualiarts.idolypride?version=latest) 下载。下载后校验官方签名和版本；版本不匹配时停止，需维护者确认适配后更新 `GAME_VERSION`。

修补完成后发布 APK 集合 ZIP、校验报告和 `SHA256SUMS`；Artifact 另保留 1 天。解压 ZIP 后使用其中的安装脚本，必须一起安装全部 APK。

### 自动跟随插件发布

翻译仓库成功发布新版插件后，会直接触发本仓库修补。

游戏版本和校验方式可通过 Patcher 的仓库 Variables 配置，详见 [使用说明](docs/usage.md)。

## 本地修补

需要 Python 3.11+、JDK 21+、Android SDK Build Tools 35.0.0+。配置 `JAVA_HOME`、`ANDROID_HOME`，将 `adb` 加入 PATH。

1. 将原版 APK 集合放入 `inputs/game/`，XAPK 可直接解压；也可从设备提取：

   ```sh
   python3 patcher.py pull --serial 设备序列号
   ```

   设备序列号是 `adb devices` 中的标识，例如 `emulator-5554`。

2. 设置环境变量 `IDOLY_KS_PASS` 作为签名密码，然后执行：

   ```sh
   python3 patcher.py init-key
   python3 patcher.py patch --game-reference game-reference.json
   ```

3. 安装 `output/` 中的结果：

   ```sh
   sh output/install.sh -s 设备序列号
   ```

   Windows 使用 `./output/install.ps1 -s 设备序列号`。请备份 `secrets/patcher.jks` 和密码，后续覆盖升级必须沿用。

首次修补包与商店原版签名不同，请先绑定账号并确认可重新登录，再自行卸载原版；工具不会自动卸载。

## 原包校验与许可

Action 使用 [Google Play 原版基准](game-reference.json) 验证 APK 签名。默认 `signature` 模式可复用旧版本证书校验新版；`exact` 模式还要求版本及每个 APK 的哈希完全一致。基准更新和命令参数见 [使用说明](docs/usage.md)。

代码采用 GPL-3.0，使用 [JingMatrix/LSPatch](https://github.com/JingMatrix/LSPatch)；游戏资源和插件中的字体、图片遵循各自许可。安装包、缓存和私钥不提交源码仓库。公开仓库的 Release 会公开修补后的游戏文件；需要限制访问时使用私有副本或本地修补。
