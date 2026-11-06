"""A provider-neutral loopback agent protocol, also usable by an embedded host."""

from ...domain.agents import AgentJob


class HttpAgentGateway:
    provider_id = "http"

    def __init__(self, client):
        self.client = client

    def prepare(self):
        return self.client.request("/v1/prepare", {})

    def status(self):
        return self.client.request("/v1/status")

    def submit(self, id, prompt):
        return self._job(
            id, self.client.request("/v1/jobs", {"id": id, "prompt": prompt})
        )

    def poll(self, id):
        return self._job(id, self.client.request("/v1/jobs/" + id))

    def _job(self, id, value):
        job = AgentJob(**value)
        if job.id != id or job.provider_id != self.provider_id:
            raise ValueError("Agent returned another provider's job")
        return job
