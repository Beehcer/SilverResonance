# 共享工作台部署交接

已于2026-11-11发布；当前线上状态和维护命令见 ../docs/LIVE-DEPLOYMENT.md。用户明确要求公开免密码访问。目标为现有 Ubuntu 20.04 + 宝塔 Nginx + Docker 服务器。系统 Python 3.8 和宝塔 Python 3.7 不用于本项目；优先使用独立 Python 3.11 容器。继续保留已有网站和宝塔管理入口。

## 运行边界

- 一个容器、一个 Python 进程、一个行情线程，24 个 HTTP 工作线程支持多人协作和 SSE。不能加副本、不能用多个 WSGI worker；目前行情缓存、在线成员和广播存在进程内。
- `serve_workspace.py` 只监听 `127.0.0.1:8850`，强制真实行情。桌面 `mock_mode=true` 不会改变生产运行模式；缺失行情账号、密码、合约或交易规则不启动。该程序拉行情、审阅计划，不执行实盘订单。
- Nginx 独立域名 + HTTPS；公开免密码，打开即加入共同画板。协作者身份用于区分编辑者，同源与 CSRF 校验保留。
- 所有配置、标记、计划、协作用户、缓存、账本放到 `/var/lib/SilverResonance`。私有规则及入口配置放到 `/etc/SilverResonance`。源码版本放到 `/opt/SilverResonance/releases/<commit>`，`current` 指向当前版本。不要把数据或秘密放进宝塔 `wwwroot`、Git、镜像、工单或命令输出。
- Docker 使用 Linux `network_mode: host`，没有 `ports` 映射，Waitress 的监听仍只有 loopback。这样本机 Nginx 的来源仍为 `127.0.0.1`，无需信任网桥或 `*`。不要改成全网卡监听。模板不支持直接搬到 Docker Desktop 后以端口映射方式使用。
- 仅 `--behind-proxy` 信任 loopback 来源的 `X-Forwarded-Proto`，只修正协议，不信任转发主机、客户端 IP 或前缀。Nginx 覆盖 Host 和协议头，使 HTTPS Cookie 和同源校验一致；不放宽 CSRF 校验。
- 行情线程结束时，看门狗在约 5 秒内让整个进程退出，容器重启。正常休市、没有新成交或 SDK 正在重连不因此重启。健康接口检测线程存活，不代表数据足够新或可开仓；真实行情状态仍以面板显示为准。
- NumPy/SciPy 等计算库固定为 1 个内部计算线程，为协作和行情线程留出余量。镜像构建包含依赖导入及 V8 引擎自检，不使用行情账号、不构造 TqApi。SDK 的用户缓存使用容器用户家目录 `/var/lib/SilverResonance`，与只读源码分开。

## 上线前补齐

1. 用户已确定专用域名 `resonance.cg-x.com`；2026-11-11 已在阿里云 DNS 添加 A 记录并核实解析，指向 47.96.180.144。模板引用已只读核实的 `/www/server/panel/vhost/cert/blog.ipao.tv/fullchain.pem` 和配套 `privkey.pem`；没有读取私钥内容。复用证书路径应保留现有续期流程，并在续期后核验新站点证书。不能覆盖现有站点。公开入口已完成匿名访问和双人协作验收。
2. 确认行情账号授权范围、所需品种权限及从服务器连接的可用性。同账号从本机迁到服务器时先停止本机真实行情进程，避免重复会话；本机模拟预览不受影响。
3. 准备完整的当前交易规则原文件，不改编码/换行；规则版本必须和代码核对一致。规则文件不会自动从个人 Downloads 目录上传。
4. 确认数据目录的备份位置与保留周期。确认 UID/GID `10001` 未与服务器现有账号冲突；有冲突时同时调整 Dockerfile、Compose 和目录属主。
5. 服务器当前余量约 1.64 GiB，模板限制容器 1 GiB。需在业务负载下测量实际内存，观察是否影响已有网站，不要扩容副本规避内存问题。

## 准备版本与外部数据

以下是后续发布操作参考；已部署服务的准确命令优先使用 ../docs/LIVE-DEPLOYMENT.md。发布前先记下 `current` 指向、镜像标签及已有服务状态。构建使用提交 SHA 标签，不能用会被覆盖的 `latest`。不要运行 `docker system prune`。

```sh
# 管理员准备独立目录；不要递归修改已有站点权限。
install -d -m 0755 /opt/SilverResonance/releases
install -d -o 10001 -g 10001 -m 0700 /var/lib/SilverResonance
install -d -m 0750 /etc/SilverResonance
```

将经过审阅的 Git 提交检出至 `/opt/SilverResonance/releases/<commit>`。将 `deploy/user_config.example.json` 的字段填写到 `/var/lib/SilverResonance/user_config.json`，只填写真实行情凭据、有效合约及 `mock_mode: false`，权限 `0600`、属主 `10001:10001`。不要把密码写在 shell 命令参数里。将用户规则原文件放到 `/etc/SilverResonance/trading-system.md`，服务用户须可读，容器只读挂载。本项目按用户要求不设置访问密码。

如要延续本地计划，停写后备份并迁移**整个** `state` 目录；浏览器身份保存在 Cookie 中，用户首次打开新域名会获得新的浏览器身份，不能把此前的私人风险设置当作已自动迁移。不要将预览工作台误当正式工作台导入。

根目录 `.dockerignore` 采用白名单：真实 `user_config.json`、`state`、规则文件及其他本地资料不进入构建上下文。镜像仅含代码、静态页面和运行依赖。主要应用依赖锁定为已验证版本，另含固定 Waitress 3.0.2；实际发布时保留构建日志和 `pip freeze`，下一版本应在回归通过后更新依赖，不能静默重建覆盖旧镜像标签。

## Docker 预检与启动

在目标 release 目录执行，`SILVER_RELEASE` 填当前提交 SHA。预检不会导入应用或连接行情，只会读取配置、校验规则、短暂测试目录写权限。缺少配置、规则不匹配或依赖不足会以非零退出，日志不输出账号密码。

```sh
export SILVER_RELEASE=<commit>
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml build workspace
docker compose -f deploy/compose.yaml run --rm --no-deps workspace python serve_workspace.py --check
```

确认本机真实行情进程已停止，且服务器 `127.0.0.1:8850` 未被占用后，再启动单个服务。启动器先取得数据目录运行锁并绑定端口，随后才连接行情；相同数据目录的第二个生产实例会被拒绝。

```sh
docker compose -f deploy/compose.yaml up -d --no-deps workspace
curl --fail --silent http://127.0.0.1:8850/healthz
docker compose -f deploy/compose.yaml ps
docker stats --no-stream
```

先核实本机健康和行情，再在宝塔增加独立站点，按 `nginx-workspace.conf.example` 配置证书及允许的代理路径。使用现有宝塔 Nginx 的配置检查命令，通过后才重载该 Nginx；不要安装/启动第二个 Nginx，不要改宝塔自身配置。模板允许匿名工作台访问，只允许 `/`、`/workspace`、`/static/`、`/api/workspace/`，拒绝 `/setup`、`/monitor`、旧接口和公开健康接口。全站 HTTPS 下完成双人实测。

## 验收、监控与回退

正式入口自动备份实际共享工作区到 `/var/lib/SilverResonance/backups/workspace/`：内容变化最多每30秒一份、每30分钟定时一份，分别保留40份与10份。该目录必须保持私有；检查其中 `status.json` 的成功时间与失败状态。范围、恢复和容量限制见 [工作区自动备份](../docs/WORKSPACE_BACKUP.md)。独立的发布前私有备份不计入自动保留份数。

- 两个浏览器通过 HTTPS 进入：输入区域标题、改颜色、拖点、光标移动，双方及时可见；刷新仍保留。SSE 无代理缓冲，自动断线续接；不要凭 HTTP 200 判定协作成功。
- 检查 HTTPS 请求会发安全 Cookie，同源修改成功，跨源修改被拒绝；直访 `/setup` 和旧账本接口被拒绝；公网不能访问 8850。
- 逐一核实目标合约代码、行情来源、K线末根时间、收盘确认状态；休市保留最后行情但不冒充实时触发。正式验收须覆盖一个开盘时段。不要把现价或连通性当成足以开仓的依据。
- 接口验收先访问 `/api/workspace/session` 并保留该会话 Cookie，再访问 `/api/workspace/symbols` 获取有效合约，随后查询 `/api/workspace/market?symbol=<合约>`。首次查询会登记订阅，应在后续查询确认 `source=live`、`connected=true`、日线/30分/3分都有K线且 `freshness` 的 `checked_recent` 均为真。休市 `feed_status=idle`、`fresh=false` 正常；开盘时还需观察K线或报价变化，不能用反复请求接口让过期数据冒充新行情。长时间 `loading`、`disconnected`、`stale` 不通过行情验收。
- `/healthz` 只在服务器本机可用，只返回线程状态。Docker healthcheck 本身不会自动重启 unhealthy 容器；行情线程看门狗会让进程退出触发 `restart`。SDK 线程卡住而未退出、HTTP 饱和、长期重连等需外部监控告警与人工诊断，不能以强制重启掩盖休市或账号权限问题。
- 协作长连接会占用工作线程；24 线程面向少量协作者，预留普通请求容量。先压测同时在线人数和内存，再调线程数；禁止直接横向扩容。
- 每个查看过的品种会保留 6000 根30分K和10000根3分K，行情订阅及缓存可能跨切换保留。必须比较单品种与多个品种查看后的内存、CPU和 OOM 记录；首屏正常不代表所有品种负载通过。1 GiB 限制优先保护现有服务器，不能未经测量直接取消。
- 每次更新前停止服务写入，将整个 `/var/lib/SilverResonance` 打包到受保护备份目录，包含 workspace、计划、用户、个人风险和缓存；凭据仅进入受保护备份。不要公开备份或把它放进网站目录。记录版本、备份路径和备份时间，并验证可恢复。
- 回退时停新容器，使用上一提交的 Compose 文件和旧镜像标签恢复单实例；仅在确认新旧数据格式兼容时复用当前 state。不兼容时先另存新数据，再经确认恢复发布前备份；会丢失备份之后的编辑，不能静默覆盖。
- `docker compose ... stop workspace` 为停服操作；不要使用清卷、递归删除数据、重置数据库或全局容器清理。删除源码 release 不应删除 `/var/lib/SilverResonance`。

## 可选：不用 Docker 的独立 Python 3.11

`silver-resonance.service` 仅用于已另行安装专用 Python 3.11/venv 的情况，不得复用系统 Python 3.8 或宝塔 Python 3.7。与 Docker 方式二选一，不能同时启用。用非 root 专用用户 `silver-resonance`，将外部目录授予该用户，虚拟环境放到 `/opt/SilverResonance/venv`，`current` 指向已验收版本。安装 `requirements-server.txt`，把 `runtime.env.example` 放到 `/etc/SilverResonance/runtime.env`，先 `--check` 再启用服务。该单元只允许写入外部数据目录，行情线程死亡自动重启。

## 技术依据

- [Flask：生产部署与 Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
- [Flask：反向代理与 ProxyFix](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)
- [Waitress：线程、代理头和连接配置](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)
- [Docker：Linux host network](https://docs.docker.com/engine/network/drivers/host/)
