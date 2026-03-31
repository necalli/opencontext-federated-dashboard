import unittest

try:
    from app import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask runtime not available in this environment")
class HealthEndpointTests(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        app = create_app()
        client = app.test_client()

        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("status"), "ok")
        self.assertEqual(payload.get("service"), "opencontext-federated-dashboard")


if __name__ == "__main__":
    unittest.main()
