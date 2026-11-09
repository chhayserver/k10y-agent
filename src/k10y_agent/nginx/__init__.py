"""k10y agent for Nginx (K10Y_MODE=nginx): reads the configuration, checks and applies changes, and turns the
access log into live numbers per site, route and upstream server. Runs next to Nginx (on the host or in the
k10y/k10y-nginx image)."""
