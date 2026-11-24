# EPG 控制台

支持频道管理、多来源节目抓取、XMLTV 导入导出、定时任务和备份恢复。

## 部署

使用 [compose.yaml](compose.yaml) 启动，自动构建 Linux amd64 / arm64 镜像。

```sh
docker compose pull
docker compose up -d
```

- 首页：`http://设备IP:8000/`
- 后台：`http://设备IP:8000/admin/`
- 新部署账号/密码：`admin` / `admin`，登录后请修改。
- 数据保存在本地 `./data`，升级保留原目录映射。
- 抓取计划及其他功能在后台设置中心配置。

镜像：`zsz520/epg-console:2.12`，也提供 `latest`。
IPv6 来源沿用宿主机已有的 IPv6 路由和 NAT 配置。

## 节目单地址

- XML：`/download/all.xml`
- XML.GZ：`/download/all.xml.gz`
- DIYP：`/api/diyp/?ch=频道名&date=YYYYMMDD`

外置来源放入 `data/sources/`，写法见 [新增来源模板说明](新增来源模板说明.md)。

## 升级

备份 `data` 后执行上面的两条部署命令，浏览器强制刷新即可。已有账号、设置和来源文件保留。

## 自动构建

GitHub Actions 在主分支代码更新时发布 `latest`，推送 `v2.12` 等版本标签时发布 `2.12`；也可手动运行。
仓库需配置 Secret `DOCKERHUB_TOKEN`（Docker Hub 读写 Access Token）。未配置时只构建，不推送；PR 也只构建。
