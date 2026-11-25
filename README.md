# 容器网关发布系统

一套可启动的 Docker Compose 环境：Flask 发布页 + HTTP 网关 + 两个样例后端。
发布页与网关共用同一个配置卷，发布时整表替换路由配置；网关每个请求在入口处
取一次配置快照，在途请求不受后续发布影响。

## 启动

```bash
docker compose up -d --build
```

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 发布页 | http://localhost:8081/ | 填写后端表 + 路由表，发布；延时试请求 |
| 网关 | http://localhost:8080/ | 按配置卷中的路由转发；`/__gateway/status` 查看现役配置 |
| 样例 A | http://localhost:9001/ | Compose 中的 `sample-a` |
| 样例 B | http://localhost:9002/ | Compose 中的 `sample-b` |

首次启动时 `config-init` 会准备共享卷并写入版本 1 的示例配置：
`/sample-a` → `sample-a`，`/sample-b` → `sample-b`。

```bash
curl -i http://localhost:8080/sample-a/hello   # 200，X-Gateway-Backend: sample-a
curl -i http://localhost:8080/nope             # 404 route_not_found
```

## 关键行为

- **整表发布**：发布页一次提交完整的后端表与路由表，校验通过后写入整个配置，
  不做逐条合并。每次写入 `version` 自增。
- **后端选自 Compose**：后端名必须是 `BACKEND_CATALOG` 中的样例服务
  （`sample-a` / `sample-b`），且地址必须与 Compose 声明一致；服务端校验，
  不依赖网页下拉框。
- **无效配置不生效**：路由引用不存在的后端、路径不合法或重复、后端名不在
  样例列表等，发布页返回 400 并列出全部错误，磁盘文件与现役配置均不变。
- **写入失败不生效**：写入使用同目录临时文件 + `fsync` + `os.replace` 原子替换。
  失败时发布页返回 500，原文件保持不变。
- **网关按快照转发**：每个请求在 `before_request` 取一次不可变配置快照，
  路由匹配与后端选择都基于该快照；发布只替换引用，不修改在途请求使用的对象。
- **撤下路径**：新配置中不存在的路径对新请求立即返回 404
  （`{"error":"route_not_found"}`），在途请求继续用旧快照完成。
- **坏配置兜底**：网关读取或校验配置失败时记录错误并继续使用上一次成功加载的
  配置；`/__gateway/status` 的 `config_file_error` 会显示原因。
- **延时试请求**：发布页向网关发请求并带上 `?delay_ms=N`，样例后端在处理期间
  睡眠 N 毫秒；结果显示状态码、实际后端（`X-Gateway-Backend`）、命中的路由
  （`X-Gateway-Route`）和使用的配置版本（`X-Gateway-Config-Version`）。
  因此可以在一次慢请求进行期间发布新配置，观察“新请求 404 / 在途请求 200”。

## 配置格式

共享卷中的 `/data/routes.json`：

```json
{
  "schema_version": 1,
  "version": 7,
  "published_at": "2026-11-24T12:00:00+00:00",
  "backends": [
    {"name": "sample-a", "url": "http://sample-a:9000"},
    {"name": "sample-b", "url": "http://sample-b:9000"}
  ],
  "routes": [
    {"path": "/api", "match": "prefix", "backend": "sample-a"},
    {"path": "/health", "match": "exact", "backend": "sample-b"}
  ]
}
```

匹配规则：`exact` 只匹配完全相同的路径；`prefix` 匹配该路径本身及其
`/` 子路径（`/api` 不会匹配 `/apifoo`）；多条命中时取最长路径。

### 前缀路由的挂载（`mount`）

路由可加 `"mount": true`（仅 `prefix` 路由支持，缺省为 `false`）。挂载把一个
本身运行在根目录的应用暴露到网关子路径下，语义与普通前缀路由不同：

- **转发剥前缀**：普通前缀路由把完整路径透传给后端；挂载路由在转发前去掉挂载
  前缀，后端始终看到自己的根布局。例如挂载 `/apps/demo` 时，网关请求
  `/apps/demo/echo/x?a=1` 转发为后端的 `/echo/x?a=1`，查询串原样保留。
- **入口尾斜杠规范化**：打开不带尾斜杠的挂载入口（`/apps/demo`）时网关返回
  `308` 跳转到带尾斜杠的地址（保留查询串），使页面里的相对引用在挂载目录内
  解析；`308` 保留方法和请求体，POST 也能正常跟随。
- **Location 重写**：后端返回的 `Location` 只要指向本次转发的后端（按
  scheme/主机/端口判定，绝对地址和网络路径引用都算），就改写为该挂载下的公开
  地址并保留查询串与锚点；相对地址先按上游当前路径解析 `./`、`../` 再挂到挂载
  前缀下；指到其他站点的跳转（或 `mailto:` 等非 HTTP scheme）原样返回。
- **同一应用多处挂载**：同一后端可以挂在多个路径，Location 按本次命中的挂载
  分别改写，浏览器提交后沿原入口跳到结果页。
- **冲突拒绝发布**：挂载路由拥有其整个公开子树。若新表中另有规则的路径会截走
  挂载入口下的请求（如挂载 `/demo` 又配置了 `/demo/api`），发布返回 400 并列出
  冲突的两条路由，磁盘文件与现役配置均不变。同级名称（`/demo` 与 `/demolition`）
  不算冲突。

挂载配置示例：

```json
"routes": [
  {"path": "/apps/demo", "match": "prefix", "backend": "sample-a", "mount": true},
  {"path": "/apps/twin", "match": "prefix", "backend": "sample-a", "mount": true}
]
```

样例后端（`samples/app.py`）的 `/` 提供一个全部使用相对地址的演示页面：无
`action` 的表单（提交到当前 URL，带不带尾斜杠都能提交）、PRG 结果页，以及相对/
绝对/站外三类 `Location` 重定向端点，可直接用于验证上述行为。


## 配置项（`x-app-env`，见 `docker-compose.yml`）

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `CONFIG_PATH` | `/data/routes.json` | 共享卷中的配置文件 |
| `BACKEND_CATALOG` | 两个样例服务 | 允许的后端名 → 地址（JSON） |
| `BACKEND_LABELS` | 中文名 | 发布页显示名（JSON） |
| `UPSTREAM_CONNECT_TIMEOUT` / `UPSTREAM_READ_TIMEOUT` | 3 / 150 秒 | 网关到后端超时 |
| `PROBE_TIMEOUT` | 180 秒 | 发布页试请求超时 |

## 目录结构

```
common/      共享配置模型、原子存储、初始化脚本
gateway/     Flask 反向代理 + 配置快照存储
publisher/   发布页（Flask + 服务端校验 + 延时试请求）
samples/     可自报身份、可延时的样例后端
tests/       pytest 单元/集成测试
```

## 本地测试

```bash
python3 -m pytest -q tests
```

测试覆盖：配置校验与路由匹配、挂载冲突检测、原子写入与并发读取、网关转发/404/
在途请求/坏配置兜底/后端不可达、挂载剥前缀/入口规范化/Location 重写（相对、绝对、
站外、两处挂载）/POST 提交沿原入口跳转、发布页的成功发布/校验拒绝/写入失败/试请求。

## 已知限制

- 发布页的试请求任务记录保存在进程内存，发布页重启后丢失（不影响配置）。
- 配置卷为 Docker 命名卷，多发布页实例写同一文件时靠文件锁串行化版本号，
  但本项目仅运行一个发布页。
- 网关只支持 HTTP 后端，不终止 TLS；鉴权、限流不在本任务范围内。
