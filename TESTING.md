# Local Testing Workflow

## Quick Testing Commands

```bash
# Test frontend build only
./test-frontend-build.sh

# Test individual service (without Docker due to corporate restrictions)
./test-single-service.sh <service-name>

# Test Python requirements (if network allows)
./test-requirements.sh
```

## Manual Build Verification

Since Docker Desktop is restricted, verify builds using:

1. **Check requirements file sizes**:
```bash
wc -l requirements*.txt
```

2. **Validate Dockerfile references**:
```bash
grep -l "requirements-" docker/Dockerfile.*
```

3. **Test frontend locally**:
```bash
cd frontend && npm install && npm run build
```

## GitHub Actions Testing Strategy

1. **Push small changes** to test individual services
2. **Use fail-fast: false** to see all build results
3. **Monitor build times** in Actions tab
4. **Check logs** for specific timeout/dependency issues

## Expected Build Times After Optimization

- **agent-comms**: ~2-3 minutes (was timing out)
- **task-generator**: ~4-5 minutes (reduced from full LangGraph)
- **task-solver**: ~4-5 minutes (reduced from full LangGraph)
- **chat-interface**: ~3-4 minutes (frontend + minimal backend)

## Troubleshooting

- **Cache issues**: Check Node.js cache path in GitHub Actions
- **Build timeouts**: Verify service-specific requirements are being used
- **Frontend issues**: Ensure `dist/` directory exists after npm build