# UI 图片包

`ui-6.0.2` 来自 DreamGallery/Idoly-localify 的已审核精准像素修改素材：2026-10-01 最终边缘清理合并清单（源清单 SHA-256 记录于 manifest）。对应工具源码版本 `aa795011ff0399ced3eff0d0607d97db8ef86274`；上游为私有仓库，本仓库自带执行所需代码与区域 PNG，无需访问上游或固定本机路径。

包含 HomeAtlas 的 9 个首页按钮、GachaAtlas 的 2 个扭蛋按钮、CommonAtlas 的横竖“已选择”，以及 LoveAtlas 和 LoveADVAtlas 的若恋按钮、若恋标题与剧情控制标签。共 5 张纹理、26 个审核区域，包括 2 像素边缘采样留白。“第 N 话进行中”由 Idoly-localify 插件运行时处理；本包不修改该动态提示，建议使用最新正式插件。

这里只分发需要替换的区域 PNG，不分发完整图集、游戏 APK 或缓存。区域来自《IDOLY PRIDE／偶像荣耀》游戏美术的本地化修改，原游戏美术与商标权利归各自权利人；GPL 不代表对原游戏美术授予许可。中文字形由 Resource Han Rounded SC Bold 渲染，字体项目采用 SIL Open Font License 1.1，本仓库不分发字体文件。来源：https://github.com/CyanoHao/Resource-Han-Rounded 。

`patch_images.py` 及相关区域校验测试改编自 DreamGallery/Idoly-localify 的同名工具，保留 GPL-3.0，全文见仓库根目录 LICENSE。导出工具与打包集成采用同一代码许可证。

## 更新素材

准备原版 APK 和已审核 schema-1 完整图集清单，在本仓库执行：

```sh
python3 tools/export_image_pack.py --base /path/to/base.apk \
  --manifest /path/to/reviewed/manifest.json --output image-patches/new-pack
python3 patcher.py check-images --base /path/to/base.apk \
  --image-manifest image-patches/new-pack/manifest.json
```

导出必须通过原资源哈希、PNG 哈希和逐通道区域边界校验。schema-2 清单记录原 Unity 资源哈希、纹理 ID/名称、区域 PNG 哈希以及完整目标 RGBA 像素哈希。打包只在原图上回填这些区域；不会重新绘字或调用图片生成服务。更新哈希不能代替图像审核和游戏内检查。

修补采用 RGBA32，无损保留审核像素，APK 和纹理内存占用会增加。LSPatch 先读取原版签名，再更新外层资源及嵌入的 origin.apk，嵌入前对 origin.apk、最终签名前对外层 APK 执行 zipalign（4 字节资源对齐、16 KB 原生库页面对齐）；签名模拟配置保持原版。最终再次核对外层及提取后的 origin.apk 对齐。流程核对全部其他 ZIP 条目、Unity 对象，以及最终内外层纹理。
