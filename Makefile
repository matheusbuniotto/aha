# Run `make` to see what is available.

# Jev is optional: `make demo JEV=1` adds it, everything works without it.
GROUPS   := --group dbt $(if $(JEV),--group jev)
DBT      := uv run $(GROUPS)
DEV      := uv run $(GROUPS) --group dev
TASK     ?= TASK-1
DEMO     := /tmp/aha-demo-$(TASK)
PROJECT  := examples/jaffle
JOURNAL  ?= .aha/journal.db
GOAL     ?= add a staging model for raw orders, with a uniqueness test on its key
WORKSPACE?= $(DEMO)
ALLOW    ?= --allow dbt_build --allow dbt_seed
REVIEW   ?=

.DEFAULT_GOAL := help
.PHONY: help install check test test-unit lint format doctor packs skills classify \
        demo demo-supervised explore diff shell runs journal clean build reset-demo

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install every dependency group
	uv sync --group dbt --group dev --group jev

check: lint test ## Lint and test, what CI would run

test: ## Run the whole suite, including the dbt end-to-end runs
	$(DEV) pytest -q

test-unit: ## Run only the fast tests, skipping anything that shells out to dbt
	$(DEV) pytest -q -m 'not slow'

lint: ## Check formatting and lint rules
	uv run --group dev ruff format --check .
	uv run --group dev ruff check .

format: ## Apply formatting and autofixable lint rules
	uv run --group dev ruff check --fix .
	uv run --group dev ruff format .

doctor: ## Show model routing and which provider credentials are present
	$(DBT) aha doctor

packs: ## List the registered packs and the tasks each understands
	$(DBT) aha packs

skills: ## List the dbt skills and when each one fires
	@$(DBT) python -c "from pydantic_ai_harness import Skills; from aha.fabric import get; \
	[print(f'  {c.id:22} {c.description}') for c in Skills(get('dbt').skills())._deferred_capabilities]"

classify: ## Show how a request routes, no model call. Override with GOAL="..."
	$(DBT) aha classify "$(GOAL)"

explore: ## Show the table candidates and lineage a run would start from
	$(DBT) aha explore "$(GOAL)" -w $(PROJECT)

reset-demo: ## Copy the example dbt project to a clean scratch workspace
	@rm -rf $(DEMO) && mkdir -p $(DEMO)
	@cd $(PROJECT) && git ls-files | while read -r f; do \
		mkdir -p "$(DEMO)/$$(dirname "$$f")"; cp "$$f" "$(DEMO)/$$f"; done
	@printf '.aha/\n.user.yml\ntarget/\nlogs/\n*.duckdb\n' > $(DEMO)/.gitignore
	@git -C $(DEMO) init -q --initial-branch main
	@git -C $(DEMO) add -A && git -C $(DEMO) -c user.email=demo@local -c user.name=demo \
		commit -qm "jaffle before the agent"
	@echo "clean project at $(DEMO), on main"

demo: reset-demo ## Unattended run on a scratch project. GOAL=".." TASK=.. REVIEW=1
	PYDANTIC_AI_NO_BANNER=1 $(DBT) aha run "$(GOAL)" -t $(TASK) $(if $(REVIEW),--review) \
		-w $(WORKSPACE) -a autonomous $(ALLOW) --journal $(DEMO)/.aha/journal.db

demo-supervised: reset-demo ## Same run, approving each risky call at the terminal
	PYDANTIC_AI_NO_BANNER=1 $(DBT) aha run "$(GOAL)" -t $(TASK) \
		-w $(WORKSPACE) -a supervised --journal $(DEMO)/.aha/journal.db

diff: ## Review what the last demo run changed, on its own branch
	@git -C $(DEMO) log --oneline -1 && git -C $(DEMO) status --short && git -C $(DEMO) diff main

runs: ## List recent runs from the demo journal
	$(DBT) aha runs --journal $(DEMO)/.aha/journal.db

journal: ## Break the last demo run down by event kind
	@uv run python -c "import sqlite3; db = sqlite3.connect('$(DEMO)/.aha/journal.db'); \
	[print(f'{c:5}  {k}') for k, c in db.execute('select kind, count(*) from events group by 1 order by 2 desc')]"

shell: ## Open a dbt shell in the demo project
	cd $(DEMO) && $(DBT) dbt --project-dir . --profiles-dir . debug

build: ## Build the wheel and confirm the skills ship inside it
	uv build --wheel -o dist
	@unzip -l dist/*.whl | grep -c 'SKILL.md' | xargs -I{} echo "  {} skills packaged"

clean: ## Remove build output, dbt artefacts, and local run state
	rm -rf dist $(DEMO) .aha logs .pytest_cache
	find . -name 'target' -path '*/examples/*' -prune -exec rm -rf {} +
	find . -name '*.duckdb' -not -path './.venv/*' -delete
	find . -name '__pycache__' -not -path './.venv/*' -prune -exec rm -rf {} +
