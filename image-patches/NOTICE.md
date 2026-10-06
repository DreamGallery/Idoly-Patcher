# UI 图片包

包含游戏 **6.0.2** 版本 HomeAtlas 的 9 个首页按钮、GachaAtlas 的 2 个扭蛋按钮、PhotoAtlas 的 360°LIVE“设置”按钮、CommonAtlas 的横竖“已选择”及“期间限定”，以及 LoveAtlas 和 LoveADVAtlas 的若恋按钮（含结局达成情况）、若恋标题、剧情控制标签与选项“已选择”标记。还包含工作页的“核心粉丝率提升中”“粉丝获取中”“体力恢复中”状态标签。共 6 张纹理、33 个审核区域，包括 2 像素边缘采样留白。

这里只分发需要替换的区域 PNG，不分发完整图集、游戏 APK 或缓存。区域来自《IDOLY PRIDE／偶像荣耀》游戏美术的本地化修改，原游戏美术与商标权利归各自权利人；GPL 不代表对原游戏美术授予许可。中文字形由 Resource Han Rounded SC Bold 渲染，字体项目采用 SIL Open Font License 1.1，本仓库不分发字体文件。来源：https://github.com/CyanoHao/Resource-Han-Rounded 。

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
