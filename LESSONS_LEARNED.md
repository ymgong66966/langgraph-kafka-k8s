# Human Escalation部署过程：问题分析与经验总结

## 📋 问题Timeline

### 1. 初始状态
- **Commit**: `452b637` - 包含human escalation新功能的代码
- **GitHub Actions**: 成功构建镜像 `main-452b637`
- **Deploy Job**: GitHub Actions显示部署成功（revision 4）

### 2. 发现问题
- **测试时发现**: Pods还在运行旧镜像 `main-f378acd`
- **困惑**: GitHub Actions明明成功了，为什么没有更新？

### 3. 尝试修复（走了弯路）
1. 手动helm upgrade设置镜像tag → pods启动失败（secret问题）
2. 创建空secret → pods还是报错缺少API key
3. kubectl set image手动更新 → 绕过helm，deployment配置不完整
4. 多次rollback和重新部署 → 问题依然存在

### 4. 最终发现
- **Deployment缺少ANTHROPIC_API_KEY环境变量**
- **虽然helm values有anthropicApiKey，但deployment没有使用**

---

## 🔍 根本原因分析

### 问题1: values-dev.yaml中硬编码了镜像版本

**文件位置**: `helm/values-dev.yaml`

**问题代码**:
```yaml
taskGenerator:
  image:
    tag: main-f378acd  # ❌ 硬编码旧版本
```

**影响**:
- GitHub Actions通过`--set taskGenerator.image.tag=main-452b637`覆盖
- 但本地运行helm时会使用values-dev.yaml中的旧tag
- 导致本地部署和CI/CD部署不一致

**根本原因**:
- **Helm优先级**: 命令行`--set` > values文件
- GitHub Actions的`--set`只在那次部署有效
- 后续任何不带`--set`的helm操作都会回退到values文件中的旧版本

---

### 问题2: 绕过Helm直接修改Kubernetes资源

**错误操作**:
```bash
# ❌ 直接修改deployment，绕过helm
kubectl set image deployment/langgraph-kafka-task-generator \
  task-generator=ghcr.io/.../task-generator:main-452b637
```

**后果**:
1. **Deployment配置不完整**:
   - 只更新了镜像，没有更新环境变量
   - Helm template的条件逻辑（如`{{- if .Values.env.anthropicApiKey }}`）没有执行

2. **状态不一致**:
   - Helm认为deployment应该是revision 8的配置
   - 实际deployment是手动修改的配置
   - `helm get`显示的和实际运行的不一致

3. **环境变量丢失**:
   - ANTHROPIC_API_KEY没有添加到deployment
   - 虽然secret存在，但deployment不引用它

**为什么会丢失环境变量？**

Helm template中的条件逻辑：
```yaml
{{- if .Values.env.anthropicApiKey }}
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: langgraph-kafka-secrets
      key: anthropic-api-key
{{- end }}
```

当用`kubectl set image`时：
- 只修改了镜像字段
- Helm template根本没有执行
- 条件逻辑没有机会添加ANTHROPIC_API_KEY
- 结果：deployment中没有这个环境变量

---

### 问题3: Secret vs Helm Values的混淆

**错误理解**:
"创建secret就能让pods使用API key"

**实际情况**:
1. **Secret创建**不会自动添加到deployment
2. 需要deployment配置中明确引用secret
3. 这个引用是通过**helm template**生成的
4. Helm template需要**values中有值**才会生成引用

**正确流程**:
```
Helm Values (anthropicApiKey设置)
  ↓
Helm Template渲染 (生成env配置)
  ↓
Deployment创建 (包含secretKeyRef)
  ↓
Kubernetes Secret (实际存储API key)
  ↓
Pod Environment (最终可用)
```

**我们的错误**:
- 跳过了前两步，直接创建secret
- Deployment没有引用secret的配置
- Secret形同虚设

---

## 💡 学到的经验

### 1. 不要在Values文件中硬编码版本号

**❌ 错误做法**:
```yaml
# helm/values-dev.yaml
taskGenerator:
  image:
    tag: main-f378acd  # 硬编码
```

**✅ 正确做法**:

**选项A**: 完全不设置tag，强制通过--set提供
```yaml
# helm/values-dev.yaml
taskGenerator:
  image:
    repository: ghcr.io/user/repo/task-generator
    # tag不设置，必须通过--set提供
```

**选项B**: 使用placeholder或latest
```yaml
taskGenerator:
  image:
    tag: latest  # 或 "{{ .Values.global.version }}"
```

**选项C**: 使用环境变量或CI变量（最佳）
```yaml
taskGenerator:
  image:
    tag: ${IMAGE_TAG:-latest}  # 默认latest，可被环境变量覆盖
```

---

### 2. 遵循单一部署路径

**❌ 混合使用helm和kubectl**:
```bash
helm upgrade ...          # 用helm部署
kubectl set image ...     # 用kubectl修改 ❌ 破坏helm状态
helm rollback ...         # helm不知道kubectl的修改
```

**✅ 统一使用helm**:
```bash
# 所有修改都通过helm
helm upgrade --set image.tag=new-version
helm upgrade --set env.apiKey=new-key
helm rollback  # 完整回滚所有配置
```

---

### 3. 理解Helm Template的条件逻辑

**关键认知**:
```yaml
{{- if .Values.env.anthropicApiKey }}
  # 这段代码只在values中设置了anthropicApiKey时才会渲染
{{- end }}
```

**影响**:
- 如果忘记在values或--set中提供这个值
- 这段配置根本不会出现在最终的deployment中
- 即使后来创建了secret，deployment也不会使用它

**最佳实践**:
- **必需的配置不要用条件判断**
- 或者在values中提供默认值：
  ```yaml
  env:
    anthropicApiKey: ""  # 默认空，但字段存在
  ```

---

### 4. Secret管理的正确姿势

**Helm自动创建Secret**:
```yaml
# secret.yaml template
{{- if .Values.env.anthropicApiKey }}
apiVersion: v1
kind: Secret
data:
  anthropic-api-key: {{ .Values.env.anthropicApiKey | b64enc }}
{{- end }}
```

**Deployment引用Secret**:
```yaml
# deployment.yaml template
env:
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: {{ .Release.Name }}-secrets
        key: anthropic-api-key
```

**关键点**:
1. Secret和Deployment的引用必须**配套**
2. 都通过helm template生成
3. 都依赖同一个values值
4. 不要手动创建secret，让helm管理

---

### 5. GitHub Actions部署成功 ≠ 本地看到更新

**为什么会这样？**

1. **GitHub Actions使用--set覆盖**:
   ```bash
   helm upgrade --set image.tag=main-452b637 \
                --set env.anthropicApiKey=$SECRET
   ```

2. **这次部署确实成功了**（revision 4）

3. **但后续操作覆盖了它**:
   - 我们手动helm upgrade（没带--set）
   - 使用了values-dev.yaml中的旧tag
   - 创建了新revision（5, 6, 7...）
   - GitHub的revision 4被覆盖

4. **最终状态**:
   - Helm当前revision: 不是GitHub Actions的
   - 运行的镜像: 旧版本（来自values-dev.yaml）

---

## 🎯 最佳实践总结

### 部署流程

1. **代码变更**
   ```bash
   git add .
   git commit -m "feat: new feature"
   git push origin main
   ```

2. **GitHub Actions自动处理**
   - 构建镜像（tag: main-SHORT_SHA）
   - Helm部署（--set覆盖所有动态值）
   - 无需手动干预

3. **本地测试**
   ```bash
   # 如果需要本地测试，同步values文件
   # 更新values-dev.yaml中的tag
   # 或者使用--set
   helm upgrade --set image.tag=main-452b637
   ```

4. **验证部署**
   ```bash
   # 检查实际运行的版本
   kubectl get pods -o jsonpath='{.items[*].spec.containers[*].image}'

   # 检查helm状态
   helm get values langgraph-kafka -n langgraph
   ```

---

### CI/CD配置建议

**GitHub Actions Workflow改进**:

```yaml
# 在部署后添加验证步骤
- name: Verify Deployment
  run: |
    # 等待rollout完成
    kubectl rollout status deployment/langgraph-kafka-task-generator -n langgraph

    # 验证镜像版本
    ACTUAL_IMAGE=$(kubectl get deployment langgraph-kafka-task-generator -n langgraph \
      -o jsonpath='{.spec.template.spec.containers[0].image}')
    EXPECTED_IMAGE="ghcr.io/${{ github.repository }}/task-generator:main-${SHORT_SHA}"

    if [ "$ACTUAL_IMAGE" != "$EXPECTED_IMAGE" ]; then
      echo "❌ Image mismatch!"
      echo "Expected: $EXPECTED_IMAGE"
      echo "Actual: $ACTUAL_IMAGE"
      exit 1
    fi

    # 验证环境变量
    kubectl exec -n langgraph deployment/langgraph-kafka-task-generator -- \
      env | grep "ANTHROPIC_API_KEY" || {
      echo "❌ ANTHROPIC_API_KEY not found in pod!"
      exit 1
    }

    echo "✅ Deployment verified successfully"
```

---

## 🚨 常见陷阱

### 陷阱1: "我更新了secret，为什么pod还是用旧的？"

**原因**: Pod不会自动重启来加载新secret

**解决**:
```bash
kubectl rollout restart deployment/your-deployment
```

---

### 陷阱2: "Helm values有API key，为什么pod没有？"

**原因**: 可能是用kubectl绕过helm修改了deployment

**检查**:
```bash
# 查看deployment实际配置
kubectl get deployment -o yaml | grep -A 10 env:

# 查看helm认为的配置
helm get manifest langgraph-kafka | grep -A 10 env:

# 如果不一致，说明有人绕过helm修改了
```

**解决**: 用helm重新部署
```bash
helm upgrade --reuse-values langgraph-kafka ./helm
```

---

### 陷阱3: "GitHub Actions显示成功，但功能不work"

**可能原因**:
1. ✅ 镜像构建成功
2. ✅ Helm部署成功
3. ❌ 但后续有人手动修改了配置
4. ❌ 或者values文件中的旧配置被复用了

**验证**:
```bash
# 检查最近的helm操作
helm history langgraph-kafka -n langgraph

# 看看哪个revision是GitHub Actions创建的
# 看看当前revision是哪个
```

---

## 📚 关键概念

### Helm的工作原理

```
Values (*.yaml + --set)
  ↓
Template Engine渲染
  ↓
Kubernetes Manifests (YAML)
  ↓
kubectl apply
  ↓
Kubernetes Resources
```

**重要特性**:
1. **Atomic**: 要么全部成功，要么全部回滚
2. **Versioned**: 每次upgrade创建新revision
3. **Templated**: 动态生成配置，支持条件逻辑
4. **Declarative**: 描述desired state，helm负责达到这个状态

**绕过Helm的后果**:
- Helm不知道你的手动修改
- Rollback会丢失手动修改
- Helm upgrade可能覆盖手动修改
- 状态不一致，难以debug

---

## ✅ 正确的工作流程

### 开发新功能
1. 修改代码
2. 本地测试（可选）
3. Commit并push到GitHub
4. GitHub Actions自动构建和部署
5. 验证部署结果
6. 如果有问题，修改代码重新push，不要手动fix生产环境

### 修复生产问题
1. **紧急情况**: 可以手动修改，但要**立即**记录
2. 修改代码并push，让GitHub Actions重新部署
3. 验证问题已修复
4. 如果手动修改过，确保代码包含了这些修改

### 更新配置
1. **不要直接改生产环境**
2. 修改values文件或GitHub Secrets
3. Commit并push
4. 让CI/CD重新部署

---

## 🎓 总结

**为什么花了这么多功夫？**
- 因为我们**绕过了Helm**，破坏了声明式配置
- 因为**values文件硬编码了版本**，导致本地和CI/CD不一致
- 因为**不理解Helm template逻辑**，手动创建secret但deployment不引用

**最根本的问题？**
- **没有遵循"Infrastructure as Code"原则**
- 配置应该在代码中（values文件）
- 部署应该通过CI/CD
- 手动修改只应该用于紧急调试，不应该是常态

**GitHub部署的问题？**
- GitHub Actions其实**没有问题**，它正确部署了
- 问题是**我们后续手动操作**覆盖了它的部署
- values-dev.yaml中的硬编码tag导致任何不带--set的helm操作都会回退

**核心教训**:
> **相信你的CI/CD pipeline，不要手动修改生产环境。如果需要修改，通过代码和CI/CD来做。**

---

## 📖 推荐阅读

- [Helm Best Practices](https://helm.sh/docs/chart_best_practices/)
- [GitOps Principles](https://www.gitops.tech/)
- [Kubernetes Production Best Practices](https://learnk8s.io/production-best-practices)
