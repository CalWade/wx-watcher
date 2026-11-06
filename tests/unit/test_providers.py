import unittest

from wx_watcher.domain.agents import AgentJob
from wx_watcher.infrastructure.agents.dsh import DshGateway, validate_options
from wx_watcher.infrastructure.agents.http import HttpAgentGateway
from wx_watcher.infrastructure.agents.legacy import upgrade_task
from wx_watcher.infrastructure.agents.registry import AgentRegistry

from .fakes import PROPOSAL, EmbeddedGateway


class WireClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, route, data=None):
        self.calls.append((route, data))
        return dict(self.response)


class AgentProviderTests(unittest.TestCase):
    def test_dsh_translation_is_confined_to_its_adapter(self):
        wire = WireClient(
            {
                "id": "one",
                "status": "completed",
                "output": PROPOSAL,
                "session_id": "session",
                "request_tool_count": 0,
                "usage": {"input": 2},
                "cost_meter": {"found": True, "own": {"cost": 0.01}},
            }
        )
        gateway = DshGateway(wire)
        job = gateway.submit("one", "question")
        self.assertEqual(job.session_ref, "session")
        self.assertEqual(job.cost["amount"], 0.01)
        self.assertEqual(gateway.poll("one"), job)
        self.assertNotIn("cost_meter", job.as_dict())
        self.assertTrue(
            DshGateway(wire, prepare=lambda: {"ready": True}).prepare()["ready"]
        )
        wire.response = {"ready": True}
        self.assertTrue(gateway.status()["ready"])
        self.assertTrue(gateway.prepare()["ready"])
        wire.response = {"id": "another"}
        with self.assertRaises(ValueError):
            gateway.poll("one")

    def test_neutral_http_provider_uses_the_same_job_contract(self):
        job = AgentJob("one", "http", "completed", output=PROPOSAL, tool_count=0)
        wire = WireClient(job.as_dict())
        gateway = HttpAgentGateway(wire)
        self.assertEqual(gateway.submit("one", "question"), job)
        self.assertEqual(gateway.poll("one"), job)
        wire.response = {"ready": True}
        self.assertTrue(gateway.prepare()["ready"])
        self.assertTrue(gateway.status()["ready"])
        wire.response = {**job.as_dict(), "provider_id": "other"}
        with self.assertRaises(ValueError):
            gateway.poll("one")

    def test_registry_is_explicit_validates_options_and_copies_mutable_settings(self):
        registry = AgentRegistry()
        seen = []

        def factory(options):
            seen.append(options)
            options["port"] = 9999
            options["model"]["name"] = "changed"
            return EmbeddedGateway()

        registry.register("embedded-test", factory)
        original = {"port": 1234, "model": {"name": "original"}}
        self.assertEqual(
            registry.create("embedded-test", original).provider_id, "embedded-test"
        )
        self.assertEqual(original, {"port": 1234, "model": {"name": "original"}})
        self.assertEqual(registry.providers(), ("embedded-test",))
        with self.assertRaises(ValueError):
            registry.register("embedded-test", factory)
        with self.assertRaises(ValueError):
            registry.create("missing", {})
        with self.assertRaises(ValueError):
            registry.validate("embedded-test", [])
        registry.register("wrong", lambda options: EmbeddedGateway())
        with self.assertRaises(ValueError):
            registry.create("wrong", {})
        registry.register(
            "validated", lambda options: EmbeddedGateway(), validate=validate_options
        )
        with self.assertRaises(ValueError):
            registry.validate("validated", {"port": True})
        for options in [{"bad": True}, {"token_file": 1}]:
            with self.assertRaises(ValueError):
                validate_options(options)
        validate_options({"port": 1234, "token_file": "/synthetic/token"})

    def test_legacy_tasks_keep_provider_and_execution_identity(self):
        task = {
            "id": "one",
            "status": "inference",
            "dsh": {"id": "one", "status": "running", "session_id": "old-session"},
        }
        migrated = upgrade_task(task)
        self.assertEqual(migrated["agent_provider"], "dsh")
        self.assertEqual(migrated["agent"]["session_ref"], "old-session")
        self.assertNotIn("dsh", migrated)
        self.assertIn("dsh", task)
