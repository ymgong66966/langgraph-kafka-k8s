# Robust Deployment Strategy

## Current Issues Identified

1. **Image Repository Inconsistency**
   - Some services use correct GHCR images
   - Others use old local image references
   - Need consistent image.repository override

2. **ServiceAccount Conflicts**  
   - Multiple serviceaccounts with same name
   - Partial deployments leave orphaned resources

3. **Workflow Trigger Gaps**
   - Missing `helm/**` and `.github/**` in trigger paths
   - Infrastructure changes don't auto-deploy

## Robust Patterns

### 1. Clean Deployment Strategy
```bash
# Always clean before deploy
helm uninstall langgraph-kafka -n langgraph --ignore-not-found
kubectl delete namespace langgraph --ignore-not-found
kubectl create namespace langgraph
helm install langgraph-kafka ./helm -n langgraph --values helm/values-dev.yaml
```

### 2. Comprehensive Workflow Triggers
```yaml
paths:
  - 'src/**'           # Source code changes
  - 'docker/**'        # Container changes  
  - 'frontend/**'      # Frontend changes
  - 'requirements*.txt' # Dependency changes
  - 'helm/**'          # K8s template changes
  - '.github/**'       # Workflow changes
```

### 3. Force Image Updates
```bash
# Use specific commit SHA tags
--set image.tag=main-${SHORT_SHA}
# Ensure consistent repository
--set image.repository=ghcr.io/${GITHUB_REPOSITORY}
```

### 4. Local Testing Workflow
```bash
# Test before push
./test-frontend-build.sh
helm template langgraph-kafka ./helm --values ./helm/values-dev.yaml --dry-run
```

## Next Steps
1. Fix workflow trigger paths
2. Add cleanup step to GitHub Actions
3. Ensure consistent image references
4. Test complete pipeline