from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent settings, read from K10Y_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="K10Y_")

    # "agent": talks to its cluster and serves API + dashboard.
    # "dashboard": serves only the web UI (no cluster access); the browser talks to agents.
    mode: str = "agent"
    # Name this agent reports for its cluster; must be unique across clusters.
    cluster_name: str = "default"
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
    # CPU/memory history kept in memory for the live charts.
    metrics_poll_s: float = 5.0
    metrics_retention_s: int = 3600
    # Append-only JSON-lines audit log of control actions.
    audit_path: str = "/data/audit.jsonl"

    @property
    def protected(self) -> List[str]:
        return [ns.strip() for ns in self.protected_namespaces.split(",") if ns.strip()]
