# 🚀 简化部署指南（使用latest tag）

## 📋 一次性设置（仅需第一次）

如果你是第一次部署或者需要设置Ingress/ALB：

```bash
./QUICK_DEPLOY.sh
```

这会：
- ✅ 设置AWS Load Balancer Controller
- ✅ 创建ALB Ingress
- ✅ 获得stable的public URL

**只需要运行一次！**

---

## 🔄 日常开发流程（每次更新代码）

### 1. 修改代码并推送

```bash
# 修改代码
vim src/task_generator.py

# Commit
git add .
git commit -m "feat: your new feature"
git push origin main
```

### 2. 等待GitHub Actions完成

GitHub Actions会自动：
- ✅ 构建Docker镜像
- ✅ 打上`latest` tag
- ✅ 推送到ghcr.io
- ✅ 自动部署到Kubernetes

**通常需要5-10分钟**

在GitHub查看进度：
```
https://github.com/ymgong66966/langgraph-kafka-k8s/actions
```

### 3. （可选）手动触发部署

如果GitHub Actions的自动部署失败，或者你想立即部署：

```bash
./deploy-from-github.sh
```

这会：
- ✅ 使用GitHub上的`latest`镜像
- ✅ Helm部署到Kubernetes
- ✅ 等待pods就绪

**不需要手动helm upgrade！**

---

## 🎯 完整流程示例

```bash
# 1. 修改代码
vim src/task_generator.py

# 2. Git push
git add .
git commit -m "feat: add new feature"
git push origin main

# 3. 等待GitHub Actions（5-10分钟）
# 在浏览器查看: https://github.com/你的用户名/langgraph-kafka-k8s/actions

# 4. 验证部署
kubectl get pods -n langgraph

# 5. 测试
kubectl port-forward -n langgraph svc/langgraph-kafka-task-generator 8001:8001
./test_human_escalation_complete.sh
```

---

## 🔍 常用命令

### 检查部署状态
```bash
# 查看pods
kubectl get pods -n langgraph

# 查看镜像版本
kubectl get pods -n langgraph -o jsonpath='{.items[*].spec.containers[*].image}'

# 查看日志
kubectl logs -n langgraph -l component=task-generator --tail=50
```

### 获取Ingress URL
```bash
kubectl get ingress -n langgraph
# 输出: k8s-langgrap-langgrap-xxx.us-east-2.elb.amazonaws.com
```

### 快速重启（如果需要）
```bash
kubectl rollout restart deployment -n langgraph
```

---

## ❓ 常见问题

### Q: 为什么pods没有更新到最新代码？

**A**: 确认以下几点：
1. GitHub Actions是否成功完成？
2. 是否等待了5-10分钟让镜像构建完成？
3. Kubernetes是否配置了`imagePullPolicy: Always`？

**解决方法**:
```bash
# 强制重新拉取镜像
kubectl rollout restart deployment -n langgraph
```

---

### Q: GitHub Actions部署失败了怎么办？

**A**: 手动运行部署脚本：
```bash
./deploy-from-github.sh
```

---

### Q: 如何回滚到之前的版本？

**A**: 使用helm rollback：
```bash
# 查看历史
helm history langgraph-kafka -n langgraph

# 回滚到之前版本
helm rollback langgraph-kafka -n langgraph
```

但注意：由于使用`latest` tag，回滚后镜像还是最新的。
如果需要真正回滚代码，需要：
1. Git revert commit
2. 重新push触发新构建

---

### Q: 我需要手动helm upgrade吗？

**A**: **不需要！**

使用`latest` tag的优势就是：
- ✅ GitHub Actions自动部署
- ✅ 或者运行`./deploy-from-github.sh`
- ❌ 不需要手动`helm upgrade --set image.tag=xxx`

---

## 🎓 关键理解

### values-dev.yaml
```yaml
taskGenerator:
  image:
    tag: latest  # 总是使用latest
```

### GitHub Actions
- 每次push到main → 构建镜像 → 打tag `latest`
- 自动部署到Kubernetes

### deploy-from-github.sh
- 使用values-dev.yaml中的`latest` tag
- 不需要指定commit SHA
- 简单快速

---

## 📚 脚本对比

| 脚本 | 用途 | 运行频率 |
|------|------|---------|
| `QUICK_DEPLOY.sh` | 设置Ingress/ALB | 一次性 |
| `deploy-from-github.sh` | 部署应用 | 每次更新（可选） |
| `test_human_escalation_complete.sh` | 测试功能 | 按需 |

---

## ✅ 未来你只需要做

1. **修改代码**
2. **Git push**
3. **等待GitHub Actions**（自动部署）
4. **测试验证**

就这么简单！🎉
