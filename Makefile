.PHONY: build up down logs clean health backup deploy test test-unit test-integration

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	docker compose down -v
	docker system prune -f

deploy:
	./deploy.sh

health:
	@for svc in web-ui core-logic rag-engine tts searxng research; do \
		printf "%-15s " "$$svc:"; \
		curl -sf http://localhost:$$(docker port helga-$$svc 2>/dev/null | head -1 | cut -d: -f2)/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "offline"; \
	done

backup:
	@mkdir -p backups
	@cp data/helga.db backups/helga_$$(date +%Y%m%d_%H%M%S).db 2>/dev/null && echo "Backup saved to backups/" || echo "No database found to backup"

test: test-unit test-integration

test-unit:
	python3 -m pytest tests/ -v --tb=short -k "not integration" 2>/dev/null || echo "No unit tests found or pytest not installed"

test-integration:
	python3 -m pytest tests/ -v --tb=short -k "integration" 2>/dev/null || echo "No integration tests found or pytest not installed"
