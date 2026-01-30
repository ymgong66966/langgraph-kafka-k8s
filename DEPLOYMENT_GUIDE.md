# Human Escalation Feature Deployment Guide

## 功能概述

这次更新实现了智能人工干预（human escalation）机制：

### 新功能
1. **needs_human 状态追踪** - 追踪每个用户是否需要人工支持
2. **智能LLM判断** - 使用Claude判断用户消息是否需要社工介入（而不是硬编码列表）
3. **三种路由逻辑**：
   - **情况A**: `needs_human=true` + 用户再次提问（不在question list中）→ 导向agentic system
   - **情况B**: `needs_human=false` + 被触发question list → 设置`needs_human=true`，返回escalation消息
   - **情况C**: `needs_human=false` + 没触发question list → 正常导向agentic system

### 修改的文件
- `src/task_generator.py` - 核心路由逻辑和状态管理
- `src/task_generator_api.py` - API接口更新
- `src/chat_interface_api.py` - 用户状态追踪

---

## 部署流程

### 方法1: 使用GitHub Actions自动部署 (推荐)

如果你的仓库已配置GitHub Actions：

```bash
# Step 1: 提交代码到GitHub
git add .
git commit -m "feat: add intelligent human escalation with LLM-based question matching"
git push origin main

# Step 2: 等待GitHub Actions构建镜像 (大约5-10分钟)
# 可以在GitHub仓库的Actions标签页查看构建进度

# Step 3: 运行部署脚本
./deploy-from-github.sh
```

### 方法2: 手动构建和部署

如果没有GitHub Actions或需要手动部署：

```bash
# Step 1: 设置环境变量
export GITHUB_USER="your-github-username"
export REPO_NAME="langgraph-kafka-k8s"
export REGISTRY="ghcr.io"
export TAG="$(git branch --show-current)-$(git rev-parse --short HEAD)"

# Step 2: 构建Docker镜像
docker build -f docker/Dockerfile.task-generator \
  -t $REGISTRY/$GITHUB_USER/$REPO_NAME/task-generator:$TAG .

docker build -f docker/Dockerfile.chat-interface \
  -t $REGISTRY/$GITHUB_USER/$REPO_NAME/chat-interface:$TAG .

docker build -f docker/Dockerfile.task-solver \
  -t $REGISTRY/$GITHUB_USER/$REPO_NAME/task-solver:$TAG .

# Step 3: 推送镜像到registry
docker push $REGISTRY/$GITHUB_USER/$REPO_NAME/task-generator:$TAG
docker push $REGISTRY/$GITHUB_USER/$REPO_NAME/chat-interface:$TAG
docker push $REGISTRY/$GITHUB_USER/$REPO_NAME/task-solver:$TAG

# Step 4: 更新Helm依赖
cd helm
helm dependency update
cd ..

# Step 5: 部署到Kubernetes
helm upgrade --install langgraph-kafka ./helm \
  --namespace langgraph \
  --create-namespace \
  --values helm/values-dev.yaml \
  --set image.repository="$REGISTRY/$GITHUB_USER/$REPO_NAME" \
  --set image.tag="$TAG" \
  --set env.openaiApiKey="$OPENAI_API_KEY" \
  --set env.anthropicApiKey="$ANTHROPIC_API_KEY" \
  --timeout 600s

# Step 6: 等待pods就绪
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=langgraph-kafka \
  -n langgraph \
  --timeout=300s
```

### 方法3: 使用Makefile快速部署

```bash
# 如果已经有镜像在registry
make deploy-dev NAMESPACE=langgraph
```

---

## 验证部署

### 1. 检查Pod状态
```bash
kubectl get pods -n langgraph
```

所有pods应该显示 `Running` 和 `1/1 Ready`。

### 2. 检查服务健康
```bash
# Task Generator健康检查
kubectl port-forward -n langgraph svc/langgraph-kafka-task-generator 8001:8001 &
curl http://localhost:8001/health

# Chat Interface健康检查
kubectl port-forward -n langgraph svc/langgraph-kafka-chat-interface 8003:8003 &
curl http://localhost:8003/health
```

### 3. 测试Human Escalation功能

```bash
# 方法1: 通过Chat Interface UI
# 打开浏览器访问: http://localhost:8003/static/
# 尝试发送以下消息测试：

# 应该触发human escalation:
"Can you help me hire an in-home caregiver?"
"I need to schedule a consultation with a neurologist"
"Help me coordinate moving mom into assisted living"

# 不应该触发human escalation:
"What is respite care?"
"Tell me about memory care options"
"I'm feeling stressed about caregiving"

# 方法2: 使用curl测试API
curl -X POST http://localhost:8001/generate-task \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": [],
    "current_message": "Can you help me hire an in-home caregiver?",
    "user_id": "test-user-123",
    "needs_human": false
  }'

# 应该返回: needs_human=true
```

---

## 查看日志

```bash
# Task Generator日志
kubectl logs -n langgraph -l component=task-generator --tail=100 -f

# Chat Interface日志
kubectl logs -n langgraph -l component=chat-interface --tail=100 -f

# 搜索human escalation相关日志
kubectl logs -n langgraph -l component=task-generator | grep -i "scenario\|needs_human\|human support"
```

---

## 回滚到之前版本

如果新版本有问题，可以回滚：

```bash
# 查看部署历史
helm history langgraph-kafka -n langgraph

# 回滚到上一个版本
helm rollback langgraph-kafka -n langgraph

# 或回滚到特定版本
helm rollback langgraph-kafka [REVISION] -n langgraph
```

---

## 常见问题

### 问题1: Pods一直处于Pending状态
```bash
# 检查events
kubectl describe pod -n langgraph [POD_NAME]

# 常见原因: 资源不足或镜像拉取失败
```

### 问题2: needs_human状态没有更新
```bash
# 检查task generator日志
kubectl logs -n langgraph -l component=task-generator | grep "Updated needs_human"

# 检查chat interface日志
kubectl logs -n langgraph -l component=chat-interface | grep "needs_human"
```

### 问题3: Filter agent返回错误结果
```bash
# 查看filter node的LLM响应
kubectl logs -n langgraph -l component=task-generator | grep "Filter LLM response"
```

---

## 监控建议

### 关键指标
1. **Human escalation率** - 有多少消息被标记为需要人工支持
2. **Filter准确性** - LLM判断的准确率
3. **响应时间** - Filter node的处理时间

### 使用Langfuse监控
访问你的Langfuse dashboard查看：
- Filter agent的调用频率和成功率
- Human support node的触发次数
- 各个scenario的分布情况

---

## 技术细节

### needs_human状态流转

```
User → Chat Interface (检查user_needs_human[user_id])
  ↓
Task Generator API (传递needs_human)
  ↓
Filter Node (LLM判断 + 路由逻辑)
  ↓
三种Scenario处理
  ↓
返回updated needs_human
  ↓
Chat Interface (更新user_needs_human[user_id])
```

### Filter Prompt设计
- 使用语义相似度而不是精确匹配
- 覆盖所有human support场景分类
- 明确输出格式要求（只输出ROUTER或HUMAN）

### 状态持久化
- Chat Interface在内存中维护`user_needs_human: Dict[str, bool]`
- 如果需要持久化，可以考虑：
  - 添加Redis存储
  - 在user_info API中包含needs_human字段
  - 使用数据库存储用户状态

---

## 下一步优化建议

1. **持久化存储** - 将needs_human状态保存到数据库，防止chat interface重启丢失
2. **重置机制** - 添加API endpoint允许重置用户的needs_human状态
3. **Analytics** - 添加metrics追踪human escalation的使用情况
4. **A/B测试** - 对比不同filter prompt的效果
5. **监控告警** - 当human escalation率异常时发送告警

---

## 联系方式

如有问题，请查看：
- GitHub Issues: https://github.com/your-username/langgraph-kafka-k8s/issues
- Langfuse Dashboard: 查看LLM调用日志
- Kubernetes Dashboard: 查看pod状态和日志
