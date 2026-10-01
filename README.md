# Idoly-Patcher

将《IDOLY PRIDE／偶像荣耀》日服原版 Android APK 与 [Idoly-localify](https://github.com/DreamGallery/Idoly-localify-translations) 插件封装为无需 Root 的 LSPatch 安装包。支持本地命令与 GitHub Actions，不依赖其他本地项目。

字体由插件在运行时提供，游戏原版字体和图片资源保持不变。当前支持 ARM64 游戏包，已验证打包的游戏版本为 **6.0.2**。其他版本可显式放行，能成功打包不代表插件钩子已适配；x86 模拟器需要支持 ARM64 转译。

## 本地使用

需要 Python 3.11+、JDK 17+ 和 Android SDK Build Tools（`aapt`、`apksigner`）。设置 `JAVA_HOME`、`ANDROID_HOME`；提取和安装时还需要 PATH 中有 `adb`。

1. 把自己取得的原版 `base.apk` 和 **同版本的全部拆分 APK** 放入 `inputs/game/`。也可从已安装原版游戏的设备读取安装包（不读取账号数据）：

   ```sh
   python3 patcher.py pull --serial 设备序列号
   ```

2. 在本机设置 `IDOLY_KS_PASS` 环境变量作为签名密码，然后生成一次自己的密钥：

   ```sh
   python3 patcher.py init-key
   ```

   密钥保存于 `secrets/patcher.jks`。以后升级必须沿用它和原密码，请私下备份；不要提交到 Git。

3. 一条命令修补：

   ```sh
   python3 patcher.py patch
   ```

   自动下载固定版本并校验 SHA-256 的 JingMatrix LSPatch，自动选择翻译仓库的最新正式插件 APK（不会把文本 Release 当作插件）。也可加 `--module-tag v0.2.1` 指定版本，或 `--module /path/to/plugin.apk` 使用已有插件。

4. 结果位于 `output/`，包含 APK、校验报告和安装脚本：

   ```sh
   sh output/install.sh -s 设备序列号
   ```

   Windows 使用 PowerShell：`./output/install.ps1 -s 设备序列号`。有多份拆分 APK 时必须一起安装，不能只装 `base.apk`。

修补签名与商店原版不同，首次不能覆盖原版；请先确保账号已绑定、可以重新登录，再自行处理原版安装。工具不会自动卸载或操作设备上的游戏。后续使用同一密钥修补即可覆盖升级。已有产物不会被覆盖，可用 `--output output/new-version` 指定新目录。

## GitHub Actions

在自己的副本中启用 Actions → **Patch game APKs** → Run workflow。当前仓库为私有，暂不面向公共 Fork；公开后用户可以 Fork 使用。工作流使用 GitHub 的临时 Ubuntu x86 运行器，无需配置自托管设备。

先把自己的原版 APK 集合打成 ZIP，放在可通过 HTTPS 直接下载的位置，然后在仓库 Settings → Secrets and variables → Actions 配置：

| Secret | 内容 |
| --- | --- |
| `GAME_APKS_URL` | APK ZIP 的下载地址，可使用短期签名地址 |
| `PATCH_KEYSTORE_BASE64` | 上面生成的 `patcher.jks` 的 Base64 内容 |
| `PATCH_KEYSTORE_PASSWORD` | 密钥库密码，与本机 `IDOLY_KS_PASS` 相同 |
| `PATCH_KEY_PASSWORD` | 可选，私钥密码与密钥库密码不同时设置 |

密钥别名默认 `idoly-patcher`，使用已有其他密钥时可设置仓库 Variable `PATCH_KEY_ALIAS`。不要使用插件官方签名密钥；修补游戏使用自己的密钥。

可在本机生成用于填写 Secret 的 Base64 文件，填写后删除该中转文件：

```sh
python3 -c "import base64,pathlib; p=pathlib.Path('secrets'); (p/'patcher.b64').write_bytes(base64.b64encode((p/'patcher.jks').read_bytes()))"
```

运行时可填写插件版本，留空选最新正式插件。下载 Actions 的 `idoly-patched-*` Artifact 后按本地安装步骤安装；产物只保留 **1 天**，不自动创建 Release。ZIP 和修补包包含游戏文件，公开仓库的 Artifact 可被有访问权限的人下载，请使用私有副本或本地修补。

## 文件

| 路径 | 内容 |
| --- | --- |
| `patcher.py` | 下载、校验、签名及封装工具 |
| `ci_prepare.py` | Actions 私有输入准备 |
| `.github/workflows/patch.yml` | 手动修补入口 |
| `tests/` | 输入、签名和输出校验测试 |
| `inputs/`、`cache/`、`output/`、`secrets/` | 本地生成，全部排除提交 |

代码采用 GPL-3.0。使用 [JingMatrix/LSPatch](https://github.com/JingMatrix/LSPatch) 的官方发布包，未内置游戏、第三方二进制或个人密钥。
