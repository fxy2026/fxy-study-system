# FXY 知识库项目

这是 FXY 的 Obsidian 知识库 vault，通过 WebDAV (Remotely Save) 同步到 2H4G 服务器。

## 快速参考

- **服务器**: `ssh -i ~/.ssh/server.pem root@202.120.1.230`
- **后端代码**: `/root/chat-server.cjs` (端口 3457)
- **前端页面**: `/root/quartz/public/apps/`
- **Vault 同步目录**: `/root/webdav-vault/`
- **健康检查**: `curl http://127.0.0.1:3457/api/health`

## 部署命令

```bash
# 部署前端
scp -i ~/.ssh/server.pem <文件> root@202.120.1.230:/root/quartz/public/apps/

# 部署后端并重启
scp -i ~/.ssh/server.pem <文件> root@202.120.1.230:/root/chat-server.cjs
ssh -i ~/.ssh/server.pem root@202.120.1.230 "systemctl restart chat-server"

# 检查服务
ssh -i ~/.ssh/server.pem root@202.120.1.230 "systemctl status chat-server --no-pager"
```

## 注意事项

- 服务器是 ARM64 架构，Docker 镜像需确认兼容
- AI 笔记保存后需 chown www-data（已在代码中自动处理）
- 课表数据存在于 chat-server.cjs 和 planner.html 两处，修改需同步
- planner.html 日期函数必须用本地时间 API，不能用 toISOString()
- 详细上下文见 memory 文件（~/.claude/projects/ 下）
