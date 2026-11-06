"""Compatibility for settings written before agent providers were configurable."""


def normalize_agent_settings(settings):
    settings = dict(settings)
    settings.setdefault("agent_provider", "dsh")
    settings.setdefault("agent_options", {"port": settings.get("dsh_port", 8766)})
    return settings


def merge_agent_settings(previous, changes):
    changes = dict(changes)
    provider = changes.get("agent_provider", previous["agent_provider"])
    if provider != previous["agent_provider"]:
        changes.setdefault("agent_options", {})
    if provider == "dsh":
        if "dsh_port" in changes:
            changes["agent_options"] = {
                **previous["agent_options"],
                **changes.get("agent_options", {}),
                "port": changes["dsh_port"],
            }
        elif "port" in changes.get("agent_options", {}):
            changes["dsh_port"] = changes["agent_options"]["port"]
    return changes
