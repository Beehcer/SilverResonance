# SilverResonance 部署交接

核查日期：2026-11-11。已完成公网发布，当前维护记录见 [LIVE-DEPLOYMENT.md](LIVE-DEPLOYMENT.md)。本文为通用部署参考，不包含服务器管理凭据或行情账号。

## 当前验证结果

- 本地天勤真实行情连接成功，行情源已将白银主力更新为 SHFE.ag2702。
- 白银、黄金、螺纹的日线、30 分钟、3 分钟数据均已返回；午间核查时最新 3 分钟 K 为 11:27，30 分钟为 11:00。
- 前端约每 5 秒核对数据，并非逐笔成交终端。页面会分别显示 K 线时间和检查时间。
- 连接正常但没有新报价时，保留历史 K 线和计划功能；不因为重复读取旧数据而放宽开仓核对条件。
- 现有模拟计划保留在 `state/workspace-preview.json`；真实工作区使用 `state/workspace.json`，避免把模拟价位直接套到真实合约。
- https://resonance.cg-x.com/workspace 已公开免密码上线，DNS、HTTPS、真实行情和双人协作均已验证。

## 已有服务器适配

通过正常宝塔登录后只读检查服务器 A，实测如下：

| 项目 | 结果 |
| --- | --- |
| 地址 | 47.96.180.144 |
| 系统 | Ubuntu 20.04.6 LTS |
| 内存 | 总约 3.69 GiB，核查时可用约 1.64 GiB |
| 磁盘 | 可用约 26.3 GB |
| 系统 Python | 3.8；宝塔自带为 3.7 |
| 可复用基础设施 | Docker、Compose、systemd、现有 Nginx |
| 本项目目录 | `/opt/SilverResonance` 未占用 |
| 本项目端口 | `127.0.0.1:8850` 未占用 |
| 通配证书 | 覆盖 `*.cg-x.com`，有效至 2027-01-23 04:45:24 UTC |
| 已选域名 | `resonance.cg-x.com`，已新增 A 记录并验证 |

证书公开链位于 `/www/server/panel/vhost/cert/blog.ipao.tv/fullchain.pem`，配套私钥路径由现有 Nginx 指令确认为同目录的 `privkey.pem`；未读取私钥内容，也不复制私钥到代码仓库。现有其他项目、端口和停用站点保持原状。

## 建议架构

独立子域名 → 现有 Nginx 的 HTTPS → 本机 8850 → 独立 Python 3.11 容器。

- 单容器、单进程、多线程：共享一条行情连接及协作者指针、缓存；不要复制多个行情进程。
- Linux 容器使用 host 网络，但应用只监听 `127.0.0.1`。不向公网开放 8850。
- 代码与可写数据分离：代码 `/opt/SilverResonance`，数据 `/var/lib/SilverResonance`，规则与访问文件 `/etc/SilverResonance`。
- `SILVER_DATA_DIR` 包含 `user_config.json` 和整个 `state/`。保护目录权限，凭据不可置于 Nginx 静态目录。
- `SILVER_RULES_PATH` 指向服务器上的交易规则 Markdown。生产启动检查规则版本与实现兼容性。
- 按用户要求公开免密码；每位伙伴打开即获得浏览器协作者身份。
- Nginx 只公开工作台、工作台接口和静态文件；主机设置、旧接口与健康检查不向公网开放。
- 协作事件流关闭代理缓冲；HTTPS 协议信息只接受本机可信代理，保留同源校验。

## 本地真实行情

1. 停止之前的模拟面板和临时行情验证进程，确保同端口和账号没有重复实例。
2. 使用已有的本地 `user_config.json`。不要把该文件发到聊天或提交 Git。
3. 安装 `requirements-server.txt` 中依赖，运行 `python serve_workspace.py --check` 做配置检查。
4. 双击 `start-workspace-live.bat`，或运行 `python serve_workspace.py --port 8850`。
5. 打开 `http://127.0.0.1:8850/workspace`，确认真实来源、合约、K 线时间与各周期显示。

模拟演练入口仍为 `start-workspace-preview.bat`，与真实服务不能同时占用 8850。

## 发布顺序

1. 在阿里云万网 DNS 为 `resonance` 新增 A 记录，指向 `47.96.180.144`。权威 NS 为 dns31/dns32.hichina.com；此记录已配置并验证。
2. 构建部署包，检查清单和 SHA256；包中不含本地凭据、计划、行情状态、个人文件与测试产物。
3. 在独立目录解包，使用随包 Dockerfile 创建 Python 3.11 镜像，不改变服务器系统或宝塔 Python。
4. 在服务器私有目录放置行情配置与规则文件；如需迁移真实计划，只迁移真实 `state/`，不要默认导入模拟画板。
5. 停止使用同一行情账号的旧行情实例，先运行生产入口 `--check`，再启动单实例。
6. 通过服务器回环地址检查进程健康、行情线程存活和各周期数据；休市暂无新报价不算进程故障。
7. 配置独立 Nginx 站点、证书。先检查配置，再平滑重载 Nginx。
8. 两个浏览器分别验证：免密码进入、画线/拖拽/改色/备注同步、指针同步、断线重连，以及重新进入后的计划恢复。
9. 在开盘时验证报价与 K 线推进；确认其他站点仍可访问后完成发布。

## 运行与回滚

- 备份整个私有数据目录，并单独保护凭据和规则；发布前后记录版本与检查结果。
- 行情线程退出时由运行入口结束服务，让容器或 systemd 重启。健康状态区分页面存活与行情线程，不以休市旧报价判断崩溃。
- 行情历史缓存目前在内存中，重启需要向行情源重新加载；JSON 持久保存的是协作计划等状态，不能当作完整离线 K 线库。
- 协作事件流占用线程，初期按小规模协作验证；当前服务器资源核查不等同于压力测试通过。
- 回滚先停止当前单实例，再使用上一版本镜像与兼容的数据备份；不要同时启动两个版本连接同一账号。
- 通配证书更新沿用现有续期机制，上线后仍需确认新站点引用的证书同步更新。

## 参考

- Flask 生产部署：https://flask.palletsprojects.com/en/stable/deploying/
- Waitress 单进程线程服务：https://flask.palletsprojects.com/en/stable/deploying/waitress/
- 可信代理：https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/
- Nginx 代理及事件流缓冲：https://nginx.org/en/docs/http/ngx_http_proxy_module.html
