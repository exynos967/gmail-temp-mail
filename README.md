# gmail-temp-mail

一个基于 Gmail IMAP 的精简临时邮箱服务，只提供 Docker 部署，支持单账号或多账号邮箱池。

## 特性

- 基于 `.env` 中的 Gmail 单账号或账号池随机生成别名
- 别名规则：`点号 + 大小写 + gmail.com/googlemail.com`
- `POST /api/new_address` 返回别名和对应 Bearer JWT
- 只从“别名创建之后”开始接收新邮件
- 后台按账号分别通过 Gmail IMAP 增量同步，接口只返回原始 RFC822 邮件 `raw`
- 使用 SQLite 落盘，支持重启恢复
- 自动清理过期别名与超时邮件

## 前置要求

1. 个人 Gmail 账号（`gmail.com` / `googlemail.com`）
2. 开启 2FA
3. 生成 Gmail App Password

参考：
- Gmail IMAP: <https://developers.google.com/gmail/imap/imap-smtp>
- App Password: <https://support.google.com/mail/answer/185833>

## 快速开始

默认 Compose 会直接拉取 GHCR 镜像 `ghcr.io/exynos967/gmail-temp-mail:latest`。

```bash
cp .env.example .env
# 编辑 .env，填入真实 Gmail 地址和 App Password

docker compose pull
docker compose up -d
```

服务默认监听 `http://127.0.0.1:8080`。

## 镜像发布

- GitHub Actions 会在推送到 `main` 分支时构建并推送镜像到 `ghcr.io/exynos967/gmail-temp-mail`
- 默认部署标签为 `ghcr.io/exynos967/gmail-temp-mail:latest`
- 如需本地自行构建，可直接执行 `docker build -t gmail-temp-mail:local .`

## 环境变量

| 变量 | 说明 |
|---|---|
| `GMAIL_ADDRESS` | 单账号模式使用的 Gmail 主账号 |
| `GMAIL_APP_PASSWORD` | 单账号模式使用的 Gmail App Password |
| `GMAIL_ACCOUNTS` | 多账号模式邮箱池，格式：`邮箱1:密码1,邮箱2:密码2`，配置后优先于单账号字段 |
| `SERVICE_API_KEY` | 创建别名时使用的 `x-custom-auth` |
| `JWT_SECRET` | 别名级 Bearer token 的签名密钥 |
| `DATABASE_PATH` | SQLite 文件路径，默认挂载到 `/data` |
| `POLL_INTERVAL_SECONDS` | IMAP 同步轮询间隔 |
| `ALIAS_TTL_MINUTES` | 别名有效期 |
| `MAIL_TTL_MINUTES` | 邮件保留期 |

多账号示例：

```env
GMAIL_ACCOUNTS=alpha.one@gmail.com:app_password_one,beta.two@gmail.com:app_password_two
```

说明：
- `GMAIL_ACCOUNTS` 配置后，会进入邮箱池并在创建别名时随机选择一个账号
- 每个账号都会独立维护自己的 IMAP 增量同步游标
- 旧的 `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` 仍可继续使用

## API

### 1. 创建别名

```bash
curl -X POST http://127.0.0.1:8080/api/new_address \
  -H 'x-custom-auth: your-service-api-key'
```

响应示例：

```json
{
  "address_id": 1,
  "address": "aB.cdEf@googlemail.com",
  "jwt": "<token>",
  "created_at": "2026-04-06T12:00:00+00:00",
  "expires_at": "2026-04-06T13:00:00+00:00"
}
```

### 2. 拉取邮件列表

```bash
curl 'http://127.0.0.1:8080/api/mails?limit=20&offset=0' \
  -H 'Authorization: Bearer <token>'
```

### 3. 拉取单封邮件

```bash
curl http://127.0.0.1:8080/api/mail/1 \
  -H 'Authorization: Bearer <token>'
```

### 4. 删除单封邮件

```bash
curl -X DELETE http://127.0.0.1:8080/api/mails/1 \
  -H 'Authorization: Bearer <token>'
```

## 存储说明

- 别名创建时会记录当前 Gmail 收件箱的最新 UID 作为起点
- 只有 UID 大于该起点的邮件才会被纳入该别名
- 邮件内容以原始 `raw` 文本存入 SQLite，接口不做附件下载与 HTML 解析

## 限制

- 仅支持个人 Gmail，不支持 Google Workspace 自定义域
- 依赖 Gmail IMAP，可用性取决于 Gmail 登录状态与 App Password
- 未实现发送邮件、Webhook、前端页面
