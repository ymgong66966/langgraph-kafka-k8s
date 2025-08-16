#!/bin/bash

# Test frontend build locally
set -e

echo "🧪 Testing frontend build locally..."

cd frontend

echo "📦 Installing dependencies..."
npm install

echo "🏗️ Building frontend..."
npm run build

echo "📋 Validating build output..."
if [ -d "dist" ] && [ -f "dist/index.html" ]; then
    echo "✅ Frontend build successful!"
    echo "📁 Build contents:"
    ls -la dist/
else
    echo "❌ Frontend build failed - dist directory or index.html not found"
    exit 1
fi

cd ..

echo "✅ Frontend build test completed successfully!"