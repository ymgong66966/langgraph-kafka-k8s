# LangGraph Kafka Communication System

.PHONY: help build deploy clean test

# Variables
REGISTRY ?= your-registry.com
TAG ?= latest
NAMESPACE ?= langgraph

help: ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Build Docker images
	@echo "Building Docker images..."
	docker build -f docker/Dockerfile.agent-comms -t $(REGISTRY)/langgraph-kafka/agent-comms:$(TAG) .
	docker build -f docker/Dockerfile.task-generator -t $(REGISTRY)/langgraph-kafka/task-generator:$(TAG) .
	docker build -f docker/Dockerfile.task-solver -t $(REGISTRY)/langgraph-kafka/task-solver:$(TAG) .

push: build ## Build and push Docker images
	@echo "Pushing Docker images..."
	docker push $(REGISTRY)/langgraph-kafka/agent-comms:$(TAG)
	docker push $(REGISTRY)/langgraph-kafka/task-generator:$(TAG)
	docker push $(REGISTRY)/langgraph-kafka/task-solver:$(TAG)

helm-deps: ## Update Helm dependencies
	@echo "Updating Helm dependencies..."
	cd helm && helm dependency update

create-secrets: ## Create Kubernetes secrets manually
	@if [ -z "$(OPENAI_API_KEY)" ]; then \
		echo "Error: OPENAI_API_KEY environment variable is required"; \
		echo "Usage: make create-secrets OPENAI_API_KEY=sk-your-key"; \
		exit 1; \
	fi
	@echo "Creating Kubernetes secrets..."
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret generic langgraph-kafka-secrets \
		--from-literal=openai-api-key="$(OPENAI_API_KEY)" \
		--namespace $(NAMESPACE) \
		--dry-run=client -o yaml | kubectl apply -f -

deploy: helm-deps ## Deploy to Kubernetes using Helm
	@echo "Deploying to Kubernetes..."
	@if [ -f secrets.yaml ]; then \
		echo "Using secrets.yaml file..."; \
		helm upgrade --install langgraph-kafka ./helm \
			--namespace $(NAMESPACE) \
			--create-namespace \
			--values secrets.yaml \
			--set image.repository=$(REGISTRY)/langgraph-kafka \
			--set image.tag=$(TAG); \
	else \
		echo "No secrets.yaml found. Please create one or set OPENAI_API_KEY environment variable."; \
		helm upgrade --install langgraph-kafka ./helm \
			--namespace $(NAMESPACE) \
			--create-namespace \
			--set image.repository=$(REGISTRY)/langgraph-kafka \
			--set image.tag=$(TAG) \
			--set env.openaiApiKey="$(OPENAI_API_KEY)"; \
	fi

deploy-dev: helm-deps ## Deploy to Kubernetes with development values
	@echo "Deploying development version..."
	helm upgrade --install langgraph-kafka ./helm \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--values helm/values-dev.yaml \
		--set image.repository=$(REGISTRY)/langgraph-kafka \
		--set image.tag=$(TAG)

uninstall: ## Uninstall Helm release
	@echo "Uninstalling Helm release..."
	helm uninstall langgraph-kafka --namespace $(NAMESPACE)

status: ## Check deployment status
	@echo "Checking deployment status..."
	kubectl get pods -n $(NAMESPACE)
	kubectl get services -n $(NAMESPACE)
	kubectl get ingress -n $(NAMESPACE)

logs: ## View logs
	@echo "Viewing logs..."
	kubectl logs -l component=agent-comms -n $(NAMESPACE) --tail=100 -f

test: ## Run tests
	@echo "Running tests..."
	curl -f http://localhost:8000/health || echo "Service not ready"

clean: ## Clean up resources
	@echo "Cleaning up..."
	docker rmi $(REGISTRY)/langgraph-kafka/agent-comms:$(TAG) || true
	docker rmi $(REGISTRY)/langgraph-kafka/task-generator:$(TAG) || true
	docker rmi $(REGISTRY)/langgraph-kafka/task-solver:$(TAG) || true

lint: ## Lint Helm charts
	@echo "Linting Helm charts..."
	helm lint helm/

template: ## Generate Kubernetes templates
	@echo "Generating templates..."
	helm template langgraph-kafka ./helm --namespace $(NAMESPACE) > k8s-templates.yaml

dry-run: helm-deps ## Dry run deployment
	@echo "Performing dry run..."
	helm upgrade --install langgraph-kafka ./helm \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--dry-run \
		--debug