function normalize(tag, timestamp, record)
    local attrs = record["attrs"] or {}
    local project = os.getenv("COMPOSE_PROJECT_NAME") or "self-healing-agent"
    if attrs["com.docker.compose.project"] ~= project then
        return -1, timestamp, record
    end
    local path = record["log_file"] or ""
    record["container_id"] = string.match(path, "/containers/([^/]+)/") or "unknown"
    record["service"] = attrs["com.docker.compose.service"] or record["service"] or record["container_id"]
    record["scenario_id"] = record["scenario_id"] or "BASELINE"
    record["correlation_id"] = record["correlation_id"] or "none"
    record["level"] = record["level"] or "INFO"
    return 1, timestamp, record
end
