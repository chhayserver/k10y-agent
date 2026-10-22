from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent settings, read from K10Y_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="K10Y_")

    # "agent": talks to its cluster and serves the cluster API.
    # "hub": central server: web UI, login, users/roles, clusters, audit, alerts (PostgreSQL);
    #        proxies to agents. "dashboard": legacy UI-only mode.
    mode: str = "agent"
    # HTTPS. With no cert/key files a self-signed certificate is generated in tls_dir.
    tls: bool = False
    tls_cert: str = ""
    tls_key: str = ""
    tls_dir: str = "/tmp/k10y-tls"
    # Hub only.
    database_url: str = ""
    # Master key that encrypts stored secrets (agent tokens, AI keys, alert tokens).
    # If empty, a key is generated once and kept in <data_dir>/secret.key.
    secret_key: str = ""
    data_dir: str = "/data"
    admin_user: str = "admin"
    admin_password: str = ""
    session_hours: int = 12
    public_url: str = ""          # hub address used in alert links, e.g. https://k10y.example:8443
    alerts_enabled: bool = True
    # Name this agent reports for its cluster; must be unique across clusters.
    cluster_name: str = "kubernetes"
    # Bearer token required on /api/*. Empty disables auth (local dev only),
    # unless token_secret is set.
    api_token: str = ""
    # When api_token is empty: read the token from this Secret in the agent's own namespace,
    # creating it with a random token on first start (used by install.yaml).
    token_secret: str = ""
    # Used only when not running in-cluster.
    kubeconfig: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"

    # Control actions (scale, delete, apply, ...). Also needs the control RBAC role.
    control_enabled: bool = False
    # Comma-separated namespaces no control action may touch.
    protected_namespaces: str = "kube-system"
    # Browser origins allowed to call this agent directly (dashboard cluster switcher).
    # "*" is safe here because auth is a bearer token, never a cookie. Empty disables CORS.
    cors_origins: str = "*"
    # AI assistant defaults; the dashboard's AI settings (stored in Secret k10y-ai) override them.
    ai_provider: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_base_url: str = ""
    # CPU/memory history kept in memory for the live charts.
    metrics_poll_s: float = 5.0
    metrics_retention_s: int = 3600
    # Append-only JSON-lines audit log of control actions.
    audit_path: str = "/data/audit.jsonl"

    @property
    def protected(self) -> List[str]:
        return [ns.strip() for ns in self.protected_namespaces.split(",") if ns.strip()]
