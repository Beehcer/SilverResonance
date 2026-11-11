# 当前线上工作台

2026-11-11 已发布：https://resonance.cg-x.com/workspace

用户明确要求公开访问，无需账号和密码。打开页面后自动加入共同画板。主机设置、行情配置、旧接口及健康接口仍不对公网开放；同源校验继续生效。

## 当前运行位置

- 服务器：47.96.180.144，使用既有宝塔 Nginx。
- DNS：阿里云，resonance A 记录指向服务器，TTL 600。
- 源码版本：`/opt/SilverResonance/releases/20261111-52af178a7141`。
- 当前版本链接：`/opt/SilverResonance/current`。
- 镜像：`silver-resonance:20261111-52af178a7141`。
- 容器：`silver-resonance-workspace-1`，单进程，监听 `127.0.0.1:8850`，自动重启。
- 配置与真实计划：`/var/lib/SilverResonance`，专用 UID 10001，仅该用户可读写。
- 规则文件：`/etc/SilverResonance/trading-system.md`。
- 运行覆盖配置：`/etc/SilverResonance/compose.runtime.yaml`，注入 `TZ=CST-8`，使基本面更新时刻按北京时间计算。
- 当前站点配置：`/www/server/panel/vhost/nginx/resonance.cg-x.com.conf`。已按用户要求去掉密码验证；当前站点配置优先于发布包内最初的认证模板。
- 证书引用现有 `blog.ipao.tv` 目录的通配证书，保留原续期方式。
- Nginx 对 K 线 JSON 和静态资源启用压缩，协作事件流保持不压缩且不缓冲。
- 依赖快照：`/opt/SilverResonance/staging/dependencies.lock.txt`。

本机原来的真实行情进程已停止，避免同一账号重复连接。原 `127.0.0.1:8850` 地址在本机转跳线上工作台；该转跳进程只在本机运行时有效。长期请收藏 HTTPS 地址，或使用仓库根目录的 `打开在线工作台.bat`。模拟计划仍保留在本地 `state/workspace-preview.json`，未套用到真实价格。

## 已完成验收

- 公网 HTTPS 无凭据访问 200；两个全新匿名会话测试通过。
- 黄金、白银、螺纹三个周期真实行情均可用；主力合约来源为实际行情连接。
- 绘图、区域颜色、标题、备注、浪点拖拽及插入删除、序号更新、鼠标、拖拽预览和视角协作通过。
- 无凭据完整验收 13 项全部通过，事件通知实测约 1.7 秒。
- 缺少 CSRF 与跨源写请求均被拒绝，默认计划在验收前后保持一致。
- 两轮验收仅使用独立 QA 画板，结束后已清空并归档。
- 验收时为午间休市，三个品种最后报价参考为 11:30、3 分钟末根 11:27。验证了真实连接及 K 线加载，尚未在本次验收中观察午后连续跳价。

验收后三品种订阅时容器约716 MiB / 1 GiB，无 OOM、无重启。更多同时订阅品种仍需继续观察资源。

## 维护命令

在服务器执行，勿同时启动本地同账号的真实行情进程：

```sh
cd /opt/SilverResonance/current
export SILVER_RELEASE=20261111-52af178a7141
docker compose -f deploy/compose.yaml -f /etc/SilverResonance/compose.runtime.yaml ps
curl --fail --silent http://127.0.0.1:8850/healthz
docker stats --no-stream silver-resonance-workspace-1
```

更新前备份私有数据目录，保留旧镜像；新版本必须继续使用公开免密码配置。不要把旧认证模板覆盖到线上，也不要覆盖其他站点。
