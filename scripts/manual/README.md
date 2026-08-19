# Manual verification scripts

These scripts are historical end-to-end checks that require explicitly started local AgentBoard API and web services. They are not collected by pytest and are not part of CI.

- `verify_documents_api.py` exercises document CRUD, workflow transitions, and comments against the configured local API endpoint.
- `verify_documents_frontend.py` exercises the document UI with Playwright.
- `verify_mermaid_rendering.py` exercises Mermaid rendering with Playwright.

Review the endpoint constants and test credentials inside each script before running it. The scripts create or mutate local test data and should only be used against a disposable development environment.

