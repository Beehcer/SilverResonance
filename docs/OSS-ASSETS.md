# 前端静态资源

Bucket：silver-resonance，杭州。Bucket ACL保持private，已按用户授权关闭桶级阻止公共访问。仅 `workspace/<内容指纹>/` 中经白名单筛选的JS/CSS对象设置public-read；不上传账户、配置、交易规则、计划、历史行情、服务器Python或模板。

`scripts/publish_workspace_assets.py` 读取本机 `.ossutilconfig`，凭据不进入浏览器、Git或发布包。上传25项静态资源，包括预生成的双窗运行脚本及图表适配脚本。gzip、明确内容类型、immutable一年缓存；禁止覆盖同名对象，匿名读取并校验SHA-256全部成功后才写 `static/workspace-assets.json`。本次770121字节压缩至221674字节。

页面入口与API继续同源，`workspace_web.render_asset_page` 根据发布包中的清单改写静态资源URL。接口、Cookie、SSE、iframe和多人计划仍在原服务；服务端保留原静态文件。回退旧代码版本会恢复旧资源入口，保留最新用户数据；不要删除旧OSS版本。前端有新变更时先重新运行发布脚本，测试清单与源码一致，再构建发布包。运行服务器不需要OSS凭据。

OSS直出用于减少静态传输与重复下载，不代表已启用CDN，也不会消除行情首次订阅、完整历史和协作连接的等待。页面传输体积减少比例不等于端到端加载提速比例。

非编辑区域统一隐藏文字插入光标；仅文本、数字、日期时间输入及正文编辑器保留。颜色、滑杆、下拉框、按钮保持键盘焦点和原操作，不使用强制失焦或屏蔽键盘。

参考：阿里云 [Bucket阻止公共访问](https://www.alibabacloud.com/help/en/oss/developer-reference/putbucketpublicaccessblock)、[网站引用OSS资源](https://www.alibabacloud.com/help/en/oss/how-to-apply-oss-resources-in-the-site)。
