import os
import unittest

from services.runtime_config import backend_debug_from_env, cors_origins_from_env


class AppConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_cors = os.environ.get("BACKEND_CORS_ORIGINS")
        self._orig_debug = os.environ.get("BACKEND_DEBUG")

    def tearDown(self) -> None:
        if self._orig_cors is None:
            os.environ.pop("BACKEND_CORS_ORIGINS", None)
        else:
            os.environ["BACKEND_CORS_ORIGINS"] = self._orig_cors

        if self._orig_debug is None:
            os.environ.pop("BACKEND_DEBUG", None)
        else:
            os.environ["BACKEND_DEBUG"] = self._orig_debug

    def test_cors_origins_from_env_parses_and_dedupes(self) -> None:
        os.environ["BACKEND_CORS_ORIGINS"] = "http://localhost:3000, https://example.com , http://localhost:3000"
        self.assertEqual(
            cors_origins_from_env(),
            ["http://localhost:3000", "https://example.com"],
        )

    def test_cors_origins_from_env_falls_back_to_defaults(self) -> None:
        os.environ["BACKEND_CORS_ORIGINS"] = "   "
        self.assertEqual(
            cors_origins_from_env(),
            ["http://localhost:3000", "http://127.0.0.1:3000"],
        )

    def test_backend_debug_from_env(self) -> None:
        os.environ["BACKEND_DEBUG"] = "true"
        self.assertTrue(backend_debug_from_env())

        os.environ["BACKEND_DEBUG"] = "0"
        self.assertFalse(backend_debug_from_env())


if __name__ == "__main__":
    unittest.main()
